"""库存变更服务层（参考 Sentry WMS：API → service → SQL，路由不直接写库存）。

所有库存变动（收货/上架/拣货/移库/盘点调整）统一经此模块：
- BEGIN IMMEDIATE 显式事务（SQLite 行级锁语义），防止并发双扣/超卖
- stock 行 upsert（保留零行：清零不删行，区分「用完」与「从未入库」）
- 每次变动写一条 stock_movement 流水，余额 = 流水累加可核对
"""
import sqlite3  # noqa: F401  (保留以便类型提示；实际由调用方传入连接)


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


def _lock_stock_row(db, owner_id, product_id, location_id):
    """BEGIN IMMEDIATE 下读行（存在即锁写），不存在返回 None。"""
    return db.execute(
        "SELECT id, qty, allocated_qty FROM stock "
        "WHERE owner_id = ? AND product_id = ? AND location_id = ?",
        (owner_id, product_id, location_id),
    ).fetchone()


def adjust_stock(db, owner_id, product_id, location_id, qty_delta, ref_type, ref_id,
                 allocated_delta=0.0, note=""):
    """单笔库存变更（调用方负责已开启 BEGIN IMMEDIATE 事务）。

    - qty_delta: 在手数量增减（正入负出）
    - allocated_delta: 已分配数量增减（M5 拣货分配使用）
    - 不允许把 qty 或 allocated_qty 扣成负数；扣到 0 保留行
    - 同时写一条 stock_movement 流水
    """
    row = _lock_stock_row(db, owner_id, product_id, location_id)
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
    db.execute(
        "INSERT INTO stock_movement (owner_id, product_id, location_id, qty_delta, "
        "ref_type, ref_id, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (owner_id, product_id, location_id, qty_delta, ref_type, ref_id, note),
    )


def stock_up(db, owner_id, product_id, location_id, qty, ref_type, ref_id, note=""):
    """入库类变更（收货/上架入目标位），qty 必须为正。"""
    if qty <= 0:
        raise StockError("入库数量必须大于 0")
    adjust_stock(db, owner_id, product_id, location_id, qty, ref_type, ref_id, note=note)


def stock_move(db, owner_id, product_id, from_location_id, to_location_id, qty,
               ref_type_out, ref_type_in, ref_id, note=""):
    """移库类变更（上架/移库）：同数量一减一增，同一事务原子完成。"""
    if qty <= 0:
        raise StockError("移库数量必须大于 0")
    adjust_stock(db, owner_id, product_id, from_location_id, -qty,
                 ref_type_out, ref_id, note=note)
    adjust_stock(db, owner_id, product_id, to_location_id, qty,
                 ref_type_in, ref_id, note=note)
