"""收货单（ASN）与上架模块：/api/asns。

流程（单据驱动库存，参考 GreaterWMS 的 ASN 与 Sentry 的收货/上架动线）：
1. 创建 ASN（指定货主 + 行：物料 × 预期数量）
2. 收货 receive：逐行收到「收货区」库位（receiving），库存 +，流水 asn_receive
3. 上架 putaway：从收货区位移到存储/拣货位，一减一增两条流水，同一事务
4. 收满且上完自动 closed；未收货可 cancel

一致性规则：
- ASN 的货主必须与行内物料的货主一致（都为自营/都为同一货主）
- 收货目标位必须 loc_type=receiving 且启用；上架目标位必须 storage/picking 且启用
"""
import json
import math

from flask import Blueprint, jsonify, request

import constants as C
from batch_service import record_receipt
from inventory_service import StockError, stock_move, stock_up
from main import get_db, gen_doc_no, request_payload

asn_bp = Blueprint("asn", __name__, url_prefix="/api/asns")

# 收货明细随行落库的商品档案 + 批次信息快照字段。
# 全部字段在表单里均可人工编辑（自动带出只是默认值），单据留存用户最终填写的历史值。
LINE_SNAPSHOT_FIELDS = (
    "product_name", "spec", "article_no", "unit", "brand", "mfg_enterprise",
    "production_enterprise", "origin", "udi", "reg_cert_no", "registrant",
    "mfg_license", "shelf_life_days",
    "batch_no", "sterilization_batch_no", "production_date", "expiry_date",
    "internal_batch_no", "goods_property", "patient_name",
)


def _line_snapshot(line):
    """解析 asn_line.extra（JSON 明细快照）；老单据返回空字典。"""
    try:
        snap = json.loads(line["extra"] or "") if line["extra"] else {}
    except (ValueError, TypeError, KeyError):
        snap = {}
    return snap if isinstance(snap, dict) else {}


def _load_asn(db, asn_id):
    return db.execute("SELECT * FROM asn WHERE id = ?", (asn_id,)).fetchone()


def _load_lines(db, asn_id):
    return db.execute(
        """SELECT al.*, p.sku, p.name AS product_name, p.owner_id AS product_owner_id,
                  p.spec AS p_spec, p.article_no AS p_article_no, p.unit AS p_unit,
                  p.brand AS p_brand, p.mfg_enterprise AS p_mfg_enterprise,
                  p.reg_cert_no AS p_reg_cert_no, p.registrant AS p_registrant,
                  p.mfg_license AS p_mfg_license, p.shelf_life_days AS p_shelf_life_days,
                  p.origin AS p_origin
           FROM asn_line al JOIN product p ON p.id = al.product_id
           WHERE al.asn_id = ? ORDER BY al.id""",
        (asn_id,),
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


def _advance_status(db, asn_id, lines):
    """根据行数据推进单据状态（open→receiving→putaway→closed）。"""
    fully_received = all(l["received_qty"] >= l["expected_qty"] for l in lines)
    fully_putaway = all(l["putaway_qty"] >= l["expected_qty"] for l in lines)
    any_received = any(l["received_qty"] > 0 for l in lines)

    if fully_putaway:
        status = C.ASN_CLOSED
    elif fully_received:
        status = C.ASN_PUTAWAY
    elif any_received:
        status = C.ASN_RECEIVING
    else:
        status = C.ASN_OPEN
    db.execute(
        "UPDATE asn SET status = ?, updated_at = datetime('now','localtime') WHERE id = ?",
        (status, asn_id),
    )
    return status


@asn_bp.route("", methods=["POST"])
def create_asn():
    payload = request_payload()
    code = str(payload.get("code") or "").strip()
    owner_id = payload.get("owner_id")
    lines = payload.get("lines")
    note = str(payload.get("note") or "")

    # 单据字段（M16）：单据类型/外部单号/订单来源/来源仓库/供应商/客户/承运商/自定义字段
    # 单据类型默认「采购」；来源仓库为必填
    doc_type = str(payload.get("doc_type") or "").strip() or "采购"
    external_no = str(payload.get("external_no") or "").strip()
    order_source = str(payload.get("order_source") or "").strip()
    source_warehouse = payload.get("source_warehouse")
    supplier_id = payload.get("supplier_id")
    customer_id = payload.get("customer_id")
    customer_name = str(payload.get("customer_name") or "").strip()
    carrier_name = str(payload.get("carrier_name") or "").strip()
    extra1 = str(payload.get("extra1") or "").strip()
    extra2 = str(payload.get("extra2") or "").strip()
    extra3 = str(payload.get("extra3") or "").strip()
    # 往来单位联系方式：下单时可覆盖主数据带出的默认值
    ov = {
        k: str(payload.get(k) or "").strip()
        for k in ("supplier_name", "supplier_contact", "supplier_phone", "supplier_address",
                  "customer_contact", "customer_phone", "customer_address")
    }

    errors = []
    if doc_type not in C.ASN_DOC_TYPES:
        errors.append(f"doc_type 只能是 {'/'.join(C.ASN_DOC_TYPES)}")
    try:
        owner_id = int(owner_id)
    except (TypeError, ValueError):
        errors.append("owner_id 必填且为整数")
    if not isinstance(lines, list) or not lines:
        errors.append("lines 必须为非空数组")
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    parsed_lines = []
    for i, ln in enumerate(lines):
        try:
            pid = int(ln.get("product_id"))
            qty = float(ln.get("expected_qty"))
            snap = {k: str(ln.get(k) or "").strip() for k in LINE_SNAPSHOT_FIELDS}
        except (TypeError, ValueError, AttributeError):
            return jsonify({"ok": False, "errors": [f"lines[{i}] 字段不合法"]}), 400
        if math.isnan(qty) or qty <= 0:
            return jsonify({"ok": False, "errors": [f"lines[{i}] expected_qty 必须为正数"]}), 400
        gp = str(snap.get("goods_property") or "").strip()
        if gp and gp not in C.GOODS_PROPERTIES:
            return jsonify({"ok": False, "errors": [
                f"lines[{i}] goods_property 只能是 {'/'.join(C.GOODS_PROPERTIES)}"
            ]}), 400
        parsed_lines.append((pid, qty, snap))
    # 同一物料在明细中不可重复（asn_line 唯一约束 (asn_id, product_id)）
    dup = {pid for pid, _, _ in parsed_lines}
    if len(dup) != len(parsed_lines):
        return jsonify({"ok": False, "errors": ["同一物料在明细中出现多次，请合并数量"]}), 400

    db = get_db()
    if db.execute("SELECT id FROM owner WHERE id = ?", (owner_id,)).fetchone() is None:
        return jsonify({"ok": False, "errors": [f"货主不存在: id={owner_id}"]}), 400

    # 来源仓库：表单层必填；API 层仍允许省略（兼容内部作业链路与后续外部对接），
    # 但一旦提供就必须存在且启用
    wh_code = None
    if source_warehouse in (None, "", 0):
        source_warehouse = None
    else:
        try:
            source_warehouse = int(source_warehouse)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "errors": ["source_warehouse 必须为整数"]}), 400
        wh = db.execute(
            "SELECT code, status FROM warehouse WHERE id = ?", (source_warehouse,)
        ).fetchone()
        if wh is None:
            return jsonify({"ok": False, "errors": [f"来源仓库不存在: id={source_warehouse}"]}), 400
        if wh["status"] != "active":
            return jsonify({"ok": False, "errors": [f"来源仓库已停用: {wh['code']}"]}), 400
        wh_code = wh["code"]

    # 供应商（多货主隔离：必须属本货主）；同时快照名称/联系人/电话/地址
    sup = None
    if supplier_id in (None, "", 0):
        supplier_id = None
    else:
        try:
            supplier_id = int(supplier_id)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "errors": ["supplier_id 必须为整数"]}), 400
        sup = db.execute("SELECT * FROM supplier WHERE id = ?", (supplier_id,)).fetchone()
        if sup is None:
            return jsonify({"ok": False, "errors": [f"供应商不存在: id={supplier_id}"]}), 400
        if sup["owner_id"] != owner_id:
            return jsonify({"ok": False, "errors": [
                f"供应商 {sup['name']} 不属于该货主（多货主隔离）"
            ]}), 400

    # 客户（可选，关联客户主数据；归属货主必须与单据一致）
    cust = None
    if customer_id in (None, "", 0):
        customer_id = None
    else:
        try:
            customer_id = int(customer_id)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "errors": ["customer_id 必须为整数"]}), 400
        cust = db.execute("SELECT * FROM customer WHERE id = ?", (customer_id,)).fetchone()
        if cust is None:
            return jsonify({"ok": False, "errors": [f"客户不存在: id={customer_id}"]}), 400
        if cust["owner_id"] != owner_id:
            return jsonify({"ok": False, "errors": [
                f"客户 {cust['name']} 不属于该货主（多货主隔离）"
            ]}), 400

    # 单号：留空则自动生成 ASN-YYYYMMDD-NNN（英文开头 + 日期流水）
    if not code:
        code = gen_doc_no(db, "asn", "ASN")
    elif len(code) > 32:
        return jsonify({"ok": False, "errors": ["code 最长 32 字符"]}), 400
    if db.execute("SELECT id FROM asn WHERE code = ?", (code,)).fetchone() is not None:
        return jsonify({"ok": False, "errors": [f"ASN 编码已存在: {code}"]}), 409

    # 物料必须存在，且货主归属与单据一致
    for pid, _, _ in parsed_lines:
        p = db.execute(
            "SELECT owner_id FROM product WHERE id = ?", (pid,)
        ).fetchone()
        if p is None:
            return jsonify({"ok": False, "errors": [f"物料不存在: id={pid}"]}), 400
        if (p["owner_id"] or None) != owner_id:
            return jsonify({"ok": False, "errors": [
                f"物料 id={pid} 的货主与 ASN 货主不一致（多货主隔离）"
            ]}), 400

    # 明细汇总：订单总数量 = Σ 预期数量，品类数 = 不同物料数
    total_qty = round(sum(q for _, q, _ in parsed_lines), 6)
    category_count = len({pid for pid, _, _ in parsed_lines})

    # 往来单位快照：优先用下单时填写的覆盖值，否则取主数据
    snap_sup_name = ov["supplier_name"] or (sup["name"] if sup else "")
    snap_sup_contact = ov["supplier_contact"] or (sup["contact"] if sup else "")
    snap_sup_phone = ov["supplier_phone"] or (sup["phone"] if sup else "")
    snap_sup_address = ov["supplier_address"] or (sup["address"] if sup else "")
    snap_cus_name = customer_name or (cust["name"] if cust else "")
    snap_cus_contact = ov["customer_contact"] or (cust["contact"] if cust else "")
    snap_cus_phone = ov["customer_phone"] or (cust["phone"] if cust else "")
    snap_cus_address = ov["customer_address"] or (cust["address"] if cust else "")

    cur = db.execute(
        """INSERT INTO asn (code, owner_id, doc_type, external_no, order_source,
               source_warehouse, supplier_id, customer_id, customer_name, carrier_name,
               supplier_name, supplier_contact, supplier_phone, supplier_address,
               customer_contact, customer_phone, customer_address,
               total_qty, category_count, extra1, extra2, extra3, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (code, owner_id, doc_type, external_no, order_source, source_warehouse,
         supplier_id, customer_id, snap_cus_name, carrier_name,
         snap_sup_name, snap_sup_contact, snap_sup_phone, snap_sup_address,
         snap_cus_contact, snap_cus_phone, snap_cus_address,
         total_qty, category_count, extra1, extra2, extra3, note),
    )
    asn_id = cur.lastrowid
    db.executemany(
        "INSERT INTO asn_line (asn_id, product_id, expected_qty, extra) VALUES (?, ?, ?, ?)",
        [(asn_id, pid, qty, json.dumps(snap, ensure_ascii=False))
         for pid, qty, snap in parsed_lines],
    )
    db.commit()
    asn = _load_asn(db, asn_id)
    lines_out = [_dict_line(l) for l in _load_lines(db, asn_id)]
    body = dict(asn)
    body["source_warehouse_code"] = wh_code
    return jsonify({"ok": True, "asn": body, "lines": lines_out}), 201


def _dict_line(l):
    d = dict(l)
    d["remaining_receive"] = max(0, l["expected_qty"] - l["received_qty"])
    d["remaining_putaway"] = max(0, l["received_qty"] - l["putaway_qty"])
    snap = _line_snapshot(l)
    d.pop("extra", None)
    # 行快照优先；老单据未存快照时回退到物料主数据当前值（人工编辑过的字段不会被覆盖）
    p_mfg = d.pop("p_mfg_enterprise", "")
    p_origin = d.pop("p_origin", "")
    fallbacks = {
        "product_name": d.get("product_name"),
        "spec": d.pop("p_spec", ""),
        "article_no": d.pop("p_article_no", ""),
        "unit": d.pop("p_unit", ""),
        "brand": d.pop("p_brand", ""),
        "mfg_enterprise": p_mfg,
        "production_enterprise": p_mfg,
        "origin": p_origin,
        "udi": "",
        "reg_cert_no": d.pop("p_reg_cert_no", ""),
        "registrant": d.pop("p_registrant", ""),
        "mfg_license": d.pop("p_mfg_license", ""),
        "shelf_life_days": d.pop("p_shelf_life_days", ""),
        "batch_no": "",
        "sterilization_batch_no": "",
        "production_date": "",
        "expiry_date": "",
        "internal_batch_no": "",
        "goods_property": "合格",
        "patient_name": "",
    }
    for k, fallback in fallbacks.items():
        v = snap.get(k, "")
        d[k] = v if v not in ("", None) else (fallback or "")
    # 兼容后续新增的快照字段（不在字典里也原样返回）
    for k, v in snap.items():
        if k not in d:
            d[k] = v
    d.pop("product_owner_id", None)
    return d


@asn_bp.route("", methods=["GET"])
def list_asns():
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
        where.append("a.status = ?")
        params.append(status)
    if keyword:
        where.append("a.code LIKE ?")
        params.append(f"%{keyword}%")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    db = get_db()
    total = db.execute(
        f"SELECT COUNT(*) AS n FROM asn a {where_sql}", params
    ).fetchone()["n"]
    rows = db.execute(
        f"""SELECT a.*, o.code AS owner_code, o.name AS owner_name,
                   w.code AS source_warehouse_code, w.name AS source_warehouse_name,
                   s.code AS supplier_code, s.name AS supplier_name
            FROM asn a
            JOIN owner o ON o.id = a.owner_id
            LEFT JOIN warehouse w ON w.id = a.source_warehouse
            LEFT JOIN supplier s ON s.id = a.supplier_id {where_sql}
            ORDER BY a.id DESC LIMIT ? OFFSET ?""",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()
    return jsonify({"ok": True, "total": total, "page": page,
                    "page_size": page_size, "asns": [dict(r) for r in rows]})


@asn_bp.route("/<int:asn_id>", methods=["GET"])
def get_asn(asn_id):
    db = get_db()
    asn = _load_asn(db, asn_id)
    if asn is None:
        return jsonify({"ok": False, "error": "ASN 不存在"}), 404
    o = db.execute("SELECT code, name FROM owner WHERE id = ?", (asn["owner_id"],)).fetchone()
    wh = db.execute("SELECT code, name FROM warehouse WHERE id = ?", (asn["source_warehouse"],)).fetchone()
    sp = db.execute("SELECT code FROM supplier WHERE id = ?", (asn["supplier_id"],)).fetchone()
    cu = db.execute("SELECT code FROM customer WHERE id = ?", (asn["customer_id"],)).fetchone()
    body = dict(asn)
    body["owner_code"], body["owner_name"] = (o["code"], o["name"]) if o else (None, None)
    body["source_warehouse_code"], body["source_warehouse_name"] = (wh["code"], wh["name"]) if wh else (None, None)
    # 名称保留单据快照（下单时允许覆盖主数据），仅补充编码
    body["supplier_code"] = sp["code"] if sp else None
    body["customer_code"] = cu["code"] if cu else None
    lines_out = [_dict_line(l) for l in _load_lines(db, asn_id)]
    # 明细行带出本行已生成的验收入库批次（每次收货一个唯一批次号）
    if db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='batch_receipt'"
    ).fetchone():
        for ln in lines_out:
            ln["batch_receipts"] = [dict(r) for r in db.execute(
                "SELECT id, batch_no, internal_batch_no, internal_source, qty, "
                "goods_property, batch_id, created_at FROM batch_receipt "
                "WHERE asn_line_id = ? ORDER BY id", (ln["id"],)
            ).fetchall()]
    return jsonify({"ok": True, "asn": body, "lines": lines_out})


@asn_bp.route("/<int:asn_id>/receive", methods=["POST"])
def receive(asn_id):
    """收货：line_id + receiving 库位 + 数量 → 库存 +、批次信息落库、单据状态推进。

    批次写入与库存增加处于同一 BEGIN IMMEDIATE 事务：要么都成功，要么一起回滚。
    `internal_batch_no` 可由外部数据/退货场景传入；留空时按首次入库或销退规则确定。
    """
    payload = request_payload()
    internal_batch_no = str(payload.get("internal_batch_no") or "").strip()
    try:
        line_id = int(payload.get("line_id"))
        location_id = int(payload.get("location_id"))
        qty = float(payload.get("qty"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "errors": ["line_id/location_id/qty 必填且合法"]}), 400

    db = get_db()
    batch = None
    try:
        db.execute("BEGIN IMMEDIATE")
        asn = _load_asn(db, asn_id)
        if asn is None:
            raise ValueError("ASN 不存在")
        if asn["status"] in (C.ASN_CLOSED, C.ASN_CANCELLED):
            raise ValueError(f"单据状态为 {asn['status']}，不可收货")
        line = db.execute("SELECT * FROM asn_line WHERE id = ? AND asn_id = ?",
                          (line_id, asn_id)).fetchone()
        if line is None:
            raise ValueError("行不存在或不属于该 ASN")
        remaining = line["expected_qty"] - line["received_qty"]
        if qty <= 0 or qty > remaining:
            raise ValueError(f"收货数量必须为正且不超过剩余 {remaining}")
        _check_location(db, location_id, ("receiving",), "收货目标")

        stock_up(db, asn["owner_id"], line["product_id"], location_id, qty,
                 C.MOVEMENT_ASN_RECEIVE, asn_id, note=f"ASN {asn['code']} 收货")
        # 验收入库 → 写入批次信息（批次档案按关键批次信息去重，每次收货一个唯一批次号）
        batch = record_receipt(
            db, owner_id=asn["owner_id"], product_id=line["product_id"],
            location_id=location_id, asn_id=asn_id, asn_line_id=line_id,
            qty=qty, doc_type=asn["doc_type"] or "",
            line_info=_line_snapshot(line), internal_batch_no=internal_batch_no,
        )
        db.execute(
            "UPDATE asn_line SET received_qty = received_qty + ? WHERE id = ?",
            (qty, line_id),
        )
        lines = _load_lines(db, asn_id)
        status = _advance_status(db, asn_id, lines)
        db.commit()
    except (ValueError, StockError) as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception:
        db.rollback()
        raise

    return jsonify({"ok": True, "status": status, "batch": batch,
                    "lines": [_dict_line(l) for l in _load_lines(db, asn_id)]})


@asn_bp.route("/<int:asn_id>/putaway", methods=["POST"])
def putaway(asn_id):
    """上架：从收货区位 → 存储/拣货位，一减一增同事务。"""
    payload = request_payload()
    try:
        line_id = int(payload.get("line_id"))
        from_loc = int(payload.get("from_location_id"))
        to_loc = int(payload.get("to_location_id"))
        qty = float(payload.get("qty"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "errors": ["line_id/from_location_id/to_location_id/qty 必填且合法"]}), 400

    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        asn = _load_asn(db, asn_id)
        if asn is None:
            raise ValueError("ASN 不存在")
        if asn["status"] in (C.ASN_CLOSED, C.ASN_CANCELLED, C.ASN_OPEN):
            raise ValueError(f"单据状态为 {asn['status']}，无已收货库存可上架")
        if from_loc == to_loc:
            raise ValueError("上架来源位与目标位相同")
        line = db.execute("SELECT * FROM asn_line WHERE id = ? AND asn_id = ?",
                          (line_id, asn_id)).fetchone()
        if line is None:
            raise ValueError("行不存在或不属于该 ASN")
        remaining = line["received_qty"] - line["putaway_qty"]
        if qty <= 0 or qty > remaining:
            raise ValueError(f"上架数量必须为正且不超过待上架 {remaining}")
        _check_location(db, from_loc, ("receiving",), "上架来源")
        _check_location(db, to_loc, ("storage", "picking"), "上架目标")

        stock_move(db, asn["owner_id"], line["product_id"], from_loc, to_loc, qty,
                   C.MOVEMENT_ASN_PUTAWAY_OUT, C.MOVEMENT_ASN_PUTAWAY_IN, asn_id,
                   note=f"ASN {asn['code']} 上架")
        db.execute("UPDATE asn_line SET putaway_qty = putaway_qty + ? WHERE id = ?",
                   (qty, line_id))
        lines = _load_lines(db, asn_id)
        status = _advance_status(db, asn_id, lines)
        db.commit()
    except (ValueError, StockError) as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 400

    return jsonify({"ok": True, "status": status,
                    "lines": [_dict_line(l) for l in _load_lines(db, asn_id)]})


@asn_bp.route("/<int:asn_id>/cancel", methods=["POST"])
def cancel_asn(asn_id):
    """取消：仅未收货的 ASN 可取消。"""
    db = get_db()
    asn = _load_asn(db, asn_id)
    if asn is None:
        return jsonify({"ok": False, "error": "ASN 不存在"}), 404
    if asn["status"] != C.ASN_OPEN:
        return jsonify({"ok": False, "error": f"状态为 {asn['status']}，仅未收货单可取消"}), 409
    db.execute("UPDATE asn SET status = ?, updated_at = datetime('now','localtime') WHERE id = ?",
               (C.ASN_CANCELLED, asn_id))
    db.commit()
    return jsonify({"ok": True, "status": C.ASN_CANCELLED})
