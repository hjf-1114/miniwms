"""库存变更服务层（参考 Sentry WMS：API → service → SQL，路由不直接写库存）。

所有库存变动（收货/上架/拣货/移库/盘点调整）统一经此模块：
- BEGIN IMMEDIATE 显式事务（SQLite 行级锁语义），防止并发双扣/超卖
- stock 行 upsert（保留零行：清零不删行，区分「用完」与「从未入库」）
- 每次变动写一条或多条 stock_movement 流水，余额 = 流水累加可核对

M21 批次级库存明细层：新增 ``stock_batch`` 作为明细层，``stock`` 仍是汇总层且主键不变。
三层不变式（容差 1e-9，硬校验、无灰度豁免）：

- I1 ``stock.qty == SUM(stock_movement.qty_delta)``，按 (owner, product, location)
- I2 ``stock_batch.qty == SUM(stock_movement.qty_delta)``，按
  (owner, product, location, batch_no, designated_customer_id, goods_property)
- I3 ``stock.qty == SUM(stock_batch.qty)``，按 (owner, product, location)

**纪律**：路由层禁止直接写 ``stock_batch``，一律经 ``adjust_stock(..., batches=...)``。
不传 batches 时由本模块按动作类型自动推断维度，保证老链路也不会让明细层漂移。
"""
import sqlite3  # noqa: F401  (保留以便类型提示；实际由调用方传入连接)

EPS = 1e-9
LEGACY_BATCH_NO = "LEGACY-UNKNOWN"

# stock_batch / stock_movement 共用的批次维度（production_date 只在明细层，不进流水）
BATCH_DIMS = (
    "batch_no", "batch_key", "internal_batch_no", "designated_customer_id",
    "goods_property", "stock_status", "expiry_date",
)


class StockError(Exception):
    """库存操作失败（数量不足/库位不可用等），由路由层转成 4xx 响应。"""


def ensure_stock_schema(db):
    """确保 stock 表存在并具备 allocated_qty 列（M3 建表，M4 加列）。"""
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS stock (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id      INTEGER NOT NULL REFERENCES owner(id),
            product_id    INTEGER NOT NULL REFERENCES product(id),
            location_id   INTEGER NOT NULL REFERENCES location(id),
            qty           REAL    NOT NULL DEFAULT 0,
            allocated_qty REAL    NOT NULL DEFAULT 0,
            updated_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
            UNIQUE(owner_id, product_id, location_id)
        );
        CREATE INDEX IF NOT EXISTS idx_stock_location ON stock(location_id);
        CREATE INDEX IF NOT EXISTS idx_stock_owner ON stock(owner_id);
        """
    )


def legacy_dims(operator=""):
    """兜底维度：可追溯到具体批次时必须显式传 batches，否则统一归到占位明细。

    占位维度与 ``init_db()`` 一次性回填历史流水时使用的维度完全一致，
    因此 I2 对占位行同样成立，不需要任何「灰度豁免」。
    """
    return {
        "batch_no": LEGACY_BATCH_NO, "batch_key": "", "internal_batch_no": "",
        "designated_customer_id": None, "goods_property": "", "stock_status": "",
        "expiry_date": "", "production_date": "", "operator": operator or "",
    }


def _lock_stock_row(db, owner_id, product_id, location_id):
    """BEGIN IMMEDIATE 下读行（存在即锁写），不存在返回 None。"""
    return db.execute(
        "SELECT id, qty, allocated_qty FROM stock "
        "WHERE owner_id = ? AND product_id = ? AND location_id = ?",
        (owner_id, product_id, location_id),
    ).fetchone()


def _find_batch_row(db, owner_id, product_id, location_id, dim):
    """按表达式唯一索引的等价条件查明细行（IFNULL 让 NULL 与 0 视为同一维度）。"""
    return db.execute(
        """SELECT id, qty, allocated_qty FROM stock_batch
           WHERE owner_id = ? AND product_id = ? AND location_id = ?
             AND batch_no = ? AND batch_key = ?
             AND IFNULL(designated_customer_id, 0) = IFNULL(?, 0)
             AND goods_property = ? AND stock_status = ?""",
        (owner_id, product_id, location_id, dim["batch_no"], dim["batch_key"],
         dim.get("designated_customer_id"), dim["goods_property"], dim["stock_status"]),
    ).fetchone()


def upsert_stock_batch(db, owner_id, product_id, location_id, dim, qty_delta,
                       allocated_delta=0.0):
    """明细行 upsert（零行保留：扣到 0 不删行）。

    只允许本模块调用；``dim`` 缺失的字段自动补默认值，保证唯一键判定稳定。
    """
    d = legacy_dims()
    d.update({k: v for k, v in dim.items() if k in d})
    row = _find_batch_row(db, owner_id, product_id, location_id, d)
    if row is None:
        db.execute(
            """INSERT INTO stock_batch
               (owner_id, product_id, location_id, batch_no, batch_key,
                internal_batch_no, designated_customer_id, goods_property,
                stock_status, expiry_date, production_date, qty, allocated_qty)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (owner_id, product_id, location_id, d["batch_no"], d["batch_key"],
             d["internal_batch_no"], d["designated_customer_id"], d["goods_property"],
             d["stock_status"], d["expiry_date"], d["production_date"],
             qty_delta, allocated_delta),
        )
        return
    new_qty = row["qty"] + qty_delta
    new_alloc = row["allocated_qty"] + allocated_delta
    if new_qty < -EPS:
        raise StockError(
            f"批次明细库存不足: batch={d['batch_no']} 在手={row['qty']}, 需扣 {-qty_delta}"
        )
    if new_alloc < -EPS:
        raise StockError(
            f"批次明细分配数不足: batch={d['batch_no']} 已分配={row['allocated_qty']}"
        )
    db.execute(
        "UPDATE stock_batch SET qty = ?, allocated_qty = ?, "
        "updated_at = datetime('now','localtime') WHERE id = ?",
        (new_qty, new_alloc, row["id"]),
    )


def align_stock_batch(db, owner_id, product_id, location_id, book_qty, book_alloc):
    """把明细层对齐到汇总层的**变动前**余额，补齐历史遗留 / 手工造数产生的差额。

    补的是占位明细行、不写新流水：历史流水在 ``init_db()`` 回填时已统一归到同一占位维度，
    因此 I2 仍成立。正常链路每次过账都同步明细，此函数通常为空操作。
    """
    row = db.execute(
        """SELECT COALESCE(SUM(qty), 0) AS q, COALESCE(SUM(allocated_qty), 0) AS a
           FROM stock_batch WHERE owner_id = ? AND product_id = ? AND location_id = ?""",
        (owner_id, product_id, location_id),
    ).fetchone() if _has_batch_table(db) else None
    if row is None:
        return
    dq = book_qty - row["q"]
    da = book_alloc - row["a"]
    if abs(dq) > EPS or abs(da) > EPS:
        upsert_stock_batch(db, owner_id, product_id, location_id, legacy_dims(), dq, da)


def _has_batch_table(db):
    return db.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='stock_batch'"
    ).fetchone()[0] > 0


def spread_batches(db, owner_id, product_id, location_id, qty, *,
                   for_allocation=False):
    """按 FEFO（效期升序，空效期排最后）把数量分摊到现有明细行，返回维度列表。

    出库 / 上架来源位 / 盘点盘亏 / 拣货分配共用此函数；
    ``for_allocation=True`` 时只产生 ``allocated`` 增量、``qty`` 恒为 0（I1/I2 不受影响）。
    """
    if qty <= 0:
        return []
    if not _has_batch_table(db):
        return []
    rows = db.execute(
        """SELECT * FROM stock_batch
           WHERE owner_id = ? AND product_id = ? AND location_id = ? AND qty > ?
           ORDER BY (expiry_date = ''), expiry_date, id""",
        (owner_id, product_id, location_id, EPS),
    ).fetchall()
    remaining, out = qty, []
    for r in rows:
        if remaining <= EPS:
            break
        take = min(r["qty"], remaining)
        dim = {k: r[k] for k in BATCH_DIMS}
        dim["production_date"] = r["production_date"]
        item = dict(dim)
        item["qty"] = 0.0 if for_allocation else take
        item["allocated"] = take if for_allocation else 0.0
        out.append(item)
        remaining -= take
    if remaining > EPS:
        raise StockError(
            f"批次明细不足: location={location_id} 需分摊 {qty}, 明细仅覆盖 {qty - remaining}"
        )
    return out


def _normalize_batches(db, owner_id, product_id, location_id,
                       batches, qty_delta, allocated_delta, operator):
    """把调用方的批次列表规范化；None 时按动作类型自动推断维度。"""
    if batches:
        items = []
        for raw in batches:
            d = legacy_dims(operator)
            d.update({k: v for k, v in raw.items()
                      if k in d and v is not None})
            item = dict(d)
            item["qty"] = float(raw.get("qty", 0))
            item["allocated"] = float(raw.get("allocated", 0))
            items.append(item)
    elif qty_delta < -EPS:
        items = spread_batches(db, owner_id, product_id, location_id, -qty_delta)
        for i in items:
            i["qty"] = -i["qty"]
            # 发货时 qty 与 allocated 同步释放，且落在同一批明细行上
            if allocated_delta < -EPS:
                i["allocated"] = allocated_delta * (i["qty"] / qty_delta)
        if allocated_delta < -EPS and items:
            drift = allocated_delta - sum(i["allocated"] for i in items)
            if abs(drift) > EPS:
                items[0]["allocated"] += drift
    elif abs(qty_delta) <= EPS and allocated_delta > EPS:
        items = spread_batches(db, owner_id, product_id, location_id,
                               allocated_delta, for_allocation=True)
    elif abs(qty_delta) <= EPS and allocated_delta < -EPS:
        items = _release_allocation_items(db, owner_id, product_id, location_id,
                                          -allocated_delta)
    else:
        items = [dict(legacy_dims(operator), qty=qty_delta, allocated=allocated_delta)]

    if not items:
        items = [dict(legacy_dims(operator), qty=qty_delta, allocated=allocated_delta)]
    sq = sum(i["qty"] for i in items)
    sa = sum(i["allocated"] for i in items)
    if abs(sq - qty_delta) > EPS:
        raise StockError(f"批次数量之和 {sq} 与库存变动量 {qty_delta} 不一致")
    if abs(sa - allocated_delta) > EPS:
        raise StockError(f"批次分配量之和 {sa} 与分配变动量 {allocated_delta} 不一致")
    return items


def _release_allocation_items(db, owner_id, product_id, location_id, qty):
    """发货时按已分配明细对称释放（优先释放 allocated_qty 最多的行）。"""
    if qty <= 0 or not _has_batch_table(db):
        return []
    rows = db.execute(
        """SELECT * FROM stock_batch
           WHERE owner_id = ? AND product_id = ? AND location_id = ?
             AND allocated_qty > ? ORDER BY allocated_qty DESC, id""",
        (owner_id, product_id, location_id, EPS),
    ).fetchall()
    remaining, out = qty, []
    for r in rows:
        if remaining <= EPS:
            break
        take = min(r["allocated_qty"], remaining)
        item = {k: r[k] for k in BATCH_DIMS}
        item["production_date"] = r["production_date"]
        item["qty"] = 0.0
        item["allocated"] = -take
        out.append(item)
        remaining -= take
    if remaining > EPS:
        raise StockError(
            f"批次明细已分配数不足: location={location_id} 需释放 {qty}, 缺 {remaining}"
        )
    return out


def _insert_movement(db, owner_id, product_id, location_id, dim,
                     qty_delta, allocated_delta, ref_type, ref_id, note):
    """写一条带批次维度的流水；N 个批次写 N 条，Σ qty_delta == 主账变动量。"""
    db.execute(
        """INSERT INTO stock_movement
           (owner_id, product_id, location_id, qty_delta, ref_type, ref_id, note,
            batch_no, batch_key, internal_batch_no, designated_customer_id,
            goods_property, stock_status, expiry_date, allocated_delta, operator)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (owner_id, product_id, location_id, qty_delta, ref_type, ref_id, note,
         dim["batch_no"], dim["batch_key"], dim["internal_batch_no"],
         dim["designated_customer_id"], dim["goods_property"], dim["stock_status"],
         dim["expiry_date"], allocated_delta, dim.get("operator", "")),
    )


def adjust_stock(db, owner_id, product_id, location_id, qty_delta, ref_type, ref_id,
                 allocated_delta=0.0, note="", batches=None, operator=""):
    """单笔库存变更（调用方负责已开启 BEGIN IMMEDIATE 事务）。

    - qty_delta: 在手数量增减（正入负出）
    - allocated_delta: 已分配数量增减（M5 拣货分配使用）
    - batches: 可选批次维度列表，每项含 ``qty`` / ``allocated`` 与批次字段；
      ``None`` 时按动作自动推断（出库/移库按 FEFO 分摊、分配按已占用明细、入库用占位）
    - 不允许把 qty 或 allocated_qty 扣成负数；扣到 0 保留行
    - 主账只 UPDATE 一次，明细层与流水同步写入，保证 I1/I2/I3
    """
    row = _lock_stock_row(db, owner_id, product_id, location_id)
    book_qty = row["qty"] if row else 0
    book_alloc = row["allocated_qty"] if row else 0
    align_stock_batch(db, owner_id, product_id, location_id, book_qty, book_alloc)

    if row is None:
        # 无库存行时不允许凭空出库/分配（qty_delta<=0 或纯分配都会产生非法负/超配行）
        if qty_delta < 0 or allocated_delta < 0 or allocated_delta > qty_delta:
            raise StockError(
                f"库存不足: location={location_id} 无库存记录, 变更量={qty_delta}"
            )
        db.execute(
            "INSERT INTO stock (owner_id, product_id, location_id, qty, allocated_qty) "
            "VALUES (?, ?, ?, ?, ?)",
            (owner_id, product_id, location_id, qty_delta, allocated_delta),
        )
    else:
        new_qty = row["qty"] + qty_delta
        new_alloc = row["allocated_qty"] + allocated_delta
        if new_qty < 0:
            raise StockError(
                f"库存不足: location={location_id} 在手={row['qty']}, 需扣 {-qty_delta}"
            )
        if new_alloc < 0:
            raise StockError(f"分配数不足: location={location_id} 已分配={row['allocated_qty']}")
        if new_alloc > new_qty:
            raise StockError(
                f"分配数不可超过在手: location={location_id} 在手={new_qty}, 分配后={new_alloc}"
            )
        db.execute(
            "UPDATE stock SET qty = ?, allocated_qty = ?, "
            "updated_at = datetime('now', 'localtime') WHERE id = ?",
            (new_qty, new_alloc, row["id"]),
        )

    items = _normalize_batches(db, owner_id, product_id, location_id,
                               batches, qty_delta, allocated_delta, operator)
    for item in items:
        if _has_batch_table(db):
            upsert_stock_batch(db, owner_id, product_id, location_id,
                               item, item["qty"], item["allocated"])
        _insert_movement(db, owner_id, product_id, location_id, item,
                         item["qty"], item["allocated"], ref_type, ref_id, note)


def stock_up(db, owner_id, product_id, location_id, qty, ref_type, ref_id,
             note="", batches=None, operator=""):
    """入库类变更（收货/上架入目标位），qty 必须为正。"""
    if qty <= 0:
        raise StockError("入库数量必须大于 0")
    adjust_stock(db, owner_id, product_id, location_id, qty, ref_type, ref_id,
                 note=note, batches=batches, operator=operator)


def stock_move(db, owner_id, product_id, from_location_id, to_location_id, qty,
               ref_type_out, ref_type_in, ref_id, note="", batches=None,
               operator=""):
    """移库类变更（上架/移库）：同数量一减一增，同一事务原子完成。

    从来源位按 FEFO 分摊出的批次维度，原样搬到目标位，保证 I3 在两端都成立；
    返回实际分摊的批次列表，供上架动作写 ``batch_location`` 实物分布。
    """
    if qty <= 0:
        raise StockError("移库数量必须大于 0")
    src = batches or spread_batches(db, owner_id, product_id, from_location_id, qty)
    if src:
        out_items = []
        in_items = []
        for b in src:
            take = abs(b["qty"])
            out_items.append(dict(b, qty=-take))
            in_items.append(dict(b, qty=take))
        out_batches, in_batches = out_items, in_items
    else:
        out_batches = in_batches = None
    adjust_stock(db, owner_id, product_id, from_location_id, -qty,
                 ref_type_out, ref_id, note=note, batches=out_batches, operator=operator)
    adjust_stock(db, owner_id, product_id, to_location_id, qty,
                 ref_type_in, ref_id, note=note, batches=in_batches, operator=operator)
    return src
