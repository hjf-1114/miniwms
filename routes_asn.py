"""收货单（ASN）与上架模块：/api/asns。

流程（单据驱动库存，参考 GreaterWMS 的 ASN 与 Sentry 的收货/上架动线）：
1. 创建 ASN（指定货主 + 行：物料 × 预期数量）
2. 收货 receive：逐行收到「收货区」库位（receiving），库存 +，流水 asn_receive
3. 上架 putaway：从收货区位移到存储/拣货位，一减一增两条流水，同一事务
4. 可入库量归零后关闭入库单，已验收批次可继续完成上架

一致性规则：
- ASN 的货主必须与行内物料的货主一致（都为自营/都为同一货主）
- 收货目标位必须 loc_type=receiving 且启用；上架目标位必须 storage/picking 且启用
"""
import json
import math
from datetime import date

import scope as S
from flask import Blueprint, g, jsonify, request

import constants as C
from batch_fields import FIELD_NAMES, clean_info, decode_batch_info
from batch_service import ASN_RECEIPTS_SQL, get_asn_receipt, record_receipt, validate_external_batch_no
from inventory_service import EPS, StockError, spread_batches, stock_move, stock_up
from reservation_rules import stock_status_allowed
from putaway_rules import record_putaway_choice, validate_location
from main import get_control_db, get_db, gen_doc_no, request_payload

asn_bp = Blueprint("asn", __name__, url_prefix="/api/asns")


def _operator():
    user = g.current_user
    return user["username"] if user else "匿名作业"


def _default_receiving_location(db, warehouse):
    return db.execute("""
        SELECT id, code, name FROM location
        WHERE loc_type = 'receiving' AND status = 'active'
          AND (? IS NULL OR warehouse_id = ? OR warehouse_id IS NULL)
        ORDER BY CASE WHEN warehouse_id = ? THEN 0 WHEN warehouse_id IS NULL THEN 1 ELSE 2 END,
                 CASE WHEN name = '验收暂存货位' OR code LIKE '%-RCV-001' THEN 0 ELSE 1 END,
                 code, id LIMIT 1
    """, (warehouse, warehouse, warehouse)).fetchone()

# 收货明细随行落库的商品档案 + 批次信息快照字段。
# 全部字段在表单里均可人工编辑（自动带出只是默认值），单据留存用户最终填写的历史值。
LINE_SNAPSHOT_FIELDS = (
    "product_name", "spec", "article_no", "unit", "brand", "mfg_enterprise",
    "production_enterprise", "origin", "udi", "reg_cert_no", "registrant",
    "mfg_license", "shelf_life_days",
    "batch_no", "sterilization_batch_no", "production_date", "expiry_date",
    "internal_batch_no", "goods_property", "patient_name",
    # M23：指定客户只在明细行（默认空、验收时可改）；销退外部批次号行级可覆盖单头默认值
    "designated_customer_id", "external_batch_no",
)


def _parse_customer_id(value):
    """把可选「指定客户」规整为正整数 / None；返回 (值, 错误)。"""
    if value in (None, "", 0, "0", False):
        return None, ""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None, "指定客户 ID 必须为整数"
    if n <= 0:
        return None, "指定客户 ID 必须为正整数"
    return n, ""


def _check_customer_owner(db, owner_id, customer_id):
    """指定客户必须存在且归属该货主，防止跨货主锁定库存。"""
    if customer_id is None:
        return ""
    row = db.execute(
        "SELECT id FROM customer WHERE id = ? AND owner_id = ?",
        (customer_id, owner_id),
    ).fetchone()
    return "" if row else f"指定客户不存在或不属于该货主: id={customer_id}"


def _line_snapshot(line):
    """解析 asn_line.extra（JSON 明细快照）；老单据返回空字典。"""
    try:
        snap = json.loads(line["extra"] or "") if line["extra"] else {}
    except (ValueError, TypeError, KeyError):
        snap = {}
    return snap if isinstance(snap, dict) else {}


def _load_asn(db, asn_id):
    row = db.execute("SELECT * FROM asn WHERE id = ?", (asn_id,)).fetchone()
    if row and g.warehouse_id and row["source_warehouse"] not in (None, g.warehouse_id):
        raise S.ScopeError("该入库单不属于当前仓库", status=403)
    return row


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


def _check_location(db, location_id, allowed_types, label, warehouse_id=None):
    row = db.execute(
        "SELECT code, status, loc_type, warehouse_id FROM location WHERE id = ?", (location_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"{label}库位不存在: id={location_id}")
    if row["status"] != "active":
        raise ValueError(f"{label}库位已停用: {row['code']}")
    if row["loc_type"] not in allowed_types:
        raise ValueError(f"{label}库位类型不正确: {row['code']}")
    if warehouse_id and row["warehouse_id"] not in (None, warehouse_id):
        raise ValueError(f"{label}库位不属于当前仓库: {row['code']}")
    return row


def _advance_status(db, asn_id, lines):
    """根据未验数量和已验库存的上架进度更新订单状态。"""
    acceptance_done = all(_receivable(l) <= EPS for l in lines)
    any_received = any(l["received_qty"] > EPS for l in lines)
    if acceptance_done:
        if not any_received:
            status = C.ASN_CANCELLED
        elif any(l["received_qty"] - l["putaway_qty"] > EPS for l in lines):
            status = C.ASN_PUTAWAY
        else:
            status = C.ASN_CLOSED
    elif any_received or _pending_count(db, asn_id=asn_id) > EPS:
        status = C.ASN_RECEIVING
    else:
        status = C.ASN_OPEN
    db.execute(
        "UPDATE asn SET status = ?, updated_at = datetime('now','localtime') WHERE id = ?",
        (status, asn_id),
    )
    return status


def _receivable(line):
    return max(0, line["expected_qty"] - line["received_qty"] - line["cancelled_qty"])


def _pending_count(db, *, line_id=None, asn_id=None):
    sql = """SELECT COALESCE(SUM(rc.qty - rc.accepted_qty - rc.cancelled_qty), 0)
             FROM asn_receiving_count rc JOIN asn_line al ON al.id = rc.asn_line_id"""
    if line_id is not None:
        return db.execute(sql + " WHERE al.id = ?", (line_id,)).fetchone()[0]
    return db.execute(sql + " WHERE al.asn_id = ?", (asn_id,)).fetchone()[0]


@asn_bp.route("", methods=["POST"])
def create_asn():
    payload = request_payload()
    code = str(payload.get("code") or "").strip()
    owner_id = payload.get("owner_id")
    lines = payload.get("lines")
    note = str(payload.get("note") or "")

    # M20 剩余（v3.3）：4 个单据标识位 + 外部单据类别。这里只落字段，
    # 混放/冷链/危险是否拦截由 M25/M26 的上架规则配置决定，本层不附加业务校验。
    flags, external_doc_type, flag_errors = C.parse_doc_flags(payload)

    # 单据字段（M16）：单据类型/外部单号/订单来源/来源仓库/供应商/客户/承运商/自定义字段
    # 单据类型默认「采购」；来源仓库为必填
    doc_type = str(payload.get("doc_type") or "").strip() or "采购"
    external_no = str(payload.get("external_no") or "").strip()
    order_source = str(payload.get("order_source") or "").strip()
    source_warehouse = g.warehouse_id or payload.get("source_warehouse")
    supplier_id = payload.get("supplier_id")
    customer_id = payload.get("customer_id")
    customer_name = str(payload.get("customer_name") or "").strip()
    carrier_name = str(payload.get("carrier_name") or "").strip()
    extra1 = str(payload.get("extra1") or "").strip()
    extra2 = str(payload.get("extra2") or "").strip()
    extra3 = str(payload.get("extra3") or "").strip()
    # M23 销退：单据头登记外部传入的原内部批次号，作为各明细行的默认值；
    # 行级值可覆盖。确实没有时显式填 '/'（见 batch_service.validate_external_batch_no）。
    head_ext = str(payload.get("external_batch_no") or "").strip()
    # 往来单位联系方式：下单时可覆盖主数据带出的默认值
    ov = {
        k: str(payload.get(k) or "").strip()
        for k in ("supplier_name", "supplier_contact", "supplier_phone", "supplier_address",
                  "customer_contact", "customer_phone", "customer_address")
    }

    errors = list(flag_errors)
    if len(head_ext) > C.EXTERNAL_BATCH_NO_MAXLEN:
        errors.append(f"external_batch_no 最长 {C.EXTERNAL_BATCH_NO_MAXLEN} 字符")
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
        # M23：销退行必须有外部批次号（缺则 400）；'/' 是「确实没有」的合法占位。
        raw_ext = str(ln.get("external_batch_no") or "").strip() or head_ext
        ext_norm, _, ext_err = validate_external_batch_no(doc_type, raw_ext)
        if ext_err:
            return jsonify({"ok": False, "errors": [f"lines[{i}] {ext_err}"]}), 400
        snap["external_batch_no"] = ext_norm
        # M23：指定客户只放明细行、默认空，创建时可不填、验收时可改。
        dcid, dcid_err = _parse_customer_id(ln.get("designated_customer_id"))
        if dcid_err:
            return jsonify({"ok": False, "errors": [f"lines[{i}] {dcid_err}"]}), 400
        snap["designated_customer_id"] = str(dcid or "")
        parsed_lines.append((pid, qty, snap))
    # 同一物料在明细中不可重复（asn_line 唯一约束 (asn_id, product_id)）
    dup = {pid for pid, _, _ in parsed_lines}
    if len(dup) != len(parsed_lines):
        return jsonify({"ok": False, "errors": ["同一物料在明细中出现多次，请合并数量"]}), 400

    db = get_db()
    if db.execute("SELECT id FROM owner WHERE id = ?", (owner_id,)).fetchone() is None:
        return jsonify({"ok": False, "errors": [f"货主不存在: id={owner_id}"]}), 400
    # M23：指定客户必须归属该货主，避免跨货主把库存锁给别家客户。
    for _, _, snap in parsed_lines:
        cid, _ = _parse_customer_id(snap.get("designated_customer_id"))
        owner_err = _check_customer_owner(db, owner_id, cid)
        if owner_err:
            return jsonify({"ok": False, "errors": [owner_err]}), 400

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
        S.assert_document_warehouse(S.current_scope(), get_control_db(), source_warehouse, owner_id,
                                    require_binding=bool(g.warehouse_id or g.current_user))
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
               total_qty, category_count, extra1, extra2, extra3, note,
               is_virtual, is_direct, is_cold_chain, is_hazard, external_doc_type)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (code, owner_id, doc_type, external_no, order_source, source_warehouse,
         supplier_id, customer_id, snap_cus_name, carrier_name,
         snap_sup_name, snap_sup_contact, snap_sup_phone, snap_sup_address,
         snap_cus_contact, snap_cus_phone, snap_cus_address,
         total_qty, category_count, extra1, extra2, extra3, note)
        + tuple(flags[k] for k in C.DOC_FLAG_FIELDS) + (external_doc_type,),
    )
    asn_id = cur.lastrowid
    db.executemany(
        "INSERT INTO asn_line (asn_id, product_id, expected_qty, extra) VALUES (?, ?, ?, ?)",
        [(asn_id, pid, qty, json.dumps(snap, ensure_ascii=False))
         for pid, qty, snap in parsed_lines],
    )
    # 单据头外部批次号单独写，避免改动 M16 起沿用至今的 INSERT 列序。
    db.execute("UPDATE asn SET external_batch_no = ? WHERE id = ?", (head_ext, asn_id))
    db.commit()
    asn = _load_asn(db, asn_id)
    lines_out = [_dict_line(l) for l in _load_lines(db, asn_id)]
    body = dict(asn)
    body["source_warehouse_code"] = wh_code
    return jsonify({"ok": True, "asn": body, "lines": lines_out}), 201


def _dict_line(l):
    d = dict(l)
    d["remaining_receive"] = d["receivable_qty"] = _receivable(l)
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
    # M23：快照里以字符串保存，按业务类型回吐给前端（默认空 = 通用库存 / 无外部批次号）。
    d["designated_customer_id"] = _parse_customer_id(
        snap.get("designated_customer_id"))[0]
    d["external_batch_no"] = str(snap.get("external_batch_no") or "").strip()
    d["batch_info"] = clean_info(d)
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
        where.append("(a.code LIKE ? OR a.external_no LIKE ?)")
        params.extend([f"%{keyword}%"] * 2)
    try:
        for key in ("owner_id", "customer_id"):
            if args.get(key):
                where.append(f"a.{key} = ?")
                params.append(int(args[key]))
        for key, op in (("date_from", ">="), ("date_to", "<=")):
            if args.get(key):
                where.append(f"date(a.created_at) {op} ?")
                params.append(date.fromisoformat(args[key]).isoformat())
    except ValueError:
        return jsonify(ok=False, error="货主、客户或日期格式不正确"), 400
    stage = args.get("stage")
    if g.warehouse_id:
        where.append("(a.source_warehouse = ? OR a.source_warehouse IS NULL)")
        params.append(g.warehouse_id)
    if stage == "accepted":
        where.append("EXISTS (SELECT 1 FROM asn_line al WHERE al.asn_id = a.id AND al.received_qty > 0.000000001)")
    elif stage in ("acceptance", "putaway"):
        where.append("a.status != 'cancelled'")
        pending = (
            "EXISTS (SELECT 1 FROM asn_receiving_count rc WHERE rc.asn_line_id = al.id "
            "AND rc.qty - rc.accepted_qty - rc.cancelled_qty > 0.000000001)" if stage == "acceptance"
            else "al.received_qty - al.putaway_qty > 0.000000001"
        )
        where.append(f"EXISTS (SELECT 1 FROM asn_line al WHERE al.asn_id = a.id AND {pending})")
    S.scoped_where(where, params, table="a")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    db = get_db()
    total = db.execute(
        f"SELECT COUNT(*) AS n FROM asn a {where_sql}", params
    ).fetchone()["n"]
    rows = db.execute(
        f"""WITH page_rows AS (
                SELECT a.* FROM asn a {where_sql}
                ORDER BY a.id DESC LIMIT ? OFFSET ?
            ), line_stats AS (
                SELECT l.id, l.asn_id, l.expected_qty, l.received_qty, l.putaway_qty, l.cancelled_qty,
                       COALESCE(SUM(c.qty), 0) AS gross_counted,
                       COALESCE(SUM(c.accepted_qty), 0) AS accepted_from_count,
                       COALESCE(SUM(c.cancelled_qty), 0) AS cancelled_from_count
                FROM page_rows a JOIN asn_line l ON l.asn_id = a.id
                LEFT JOIN asn_receiving_count c ON c.asn_line_id = l.id GROUP BY l.id
            ), totals AS (
                SELECT asn_id, SUM(expected_qty) AS order_qty, SUM(cancelled_qty) AS cancelled_total_qty,
                       SUM(gross_counted + MAX(0, received_qty - accepted_from_count)) AS counted_total_qty,
                       SUM(gross_counted - cancelled_from_count + MAX(0, received_qty - accepted_from_count)) AS net_counted_qty,
                       SUM(received_qty) AS accepted_total_qty, SUM(putaway_qty) AS putaway_total_qty,
                       SUM(received_qty - putaway_qty) AS pending_putaway_qty
                FROM line_stats GROUP BY asn_id
            )
            SELECT a.*, o.code AS owner_code, o.name AS owner_name,
                   w.code AS source_warehouse_code, w.name AS source_warehouse_name,
                   s.code AS supplier_code, COALESCE(NULLIF(a.supplier_name, ''), s.name, '') AS supplier_display_name,
                   c.code AS customer_code, COALESCE(NULLIF(a.customer_name, ''), c.name, '') AS customer_display_name,
                   COALESCE(t.order_qty, 0) AS order_qty,
                   COALESCE(t.cancelled_total_qty, 0) AS cancelled_total_qty,
                   COALESCE(t.counted_total_qty, 0) AS counted_total_qty,
                   COALESCE(t.net_counted_qty, 0) AS net_counted_qty,
                   COALESCE(t.accepted_total_qty, 0) AS accepted_total_qty,
                   COALESCE(t.putaway_total_qty, 0) AS putaway_total_qty,
                   COALESCE(t.pending_putaway_qty, 0) AS pending_putaway_qty
            FROM page_rows a
            JOIN owner o ON o.id = a.owner_id
            LEFT JOIN warehouse w ON w.id = a.source_warehouse
            LEFT JOIN supplier s ON s.id = a.supplier_id
            LEFT JOIN customer c ON c.id = a.customer_id
            LEFT JOIN totals t ON t.asn_id = a.id
            ORDER BY a.id DESC""",
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
    by_id = {ln["id"]: ln for ln in lines_out}
    for ln in lines_out:
        ln.update(batch_receipts=[], receiving_counts=[], putaway_details=[], cancellations=[], pending_acceptance=0)
    for row in db.execute("""
        SELECT c.* FROM asn_cancellation c JOIN asn_line l ON l.id = c.asn_line_id
        WHERE l.asn_id = ? ORDER BY c.id
    """, (asn_id,)):
        by_id[row["asn_line_id"]]["cancellations"].append(dict(row))
    for row in db.execute(ASN_RECEIPTS_SQL + " WHERE br.asn_id = ? ORDER BY br.id", (asn_id,)):
        receipt = dict(row)
        receipt["info"] = decode_batch_info(receipt["info"])
        receipt["info"]["internal_batch_no"] = receipt["internal_batch_no"]
        by_id[row["asn_line_id"]]["batch_receipts"].append(receipt)
    for row in db.execute("""
        SELECT rc.*, l.code AS location_code,
               MAX(0, rc.qty - rc.accepted_qty - rc.cancelled_qty) AS remaining
        FROM asn_receiving_count rc LEFT JOIN location l ON l.id = rc.location_id
        JOIN asn_line al ON al.id = rc.asn_line_id WHERE al.asn_id = ? ORDER BY rc.id
    """, (asn_id,)):
        ln = by_id[row["asn_line_id"]]
        ln["receiving_counts"].append(dict(row))
        ln["pending_acceptance"] += row["remaining"]
    for row in db.execute("""
        SELECT bl.*, src.code AS from_location_code, dst.code AS to_location_code,
               COALESCE(bl.receipt_id, br.id) AS batch_receipt_id
        FROM batch_location bl
        LEFT JOIN batch_receipt br ON br.batch_no = bl.batch_no AND br.asn_line_id = bl.asn_line_id
        LEFT JOIN location src ON src.id = bl.from_location_id
        LEFT JOIN location dst ON dst.id = bl.to_location_id
        WHERE bl.asn_id = ? AND bl.ref_type = ? ORDER BY bl.id
    """, (asn_id, C.MOVEMENT_ASN_PUTAWAY_IN)):
        if row["asn_line_id"] in by_id:
            by_id[row["asn_line_id"]]["putaway_details"].append(dict(row))
    for ln in lines_out:
        ln["remaining_count"] = max(0, ln["remaining_receive"] - ln["pending_acceptance"])
        ln["counted_qty"] = (sum(c["qty"] for c in ln["receiving_counts"])
                             + max(0, ln["received_qty"] -
                                   sum(c["accepted_qty"] for c in ln["receiving_counts"])))
        ln["acceptable_qty"] = min(ln["pending_acceptance"], ln["receivable_qty"])
        ln["batch_info"].update(receipt_customer=asn["customer_name"] or "",
                                receipt_supplier=asn["supplier_name"] or "")
        for count in ln["receiving_counts"]:
            count["acceptable_qty"] = min(count["remaining"], ln["receivable_qty"])
    default_location = _default_receiving_location(db, asn["source_warehouse"] or g.warehouse_id)
    return jsonify(ok=True, asn=body, lines=lines_out,
                   default_receiving_location=dict(default_location) if default_location else None)


@asn_bp.route("/<int:asn_id>/count", methods=["POST"])
def count_receiving(asn_id):
    """登记实际点收数量，验收确认后才记库存。"""
    payload = request_payload()
    try:
        line_id = int(payload.get("line_id"))
        qty = float(payload.get("qty"))
        if not math.isfinite(qty) or qty <= EPS:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify(ok=False, error="请选择明细，并填写有效的实收数量"), 400
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        asn = _load_asn(db, asn_id)
        if asn is None or asn["status"] not in (C.ASN_OPEN, C.ASN_RECEIVING):
            raise ValueError("该收货单当前不可点收")
        line = db.execute("SELECT * FROM asn_line WHERE id = ? AND asn_id = ?",
                          (line_id, asn_id)).fetchone()
        if line is None:
            raise ValueError("明细不属于该收货单")
        remaining = _receivable(line) - _pending_count(db, line_id=line_id)
        if qty > remaining + EPS:
            raise ValueError(f"点收数量不得超过待点收数量 {max(0, remaining):g}")
        count_id = db.execute("""
            INSERT INTO asn_receiving_count (asn_line_id, qty, operator) VALUES (?, ?, ?)
        """, (line_id, qty, _operator())).lastrowid
        db.execute("UPDATE asn SET status = ?, updated_at = datetime('now','localtime') WHERE id = ?",
                   (C.ASN_RECEIVING, asn_id))
        db.commit()
    except ValueError as e:
        db.rollback()
        return jsonify(ok=False, error=str(e)), 400
    return jsonify(ok=True, count_id=count_id), 201


@asn_bp.route("/<int:asn_id>/receive", methods=["POST"])
def receive(asn_id):
    """收货：line_id + receiving 库位 + 数量 → 库存 +、批次信息落库、单据状态推进。

    批次写入与库存增加处于同一 BEGIN IMMEDIATE 事务：要么都成功，要么一起回滚。
    M23：``designated_customer_id`` / ``external_batch_no`` / ``stock_status`` 均在
    **验收时**取值（payload 优先，其次明细行快照，最后单据头默认值）；
    销退缺外部批次号一律 400，确实没有须显式填 '/'。
    """
    payload = request_payload()
    internal_batch_no = str(payload.get("internal_batch_no") or "").strip()
    stock_status = str(payload.get("stock_status") or "").strip()
    if not stock_status_allowed(stock_status):
        return jsonify({"ok": False, "error":
                        f"stock_status 只能是 {'/'.join(C.STOCK_STATUSES)} 或留空"}), 400
    try:
        line_id = int(payload.get("line_id"))
        location_id = int(payload["location_id"]) if payload.get("location_id") not in (None, "") else None
        qty = float(payload.get("qty"))
        count_id = int(payload["receiving_count_id"]) if "receiving_count_id" in payload else None
        if not math.isfinite(qty):
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"ok": False, "errors": ["请提供有效的明细、暂存货位和验收数量"]}), 400

    db = get_db()
    batch = None
    warning = ""
    try:
        db.execute("BEGIN IMMEDIATE")
        asn = _load_asn(db, asn_id)
        if asn is None:
            raise ValueError("ASN 不存在")
        if asn["status"] in (C.ASN_PUTAWAY, C.ASN_CLOSED, C.ASN_CANCELLED):
            raise ValueError(f"单据状态为 {asn['status']}，不可收货")
        line = db.execute("SELECT * FROM asn_line WHERE id = ? AND asn_id = ?",
                          (line_id, asn_id)).fetchone()
        if line is None:
            raise ValueError("行不存在或不属于该 ASN")
        remaining = _receivable(line)
        counts = db.execute("SELECT * FROM asn_receiving_count WHERE asn_line_id = ?",
                            (line_id,)).fetchall()
        if count_id is not None or counts:
            count = next((c for c in counts if c["id"] == count_id), None)
            if count is None:
                raise ValueError("请选择该明细的有效点收记录")
            remaining = min(remaining, count["qty"] - count["accepted_qty"] - count["cancelled_qty"])
        if qty <= EPS or qty > remaining + EPS:
            raise ValueError(f"验收数量必须为正且不超过剩余 {max(0, remaining):g}")
        warehouse = asn["source_warehouse"] or g.warehouse_id
        if location_id is None:
            default_location = _default_receiving_location(db, warehouse)
            if default_location is None:
                raise ValueError("当前仓库没有可用的验收暂存货位")
            location_id = default_location["id"]
        _check_location(db, location_id, ("receiving",), "验收暂存", warehouse)

        # ── M23：验收时确定三个批次维度（payload > 行快照 > 单据头默认值）──
        snap = _dict_line(next(l for l in _load_lines(db, asn_id) if l["id"] == line_id))
        for field in FIELD_NAMES:
            if field in payload:
                snap[field] = str(payload[field] or "").strip()
        snap.update(receipt_customer=asn["customer_name"] or "",
                    receipt_supplier=asn["supplier_name"] or "")
        dcid_raw = (payload["designated_customer_id"]
                    if "designated_customer_id" in payload
                    else snap.get("designated_customer_id"))
        dcid, dcid_err = _parse_customer_id(dcid_raw)
        if dcid_err:
            raise ValueError(dcid_err)
        owner_err = _check_customer_owner(db, asn["owner_id"], dcid)
        if owner_err:
            raise ValueError(owner_err)
        ext_raw = (payload["external_batch_no"]
                   if "external_batch_no" in payload
                   else (snap.get("external_batch_no") or asn["external_batch_no"]))
        ext_norm, warning, ext_err = validate_external_batch_no(
            asn["doc_type"] or "", ext_raw)
        if ext_err:
            raise ValueError(ext_err)

        # 先登记批次流水拿到唯一批次号，再把**同一维度**写进库存明细层（M21）
        operator = _operator()
        batch = record_receipt(
            db, owner_id=asn["owner_id"], product_id=line["product_id"],
            location_id=location_id, asn_id=asn_id, asn_line_id=line_id,
            qty=qty, doc_type=asn["doc_type"] or "",
            line_info=snap, internal_batch_no=internal_batch_no,
            designated_customer_id=dcid, external_batch_no=ext_norm,
            operator=operator, stock_status=stock_status,
        )
        info = batch["info"]
        if count_id is not None:
            db.execute("UPDATE asn_receiving_count SET accepted_qty = accepted_qty + ? WHERE id = ?",
                       (qty, count_id))
            db.execute("UPDATE batch_receipt SET receiving_count_id = ? WHERE batch_no = ?",
                       (count_id, batch["batch_no"]))
        stock_up(db, asn["owner_id"], line["product_id"], location_id, qty,
                 C.MOVEMENT_ASN_RECEIVE, asn_id, note=f"ASN {asn['code']} 收货", operator=operator,
                 batches=[{
                     "batch_no": batch["batch_no"],
                     "batch_key": batch["batch_key"],
                     "internal_batch_no": batch["internal_batch_no"],
                     "designated_customer_id": dcid,
                     "goods_property": info.get("goods_property", ""),
                     "stock_status": stock_status,
                     "expiry_date": info.get("expiry_date", ""),
                     "production_date": info.get("production_date", ""),
                     "qty": qty,
                 }])
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
                    "warning": warning,
                    "lines": [_dict_line(l) for l in _load_lines(db, asn_id)]})


@asn_bp.route("/<int:asn_id>/putaway", methods=["POST"])
def putaway(asn_id):
    """上架：从收货区位 → 存储/拣货位，一减一增同事务。"""
    payload = request_payload()
    barcode = str(payload.get("receipt_barcode") or "").strip()
    try:
        line_id = int(payload.get("line_id"))
        from_loc = int(payload.get("from_location_id"))
        to_loc = int(payload.get("to_location_id"))
        recommendation_id = int(payload.get("recommendation_id") or 0)
        qty = float(payload.get("qty"))
        if not math.isfinite(qty):
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"ok": False, "errors": ["line_id/from_location_id/to_location_id/qty 必填且合法"]}), 400

    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        asn = _load_asn(db, asn_id)
        if asn is None:
            raise ValueError("ASN 不存在")
        if asn["status"] in (C.ASN_CANCELLED, C.ASN_OPEN):
            raise ValueError(f"单据状态为 {asn['status']}，无已收货库存可上架")
        if from_loc == to_loc:
            raise ValueError("上架来源位与目标位相同")
        line = db.execute("SELECT * FROM asn_line WHERE id = ? AND asn_id = ?",
                          (line_id, asn_id)).fetchone()
        if line is None:
            raise ValueError("行不存在或不属于该 ASN")
        remaining = line["received_qty"] - line["putaway_qty"]
        if qty <= EPS or qty > remaining + EPS:
            raise ValueError(f"上架数量必须为正且不超过待上架 {remaining}")
        _check_location(db, from_loc, ("receiving",), "上架来源", asn["source_warehouse"])
        _check_location(db, to_loc, ("storage", "picking"), "上架目标", asn["source_warehouse"])
        receipt = get_asn_receipt(db, asn_id, line_id, barcode) if barcode else None
        batches = None
        if receipt is not None:
            if from_loc != receipt["location_id"] or qty > receipt["remaining_putaway"] + EPS:
                raise ValueError("上架来源位或数量与批次号不符")
            rows = db.execute("""
                SELECT * FROM stock_batch WHERE owner_id = ? AND product_id = ?
                AND location_id = ? AND batch_no = ? AND qty - allocated_qty > ?
            """, (asn["owner_id"], line["product_id"], from_loc, barcode, EPS)).fetchall()
            available_rows = [dict(r, qty=r["qty"] - r["allocated_qty"]) for r in rows]
            batches = spread_batches(db, asn["owner_id"], line["product_id"], from_loc, qty,
                                     rows=available_rows)
        elif db.execute("""
            SELECT 1 FROM batch_receipt WHERE asn_line_id = ? AND receiving_count_id IS NOT NULL
        """, (line_id,)).fetchone():
            raise ValueError("请扫描或选择批次号后上架")
        # 条码上架还会校验货位和储区明确配置的混放限制。
        allowed, code, reason = validate_location(db, asn_id, line_id, to_loc, qty, receipt=receipt)
        if not allowed:
            raise ValueError(f"{code}: {reason}")

        operator = _operator()
        reason = str(payload.get("override_reason") or "").strip()
        moved = stock_move(db, asn["owner_id"], line["product_id"], from_loc, to_loc, qty,
                           C.MOVEMENT_ASN_PUTAWAY_OUT, C.MOVEMENT_ASN_PUTAWAY_IN, asn_id,
                           note=f"ASN {asn['code']} 上架", batches=batches, operator=operator)
        # M21：记录一次收货批次拆到多个库位的实物分布（明细层由 stock_move 自动搬运）
        for b in moved or []:
            db.execute(
                """INSERT INTO batch_location
                   (batch_no, owner_id, product_id, from_location_id, to_location_id,
                    qty, asn_id, asn_line_id, ref_type, ref_id, receipt_id, operator, override_reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (b["batch_no"], asn["owner_id"], line["product_id"], from_loc, to_loc,
                 abs(b["qty"]), asn_id, line_id, C.MOVEMENT_ASN_PUTAWAY_IN, asn_id,
                 receipt["id"] if receipt is not None else None, operator, reason),
            )
        db.execute("UPDATE asn_line SET putaway_qty = putaway_qty + ? WHERE id = ?",
                   (qty, line_id))
        record_putaway_choice(db, asn_id, line_id, barcode, to_loc, recommendation_id,
                              reason, operator)
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
    return _cancel_remaining(asn_id, open_only=True)


@asn_bp.route("/<int:asn_id>/finish", methods=["POST"])
def finish_asn(asn_id):
    return _cancel_remaining(asn_id)


@asn_bp.route("/<int:asn_id>/lines/<int:line_id>/cancel", methods=["POST"])
@asn_bp.route("/<int:asn_id>/lines/<int:line_id>/cancel-receiving", methods=["POST"])
def cancel_asn_line(asn_id, line_id):
    try:
        qty = float(request_payload().get("qty"))
        if not math.isfinite(qty) or qty <= EPS:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify(ok=False, error="请输入大于 0 的取消数量"), 400
    mode = "received" if request.path.endswith("/cancel-receiving") else "uncounted"
    return _cancel_remaining(asn_id, line_id=line_id, qty=qty, mode=mode)


def _cancel_remaining(asn_id, *, line_id=None, qty=None, open_only=False, mode="all"):
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        asn = _load_asn(db, asn_id)
        if asn is None:
            db.rollback()
            return jsonify(ok=False, error="ASN 不存在"), 404
        allowed = (C.ASN_OPEN,) if open_only else (C.ASN_OPEN, C.ASN_RECEIVING)
        if asn["status"] not in allowed:
            db.rollback()
            return jsonify(ok=False, error=f"当前单据状态 {asn['status']} 无法再取消或完结"), 409
        lines = _load_lines(db, asn_id)
        if line_id is not None:
            lines = [line for line in lines if line["id"] == line_id]
            if not lines:
                raise ValueError("明细不属于该入库单")
        for line in lines:
            remaining = _receivable(line)
            pending = min(remaining, max(0, _pending_count(db, line_id=line["id"])))
            cap, action = {
                "uncounted": (remaining - pending, "取消待点收"),
                "received": (pending, "取消收货"),
                "all": (remaining, "取消入库单" if open_only else "完结入库单"),
            }[mode]
            to_cancel = cap if qty is None else qty
            if to_cancel > cap + EPS:
                raise ValueError(f"{action}数量不得超过 {cap:g}")
            if to_cancel <= EPS:
                continue
            db.execute("UPDATE asn_line SET cancelled_qty = cancelled_qty + ? WHERE id = ?",
                       (min(to_cancel, cap), line["id"]))
            db.execute("INSERT INTO asn_cancellation (asn_line_id, action, qty, operator) VALUES (?, ?, ?, ?)",
                       (line["id"], action, min(to_cancel, cap), _operator()))
            to_release = min(to_cancel, cap) if mode == "received" else pending if mode == "all" else 0
            if to_release > EPS:
                for count in db.execute("""
                    SELECT id, qty - accepted_qty - cancelled_qty AS pending
                    FROM asn_receiving_count WHERE asn_line_id = ?
                    AND qty - accepted_qty - cancelled_qty > ? ORDER BY id DESC
                """, (line["id"], EPS)).fetchall():
                    released = min(to_release, count["pending"])
                    db.execute("UPDATE asn_receiving_count SET cancelled_qty = cancelled_qty + ? WHERE id = ?",
                               (released, count["id"]))
                    to_release -= released
                    if to_release <= EPS:
                        break
        status = _advance_status(db, asn_id, _load_lines(db, asn_id))
        db.commit()
    except ValueError as e:
        db.rollback()
        return jsonify(ok=False, error=str(e)), 400
    return jsonify(ok=True, status=status)
