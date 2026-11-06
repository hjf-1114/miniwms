"""盘点单（Stocktake）模块：/api/stocktakes。

M22 状态机（见 ``docs/PUTAWAY_RECOMMENDATION.md`` §18.7）：

    open ──freeze(按方案生成行)──> frozen ──count(盲盘录入)──> frozen
       ──[人工按钮] 生成库存调整单──> adjusted ──调整单审核过账──> closed

- 差异**不再**由本模块直接 ``adjust_stock()``；人工点「生成库存调整单」只产出 draft，
  审核通过才过账，保证「任何数量变化都有一张可追溯调整单」。
- 真盲盘分级接口：``count-sheet`` / ``count`` 是作业录入视角，不返回账存与差异；
  ``GET /<id>`` 与 ``diff-preview`` 是复核视角，照常返回差异。
- 冻结按 ``stocktake_plan`` / 一次性覆盖生成行，并把生效范围写进 ``scope_snapshot``，
  之后改方案不影响历史单据。
"""
import math

from flask import Blueprint, jsonify, request

import constants as C
import scope as S
from adjustment_service import AdjustmentError, create_adjustment
from inventory_service import StockError
from main import get_db, request_payload
from stocktake_service import (PlanError, collect_lines, dumps,
                               effective_config, loads)

stocktake_bp = Blueprint("stocktake", __name__, url_prefix="/api/stocktakes")

OVERRIDE_KEYS = ("scope", "scope_type", "blind_mode", "dynamic_baseline",
                 "lookback_days", "movement_from", "movement_to",
                 "movement_types", "include_zero", "baseline_at")


def _load(db, stocktake_id):
    return db.execute("SELECT * FROM stocktake WHERE id = ?", (stocktake_id,)).fetchone()


def _load_lines(db, stocktake_id):
    return db.execute(
        """SELECT sl.*, p.sku, p.name AS product_name,
                  l.code AS location_code, l.loc_type AS location_type
           FROM stocktake_line sl
           JOIN product p ON p.id = sl.product_id
           JOIN location l ON l.id = sl.location_id
           WHERE sl.stocktake_id = ? ORDER BY sl.id""",
        (stocktake_id,),
    ).fetchall()


def _dict_line(l):
    d = dict(l)
    d.pop("location_type", None)
    if l["counted_qty"] is None:
        d["diff"] = None
    else:
        d["diff"] = round(l["counted_qty"] - l["book_qty"], 6)
    return d


def _detail(db, st):
    o = db.execute("SELECT code, name FROM owner WHERE id = ?",
                   (st["owner_id"],)).fetchone()
    body = dict(st)
    body["owner_code"], body["owner_name"] = (o["code"], o["name"]) if o else (None, None)
    body["scope_snapshot"] = loads(body.get("scope_snapshot"), {})
    plan = None
    if st["plan_id"]:
        row = db.execute("SELECT code, name FROM stocktake_plan WHERE id = ?",
                         (st["plan_id"],)).fetchone()
        plan = dict(row) if row else None
    body["plan"] = plan
    return {"ok": True, "stocktake": body,
            "lines": [_dict_line(l) for l in _load_lines(db, st["id"])]}


@stocktake_bp.route("", methods=["POST"])
def create_stocktake():
    """创建盘点单；方案配置在创建时即快照，后续改方案不影响本单。"""
    payload = request_payload()
    code = str(payload.get("code") or "").strip()
    owner_id = payload.get("owner_id")
    note = str(payload.get("note") or "")

    errors = []
    if not code:
        errors.append("code 必填")
    elif len(code) > 32:
        errors.append("code 最长 32 字符")
    try:
        owner_id = int(owner_id)
    except (TypeError, ValueError):
        errors.append("owner_id 必填且为整数")
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    db = get_db()
    if db.execute("SELECT id FROM owner WHERE id = ?", (owner_id,)).fetchone() is None:
        return jsonify({"ok": False, "errors": [f"货主不存在: id={owner_id}"]}), 400
    if db.execute("SELECT id FROM stocktake WHERE code = ?", (code,)).fetchone() is not None:
        return jsonify({"ok": False, "errors": [f"盘点单编码已存在: {code}"]}), 409
    override = {k: payload[k] for k in OVERRIDE_KEYS if k in payload}
    plan_id = payload.get("plan_id") or None
    try:
        cfg = effective_config(db, plan_id=plan_id, override=override)
    except (PlanError, ValueError, TypeError) as e:
        return jsonify({"ok": False, "errors": [str(e)]}), 400

    cur = db.execute(
        "INSERT INTO stocktake (code, owner_id, note, plan_id, scope_type, "
        "blind_mode, config_override) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (code, owner_id, note, cfg["plan_id"], cfg["scope_type"],
         cfg["blind_mode"], dumps(cfg)),
    )
    db.commit()
    return jsonify({"ok": True, "stocktake": dict(_load(db, cur.lastrowid)),
                    "lines": [], "effective_config": cfg}), 201


@stocktake_bp.route("", methods=["GET"])
def list_stocktakes():
    args = request.args
    status = (args.get("status") or "").strip()
    keyword = (args.get("keyword") or "").strip()
    try:
        page = max(1, int(args.get("page", 1)))
        page_size = min(100, max(1, int(args.get("page_size", 20))))
    except ValueError:
        return jsonify(ok=False, error="page/page_size 必须为整数"), 400

    where, params = [], []
    if status:
        where.append("s.status = ?")
        params.append(status)
    if keyword:
        where.append("s.code LIKE ?")
        params.append(f"%{keyword}%")
    S.scoped_where(where, params, table="s")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    db = get_db()
    total = db.execute(
        f"SELECT COUNT(*) AS n FROM stocktake s {where_sql}", params
    ).fetchone()["n"]
    rows = db.execute(
        f"""SELECT s.*, o.code AS owner_code, o.name AS owner_name,
                   sp.code AS plan_code
            FROM stocktake s JOIN owner o ON o.id = s.owner_id
            LEFT JOIN stocktake_plan sp ON sp.id = s.plan_id {where_sql}
            ORDER BY s.id DESC LIMIT ? OFFSET ?""",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()
    return jsonify(ok=True, total=total, page=page, page_size=page_size,
                   stocktakes=[dict(r) for r in rows])


@stocktake_bp.route("/<int:stocktake_id>", methods=["GET"])
def get_stocktake(stocktake_id):
    """复核视角：返回账存 / 实盘 / 差异（复核人必须看得见差异）。"""
    db = get_db()
    st = _load(db, stocktake_id)
    if st is None:
        return jsonify(ok=False, error="盘点单不存在"), 404
    return jsonify(**_detail(db, st))


@stocktake_bp.route("/<int:stocktake_id>/freeze", methods=["POST"])
def freeze(stocktake_id):
    """冻结：按生效方案生成盘点行（全盘 / 部分 / 动碰），并把范围落盘快照。"""
    db = get_db()
    st = _load(db, stocktake_id)
    if st is None:
        return jsonify(ok=False, error="盘点单不存在"), 404
    if st["status"] != C.STOCKTAKE_OPEN:
        return jsonify(ok=False, error=f"状态为 {st['status']}，仅 open 可冻结"), 409
    try:
        db.execute("BEGIN IMMEDIATE")
        st = _load(db, stocktake_id)
        if st["status"] != C.STOCKTAKE_OPEN:
            raise ValueError(f"状态为 {st['status']}，仅 open 可冻结")
        cfg = effective_config(db, None, loads(st["config_override"], {}))
        lines, baseline = collect_lines(db, cfg, st["owner_id"])
        snapshot = dict(cfg)
        snapshot["baseline_from"], snapshot["baseline_to"] = baseline
        snapshot["line_count"] = len(lines)
        for r in lines:
            db.execute(
                "INSERT INTO stocktake_line (stocktake_id, owner_id, product_id, "
                "location_id, batch_no, book_qty, counted_qty) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL)",
                (stocktake_id, r["owner_id"], r["product_id"], r["location_id"],
                 r["batch_no"], r["book_qty"]),
            )
        db.execute(
            "UPDATE stocktake SET status = ?, scope_snapshot = ?, "
            "updated_at = datetime('now','localtime') WHERE id = ?",
            (C.STOCKTAKE_FROZEN, dumps(snapshot), stocktake_id),
        )
        db.commit()
    except (ValueError, StockError, PlanError) as e:
        db.rollback()
        return jsonify(ok=False, error=str(e)), 400
    return jsonify(ok=True, status=C.STOCKTAKE_FROZEN, line_count=len(lines),
                   snapshot=snapshot,
                   lines=[_dict_line(l) for l in _load_lines(db, stocktake_id)])


def _sheet_lines(db, st):
    """盲盘录入视角：只给定位信息，不给账存 / 已录数 / 差异。"""
    rows = _load_lines(db, st["id"])
    blind = st["blind_mode"] != C.BLIND_NONE
    out = []
    for l in rows:
        item = {"line_id": l["id"], "sku": l["sku"],
                "product_name": l["product_name"],
                "location_code": l["location_code"],
                "batch_no": l["batch_no"] or ""}
        if not blind:
            item.update({"book_qty": l["book_qty"],
                         "counted_qty": l["counted_qty"],
                         "diff": _dict_line(l)["diff"]})
        out.append(item)
    return out, blind


@stocktake_bp.route("/<int:stocktake_id>/count-sheet", methods=["GET"])
def count_sheet(stocktake_id):
    """作业录入接口：hide_book 时响应体里不出现任何账存或差异字段。"""
    db = get_db()
    st = _load(db, stocktake_id)
    if st is None:
        return jsonify(ok=False, error="盘点单不存在"), 404
    lines, blind = _sheet_lines(db, st)
    return jsonify(ok=True, blind_mode=st["blind_mode"], hidden=blind, lines=lines)


@stocktake_bp.route("/<int:stocktake_id>/count", methods=["POST"])
def count(stocktake_id):
    """盲盘录入：只回「本次已录行数」，不回差异（真盲盘分级返回）。"""
    payload = request_payload()
    entries = payload.get("lines")
    if not isinstance(entries, list) or not entries:
        return jsonify(ok=False, errors=["lines 必须为非空数组"]), 400

    parsed = []
    for i, e in enumerate(entries):
        try:
            line_id = int(e.get("line_id"))
            qty = float(e.get("counted_qty"))
        except (TypeError, ValueError, AttributeError):
            return jsonify(ok=False, errors=[f"lines[{i}] 字段不合法"]), 400
        if math.isnan(qty) or qty < 0:
            return jsonify(ok=False, errors=[f"lines[{i}] counted_qty 必须 >= 0"]), 400
        parsed.append((line_id, qty))

    db = get_db()
    st = _load(db, stocktake_id)
    if st is None:
        return jsonify(ok=False, error="盘点单不存在"), 404
    if st["status"] != C.STOCKTAKE_FROZEN:
        return jsonify(ok=False, error=f"状态为 {st['status']}，仅 frozen 可录入盲盘"), 409
    for line_id, qty in parsed:
        res = db.execute(
            "UPDATE stocktake_line SET counted_qty = ? WHERE id = ? AND stocktake_id = ?",
            (qty, line_id, stocktake_id),
        )
        if res.rowcount == 0:
            return jsonify(ok=False, errors=[f"盘点行不存在或不属于该单: id={line_id}"]), 400
    db.commit()
    return jsonify(ok=True, status=C.STOCKTAKE_FROZEN, counted_lines=len(parsed))


@stocktake_bp.route("/<int:stocktake_id>/diff-preview", methods=["GET"])
def diff_preview(stocktake_id):
    """复核视角：差异生成调整单之前先看差异。"""
    db = get_db()
    st = _load(db, stocktake_id)
    if st is None:
        return jsonify(ok=False, error="盘点单不存在"), 404
    if st["status"] not in (C.STOCKTAKE_FROZEN, C.STOCKTAKE_ADJUSTED, C.STOCKTAKE_CLOSED):
        return jsonify(ok=False, error=f"状态为 {st['status']}，未冻结不可查看差异"), 409
    lines = [_dict_line(l) for l in _load_lines(db, stocktake_id)]
    diffs = [l for l in lines if l["diff"] is not None and abs(l["diff"]) > 1e-9]
    return jsonify(ok=True, lines=lines, diff_lines=diffs,
                   diff_count=len(diffs),
                   uncounted=sum(1 for l in lines if l["counted_qty"] is None))


@stocktake_bp.route("/<int:stocktake_id>/generate-adjustment", methods=["POST"])
def generate_adjustment(stocktake_id):
    """人工按钮：按差异生成一张 draft 调整单（不自动过账）。

    差异全为 0 时无需调整单，直接结案；调整单被取消后可重新生成。
    """
    db = get_db()
    st = _load(db, stocktake_id)
    if st is None:
        return jsonify(ok=False, error="盘点单不存在"), 404
    if st["status"] not in (C.STOCKTAKE_FROZEN, C.STOCKTAKE_ADJUSTED):
        return jsonify(ok=False, error=f"状态为 {st['status']}，不可生成调整单"), 409
    if st["adjustment_id"]:
        cur = db.execute("SELECT status FROM stock_adjustment WHERE id = ?",
                         (st["adjustment_id"],)).fetchone()
        if cur and cur["status"] == C.ADJUSTMENT_DRAFT:
            return jsonify(ok=False, error="已有待审核的调整单，请先审核或取消"), 409
    try:
        db.execute("BEGIN IMMEDIATE")
        st = _load(db, stocktake_id)
        raw_lines = _load_lines(db, stocktake_id)
        uncounted = [l for l in raw_lines if l["counted_qty"] is None]
        if uncounted:
            raise ValueError("存在未盘点的行，请先完成盲盘再生成调整单")
        diffs = []
        for l in raw_lines:
            delta = l["counted_qty"] - l["book_qty"]
            if abs(delta) > 1e-9:
                diffs.append({
                    "owner_id": st["owner_id"], "product_id": l["product_id"],
                    "location_id": l["location_id"], "batch_no": l["batch_no"] or "",
                    "delta_qty": delta, "reason": "盘点差异",
                })
        if not diffs:
            db.execute(
                "UPDATE stocktake SET status = ?, closed_at = datetime('now','localtime'), "
                "updated_at = datetime('now','localtime') WHERE id = ?",
                (C.STOCKTAKE_CLOSED, stocktake_id),
            )
            db.commit()
            return jsonify(ok=True, no_diff=True, status=C.STOCKTAKE_CLOSED,
                           adjustment_id=None, diff_count=0)
        adj_id, adj_code, _ = create_adjustment(
            db, source_type=C.ADJUSTMENT_STOCKTAKE_DIFF, source_id=stocktake_id,
            reason_code="盘点差异", lines=diffs,
            note=f"盘点单 {st['code']} 差异生成",
        )
        db.execute(
            "UPDATE stocktake SET status = ?, adjustment_id = ?, "
            "updated_at = datetime('now','localtime') WHERE id = ?",
            (C.STOCKTAKE_ADJUSTED, adj_id, stocktake_id),
        )
        db.commit()
    except (ValueError, AdjustmentError, StockError) as e:
        db.rollback()
        return jsonify(ok=False, error=str(e)), 400
    except S.ScopeError as e:
        db.rollback()
        return jsonify(ok=False, error=str(e)), e.status
    return jsonify(ok=True, status=C.STOCKTAKE_ADJUSTED, no_diff=False,
                   adjustment_id=adj_id, adjustment_code=adj_code,
                   diff_count=len(diffs))


@stocktake_bp.route("/<int:stocktake_id>/adjust", methods=["POST"])
def adjust_deprecated(stocktake_id):
    """旧旁路已移除：差异不再由盘点路由直接过账（M22）。"""
    return jsonify(ok=False, error="该接口已停用；请改用 /generate-adjustment "
                                   "生成调整单并在 /api/stock-adjustments 审核过账"), 410


@stocktake_bp.route("/<int:stocktake_id>/close", methods=["POST"])
def close(stocktake_id):
    """结案兜底：adjusted 且没有待审核调整单时可手动结案。"""
    db = get_db()
    st = _load(db, stocktake_id)
    if st is None:
        return jsonify(ok=False, error="盘点单不存在"), 404
    if st["status"] != C.STOCKTAKE_ADJUSTED:
        return jsonify(ok=False, error=f"状态为 {st['status']}，仅已调整单可结案"), 409
    if st["adjustment_id"]:
        cur = db.execute("SELECT status FROM stock_adjustment WHERE id = ?",
                         (st["adjustment_id"],)).fetchone()
        if cur and cur["status"] == C.ADJUSTMENT_DRAFT:
            return jsonify(ok=False, error="调整单仍待审核，审核通过后才可结案"), 409
    db.execute(
        "UPDATE stocktake SET status = ?, closed_at = datetime('now','localtime'), "
        "updated_at = datetime('now','localtime') WHERE id = ?",
        (C.STOCKTAKE_CLOSED, stocktake_id),
    )
    db.commit()
    return jsonify(ok=True, status=C.STOCKTAKE_CLOSED)
