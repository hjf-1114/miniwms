"""出库单（DN）模块：/api/dns。

流程（单据驱动库存，与 ASN 对称的出库动线）：
1. 创建 DN（指定货主 + 客户 + 行：物料 × 订购数量）
2. 拣货 pick：从存储/拣货位按「可用量」分配（allocated_qty +，在手不变），
   分配明细落 dn_pick，流水 dn_pick（qty_delta=0，仅审计分配动作）
3. 发货 ship：要求所有行拣满，按 dn_pick 明细逐库位扣减在手与分配，
   流水 dn_ship（qty_delta=-qty），状态 shipped
4. 取消：仅未拣货可 cancel

超卖拦截：拣货时可用量 = qty - allocated_qty，且全程 BEGIN IMMEDIATE
事务锁行，并发两张单抢同一库位库存时后到者失败。
"""
import math

import scope as S
from flask import Blueprint, jsonify, request

import constants as C
from inventory_service import StockError, adjust_stock
from main import get_db, gen_doc_no, request_payload
import reservation_audit as RA

dn_bp = Blueprint("dn", __name__, url_prefix="/api/dns")


def _operator():
    from flask import g
    user = getattr(g, "current_user", None) or {}
    return user.get("username", "") if isinstance(user, dict) else ""


def _load_dn(db, dn_id):
    return db.execute("SELECT * FROM dn WHERE id = ?", (dn_id,)).fetchone()


def _load_lines(db, dn_id):
    return db.execute(
        """SELECT dl.*, p.sku, p.name AS product_name
           FROM dn_line dl JOIN product p ON p.id = dl.product_id
           WHERE dl.dn_id = ? ORDER BY dl.id""",
        (dn_id,),
    ).fetchall()


def _load_picks(db, dn_id):
    return db.execute(
        """SELECT dp.*, l.code AS location_code, dl.product_id
           FROM dn_pick dp
           JOIN location l ON l.id = dp.location_id
           JOIN dn_line dl ON dl.id = dp.line_id
           WHERE dp.dn_id = ? ORDER BY dp.id""",
        (dn_id,),
    ).fetchall()


def _check_location(db, location_id, allowed_types, label):
    row = db.execute(
        "SELECT code, status, loc_type FROM location WHERE id = ?", (location_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"{label}库位不存在: id={location_id}")
    if row["status"] != "active":
        raise ValueError(f"{label}库位已停用: {row['code']}")
    if row["loc_type"] not in allowed_types:
        raise ValueError(f"{label}库位类型不正确: {row['code']}")
    return row


def _advance_status(db, dn_id, lines):
    """根据行拣货进度推进状态（open → picking；shipped 由发货显式设置）。"""
    any_picked = any(l["picked_qty"] > 0 for l in lines)
    status = C.DN_PICKING if any_picked else C.DN_OPEN
    db.execute(
        "UPDATE dn SET status = ?, updated_at = datetime('now','localtime') WHERE id = ?",
        (status, dn_id),
    )
    return status


def _dict_line(l):
    d = dict(l)
    d["remaining_pick"] = max(0, l["ordered_qty"] - l["picked_qty"])
    return d


@dn_bp.route("", methods=["POST"])
def create_dn():
    payload = request_payload()
    code = str(payload.get("code") or "").strip()
    owner_id = payload.get("owner_id")
    customer_id = payload.get("customer_id")
    lines = payload.get("lines")
    note = str(payload.get("note") or "")

    # M20 剩余（v3.3）：4 个单据标识位 + 外部单据类别，只落字段不做业务校验
    flags, external_doc_type, flag_errors = C.parse_doc_flags(payload)

    # 单据字段（M16）：单据类型/外部单号/订单来源/来源仓库/承运商/自定义字段
    doc_type = str(payload.get("doc_type") or "").strip() or "销售"
    external_no = str(payload.get("external_no") or "").strip()
    order_source = str(payload.get("order_source") or "").strip()
    source_warehouse = payload.get("source_warehouse")
    carrier_name = str(payload.get("carrier_name") or "").strip()
    extra1 = str(payload.get("extra1") or "").strip()
    extra2 = str(payload.get("extra2") or "").strip()
    extra3 = str(payload.get("extra3") or "").strip()

    errors = list(flag_errors)
    if doc_type not in C.DN_DOC_TYPES:
        errors.append(f"doc_type 只能是 {'/'.join(C.DN_DOC_TYPES)}")
    try:
        owner_id = int(owner_id)
    except (TypeError, ValueError):
        errors.append("owner_id 必填且为整数")
    try:
        customer_id = int(customer_id)
    except (TypeError, ValueError):
        errors.append("customer_id 必填且为整数")
    if not isinstance(lines, list) or not lines:
        errors.append("lines 必须为非空数组")
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    parsed_lines = []
    for i, ln in enumerate(lines):
        try:
            pid = int(ln.get("product_id"))
            qty = float(ln.get("ordered_qty"))
        except (TypeError, ValueError, AttributeError):
            return jsonify({"ok": False, "errors": [f"lines[{i}] 字段不合法"]}), 400
        if math.isnan(qty) or qty <= 0:
            return jsonify({"ok": False, "errors": [f"lines[{i}] ordered_qty 必须为正数"]}), 400
        parsed_lines.append((pid, qty))
    # 同一物料在明细中不可重复（dn_line 唯一约束 (dn_id, product_id)）
    dup = {pid for pid, _ in parsed_lines}
    if len(dup) != len(parsed_lines):
        return jsonify({"ok": False, "errors": ["同一物料在明细中出现多次，请合并数量"]}), 400

    db = get_db()
    if db.execute("SELECT id FROM owner WHERE id = ?", (owner_id,)).fetchone() is None:
        return jsonify({"ok": False, "errors": [f"货主不存在: id={owner_id}"]}), 400

    # 来源仓库（多仓库）：存在且启用
    wh_code = None
    if source_warehouse not in (None, "", 0):
        try:
            source_warehouse = int(source_warehouse)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "errors": ["source_warehouse 必须为整数"]}), 400
        wh = db.execute("SELECT code, status FROM warehouse WHERE id = ?", (source_warehouse,)).fetchone()
        if wh is None:
            return jsonify({"ok": False, "errors": [f"来源仓库不存在: id={source_warehouse}"]}), 400
        if wh["status"] != "active":
            return jsonify({"ok": False, "errors": [f"来源仓库已停用: {wh['code']}"]}), 400
        wh_code = wh["code"]

    # 单号：留空则自动生成 DN-YYYYMMDD-NNN（英文开头 + 日期流水）
    if not code:
        code = gen_doc_no(db, "dn", "DN")
    elif len(code) > 32:
        return jsonify({"ok": False, "errors": ["code 最长 32 字符"]}), 400
    if db.execute("SELECT id FROM dn WHERE code = ?", (code,)).fetchone() is not None:
        return jsonify({"ok": False, "errors": [f"DN 编码已存在: {code}"]}), 409

    # 客户必须存在，且归属与单据货主一致（多货主隔离：甲货主不能选乙货主的客户）
    cust = db.execute(
        "SELECT id, owner_id FROM customer WHERE id = ?", (customer_id,)
    ).fetchone()
    if cust is None:
        return jsonify({"ok": False, "errors": [f"客户不存在: id={customer_id}"]}), 400
    if cust["owner_id"] != owner_id:
        return jsonify({"ok": False, "errors": [
            f"客户 id={customer_id} 不属于该货主（多货主隔离）"
        ]}), 400

    # 物料必须存在，且货主归属与单据一致（多货主隔离）
    for pid, _ in parsed_lines:
        p = db.execute(
            "SELECT owner_id FROM product WHERE id = ?", (pid,)
        ).fetchone()
        if p is None:
            return jsonify({"ok": False, "errors": [f"物料不存在: id={pid}"]}), 400
        if (p["owner_id"] or None) != owner_id:
            return jsonify({"ok": False, "errors": [
                f"物料 id={pid} 的货主与 DN 货主不一致（多货主隔离）"
            ]}), 400

    # 明细汇总：订单总数量 = Σ 订购数量，品类数 = 不同物料数
    total_qty = round(sum(q for _, q in parsed_lines), 6)
    category_count = len({pid for pid, _ in parsed_lines})

    cur = db.execute(
        """INSERT INTO dn (code, owner_id, customer_id, doc_type, external_no, order_source,
               source_warehouse, carrier_name, total_qty, category_count, extra1, extra2, extra3, note,
               is_virtual, is_direct, is_cold_chain, is_hazard, external_doc_type)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (code, owner_id, customer_id, doc_type, external_no, order_source,
         source_warehouse, carrier_name, total_qty, category_count, extra1, extra2, extra3, note)
        + tuple(flags[k] for k in C.DOC_FLAG_FIELDS) + (external_doc_type,),
    )
    dn_id = cur.lastrowid
    db.executemany(
        "INSERT INTO dn_line (dn_id, product_id, ordered_qty) VALUES (?, ?, ?)",
        [(dn_id, pid, qty) for pid, qty in parsed_lines],
    )
    db.commit()
    dn = _load_dn(db, dn_id)
    body = dict(dn)
    body["source_warehouse_code"] = wh_code
    return jsonify({"ok": True, "dn": body,
                    "lines": [_dict_line(l) for l in _load_lines(db, dn_id)]}), 201


@dn_bp.route("", methods=["GET"])
def list_dns():
    args = request.args
    status = (args.get("status") or "").strip()
    keyword = (args.get("keyword") or "").strip()
    try:
        page = max(1, int(args.get("page", 1)))
        page_size = min(100, max(1, int(args.get("page_size", 20))))
    except ValueError:
        return jsonify({"ok": False, "errors": ["page/page_size 必须为整数"]}), 400

    where, params = [], []
    if status:
        where.append("d.status = ?")
        params.append(status)
    if keyword:
        where.append("d.code LIKE ?")
        params.append(f"%{keyword}%")
    S.scoped_where(where, params, table="d")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    db = get_db()
    total = db.execute(
        f"SELECT COUNT(*) AS n FROM dn d {where_sql}", params
    ).fetchone()["n"]
    rows = db.execute(
        f"""SELECT d.*, o.code AS owner_code, o.name AS owner_name,
                   c.code AS customer_code, c.name AS customer_name,
                   w.code AS source_warehouse_code, w.name AS source_warehouse_name
            FROM dn d
            JOIN owner o ON o.id = d.owner_id
            LEFT JOIN customer c ON c.id = d.customer_id
            LEFT JOIN warehouse w ON w.id = d.source_warehouse {where_sql}
            ORDER BY d.id DESC LIMIT ? OFFSET ?""",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()
    return jsonify({"ok": True, "total": total, "page": page,
                    "page_size": page_size, "dns": [dict(r) for r in rows]})


@dn_bp.route("/<int:dn_id>", methods=["GET"])
def get_dn(dn_id):
    db = get_db()
    dn = _load_dn(db, dn_id)
    if dn is None:
        return jsonify({"ok": False, "error": "DN 不存在"}), 404
    o = db.execute("SELECT code, name FROM owner WHERE id = ?", (dn["owner_id"],)).fetchone()
    body = dict(dn)
    body["owner_code"], body["owner_name"] = (o["code"], o["name"]) if o else (None, None)
    c = db.execute("SELECT code, name FROM customer WHERE id = ?", (dn["customer_id"],)).fetchone()
    body["customer_code"], body["customer_name"] = (c["code"], c["name"]) if c else (None, None)
    wh = db.execute("SELECT code, name FROM warehouse WHERE id = ?", (dn["source_warehouse"],)).fetchone()
    body["source_warehouse_code"], body["source_warehouse_name"] = (wh["code"], wh["name"]) if wh else (None, None)
    return jsonify({"ok": True, "dn": body,
                    "lines": [_dict_line(l) for l in _load_lines(db, dn_id)],
                    "picks": [dict(p) for p in _load_picks(db, dn_id)]})


@dn_bp.route("/<int:dn_id>/pick", methods=["POST"])
def pick(dn_id):
    """拣货：按可用量分配库存（allocated_qty +，在手不变），明细落 dn_pick。"""
    payload = request_payload()
    try:
        line_id = int(payload.get("line_id"))
        location_id = int(payload.get("location_id"))
        qty = float(payload.get("qty"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "errors": ["line_id/location_id/qty 必填且合法"]}), 400

    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        dn = _load_dn(db, dn_id)
        if dn is None:
            raise ValueError("DN 不存在")
        if dn["status"] in (C.DN_SHIPPED, C.DN_CANCELLED):
            raise ValueError(f"单据状态为 {dn['status']}，不可拣货")
        line = db.execute("SELECT * FROM dn_line WHERE id = ? AND dn_id = ?",
                          (line_id, dn_id)).fetchone()
        if line is None:
            raise ValueError("行不存在或不属于该 DN")
        remaining = line["ordered_qty"] - line["picked_qty"]
        if math.isnan(qty) or qty <= 0 or qty > remaining:
            raise ValueError(f"拣货数量必须为正且不超过剩余待拣 {remaining}")
        _check_location(db, location_id, ("storage", "picking"), "拣货来源")

        # 超卖拦截：可用量 = 在手 - 已分配
        row = db.execute(
            "SELECT qty, allocated_qty FROM stock "
            "WHERE owner_id = ? AND product_id = ? AND location_id = ?",
            (dn["owner_id"], line["product_id"], location_id),
        ).fetchone()
        on_hand = row["qty"] if row else 0
        allocated = row["allocated_qty"] if row else 0
        if on_hand - allocated < qty:
            raise ValueError(
                f"可用库存不足: 库位在手 {on_hand}, 已分配 {allocated}, "
                f"可用 {on_hand - allocated}, 需拣 {qty}"
            )

        adjust_stock(db, dn["owner_id"], line["product_id"], location_id,
                     0, C.MOVEMENT_DN_PICK, dn_id, allocated_delta=qty,
                     note=f"DN {dn['code']} 拣货分配")
        db.execute("INSERT INTO dn_pick (dn_id, line_id, location_id, qty) "
                   "VALUES (?, ?, ?, ?)", (dn_id, line_id, location_id, qty))
        db.execute("UPDATE dn_line SET picked_qty = picked_qty + ? WHERE id = ?",
                   (qty, line_id))
        lines = _load_lines(db, dn_id)
        status = _advance_status(db, dn_id, lines)
        db.commit()
    except (ValueError, StockError) as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 400

    return jsonify({"ok": True, "status": status,
                    "lines": [_dict_line(l) for l in _load_lines(db, dn_id)]})


@dn_bp.route("/<int:dn_id>/reservation-check", methods=["GET"])
def reservation_check(dn_id):
    """M23：发货前**只读**预检专属库存风险，供前端做二次确认。

    用户 2026-11-09 二次确认口径：风险操作允许继续，但必须事前提示 + 留痕。
    本端点不拦截、不写库、不改分配；风险清单按与发货同一套 FEFO 分摊推断。
    """
    data = RA.inspect_dn(get_db(), dn_id)
    if data is None:
        return jsonify(ok=False, error="DN 不存在"), 404
    return jsonify(ok=True, **data)


@dn_bp.route("/<int:dn_id>/ship", methods=["POST"])
def ship(dn_id):
    """发货：所有行拣满后，按拣货明细逐库位扣减在手与分配，状态 → shipped。

    M23 补充：专属库存发给其他客户**允许继续**；发货前把风险清单写入
    ``customer_reservation_log``（与扣减同事务），响应带 warnings 供前端回显。
    """
    db = get_db()
    payload = request_payload() or {}
    acknowledged = bool(payload.get("risk_acknowledged"))
    operator = _operator()
    risks, logged = [], 0
    dn = _load_dn(db, dn_id)
    if dn is None:
        return jsonify({"ok": False, "error": "DN 不存在"}), 404
    if dn["status"] in (C.DN_SHIPPED, C.DN_CANCELLED):
        return jsonify({"ok": False, "error": f"状态为 {dn['status']}，不可重复发货"}), 409
    try:
        db.execute("BEGIN IMMEDIATE")
        dn = _load_dn(db, dn_id)
        if dn["status"] in (C.DN_SHIPPED, C.DN_CANCELLED):
            raise ValueError(f"单据状态为 {dn['status']}，不可重复发货")
        lines = _load_lines(db, dn_id)
        short = [l["sku"] for l in lines if l["picked_qty"] < l["ordered_qty"]]
        if short:
            raise ValueError(f"存在未拣满的行，不可发货: {', '.join(short)}")

        # 按拣货明细逐库位扣减（qty 与 allocated 同步清掉）
        picks = _load_picks(db, dn_id)
        # M23 风险放行：先只读预检 + 落审计日志，随后照常发货（不拦截、不改分配）
        inspection = RA.inspect_dn(db, dn_id)
        risks = inspection["risks"] if inspection else []
        logged = RA.record_risk(db, dn_id, risks, operator, acknowledged)
        for p in picks:
            adjust_stock(db, dn["owner_id"], p["product_id"],
                         p["location_id"], -p["qty"], C.MOVEMENT_DN_SHIP, dn_id,
                         allocated_delta=-p["qty"],
                         note=f"DN {dn['code']} 发货出库")
        db.execute(
            "UPDATE dn SET status = ?, updated_at = datetime('now','localtime') WHERE id = ?",
            (C.DN_SHIPPED, dn_id),
        )
        db.commit()
    except (ValueError, StockError) as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 400

    return jsonify({"ok": True, "status": C.DN_SHIPPED,
                    "risk_count": len(risks), "risk_logged": logged,
                    "warnings": risks,
                    "lines": [_dict_line(l) for l in _load_lines(db, dn_id)]})


@dn_bp.route("/<int:dn_id>/cancel", methods=["POST"])
def cancel_dn(dn_id):
    """取消：仅未拣货的 DN 可取消。"""
    db = get_db()
    dn = _load_dn(db, dn_id)
    if dn is None:
        return jsonify({"ok": False, "error": "DN 不存在"}), 404
    if dn["status"] not in (C.DN_OPEN, C.DN_PICKING):
        return jsonify({"ok": False, "error": f"状态为 {dn['status']}，不可取消"}), 409
    picked = db.execute(
        "SELECT COUNT(*) AS n FROM dn_line WHERE dn_id = ? AND picked_qty > 0",
        (dn_id,),
    ).fetchone()["n"]
    if picked:
        return jsonify({"ok": False, "error": "已拣货的单据不可取消，请先释放拣货分配"}), 409
    db.execute("UPDATE dn SET status = ?, updated_at = datetime('now','localtime') WHERE id = ?",
               (C.DN_CANCELLED, dn_id))
    db.commit()
    return jsonify({"ok": True, "status": C.DN_CANCELLED})
