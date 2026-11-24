"""M36 出库复核 API（服务层见 ``review_service.py``）。

- POST /api/dn-reviews                 从 DN 拣货明细生成独立复核单（快照批次维度）
- GET  /api/dn-reviews                 复核单列表（分页 / 状态 / DN / 关键字 + 货主隔离）
- GET  /api/dn-reviews/<id>            复核单详情（头 + 行 + 差异留痕）
- POST /api/dn-reviews/<id>/check      逐行复核（通过 / 登记差异只留痕）
- POST /api/dn-reviews/<id>/complete   完成复核（所有行已复核）
- POST /api/dn-reviews/<id>/cancel     取消复核单（非终态）
- GET  /api/dn-reviews/logs            差异留痕查询（分页 + 过滤 + 统计）
- GET  /api/dn-reviews/dict            字典（状态 / 结果 / 原因码 / 权限码）

逐行差异只留痕；完成复核更新出库状态。写操作统一 BEGIN IMMEDIATE。
"""
import sqlite3

from flask import Blueprint, jsonify, request

import constants as C
import review_service as RS
import scope as S
from main import get_db, request_payload

rv_bp = Blueprint("dn_review", __name__, url_prefix="/api/dn-reviews")


def _run(action, *args, **kwargs):
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        result = action(db, *args, **kwargs)
        db.commit()
    except RS.ReviewError as e:
        db.rollback()
        return jsonify(ok=False, error=str(e), code=e.code), 400
    except (ValueError, TypeError) as e:
        db.rollback()
        return jsonify(ok=False, error=str(e)), 400
    except sqlite3.IntegrityError as e:
        db.rollback()
        return jsonify(ok=False, error=f"数据冲突：{e}"), 409
    return result


def _operator():
    from flask import g
    user = getattr(g, "current_user", None) or {}
    return user.get("username", "") if isinstance(user, dict) else ""


@rv_bp.route("/dict", methods=["GET"])
def dictionary():
    return jsonify(ok=True, **RS.dictionary())


@rv_bp.route("", methods=["POST"])
def create_review():
    payload = request_payload() or {}
    try:
        dn_id = int(payload.get("dn_id"))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="dn_id 必填且为整数"), 400
    return _run(RS.create_review, dn_id, _operator(), payload.get("note", ""))


@rv_bp.route("", methods=["GET"])
def list_reviews():
    args = request.args
    where, params = [], []
    S.scoped_where(where, params, table="r")
    if (v := (args.get("status") or "").strip()):
        where.append("r.status = ?")
        params.append(v)
    if (v := (args.get("dn_id") or "").strip()):
        where.append("r.dn_id = ?")
        params.append(v)
    if (v := (args.get("keyword") or "").strip()):
        where.append("(r.code LIKE ? OR r.dn_code LIKE ?)")
        params += [f"%{v}%", f"%{v}%"]
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    try:
        page = max(1, int(args.get("page", 1)))
        page_size = min(100, max(1, int(args.get("page_size", 20))))
    except ValueError:
        return jsonify(ok=False, error="page/page_size 必须为整数"), 400
    db = get_db()
    total = db.execute(
        f"SELECT COUNT(*) AS n FROM dn_review r {where_sql}", params
    ).fetchone()["n"]
    rows = [dict(r) for r in db.execute(
        f"""SELECT r.*, o.code AS owner_code, o.name AS owner_name,
                   c.code AS customer_code, c.name AS customer_name
            FROM dn_review r
            JOIN owner o ON o.id = r.owner_id
            LEFT JOIN customer c ON c.id = r.customer_id
            {where_sql} ORDER BY r.id DESC LIMIT ? OFFSET ?""",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()]
    stats = {r["status"]: r["n"] for r in db.execute(
        "SELECT status, COUNT(*) AS n FROM dn_review GROUP BY status"
    ).fetchall()}
    return jsonify(ok=True, total=total, page=page, page_size=page_size,
                   reviews=rows, stats=stats)


@rv_bp.route("/<int:review_id>", methods=["GET"])
def get_review(review_id):
    db = get_db()
    review = db.execute("SELECT * FROM dn_review WHERE id = ?", (review_id,)).fetchone()
    if review is None:
        return jsonify(ok=False, error="复核单不存在"), 404
    return jsonify(ok=True, **RS.get_detail(db, review_id))


@rv_bp.route("/<int:review_id>/check", methods=["POST"])
def check_line(review_id):
    payload = request_payload() or {}
    try:
        line_id = int(payload.get("line_id"))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="line_id 必填且为整数"), 400
    return _run(RS.record, review_id, line_id, payload.get("actual_qty"),
                payload.get("result"), payload.get("reason_code", ""),
                payload.get("note", ""), _operator())


@rv_bp.route("/<int:review_id>/complete", methods=["POST"])
def complete_review(review_id):
    return _run(RS.complete, review_id, _operator())


@rv_bp.route("/<int:review_id>/cancel", methods=["POST"])
def cancel_review(review_id):
    return _run(RS.cancel, review_id, _operator())


@rv_bp.route("/logs", methods=["GET"])
def diff_logs():
    args = request.args
    where, params = [], []
    S.scoped_where(where, params, table="l")
    for key in ("review_id", "dn_id", "action", "reason_code"):
        if (v := (args.get(key) or "").strip()):
            where.append(f"l.{key} = ?")
            params.append(v)
    if (v := (args.get("keyword") or "").strip()):
        where.append("(l.review_code LIKE ? OR l.batch_no LIKE ? OR l.sku LIKE ?)")
        params += [f"%{v}%", f"%{v}%", f"%{v}%"]
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    try:
        page = max(1, int(args.get("page", 1)))
        page_size = min(200, max(1, int(args.get("page_size", 20))))
    except ValueError:
        return jsonify(ok=False, error="page/page_size 必须为整数"), 400
    db = get_db()
    total = db.execute(
        f"SELECT COUNT(*) AS n FROM dn_review_diff_log l {where_sql}", params
    ).fetchone()["n"]
    rows = [dict(r) for r in db.execute(
        f"SELECT * FROM dn_review_diff_log l {where_sql} "
        "ORDER BY l.id DESC LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()]
    stats = {r["action"]: r["n"] for r in db.execute(
        "SELECT action, COUNT(*) AS n FROM dn_review_diff_log GROUP BY action"
    ).fetchall()}
    reasons = {r["reason_code"]: r["n"] for r in db.execute(
        "SELECT reason_code, COUNT(*) AS n FROM dn_review_diff_log "
        "WHERE reason_code <> '' GROUP BY reason_code"
    ).fetchall()}
    return jsonify(ok=True, total=total, page=page, page_size=page_size,
                   logs=rows, stats=stats, reasons=reasons)
