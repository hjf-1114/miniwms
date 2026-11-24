"""M38 出库单拆单 / 合单服务层（纯单据层数量重分配）。

核心边界（与 M24/M36「净零 / 只留痕」一脉相承）：
- 只操作**未拣货的 open 出库单**（``status=open`` 且所有行 ``picked_qty=0`` 且无 ``dn_pick``）；
- **不动库存**：不写 stock / stock_movement / 调整单，只改 dn 与 dn_line 的数量与状态；
- **数量守恒**：拆单 Σ 拆出 = 源单被拆行拆前量；合单 Σ 源单量 = 目标单量（容差 1e-9）；
- **全程留痕**：每次 split / merge 写一条 ``dn_order_log``（源单 → 目标单 + 逐行数量）；
- **货主隔离**：所有源单过 ``assert_owner_allowed()``，日志带 owner_id 注入查询。

拆单 parts 结构::

    {"source_id": 1, "parts": [{"lines": [{"product_id": 9, "qty": 3}]}, ...]}

同一 product 可跨 part 拆分，但累计拆出量 ≤ 源单该行 ordered_qty；
part 内 product 不可重复。源单扣减后数量为 0 的行删除；扣完后源单若无任何行则取消。

合单结构::

    {"source_ids": [1, 2, ...]}

所有源单须同货主 + 同客户（Q28 默认），≥2 张；按 product_id 汇总为一张目标单，
源单置 ``cancelled`` 并在 note 追加「已合入 <目标单号>」（源单不删除，保留追溯）。
"""
import json
import math

import constants as C
import scope as S
from main import gen_doc_no


class DNOrderError(Exception):
    """拆单/合单业务错误；code 供前端与测试断言。"""

    def __init__(self, message, code="DN_ORDER_ERROR"):
        super().__init__(message)
        self.code = code


def _qty(value, label, positive=True):
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise DNOrderError(f"{label} 必须为数字", "INVALID_QTY")
    if math.isnan(v) or math.isinf(v):
        raise DNOrderError(f"{label} 必须为有限数字", "INVALID_QTY")
    if positive and v <= 0:
        raise DNOrderError(f"{label} 必须为正数", "INVALID_QTY")
    return round(v, 6)


def _load_dn(db, dn_id):
    row = db.execute("SELECT * FROM dn WHERE id = ?", (dn_id,)).fetchone()
    if row is None:
        raise DNOrderError(f"出库单不存在: id={dn_id}", "DN_NOT_FOUND")
    return row


def _load_lines(db, dn_id):
    return db.execute(
        "SELECT * FROM dn_line WHERE dn_id = ? ORDER BY id", (dn_id,)
    ).fetchall()


def _assert_splittable(db, dn):
    """未拣货 open 单才可拆/合：已发货/已取消/已拣货/有分配明细一律拒绝。"""
    if dn["status"] not in C.SPLIT_ALLOWED_STATUS:
        raise DNOrderError(
            f"出库单 {dn['code']} 状态为 {dn['status']}，仅 open 且未拣货的单据可拆合",
            "STATUS_NOT_ALLOWED",
        )
    lines = _load_lines(db, dn["id"])
    for ln in lines:
        if ln["picked_qty"] > C.SPLIT_QTY_EPS:
            raise DNOrderError(
                f"出库单 {dn['code']} 已有拣货量，不可拆合", "ALREADY_PICKED"
            )
    picks = db.execute("SELECT COUNT(*) AS n FROM dn_pick WHERE dn_id = ?", (dn["id"],)).fetchone()["n"]
    if picks:
        raise DNOrderError(
            f"出库单 {dn['code']} 已有拣货分配明细，不可拆合", "ALREADY_ALLOCATED"
        )


def _guard_owner(db, dn, scope=None):
    S.assert_owner_allowed(scope or S.current_scope(), dn["owner_id"])


def _write_log(db, action, owner_id, customer_id, sources, targets, detail, operator, note):
    db.execute(
        """INSERT INTO dn_order_log
               (action, owner_id, customer_id, source_dn_ids, source_codes,
                target_dn_ids, target_codes, detail, operator, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (action, owner_id, customer_id,
         ",".join(str(x["id"]) for x in sources),
         ",".join(x["code"] for x in sources),
         ",".join(str(x["id"]) for x in targets),
         ",".join(x["code"] for x in targets),
         json.dumps(detail, ensure_ascii=False),
         operator or "", note or ""),
    )


def _clone_dn(db, tpl, suffix_note, operator=None):
    """按模板单复制公共字段生成新 open 单（数量后续写入），溯源字段标记来源。"""
    code = gen_doc_no(db, "dn", C.SPLIT_CODE_PREFIX)
    fields = ("owner_id", "customer_id", "doc_type", "external_no", "order_source",
              "source_warehouse", "carrier_name", "extra1", "extra2", "extra3",
              "is_virtual", "is_direct", "is_cold_chain", "is_hazard", "external_doc_type",
              "target_warehouse", "customer_name", "customer_contact", "customer_phone", "customer_address")
    values = [code] + [tpl[f] for f in fields] + [suffix_note]
    sql = (
        "INSERT INTO dn (code, " + ",".join(fields) + ", note, total_qty, category_count) VALUES ("
        + ",".join("?" for _ in range(len(fields) + 1)) + ", ?, 0, 0)"
    )
    cur = db.execute(sql, values)
    return cur.lastrowid


def _refresh_header(db, dn_id, note_prefix=None):
    """按现存明细重算 total_qty / category_count；行拆空则取消源单。"""
    rows = _load_lines(db, dn_id)
    total = round(sum(r["ordered_qty"] for r in rows), 6)
    category = len({r["product_id"] for r in rows})
    if not rows:
        db.execute(
            "UPDATE dn SET status=?, total_qty=0, category_count=0, "
            "note = CASE WHEN note='' THEN ? ELSE note || '；' || ? END, "
            "updated_at = datetime('now','localtime') WHERE id=?",
            (C.DN_CANCELLED, "全部拆出，源单关闭", "全部拆出，源单关闭", dn_id),
        )
    else:
        db.execute(
            "UPDATE dn SET total_qty=?, category_count=?, "
            "updated_at = datetime('now','localtime') WHERE id=?",
            (total, category, dn_id),
        )
    return total, category


def split_order(db, source_id, parts, operator="", note="", scope=None):
    """把一个 open 未拣货的 DN 按 parts 拆成若干子单，返回汇总结果。"""
    try:
        source_id = int(source_id)
    except (TypeError, ValueError):
        raise DNOrderError("source_id 必须为整数", "INVALID_SOURCE")
    src = _load_dn(db, source_id)
    _assert_splittable(db, src)
    _guard_owner(db, src, scope)

    if not isinstance(parts, list) or not parts:
        raise DNOrderError("parts 必须为非空数组", "INVALID_PARTS")
    lines = {r["product_id"]: r for r in _load_lines(db, source_id)}
    # 解析并校验每个 part，累计每个 product 的拆出量
    parsed = []
    taken = {}
    for i, part in enumerate(parts):
        if not isinstance(part, dict) or not isinstance(part.get("lines"), list) or not part["lines"]:
            raise DNOrderError(f"parts[{i}].lines 必须为非空数组", "INVALID_PARTS")
        seen, block = set(), []
        for j, ln in enumerate(part["lines"]):
            if not isinstance(ln, dict):
                raise DNOrderError(f"parts[{i}].lines[{j}] 必须为对象", "INVALID_PARTS")
            try:
                pid = int(ln.get("product_id"))
            except (TypeError, ValueError):
                raise DNOrderError(f"parts[{i}].lines[{j}].product_id 非法", "INVALID_PARTS")
            if pid not in lines:
                raise DNOrderError(f"物料 id={pid} 不在源单明细中", "PRODUCT_NOT_IN_SOURCE")
            if pid in seen:
                raise DNOrderError(f"parts[{i}] 中物料 id={pid} 重复，请合并数量", "DUP_PRODUCT")
            seen.add(pid)
            qty = _qty(ln.get("qty"), f"parts[{i}].lines[{j}].qty")
            taken[pid] = round(taken.get(pid, 0) + qty, 6)
            block.append((pid, qty, str(ln.get("note") or "").strip()))
        parsed.append(block)

    for pid, q in taken.items():
        available = lines[pid]["ordered_qty"]
        if q > available + C.SPLIT_QTY_EPS:
            raise DNOrderError(
                f"物料 id={pid} 拆出 {q} 超过源单可拆量 {available}", "QTY_EXCEEDED"
            )

    targets, detail = [], []
    for idx, block in enumerate(parsed):
        suffix = f"由 {src['code']} 拆出（第 {idx + 1} 份）"
        if note:
            suffix += f"；{note}"
        child_id = _clone_dn(db, src, suffix)
        db.execute(
            "UPDATE dn SET origin_action=?, origin_parent_id=? WHERE id=?",
            (C.SPLIT_ACTION_SPLIT, source_id, child_id),
        )
        subtotal = 0.0
        for pid, qty, ln_note in block:
            db.execute(
                "INSERT INTO dn_line (dn_id, product_id, ordered_qty, extra) VALUES (?, ?, ?, ?)",
                (child_id, pid, qty, lines[pid]["extra"]),
            )
            subtotal += qty
            detail.append({"product_id": pid, "qty": qty, "to_dn_id": child_id, "note": ln_note})
        db.execute(
            "UPDATE dn SET total_qty=?, category_count=? WHERE id=?",
            (round(subtotal, 6), len(block), child_id),
        )
        child = db.execute("SELECT * FROM dn WHERE id=?", (child_id,)).fetchone()
        targets.append(dict(child))
    # 源单扣减
    for pid, q in taken.items():
        line = lines[pid]
        new_qty = round(line["ordered_qty"] - q, 6)
        if new_qty <= C.SPLIT_QTY_EPS:
            db.execute("DELETE FROM dn_line WHERE id=?", (line["id"],))
        else:
            db.execute("UPDATE dn_line SET ordered_qty=? WHERE id=?", (new_qty, line["id"]))
    _refresh_header(db, source_id)
    src_after = dict(_load_dn(db, source_id))
    _write_log(db, C.SPLIT_ACTION_SPLIT, src["owner_id"], src["customer_id"],
               [dict(src)], targets, detail, operator, note)
    return {"action": C.SPLIT_ACTION_SPLIT, "source": src_after,
            "targets": targets, "lines": [_dict_line(x) for x in _load_lines(db, source_id)]}


def _dict_line(row):
    d = dict(row)
    d["remaining_pick"] = d["ordered_qty"]
    return d


def merge_orders(db, source_ids, operator="", note="", scope=None):
    """把多张同货主同客户的 open 未拣货 DN 合并成一张目标单。"""
    if not isinstance(source_ids, list) or len(source_ids) < 2:
        raise DNOrderError("合单至少需要 2 张出库单", "INVALID_SOURCES")
    uniq = []
    for sid in source_ids:
        try:
            v = int(sid)
        except (TypeError, ValueError):
            raise DNOrderError("source_ids 必须全为整数", "INVALID_SOURCES")
        if v not in uniq:
            uniq.append(v)
    if len(uniq) < 2:
        raise DNOrderError("合单至少需要 2 张不同的出库单", "INVALID_SOURCES")

    sources = []
    for sid in uniq:
        dn = _load_dn(db, sid)
        _assert_splittable(db, dn)
        _guard_owner(db, dn, scope)
        sources.append(dn)
    owner_id, customer_id = sources[0]["owner_id"], sources[0]["customer_id"]
    for dn in sources[1:]:
        if dn["owner_id"] != owner_id:
            raise DNOrderError("合单源单必须属于同一货主", "OWNER_MISMATCH")
        if dn["customer_id"] != customer_id:
            raise DNOrderError("合单源单必须属于同一客户", "CUSTOMER_MISMATCH")
        if any(dn[field] != sources[0][field] for field in ("source_warehouse", "doc_type", "target_warehouse")):
            raise DNOrderError("合单源单的出库仓、单据类型和调入仓必须相同", "WAREHOUSE_MISMATCH")

    # 按 product 汇总，数量守恒
    agg = {}
    snapshots = {}
    detail = []
    for dn in sources:
        for ln in _load_lines(db, dn["id"]):
            pid = ln["product_id"]
            agg[pid] = round(agg.get(pid, 0) + ln["ordered_qty"], 6)
            extra = ln["extra"]
            if extra and snapshots.get(pid) and json.loads(extra) != json.loads(snapshots[pid]):
                raise DNOrderError("同一货品的单据明细属性不同，无法合并", "LINE_SNAPSHOT_MISMATCH")
            if extra:
                snapshots[pid] = extra
            detail.append({"product_id": pid, "qty": ln["ordered_qty"], "from_dn_id": dn["id"]})
    before = round(sum(ln["ordered_qty"] for dn in sources for ln in _load_lines(db, dn["id"])), 6)

    merge_note = "合并 " + "、".join(d["code"] for d in sources) + " 生成"
    if note:
        merge_note += f"；{note}"
    target_id = _clone_dn(db, sources[0], merge_note)
    db.execute(
        "UPDATE dn SET origin_action=?, origin_parent_id=? WHERE id=?",
        (C.SPLIT_ACTION_MERGE, sources[0]["id"], target_id),
    )
    for pid, qty in sorted(agg.items()):
        db.execute(
            "INSERT INTO dn_line (dn_id, product_id, ordered_qty, extra) VALUES (?, ?, ?, ?)",
            (target_id, pid, qty, snapshots.get(pid, "")),
        )
    after = round(sum(agg.values()), 6)
    if abs(after - before) > C.SPLIT_QTY_EPS:
        raise DNOrderError(f"合单数量不守恒: {before} -> {after}", "QTY_NOT_CONSERVED")
    db.execute(
        "UPDATE dn SET total_qty=?, category_count=? WHERE id=?",
        (after, len(agg), target_id),
    )
    # 源单取消（不删，保留追溯）
    for dn in sources:
        cancel_note = f"已合入 {db.execute('SELECT code FROM dn WHERE id=?', (target_id,)).fetchone()['code']}"
        db.execute(
            "UPDATE dn SET status=?, note = CASE WHEN note='' THEN ? ELSE note || '；' || ? END, "
            "updated_at = datetime('now','localtime') WHERE id=?",
            (C.DN_CANCELLED, cancel_note, cancel_note, dn["id"]),
        )
    target = dict(_load_dn(db, target_id))
    _write_log(db, C.SPLIT_ACTION_MERGE, owner_id, customer_id,
               [dict(x) for x in sources], [target], detail, operator, note)
    return {"action": C.SPLIT_ACTION_MERGE, "sources": [dict(x) for x in sources],
            "target": target, "target_lines": [_dict_line(x) for x in _load_lines(db, target_id)],
            "total_qty": after}


def dictionary():
    return {
        "actions": [{"value": a, "label": C.SPLIT_ACTION_LABELS[a]} for a in C.SPLIT_ACTIONS],
        "modes": [{"value": m, "label": C.SPLIT_MODE_LABELS[m]} for m in C.SPLIT_MODES],
        "allowed_status": list(C.SPLIT_ALLOWED_STATUS),
        "permissions": list(C.SPLIT_PERMISSIONS),
        "qty_eps": C.SPLIT_QTY_EPS,
    }
