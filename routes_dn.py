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
import json
import math
import sqlite3
from datetime import date

import scope as S
from flask import Blueprint, g, jsonify, request

import constants as C
from batch_service import inventory_batch_info
from inventory_service import BATCH_DIMS, StockError, adjust_stock, spread_batches
from main import current_scope, get_control_db, get_db, gen_doc_no, request_payload
from routes_asn import LINE_SNAPSHOT_FIELDS, _check_customer_owner, _line_snapshot, _parse_customer_id
from dn_transfer_service import prepare_destination, create_transfer_asn
from outbound_allocation import AllocationError, _under_review, manual_allocate, manual_candidates, unallocate
from wave_analysis import locked_preparation
import reservation_audit as RA

dn_bp = Blueprint("dn", __name__, url_prefix="/api/dns")


def _operator():
    from flask import g
    user = getattr(g, "current_user", None) or {}
    return user.get("username", "") if isinstance(user, dict) else ""


def _load_dn(db, dn_id):
    row = db.execute("SELECT * FROM dn WHERE id = ?", (dn_id,)).fetchone()
    if row and g.warehouse_id and row["source_warehouse"] not in (None, g.warehouse_id):
        raise S.ScopeError("该出库单不属于当前仓库", 403)
    return row


def _load_lines(db, dn_id):
    return db.execute(
        """SELECT dl.*, d.workflow_required, p.sku, p.name AS product_name, p.spec, p.article_no, p.unit,
                  COALESCE((SELECT SUM(dp.confirmed_qty) FROM dn_pick dp WHERE dp.line_id = dl.id), 0) AS confirmed_qty,
                  p.brand, p.mfg_enterprise, p.origin, p.reg_cert_no, p.registrant,
                  p.mfg_license, p.shelf_life_days
           FROM dn_line dl JOIN product p ON p.id = dl.product_id JOIN dn d ON d.id = dl.dn_id
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


def _check_location(db, location_id, allowed_types, label, warehouse=None):
    row = db.execute(
        "SELECT code, status, loc_type, warehouse_id FROM location WHERE id = ?", (location_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"{label}库位不存在: id={location_id}")
    if row["status"] != "active":
        raise ValueError(f"{label}库位已停用: {row['code']}")
    if row["loc_type"] not in allowed_types:
        raise ValueError(f"{label}库位类型不正确: {row['code']}")
    if warehouse and row["warehouse_id"] not in (warehouse, None):
        raise ValueError(f"{label}库位不属于来源仓库: {row['code']}")
    return row


def _advance_status(db, dn_id, lines):
    """根据行拣货进度推进状态（open → picking；shipped 由发货显式设置）。"""
    any_picked = any(l["picked_qty"] > 0 for l in lines)
    if all(line["ordered_qty"] - line["cancelled_qty"] - line["picked_qty"] <= 1e-9 for line in lines):
        pending = db.execute("SELECT 1 FROM dn_pick WHERE dn_id = ? AND wave_no != '' LIMIT 1", (dn_id,)).fetchone()
        status = C.DN_ALLOCATED if pending else C.DN_PICKED
    else:
        status = C.DN_PICKING if any_picked else C.DN_OPEN
    db.execute(
        "UPDATE dn SET status = ?, updated_at = datetime('now','localtime') WHERE id = ?",
        (status, dn_id),
    )
    return status


def _dict_line(l):
    d = dict(l)
    snap = _line_snapshot(l)
    d["line_snapshot"] = {key: snap.get(key) or d.get(key, "") for key in LINE_SNAPSHOT_FIELDS}
    d["allocated_qty"] = l["picked_qty"]
    d["required_qty"] = l["ordered_qty"] - l["cancelled_qty"]
    d["remaining_allocate"] = max(0, d["required_qty"] - d["allocated_qty"])
    if l["workflow_required"]:
        d["picked_qty"] = l["confirmed_qty"]
    d["remaining_pick"] = max(0, d["required_qty"] - d["picked_qty"])
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
    source_warehouse = payload.get("source_warehouse") or g.warehouse_id
    target_warehouse = None
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
    if doc_type == "调拨" and customer_id in (None, ""):
        customer_id = None
    else:
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
            snapshot = {key: str(ln.get(key) or "").strip() for key in LINE_SNAPSHOT_FIELDS}
        except (TypeError, ValueError, AttributeError):
            return jsonify({"ok": False, "errors": [f"lines[{i}] 字段不合法"]}), 400
        if not math.isfinite(qty) or qty <= 0:
            return jsonify({"ok": False, "errors": [f"lines[{i}] ordered_qty 必须为正数"]}), 400
        if snapshot["goods_property"] and snapshot["goods_property"] not in C.GOODS_PROPERTIES:
            return jsonify(ok=False, errors=[f"lines[{i}] 货品属性无效"]), 400
        parsed_lines.append((pid, qty, snapshot))
    # 同一物料在明细中不可重复（dn_line 唯一约束 (dn_id, product_id)）
    dup = {pid for pid, _, _ in parsed_lines}
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
        if g.warehouse_id and source_warehouse != g.warehouse_id:
            return jsonify(ok=False, errors=["来源仓库必须为当前仓库"]), 400
    if doc_type == "调拨":
        try:
            target_warehouse = int(payload.get("target_warehouse"))
        except (TypeError, ValueError):
            return jsonify(ok=False, errors=["调拨单必须选择调入仓库"]), 400
        if not source_warehouse or target_warehouse <= 0 or target_warehouse == source_warehouse:
            return jsonify(ok=False, errors=["调拨单的调出仓库与调入仓库必须不同"]), 400
        control = get_control_db()
        target = S.check_warehouse_access(current_scope(), target_warehouse, control)
        if target["status"] != "active":
            return jsonify(ok=False, errors=["调入仓库已停用"]), 400
        designated_owners = S.owner_warehouse_grid(control, target_warehouse)
        if designated_owners and owner_id not in designated_owners:
            return jsonify(ok=False, errors=["该货主未配置到调入仓库"]), 400

    # 单号：留空则自动生成 DN-YYYYMMDD-NNN（英文开头 + 日期流水）
    if not code:
        code = gen_doc_no(db, "dn", "DN")
    elif len(code) > 32:
        return jsonify({"ok": False, "errors": ["code 最长 32 字符"]}), 400
    if db.execute("SELECT id FROM dn WHERE code = ?", (code,)).fetchone() is not None:
        return jsonify({"ok": False, "errors": [f"DN 编码已存在: {code}"]}), 409

    # 客户必须存在，且归属与单据货主一致（多货主隔离：甲货主不能选乙货主的客户）
    cust = db.execute(
        "SELECT * FROM customer WHERE id = ?", (customer_id,)
    ).fetchone()
    if customer_id is not None and cust is None:
        return jsonify({"ok": False, "errors": [f"客户不存在: id={customer_id}"]}), 400
    if cust is not None and cust["owner_id"] != owner_id:
        return jsonify({"ok": False, "errors": [
            f"客户 id={customer_id} 不属于该货主（多货主隔离）"
        ]}), 400

    # 物料必须存在，且货主归属与单据一致（多货主隔离）
    for pid, _, snapshot in parsed_lines:
        p = db.execute(
            "SELECT * FROM product WHERE id = ?", (pid,)
        ).fetchone()
        if p is None:
            return jsonify({"ok": False, "errors": [f"物料不存在: id={pid}"]}), 400
        if (p["owner_id"] or None) != owner_id:
            return jsonify({"ok": False, "errors": [
                f"物料 id={pid} 的货主与 DN 货主不一致（多货主隔离）"
            ]}), 400
        designated, error = _parse_customer_id(snapshot["designated_customer_id"])
        error = error or _check_customer_owner(db, owner_id, designated)
        if error:
            return jsonify(ok=False, errors=[error]), 400
        for key, product_key in (("product_name", "name"), ("production_enterprise", "mfg_enterprise"),
                                 *((name, name) for name in ("spec", "article_no", "unit", "brand", "mfg_enterprise",
                                                             "origin", "reg_cert_no", "registrant", "mfg_license", "shelf_life_days"))):
            snapshot[key] = snapshot[key] or str(p[product_key] or "")

    # 明细汇总：订单总数量 = Σ 订购数量，品类数 = 不同物料数
    total_qty = round(sum(q for _, q, _ in parsed_lines), 6)
    category_count = len(parsed_lines)
    contacts = tuple(str(payload.get("customer_" + key) or (cust[key] if cust else "") or "").strip()
                     for key in ("name", "contact", "phone", "address"))

    cur = db.execute(
        """INSERT INTO dn (code, owner_id, customer_id, doc_type, external_no, order_source,
               source_warehouse, carrier_name, total_qty, category_count, extra1, extra2, extra3, note,
               is_virtual, is_direct, is_cold_chain, is_hazard, external_doc_type, target_warehouse,
               customer_name, customer_contact, customer_phone, customer_address)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (code, owner_id, customer_id, doc_type, external_no, order_source,
         source_warehouse, carrier_name, total_qty, category_count, extra1, extra2, extra3, note)
        + tuple(flags[k] for k in C.DOC_FLAG_FIELDS) + (external_doc_type, target_warehouse) + contacts,
    )
    dn_id = cur.lastrowid
    if payload.get("workflow_required") is True:
        db.execute("UPDATE dn SET workflow_required = 1 WHERE id = ?", (dn_id,))
    db.executemany(
        "INSERT INTO dn_line (dn_id, product_id, ordered_qty, extra) VALUES (?, ?, ?, ?)",
        [(dn_id, pid, qty, json.dumps(snapshot, ensure_ascii=False)) for pid, qty, snapshot in parsed_lines],
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
        where.append("(d.code LIKE ? OR d.external_no LIKE ?)")
        params.extend([f"%{keyword}%"] * 2)
    try:
        for field in ("owner_id", "customer_id"):
            if args.get(field):
                where.append(f"d.{field} = ?")
                params.append(int(args[field]))
        for field, op in (("date_from", ">="), ("date_to", "<=")):
            if args.get(field):
                where.append(f"date(d.created_at) {op} ?")
                params.append(date.fromisoformat(args[field]).isoformat())
    except ValueError:
        return jsonify(ok=False, errors=["货主、客户或日期格式不正确"]), 400
    if g.warehouse_id:
        where.append("(d.source_warehouse = ? OR d.source_warehouse IS NULL)")
        params.append(g.warehouse_id)
    S.scoped_where(where, params, table="d")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    db = get_db()
    total = db.execute(
        f"SELECT COUNT(*) AS n FROM dn d {where_sql}", params
    ).fetchone()["n"]
    rows = db.execute(
        f"""SELECT d.*, o.code AS owner_code, o.name AS owner_name,
                   c.code AS customer_code, COALESCE(NULLIF(d.customer_name, ''), c.name) AS customer_display_name,
                   w.code AS source_warehouse_code, w.name AS source_warehouse_name,
                   dest.code AS target_warehouse_code, dest.name AS target_warehouse_name
            FROM dn d
            JOIN owner o ON o.id = d.owner_id
            LEFT JOIN customer c ON c.id = d.customer_id
            LEFT JOIN warehouse w ON w.id = d.source_warehouse
            LEFT JOIN warehouse dest ON dest.id = d.target_warehouse {where_sql}
            ORDER BY d.id DESC LIMIT ? OFFSET ?""",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()
    return jsonify({"ok": True, "total": total, "page": page,
                    "page_size": page_size, "dns": [
                        dict(r, customer_name=r["customer_display_name"]) for r in rows
                    ]})


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
    body["customer_code"] = c["code"] if c else None
    body["customer_name"] = body["customer_name"] or (c["name"] if c else "")
    wh = db.execute("SELECT code, name FROM warehouse WHERE id = ?", (dn["source_warehouse"],)).fetchone()
    body["source_warehouse_code"], body["source_warehouse_name"] = (wh["code"], wh["name"]) if wh else (None, None)
    target = db.execute("SELECT code, name FROM warehouse WHERE id = ?", (dn["target_warehouse"],)).fetchone()
    body["target_warehouse_code"], body["target_warehouse_name"] = (target["code"], target["name"]) if target else (None, None)
    picks = [dict(p) for p in _load_picks(db, dn_id)]
    for p in picks:
        p["batch_info"] = inventory_batch_info(db, dn["owner_id"], p["product_id"], p)
    return jsonify({"ok": True, "dn": body,
                    "lines": [_dict_line(l) for l in _load_lines(db, dn_id)],
                    "picks": picks})


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
        if dn["status"] in (C.DN_PICKED, C.DN_REVIEWED, C.DN_SHIPPED, C.DN_CANCELLED):
            raise ValueError(f"单据状态为 {dn['status']}，不可拣货")
        if _under_review(db, dn_id):
            raise ValueError("复核中的出库单不可修改拣货")
        if dn["workflow_required"]:
            raise ValueError("请先分配库存，再按分配明细确认拣货")
        line = db.execute("SELECT * FROM dn_line WHERE id = ? AND dn_id = ?",
                          (line_id, dn_id)).fetchone()
        if line is None:
            raise ValueError("行不存在或不属于该 DN")
        remaining = line["ordered_qty"] - line["cancelled_qty"] - line["picked_qty"]
        if math.isnan(qty) or qty <= 0 or qty > remaining:
            raise ValueError(f"拣货数量必须为正且不超过剩余待拣 {remaining}")
        _check_location(db, location_id, ("storage", "picking"), "拣货来源", dn["source_warehouse"] or g.warehouse_id)

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

        picked_batches = spread_batches(db, dn["owner_id"], line["product_id"], location_id,
                                        qty, for_allocation=True)
        adjust_stock(db, dn["owner_id"], line["product_id"], location_id,
                     0, C.MOVEMENT_DN_PICK, dn_id, allocated_delta=qty,
                     batches=picked_batches, note=f"DN {dn['code']} 拣货分配")
        for batch in picked_batches:
            db.execute("""
                INSERT INTO dn_pick (dn_id, line_id, location_id, qty, batch_no, batch_key,
                    internal_batch_no, designated_customer_id, goods_property, stock_status,
                    expiry_date, production_date, confirmed_qty)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (dn_id, line_id, location_id, batch["allocated"], batch["batch_no"], batch["batch_key"],
                  batch["internal_batch_no"], batch["designated_customer_id"], batch["goods_property"],
                  batch["stock_status"], batch["expiry_date"], batch["production_date"], batch["allocated"]))
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


@dn_bp.route("/<int:dn_id>/confirm-pick", methods=["POST"])
def confirm_pick(dn_id):
    db = get_db()
    payload = request_payload()
    try:
        db.execute("BEGIN IMMEDIATE")
        dn = _load_dn(db, dn_id)
        if not dn or dn["status"] not in (C.DN_ALLOCATED, C.DN_PICKING):
            raise ValueError("仅分配完毕的出库单可确认拣货")
        if _under_review(db, dn_id):
            raise ValueError("已进入复核的单据不可修改拣货")
        if any(line["remaining_allocate"] > 1e-9 for line in map(_dict_line, _load_lines(db, dn_id))):
            raise ValueError("仍有待分配数量，无法确认拣货")
        if payload.get("pick_id"):
            record = db.execute("SELECT * FROM dn_pick WHERE id = ? AND dn_id = ?",
                                (int(payload["pick_id"]), dn_id)).fetchone()
            qty = float(payload.get("qty"))
            if not record or not math.isfinite(qty) or not 0 < qty <= record["qty"] - (record["confirmed_qty"] or 0) + 1e-9:
                raise ValueError("拣货数量不得超过该分配批次的剩余数量")
            db.execute("UPDATE dn_pick SET confirmed_qty = MIN(qty, COALESCE(confirmed_qty, 0) + ?) WHERE id = ?",
                       (qty, record["id"]))
        elif not dn["workflow_required"]:
            db.execute("UPDATE dn_pick SET confirmed_qty = qty WHERE dn_id = ?", (dn_id,))
        else:
            raise ValueError("请选择要拣货的分配明细")
        left = db.execute("SELECT 1 FROM dn_pick WHERE dn_id = ? AND qty - COALESCE(confirmed_qty, 0) > 1e-9",
                          (dn_id,)).fetchone()
        status = C.DN_PICKING if left else C.DN_PICKED
        db.execute("UPDATE dn SET status = ?, updated_at = datetime('now','localtime') WHERE id = ?",
                   (status, dn_id))
        db.commit()
    except (TypeError, ValueError) as exc:
        db.rollback()
        return jsonify(ok=False, error=str(exc)), 409
    return jsonify(ok=True, status=status)


@dn_bp.get("/<int:dn_id>/allocation-candidates")
def allocation_candidates(dn_id):
    db = get_db()
    if _load_dn(db, dn_id) is None:
        return jsonify(ok=False, error="出库单不存在"), 404
    try:
        return jsonify(ok=True, stocks=manual_candidates(db, dn_id, request.args.get("line_id", type=int)))
    except AllocationError as exc:
        return jsonify(ok=False, error=str(exc)), 400


@dn_bp.post("/<int:dn_id>/allocate")
def allocate_line(dn_id):
    db = get_db()
    payload = request_payload()
    try:
        db.execute("BEGIN IMMEDIATE")
        status = manual_allocate(db, dn_id, int(payload.get("line_id")), int(payload.get("stock_id")),
                                 float(payload.get("qty")), _operator())
        db.commit()
    except (AllocationError, ValueError, TypeError, StockError) as exc:
        db.rollback()
        return jsonify(ok=False, error=str(exc)), 400
    return jsonify(ok=True, status=status)


@dn_bp.post("/<int:dn_id>/lines/<int:line_id>/cancel")
def cancel_line(dn_id, line_id):
    db = get_db()
    try:
        qty = float(request_payload().get("qty"))
        db.execute("BEGIN IMMEDIATE")
        dn = _load_dn(db, dn_id)
        if locked_preparation(db, dn_id):
            raise ValueError("请先撤销组波方案，再取消订单明细数量")
        if not dn or dn["status"] not in (C.DN_OPEN, C.DN_ALLOCATED, C.DN_PICKING) or _under_review(db, dn_id):
            raise ValueError("当前出库单状态不允许取消明细数量")
        line = db.execute("SELECT * FROM dn_line WHERE id = ? AND dn_id = ?", (line_id, dn_id)).fetchone()
        if not line or not math.isfinite(qty) or not 0 < qty <= line["ordered_qty"] - line["cancelled_qty"] - line["picked_qty"] + 1e-9:
            raise ValueError("仅可取消未分配数量，已分配库存请先释放")
        db.execute("UPDATE dn_line SET cancelled_qty = cancelled_qty + ? WHERE id = ?", (qty, line_id))
        lines = [_dict_line(row) for row in _load_lines(db, dn_id)]
        if all(row["required_qty"] <= 1e-9 for row in lines):
            status = C.DN_CANCELLED
        elif all(row["remaining_allocate"] <= 1e-9 for row in lines):
            status = C.DN_PICKED if all(row["remaining_pick"] <= 1e-9 for row in lines) else C.DN_ALLOCATED
        else:
            status = dn["status"]
        db.execute("UPDATE dn SET status = ?, updated_at = datetime('now','localtime') WHERE id = ?", (status, dn_id))
        db.commit()
    except (ValueError, TypeError) as exc:
        db.rollback()
        return jsonify(ok=False, error=str(exc)), 400
    return jsonify(ok=True, status=status)


@dn_bp.route("/<int:dn_id>/ship", methods=["POST"])
def ship(dn_id):
    return _ship_dn(dn_id, request_payload())


@dn_bp.post("/ship-box")
def ship_box():
    db = get_db()
    code = str(request_payload().get("box_code") or "").strip()
    box = db.execute("""SELECT b.dn_id, d.owner_id FROM dn_package b JOIN dn d ON d.id = b.dn_id
                       WHERE b.code = ?""", (code,)).fetchone()
    if not box:
        return jsonify(ok=False, error="未找到箱码"), 404
    S.assert_owner_allowed(current_scope(), box["owner_id"])
    return _ship_dn(box["dn_id"], {"box_code": code})


def _ship_dn(dn_id, payload):
    """发货：所有行拣满后，按拣货明细逐库位扣减在手与分配，状态 → shipped。

    M23 补充：专属库存发给其他客户**允许继续**；发货前把风险清单写入
    ``customer_reservation_log``（与扣减同事务），响应带 warnings 供前端回显。
    """
    db = get_db()
    acknowledged = bool(payload.get("risk_acknowledged"))
    box_code = payload.get("box_code")
    operator = _operator()
    risks, logged = [], 0
    dn = _load_dn(db, dn_id)
    if dn is None:
        return jsonify({"ok": False, "error": "DN 不存在"}), 404
    if dn["status"] in (C.DN_SHIPPED, C.DN_CANCELLED):
        return jsonify({"ok": False, "error": f"状态为 {dn['status']}，不可重复发货"}), 409
    transfer_asn = None
    try:
        schema = prepare_destination(db, dn) if dn["doc_type"] == "调拨" else None
        db.execute("BEGIN IMMEDIATE")
        dn = _load_dn(db, dn_id)
        if dn["status"] in (C.DN_SHIPPED, C.DN_CANCELLED):
            raise ValueError(f"单据状态为 {dn['status']}，不可重复发货")
        lines = _load_lines(db, dn_id)
        short = [l["sku"] for l in lines if l["picked_qty"] + l["cancelled_qty"] + 1e-9 < l["ordered_qty"]]
        if short:
            raise ValueError(f"存在未拣满的行，不可发货: {', '.join(short)}")
        active_review = db.execute("SELECT 1 FROM dn_review WHERE dn_id = ? AND status IN (?, ?)",
                                   (dn_id, C.REVIEW_DRAFT, C.REVIEW_REVIEWING)).fetchone()
        if active_review or (dn["workflow_required"] and dn["status"] != C.DN_REVIEWED):
            raise ValueError("出库单需完成复核后才能发货")
        if box_code:
            if dn["status"] != C.DN_REVIEWED:
                raise ValueError("复核完成后才能扫箱码发货")
            scan = db.execute("""
                UPDATE dn_package SET shipped_scan_at = datetime('now','localtime')
                WHERE code = ? AND dn_id = ? AND shipped_scan_at IS NULL AND review_id IN (
                    SELECT id FROM dn_review WHERE dn_id = ? AND status = ?
                )
            """, (box_code, dn_id, dn_id, C.REVIEW_COMPLETED))
            if scan.rowcount != 1:
                raise ValueError("该箱已扫描，或箱码不属于已完成的复核单")
            left = db.execute("""
                SELECT COUNT(*) FROM dn_package b JOIN dn_review r ON r.id = b.review_id
                WHERE b.dn_id = ? AND r.status = ? AND b.shipped_scan_at IS NULL
            """, (dn_id, C.REVIEW_COMPLETED)).fetchone()[0]
            if left:
                db.commit()
                return jsonify(ok=True, status=C.DN_REVIEWED, remaining_boxes=left, box_code=box_code)

        # 按拣货明细逐库位扣减（qty 与 allocated 同步清掉）
        picks = _load_picks(db, dn_id)
        if schema:
            asn_id, asn_code = create_transfer_asn(db, schema, dn, lines, picks)
            transfer_asn = {"id": asn_id, "code": asn_code, "warehouse_id": dn["target_warehouse"]}
            db.execute("UPDATE dn SET transfer_asn_id = ?, transfer_asn_code = ? WHERE id = ?",
                       (asn_id, asn_code, dn_id))
        # M23 风险放行：先只读预检 + 落审计日志，随后照常发货（不拦截、不改分配）
        inspection = RA.inspect_dn(db, dn_id)
        risks = inspection["risks"] if inspection else []
        logged = RA.record_risk(db, dn_id, risks, operator, acknowledged)
        for p in picks:
            # M35：新分配行带完整批次维度，发货严格按分配记录扣减（强管控批次维度）；
            # 历史 dn_pick 行无批号，回退 M21 的 FEFO 自动推断，老单据不破（Q07）。
            batches = None
            if str(p["batch_no"] or "").strip():
                dim = {k: p[k] for k in BATCH_DIMS}
                dim["production_date"] = p["production_date"] or ""
                batches = [dict(dim, qty=-p["qty"], allocated=-p["qty"])]
            adjust_stock(db, dn["owner_id"], p["product_id"],
                         p["location_id"], -p["qty"], C.MOVEMENT_DN_SHIP, dn_id,
                         allocated_delta=-p["qty"], batches=batches,
                         note=f"DN {dn['code']} 发货出库")
        db.execute(
            "UPDATE dn SET status = ?, updated_at = datetime('now','localtime') WHERE id = ?",
            (C.DN_SHIPPED, dn_id),
        )
        db.execute("""
            UPDATE dn_package SET shipped_scan_at = COALESCE(shipped_scan_at, datetime('now','localtime'))
            WHERE dn_id = ? AND review_id IN (SELECT id FROM dn_review WHERE dn_id = ? AND status = ?)
        """, (dn_id, dn_id, C.REVIEW_COMPLETED))
        db.commit()
    except (ValueError, StockError, sqlite3.Error) as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 400

    return jsonify({"ok": True, "status": C.DN_SHIPPED,
                    "risk_count": len(risks), "risk_logged": logged,
                    "warnings": risks,
                    "transfer_asn": transfer_asn,
                    "lines": [_dict_line(l) for l in _load_lines(db, dn_id)]})


@dn_bp.route("/<int:dn_id>/cancel", methods=["POST"])
def cancel_dn(dn_id):
    """复核前取消单据，新流程同步释放库存分配。"""
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        dn = _load_dn(db, dn_id)
        if dn is None:
            db.rollback()
            return jsonify(ok=False, error="DN 不存在"), 404
        if locked_preparation(db, dn_id):
            raise ValueError("请先撤销组波方案，再取消订单")
        if dn["status"] in (C.DN_REVIEWED, C.DN_SHIPPED, C.DN_CANCELLED):
            raise ValueError("已复核、已发货或已取消的单据不可取消")
        if db.execute("SELECT 1 FROM dn_review WHERE dn_id = ? AND status != ?",
                      (dn_id, C.REVIEW_CANCELLED)).fetchone():
            raise ValueError("请先取消正在进行的复核单")
        picked = any(line["picked_qty"] > 1e-9 for line in _load_lines(db, dn_id))
        if picked:
            if not dn["workflow_required"]:
                raise ValueError("已拣货的单据不可取消，请先释放拣货分配")
            unallocate(db, dn_id, operator=_operator(), cancelling=True)
        db.execute("UPDATE dn SET status = ?, updated_at = datetime('now','localtime') WHERE id = ?",
                   (C.DN_CANCELLED, dn_id))
        db.commit()
    except (AllocationError, ValueError, StockError) as exc:
        db.rollback()
        return jsonify(ok=False, error=str(exc)), 409
    return jsonify({"ok": True, "status": C.DN_CANCELLED})
