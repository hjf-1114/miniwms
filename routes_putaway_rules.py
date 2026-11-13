"""上架规则配置 API（M25 后端先行，M26 补齐编辑器交互）。

- 策略包 / 规则行 CRUD：``/api/putaway-rule-sets``、``/api/putaway-rules``
- 编辑器数据字典：``/api/putaway-rule-types``（13 维 / 9 种寻址 / 8 项 limits / 9 条评分）
- 条件组试算：``POST /api/putaway-rules/trial``（只读，不改库）
- 推荐日志：``GET /api/putaway-recommendations`` + M26 采纳 / 驳回动作与命中率
- M26 新增：规则行拖拽排序、策略包删除保护、外部单据类别可配字典

按 v3.3 3B：约束内容全部来自规则行 limits，配置端点不写死任何业务红线。
"""
import sqlite3

from flask import Blueprint, jsonify, request

import constants as C
import putaway_rules as PR
from main import get_db, request_payload

pr_bp = Blueprint("putaway_rules", __name__, url_prefix="/api")


def _run(action, *args, **kwargs):
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        result = action(db, *args, **kwargs)
        db.commit()
    except PR.RuleError as e:
        db.rollback()
        status = 404 if e.code == "NOT_FOUND" else 400
        return jsonify(ok=False, error=str(e), code=e.code), status
    except (ValueError, TypeError) as e:
        db.rollback()
        return jsonify(ok=False, error=str(e)), 400
    except sqlite3.IntegrityError as e:
        db.rollback()
        return jsonify(ok=False, error=f"配置冲突（编码/优先级重复）：{e}"), 400
    return result


@pr_bp.route("/putaway-rule-types", methods=["GET"])
def rule_types():
    return jsonify(ok=True, **PR.rule_types())


@pr_bp.route("/putaway-rule-sets", methods=["GET"])
def list_sets():
    return jsonify(ok=True, rule_sets=PR.list_sets(get_db()))


@pr_bp.route("/putaway-rule-sets", methods=["POST"])
def create_set():
    payload = request_payload() or {}

    def action(db):
        return jsonify(ok=True, id=PR.save_set(db, payload)), 201

    return _run(action)


@pr_bp.route("/putaway-rule-sets/<int:sid>", methods=["PUT"])
def update_set(sid):
    payload = dict(request_payload() or {})
    payload["id"] = sid
    return _run(lambda db: jsonify(ok=True, id=PR.save_set(db, payload)))


@pr_bp.route("/putaway-rules", methods=["GET"])
def list_rules():
    args = request.args
    rows = PR.list_rule_rows(get_db(), args.get("set_id"), args.get("rule_type"))
    return jsonify(ok=True, rules=rows, total=len(rows))


@pr_bp.route("/putaway-rules", methods=["POST"])
def create_rule():
    payload = request_payload() or {}

    def action(db):
        return jsonify(ok=True, id=PR.save_rule(db, payload)), 201

    return _run(action)


@pr_bp.route("/putaway-rules/<int:rid>", methods=["PUT"])
def update_rule(rid):
    payload = dict(request_payload() or {})
    payload["id"] = rid
    return _run(lambda db: jsonify(ok=True, id=PR.save_rule(db, payload)))


@pr_bp.route("/putaway-rules/<int:rid>", methods=["DELETE"])
def remove_rule(rid):
    def action(db):
        PR.delete_rule(db, rid)
        return jsonify(ok=True, message="规则行已删除，该规则不再拦截")

    return _run(action)


@pr_bp.route("/putaway-rules/trial", methods=["POST"])
def trial():
    """条件组试算：只读比对 condition 与给定 ctx，不写库、不查规则表。"""
    payload = request_payload() or {}
    cond = payload.get("condition") or {}
    ctx = payload.get("ctx") or {}
    if not isinstance(cond, dict) or not isinstance(ctx, dict):
        return jsonify(ok=False, error="condition / ctx 必须是对象"), 400
    unknown = [k for k in cond if k not in C.PUTAWAY_CONDITION_DIMS]
    if unknown:
        return jsonify(ok=False, error=f"未知条件维度: {unknown}"), 400
    return jsonify(ok=True, matched=PR.matches(cond, ctx),
                   dims=len(C.PUTAWAY_CONDITION_DIMS))


@pr_bp.route("/putaway-recommendations", methods=["GET"])
def recommendations():
    args = request.args
    try:
        page = max(1, int(args.get("page", 1)))
        page_size = min(200, max(1, int(args.get("page_size", 20))))
    except ValueError:
        return jsonify(ok=False, error="page/page_size 必须为整数"), 400
    where, params = [], []
    for key in ("asn_id", "asn_line_id", "action", "rule_id"):
        val = (args.get(key) or "").strip()
        if val:
            where.append(f"{key} = ?")
            params.append(val)
    # 按业务日期（created_at 前 10 位）过滤，方便排查某一天的推荐表现
    for key, op in (("date_from", ">="), ("date_to", "<=")):
        val = (args.get(key) or "").strip()
        if val:
            where.append(f"substr(created_at, 1, 10) {op} ?")
            params.append(val)
    if (args.get("has_hit") or "").strip() == "1":
        where.append("rule_id IS NOT NULL AND rule_id > 0")
    sql = "WHERE " + " AND ".join(where) if where else ""

    db = get_db()
    total = db.execute(
        f"SELECT COUNT(*) AS n FROM putaway_recommendation {sql}", params).fetchone()["n"]
    rows = [dict(r) for r in db.execute(
        f"SELECT * FROM putaway_recommendation {sql} "
        "ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size]).fetchall()]
    # stats 沿用 M25 口径（全表）；rates 按当前过滤条件计算，供编辑器统计卡使用
    stats = {}
    for r in db.execute(
        "SELECT action, COUNT(*) AS n FROM putaway_recommendation GROUP BY action"
    ).fetchall():
        stats[r["action"]] = r["n"]
    rates = PR.recommendation_rates(db, sql, list(params))
    return jsonify(ok=True, total=total, page=page, page_size=page_size,
                   recommendations=rows, stats=stats, rates=rates)


# ── M26：采纳 / 驳回、拖拽排序、策略包删除、外部单据类别字典 ─────────────
@pr_bp.route("/putaway-recommendations/<int:rid>/action", methods=["POST"])
def recommendation_action(rid):
    """登记一次推荐结果的采纳或驳回；同一条日志重复提交只更新，不重复计数。"""
    payload = request_payload() or {}

    def action(db):
        row = PR.set_recommendation_action(
            db, rid, str(payload.get("action") or ""),
            payload.get("chosen_location_id"),
            payload.get("override_reason") or "",
            payload.get("operator") or "")
        return jsonify(ok=True, recommendation=row)

    return _run(action)


@pr_bp.route("/putaway-rules/reorder", methods=["POST"])
def reorder_rules():
    payload = request_payload() or {}
    return _run(lambda db: jsonify(
        ok=True, updated=PR.reorder_rules(db, payload.get("items"))))


@pr_bp.route("/putaway-rule-sets/<int:sid>", methods=["DELETE"])
def remove_set(sid):
    return _run(lambda db: jsonify(ok=True, deleted=PR.delete_set(db, sid)))


def _enabled_flag(value, default=1):
    """兼容浏览器 select 提交的 '0' / '1' 字符串与布尔值。"""
    if value is None or value == "":
        return default
    if value in (False, 0, "0", "false", "False"):
        return 0
    return 1


@pr_bp.route("/external-doc-types", methods=["GET"])
def list_doc_types():
    return jsonify(ok=True, doc_types=PR.list_doc_types(get_db()),
                   override_reasons=list(C.PUTAWAY_OVERRIDE_REASONS))


@pr_bp.route("/external-doc-types", methods=["POST"])
def create_doc_type():
    payload = dict(request_payload() or {})
    payload["enabled"] = _enabled_flag(payload.get("enabled"))
    return _run(lambda db: (jsonify(ok=True, id=PR.save_doc_type(db, payload)), 201))


@pr_bp.route("/external-doc-types/<int:tid>", methods=["PUT"])
def update_doc_type(tid):
    payload = dict(request_payload() or {})
    payload["id"] = tid
    payload["enabled"] = _enabled_flag(payload.get("enabled"))
    return _run(lambda db: jsonify(ok=True, id=PR.save_doc_type(db, payload)))


@pr_bp.route("/external-doc-types/<int:tid>", methods=["DELETE"])
def remove_doc_type(tid):
    return _run(lambda db: jsonify(ok=True, deleted=PR.delete_doc_type(db, tid)))
