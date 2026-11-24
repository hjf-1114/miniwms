"""Order grouping, temporary pick faces and wave-specific replenishment."""

from collections import Counter, defaultdict
from itertools import combinations
import math

import constants as C
import scope as S
from inventory_service import adjust_stock, stock_move

EPS = 1e-8


def migrate(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS wave_settings (
            warehouse_id INTEGER PRIMARY KEY REFERENCES warehouse(id),
            full_group_id INTEGER NOT NULL REFERENCES location_group(id),
            pool_group_id INTEGER REFERENCES location_group(id),
            special_types TEXT NOT NULL DEFAULT '',
            cold_locations TEXT NOT NULL DEFAULT '',
            hazard_locations TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS wave_preparation (
            id INTEGER PRIMARY KEY,
            warehouse_id INTEGER NOT NULL REFERENCES warehouse(id),
            owner_id INTEGER NOT NULL REFERENCES owner(id),
            group_id INTEGER NOT NULL REFERENCES location_group(id),
            transfer_id INTEGER UNIQUE REFERENCES transfer(id),
            kind TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            wave_no TEXT,
            operator TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS wave_preparation_order (
            preparation_id INTEGER NOT NULL REFERENCES wave_preparation(id),
            dn_id INTEGER NOT NULL REFERENCES dn(id),
            locked INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (preparation_id, dn_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS wave_one_active_order
          ON wave_preparation_order(dn_id) WHERE locked = 1;
        CREATE TABLE IF NOT EXISTS wave_pick_face (
            preparation_id INTEGER NOT NULL REFERENCES wave_preparation(id),
            product_id INTEGER NOT NULL REFERENCES product(id),
            location_id INTEGER NOT NULL REFERENCES location(id),
            previous_group TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (preparation_id, product_id),
            UNIQUE (preparation_id, location_id)
        );
        CREATE TABLE IF NOT EXISTS wave_replenishment_line (
            id INTEGER PRIMARY KEY,
            preparation_id INTEGER NOT NULL REFERENCES wave_preparation(id),
            stock_batch_id INTEGER NOT NULL REFERENCES stock_batch(id),
            target_id INTEGER NOT NULL REFERENCES location(id),
            qty REAL NOT NULL,
            actual_qty REAL,
            UNIQUE (preparation_id, stock_batch_id, target_id)
        );
    """)


def settings(db, warehouse):
    row = db.execute("SELECT * FROM wave_settings WHERE warehouse_id=?", (warehouse,)).fetchone()
    return dict(row) if row else {}


def locked_preparation(db, dn_id):
    row = db.execute("SELECT preparation_id FROM wave_preparation_order WHERE dn_id=? AND locked=1",
                     (dn_id,)).fetchone()
    return row["preparation_id"] if row else None


def generated_transfer(db, transfer_id):
    return db.execute("SELECT 1 FROM wave_preparation WHERE transfer_id=?", (transfer_id,)).fetchone()


def permit_location(db, order, row, preparation_id=None):
    config = settings(db, order["source_warehouse"])
    if config and special_order(order, config):
        full = db.execute("SELECT code FROM location_group WHERE id=? AND status='active'",
                          (config["full_group_id"],)).fetchone()
        if not full or row["location_group"] != full["code"]:
            return False
        for flag, field in (("is_cold_chain", "cold_locations"), ("is_hazard", "hazard_locations")):
            if order[flag] and row["location_code"] not in split_codes(config[field]):
                return False
    temp = db.execute("""SELECT w.id,g.status FROM location_group g JOIN wave_preparation w ON w.group_id=g.id
                         WHERE g.code=? AND w.kind<>'full' AND w.status IN ('pending','ready')""",
                      (row["location_group"],)).fetchone()
    return not temp or (temp["id"] == preparation_id and temp["status"] == "active")


def special_order(order, config):
    return (order["is_cold_chain"] or order["is_hazard"]
            or order["external_doc_type"] in split_codes(config.get("special_types")))


def split_codes(value):
    return {code.strip() for code in (value or "").replace("，", ",").split(",") if code.strip()}


def group(db, warehouse, group_id):
    row = db.execute("SELECT * FROM location_group WHERE id=? AND warehouse_id=? AND status='active'",
                     (group_id, warehouse)).fetchone()
    if not row:
        raise ValueError("请选择当前仓库已启用的货位组")
    return dict(row)


def save_settings(db, warehouse, payload):
    target = group(db, warehouse, payload.get("full_group_id"))
    pool = group(db, warehouse, payload["pool_group_id"]) if payload.get("pool_group_id") else None
    if pool and (pool["id"] == target["id"] or db.execute(
            "SELECT 1 FROM wave_preparation WHERE group_id=?", (pool["id"],)).fetchone()):
        raise ValueError("备用拣货组需独立于全量组和已生成的波次货位组")
    if db.execute("SELECT 1 FROM wave_preparation WHERE group_id=? AND kind<>'full'", (target["id"],)).fetchone():
        raise ValueError("活动或短通道货位组不能设为全量组")
    fields = [str(payload.get(k) or "").strip() for k in ("special_types", "cold_locations", "hazard_locations")]
    allowed = {r["code"] for r in db.execute(
        "SELECT code FROM location WHERE warehouse_id=? AND location_group=? AND status='active'",
        (warehouse, target["code"]))}
    if not (split_codes(fields[1]) | split_codes(fields[2])) <= allowed:
        raise ValueError("特殊作业货位必须是全量组内已启用的货位")
    db.execute("""INSERT INTO wave_settings(warehouse_id,full_group_id,pool_group_id,special_types,
                     cold_locations,hazard_locations) VALUES (?,?,?,?,?,?)
                  ON CONFLICT(warehouse_id) DO UPDATE SET full_group_id=excluded.full_group_id,
                     pool_group_id=excluded.pool_group_id,
                     special_types=excluded.special_types, cold_locations=excluded.cold_locations,
                     hazard_locations=excluded.hazard_locations""",
               (warehouse, target["id"], pool["id"] if pool else None, *fields))


def load_orders(db, warehouse, owner, args=None, ids=None):
    args = args or {}
    params = [warehouse, owner]
    conditions = ["d.source_warehouse=?", "d.owner_id=?", "d.workflow_required=1", "d.status='open'",
                  "NOT EXISTS (SELECT 1 FROM dn_pick p WHERE p.dn_id=d.id)",
                  """NOT EXISTS (SELECT 1 FROM wave_preparation_order w
                                 WHERE w.dn_id=d.id AND w.locked=1)"""]
    for key, clause in (("date_from", "d.created_at>=?"), ("date_to", "substr(d.created_at,1,10)<=?"),
                        ("customer_id", "d.customer_id=?"), ("doc_type", "d.doc_type=?"),
                        ("keyword", "(d.code LIKE ? OR d.external_no LIKE ?)")):
        if args.get(key):
            conditions.append(clause)
            value = args[key]
            params.extend([f"%{value}%"] * 2 if key == "keyword" else [value])
    if ids is not None:
        conditions.append("d.id IN (" + ",".join("?" for _ in ids) + ")")
        params.extend(ids)
    orders = {}
    for row in db.execute(f"""
        SELECT d.*, dl.id AS line_id, dl.product_id, dl.extra,
               dl.ordered_qty-dl.cancelled_qty AS demand, p.sku, p.name AS product_name
        FROM dn d JOIN dn_line dl ON dl.dn_id=d.id JOIN product p ON p.id=dl.product_id
        WHERE {" AND ".join(conditions)} AND dl.ordered_qty-dl.cancelled_qty>?
        ORDER BY d.created_at,d.id,dl.id""", (*params, EPS)):
        order = orders.setdefault(row["id"], {**dict(row), "lines": []})
        order["lines"].append({"id": row["line_id"], "product_id": row["product_id"], "extra": row["extra"],
                               "demand": row["demand"], "sku": row["sku"], "product_name": row["product_name"]})
    return list(orders.values())


def sku_set(order):
    return frozenset(line["product_id"] for line in order["lines"])


def short_skus(orders, limit, goal):
    combos = Counter(map(sku_set, orders))
    chosen = frozenset()
    while combos:
        covered = sum(n for skus, n in combos.items() if skus <= chosen)
        if covered >= len(orders) * goal or len(chosen) >= limit:
            break
        missing = Counter()
        for skus, count in combos.items():
            new = skus - chosen
            if 0 < len(new) <= limit - len(chosen):
                missing[new] += count
        scores = []
        for new in missing:
            if 2 ** len(new) < len(missing):
                added = sum(missing[frozenset(pair)] for size in range(1, len(new) + 1)
                            for pair in combinations(new, size))
            else:
                added = sum(n for pair, n in missing.items() if pair <= new)
            scores.append((added / len(new), added, -len(new), tuple(sorted(new))))
        if not scores:
            break
        chosen |= frozenset(max(scores)[-1])
    return chosen


def summarize(orders):
    products = {}
    combos = Counter()
    for order in orders:
        combos[tuple(sorted({line["sku"] for line in order["lines"]}))] += 1
        for line in order["lines"]:
            sku = products.setdefault(line["product_id"], {
                "product_id": line["product_id"], "sku": line["sku"], "name": line["product_name"], "qty": 0})
            sku["qty"] += line["demand"]
    return {"orders": [{"id": order["id"], "code": order["code"],
                        "skus": sorted({line["sku"] for line in order["lines"]})} for order in orders],
            "products": sorted(products.values(), key=lambda row: row["sku"]),
            "combinations": [{"skus": skus, "count": count} for skus, count in combos.most_common()]}


def analyze(db, warehouse, owner, args):
    orders = load_orders(db, warehouse, owner, args)
    config = settings(db, warehouse)
    minimum = int(args.get("activity_min") or 10)
    limit = int(args.get("sku_limit") or 20)
    goal = float(args.get("coverage") or 85) / 100
    if not (1 <= minimum <= 10000 and 1 <= limit <= 50 and 0 < goal <= 1):
        raise ValueError("请检查活动订单门槛、SKU 上限及覆盖率")
    special, ordinary, singles, activity = [], [], defaultdict(list), []
    for order in orders:
        if special_order(order, config):
            special.append(order)
        else:
            ordinary.append(order)
            skus = sku_set(order)
            if len(skus) == 1:
                singles[next(iter(skus))].append(order)
    activities = [items for items in singles.values() if len(items) >= minimum]
    activity_ids = {order["id"] for items in activities for order in items}
    remaining = [order for order in ordinary if order["id"] not in activity_ids]
    selected = short_skus(remaining, limit, goal)
    short = [order for order in remaining if sku_set(order) <= selected]
    full = [order for order in remaining if not sku_set(order) <= selected]
    for items in activities:
        activity.append(summarize(items))
    return {"total": len(orders), "remaining": len(remaining), "activity": activity,
            "short": summarize(short), "full": summarize(full), "special": summarize(special)}


def pick_locations(db, warehouse):
    pool = db.execute("""SELECT g.code FROM wave_settings s JOIN location_group g ON g.id=s.pool_group_id
                         WHERE s.warehouse_id=? AND g.status='active'""", (warehouse,)).fetchone()
    return [dict(row) for row in db.execute("""
        SELECT l.id,l.code,l.name,l.capacity,l.allow_mix_batch,l.capacity_volume,l.location_group
        FROM location l WHERE l.warehouse_id=? AND l.status='active' AND l.loc_type='picking'
          AND COALESCE(l.location_group,'') IN ('',?)
          AND NOT EXISTS (SELECT 1 FROM stock s WHERE s.location_id=l.id AND s.qty>0)
        ORDER BY l.code""", (warehouse, pool["code"] if pool else ""))]


def prep_record(db, warehouse, prep_id):
    row = db.execute("""SELECT w.*,g.code AS group_code,t.code AS transfer_code
                       FROM wave_preparation w JOIN location_group g ON g.id=w.group_id
                       LEFT JOIN transfer t ON t.id=w.transfer_id
                       WHERE w.id=? AND w.warehouse_id=?""", (prep_id, warehouse)).fetchone()
    if not row:
        raise ValueError("当前仓库找不到组波方案")
    S.assert_owner_allowed(S.current_scope(), row["owner_id"])
    return dict(row)


def detail(db, warehouse, prep_id):
    prep = prep_record(db, warehouse, prep_id)
    prep["moves"] = [dict(row) for row in db.execute("""
        SELECT m.id,m.qty,m.actual_qty,p.sku,p.name AS product_name,s.batch_no,s.expiry_date,
               f.code AS source,t.code AS target
        FROM wave_replenishment_line m JOIN stock_batch s ON s.id=m.stock_batch_id
        JOIN location f ON f.id=s.location_id JOIN location t ON t.id=m.target_id
        JOIN product p ON p.id=s.product_id WHERE m.preparation_id=? ORDER BY f.code,p.sku,m.id""", (prep_id,))]
    prep["orders"] = [dict(row) for row in db.execute("""
        SELECT d.id,d.code,d.status FROM dn d JOIN wave_preparation_order w ON w.dn_id=d.id
        WHERE w.preparation_id=? ORDER BY d.code""", (prep_id,))]
    prep["faces"] = [dict(row) for row in db.execute("""
        SELECT p.sku,p.name,l.code FROM wave_pick_face f JOIN product p ON p.id=f.product_id
        JOIN location l ON l.id=f.location_id WHERE f.preparation_id=? ORDER BY l.code""", (prep_id,))]
    return prep


def reserve(db, stock, qty, transfer_id, operator):
    import outbound_allocation as OA
    adjust_stock(db, stock["owner_id"], stock["product_id"], stock["location_id"], 0,
                 "wave_replenishment_hold" if qty > 0 else "wave_replenishment_release", transfer_id,
                 allocated_delta=qty, batches=[dict(OA._batch_dims(stock), qty=0, allocated=qty)],
                 note="波次补货来源占用", operator=operator)


def plan_moves(db, orders, warehouse, owner, faces, locations):
    import outbound_allocation as OA
    moves = defaultdict(float)
    remaining = {}
    lots = defaultdict(set)
    for order in orders:
        for line in order["lines"]:
            product = line["product_id"]
            target = faces[product]
            need = line["demand"]
            stocks = db.execute("""
                SELECT s.*,l.code AS location_code,l.status AS loc_status,l.loc_type
                FROM stock_batch s JOIN location l ON l.id=s.location_id
                WHERE s.owner_id=? AND s.product_id=? AND l.warehouse_id=?
                  AND l.id<>? AND s.qty-s.allocated_qty>?
                  AND NOT EXISTS (
                    SELECT 1 FROM wave_preparation w JOIN location_group g ON g.id=w.group_id
                    WHERE g.code=l.location_group AND w.kind<>'full' AND w.status IN ('pending','ready'))
                ORDER BY s.expiry_date='',s.expiry_date,s.id""", (owner, product, warehouse, target, EPS))
            for row in stocks:
                stock = dict(row)
                if OA._skip_reason(stock, order["customer_id"], True) or not OA._order_match(db, line, stock):
                    continue
                lot = stock["batch_no"]
                if not locations[target]["allow_mix_batch"] and lots[target] and lot not in lots[target]:
                    continue
                available = remaining.setdefault(stock["id"], stock["qty"] - stock["allocated_qty"])
                take = min(available, need)
                if take <= EPS:
                    continue
                moves[stock["id"], target] += take
                remaining[stock["id"]] -= take
                lots[target].add(lot)
                need -= take
                if need <= EPS:
                    break
            if need > EPS:
                raise ValueError(f"{order['code']} 的 {line['sku']} 缺 {need:g}，请检查批次库存或减少订单")
    return moves


def prepare(db, warehouse, owner, payload, operator):
    kind = payload.get("kind")
    ids = list(dict.fromkeys(int(value) for value in payload.get("dn_ids", [])))
    if not ids or kind not in ("activity", "short", "full"):
        raise ValueError("请选择需要组波的订单")
    orders = load_orders(db, warehouse, owner, ids=ids)
    if len(orders) != len(ids):
        raise ValueError("订单已分配、已确认组波或已取消，请重新分析")
    config = settings(db, warehouse)
    if any(special_order(order, config) for order in orders):
        raise ValueError("特殊订单须走全量组人工处理")
    products = {row["product_id"]: row for row in summarize(orders)["products"]}
    if kind == "activity" and len(products) != 1:
        raise ValueError("单品活动组只能包含一个 SKU")

    faces, moves, transfer_id = {}, {}, None
    if kind == "full":
        target_group = group(db, warehouse, config.get("full_group_id"))
        group_id = target_group["id"]
    else:
        faces = {int(k): int(v) for k, v in payload.get("faces", {}).items()}
        available = {row["id"]: row for row in pick_locations(db, warehouse)}
        if faces.keys() != products.keys() or len(faces) != len(set(faces.values())) or not set(faces.values()) <= available.keys():
            raise ValueError("请为每个 SKU 选择一个不同的空拣货位")
        if kind == "short" and len(faces) > 50:
            raise ValueError("短通道最多支持 50 个 SKU")
        for pid, location in faces.items():
            check_capacity(db, available[location], pid, products[pid]["qty"])
        moves = plan_moves(db, orders, warehouse, owner, faces, available)
        serial = db.execute("SELECT COALESCE(MAX(id),0)+1 FROM wave_preparation").fetchone()[0]
        code = f"WAVE-{warehouse}-{serial}"
        label = "单品活动" if kind == "activity" else "短通道"
        group_id = db.execute("INSERT INTO location_group(code,name,warehouse_id) VALUES (?,?,?)",
                              (code, f"{label} {serial}", warehouse)).lastrowid
        db.executemany("UPDATE location SET location_group=? WHERE id=?",
                       [(code, location) for location in faces.values()])
        transfer_id = db.execute("INSERT INTO transfer(code,owner_id,note) VALUES (?,?,?)",
                                 (f"RP-{warehouse}-{serial}", owner, f"组波补货 {code}")).lastrowid

    prep_id = db.execute("""
        INSERT INTO wave_preparation(warehouse_id,owner_id,group_id,transfer_id,kind,status,operator)
        VALUES (?,?,?,?,?,?,?)""",
        (warehouse, owner, group_id, transfer_id, kind, "pending" if moves else "ready", operator)).lastrowid
    db.executemany("INSERT INTO wave_preparation_order(preparation_id,dn_id) VALUES (?,?)",
                   [(prep_id, dn_id) for dn_id in ids])
    db.executemany("INSERT INTO wave_pick_face VALUES (?,?,?,?)",
                   [(prep_id, pid, loc, available[loc]["location_group"] or "") for pid, loc in faces.items()])
    totals = defaultdict(float)
    for (stock_id, target), qty in moves.items():
        stock = dict(db.execute("SELECT * FROM stock_batch WHERE id=?", (stock_id,)).fetchone())
        reserve(db, stock, qty, transfer_id, operator)
        totals[stock["product_id"], stock["location_id"], target] += qty
        db.execute("INSERT INTO wave_replenishment_line(preparation_id,stock_batch_id,target_id,qty) VALUES (?,?,?,?)",
                   (prep_id, stock_id, target, qty))
    db.executemany("""INSERT INTO transfer_line(transfer_id,product_id,from_location_id,to_location_id,qty)
                      VALUES (?,?,?,?,?)""", [(transfer_id, *key, qty) for key, qty in totals.items()])
    return detail(db, warehouse, prep_id)


def check_capacity(db, location, product_id, qty):
    product = db.execute("SELECT length_cm,width_cm,height_cm FROM product WHERE id=?",
                         (product_id,)).fetchone()
    used = db.execute("SELECT COALESCE(SUM(qty),0) FROM stock WHERE location_id=?",
                      (location["id"],)).fetchone()[0]
    volume = (product["length_cm"] or 0) * (product["width_cm"] or 0) * (product["height_cm"] or 0) / 1e6
    for limit, unit in (("capacity", 1), ("capacity_volume", volume)):
        if location[limit] and (not unit or (used + qty) * unit > location[limit] + EPS):
            raise ValueError(f"货位 {location['code']} 容量不足或商品缺少容量资料")


def finish(db, warehouse, prep_id, payload, operator):
    import outbound_allocation as OA
    prep = prep_record(db, warehouse, prep_id)
    if prep["status"] in ("ready", "allocated"):
        return detail(db, warehouse, prep_id)
    if prep["status"] != "pending":
        raise ValueError("此方案不可完成补货")
    target_group = group(db, warehouse, prep["group_id"])
    rows = db.execute("SELECT * FROM wave_replenishment_line WHERE preparation_id=? ORDER BY id", (prep_id,)).fetchall()
    actuals = payload.get("actuals")
    if actuals is not None and (not isinstance(actuals, dict) or set(actuals) != {str(r["id"]) for r in rows}):
        raise ValueError("请填写每条补货明细的实到量")
    totals = defaultdict(float)
    for row in rows:
        actual = row["qty"] if actuals is None else float(actuals[str(row["id"])])
        if not math.isfinite(actual) or not 0 <= actual <= row["qty"]:
            raise ValueError("实到量必须介于零与计划量之间")
        stock = dict(db.execute("SELECT * FROM stock_batch WHERE id=?", (row["stock_batch_id"],)).fetchone())
        source = db.execute("SELECT * FROM location WHERE id=?", (stock["location_id"],)).fetchone()
        target = db.execute("SELECT * FROM location WHERE id=?", (row["target_id"],)).fetchone()
        if (source["warehouse_id"] != warehouse or target["warehouse_id"] != warehouse
                or source["status"] != "active" or target["status"] != "active"
                or target["location_group"] != target_group["code"]):
            raise ValueError("补货货位已变更或停用，请核查")
        if actual > EPS:
            for present in db.execute("SELECT * FROM stock_batch WHERE location_id=? AND qty>?", (target["id"], EPS)):
                if (present["owner_id"] != stock["owner_id"] or present["product_id"] != stock["product_id"]
                        or (not target["allow_mix_batch"] and present["batch_no"] != stock["batch_no"])):
                    raise ValueError(f"货位 {target['code']} 内库存不符合当前商品或批号")
            check_capacity(db, target, stock["product_id"], actual)
        reserve(db, stock, -row["qty"], prep["transfer_id"], operator)
        if actual > EPS:
            stock_move(db, stock["owner_id"], stock["product_id"], stock["location_id"], target["id"],
                       actual, C.MOVEMENT_MOVE_OUT, C.MOVEMENT_MOVE_IN, prep["transfer_id"],
                       note=f"波次 {prep_id} 补货实到", batches=[dict(OA._batch_dims(stock), qty=actual)], operator=operator)
        totals[stock["product_id"], stock["location_id"], target["id"]] += actual
        db.execute("UPDATE wave_replenishment_line SET actual_qty=? WHERE id=?", (actual, row["id"]))
    db.executemany("""UPDATE transfer_line SET qty=? WHERE transfer_id=? AND product_id=?
                      AND from_location_id=? AND to_location_id=?""",
                   [(qty, prep["transfer_id"], *key) for key, qty in totals.items()])
    db.execute("UPDATE transfer SET status='done',updated_at=datetime('now','localtime') WHERE id=?",
               (prep["transfer_id"],))
    set_status(db, prep_id, "ready", operator)
    return detail(db, warehouse, prep_id)


def set_status(db, prep_id, status, operator, wave_no=None):
    db.execute("""UPDATE wave_preparation SET status=?,operator=?,wave_no=?,updated_at=datetime('now','localtime')
                  WHERE id=?""", (status, operator, wave_no, prep_id))


def cancel(db, warehouse, prep_id, operator):
    prep = prep_record(db, warehouse, prep_id)
    if prep["status"] == "allocated":
        still_working = db.execute("""SELECT 1 FROM wave_preparation_order w JOIN dn d ON d.id=w.dn_id
            WHERE w.preparation_id=? AND d.status NOT IN ('open','cancelled','shipped')""", (prep_id,)).fetchone()
        if still_working:
            raise ValueError("请先通过现有出库流程释放分配，或完成本波拣货发货")
    if prep["status"] == "pending":
        for row in db.execute("SELECT * FROM wave_replenishment_line WHERE preparation_id=?", (prep_id,)):
            stock = dict(db.execute("SELECT * FROM stock_batch WHERE id=?", (row["stock_batch_id"],)).fetchone())
            reserve(db, stock, -row["qty"], prep["transfer_id"], operator)
        db.execute("UPDATE transfer SET status='cancelled',updated_at=datetime('now','localtime') WHERE id=?",
                   (prep["transfer_id"],))
    db.execute("UPDATE wave_preparation_order SET locked=0 WHERE preparation_id=?", (prep_id,))
    if prep["kind"] != "full":
        db.execute("""UPDATE location SET location_group=(SELECT f.previous_group FROM wave_pick_face f
                      WHERE f.preparation_id=? AND f.location_id=location.id)
                      WHERE location_group=? AND id IN (SELECT location_id FROM wave_pick_face WHERE preparation_id=?)
                      AND NOT EXISTS (SELECT 1 FROM stock WHERE location_id=location.id AND qty>0)""",
                   (prep_id, prep["group_code"], prep_id))
    set_status(db, prep_id, "cancelled", operator)
    return detail(db, warehouse, prep_id)


def release(db, warehouse, prep_id, operator):
    import outbound_allocation as OA
    prep = prep_record(db, warehouse, prep_id)
    if prep["status"] == "allocated":
        return detail(db, warehouse, prep_id)
    if prep["status"] != "ready":
        raise ValueError("补货到位后才能分配")
    target = group(db, warehouse, prep["group_id"])
    ids = [r["id"] for r in detail(db, warehouse, prep_id)["orders"]]
    if not ids or any(special_order(db.execute("SELECT * FROM dn WHERE id=?", (dn_id,)).fetchone(),
                                    settings(db, warehouse)) for dn_id in ids):
        raise ValueError("订单含特殊标识，请撤销方案后人工处理")
    result = OA.plan(db, ids, operator=operator, pick_group=target["code"], apply=True, preparation_id=prep_id)
    if result["shortages"]:
        missing = result["shortages"][0]
        raise ValueError(f"{missing.get('sku','')} 在目标组不足，未分配。请检查实到量或重新分析")
    db.execute("UPDATE wave_preparation_order SET locked=0 WHERE preparation_id=?", (prep_id,))
    set_status(db, prep_id, "allocated", operator, result["wave_no"])
    return detail(db, warehouse, prep_id)
