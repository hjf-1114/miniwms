"""M38 出库单拆单 / 合单 API（服务层见 ``dn_order_service.py``）。

- GET  /api/dn-order/dict              字典（动作 / 拆分模式 / 允许状态 / 权限码）
- GET  /api/dn-order/candidates        可拆合候选单（open 未拣货、货主隔离）
- POST /api/dn-order/split             拆单（源单按 parts 拆成若干子单）
- POST /api/dn-order/merge             合单（多单并成一单）
- GET  /api/dn-order/log               拆合留痕查询（分页 + 动作 / 货主过滤）
- GET  /api/dn-order/<id>/lineage      某单的拆合血缘（作为源单 / 目标单的留痕）

所有写操作统一 ``BEGIN IMMEDIATE``；按 ID 的资源先过 owner 归属守门。
拆合只在单据层做数量重分配，不动库存、不写 stock_movement。
"""
import sqlite3

from flask import Blueprint, jsonify, request

import dn_order_service as DS
import scope as S
from main import get_db, request_payload

dn_order_bp = Blueprint("dn_order", __name__)


def _run(action, *args, status=200, **kwargs):
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        result = action(db, *args, **kwargs)
        db.commit()
    except DS.DNOrderError as e:
        db.rollback()
        code = 404 if e.code == "DN_NOT_FOUND" else 400
        return jsonify(ok=False, error=str(e), code=e.code), code
    except S.ScopeError as e:
        db.rollback()
        return jsonify(ok=False, error=str(e)), e.status
    except (ValueError, TypeError) as e:
        db.rollback()
        return jsonify(ok=False, error=str(e)), 400
    except sqlite3.IntegrityError as e:
        db.rollback()
        return jsonify(ok=False, error=f"数据冲突：{e}"), 409
    return result, status


def _operator():
    from flask import g
    user = getattr(g, "current_user", None) or {}
    return user.get("username", "") if isinstance(user, dict) else ""


def _page_args(max_size=100):
    try:
        page = max(1, int(request.args.get("page", 1)))
        size = min(max_size, max(1, int(request.args.get("page_size", 20))))
    except ValueError:
        return None, None
    return page, size


@dn_order_bp.route("/api/dn-order/dict", methods=["GET"])
def dictionary():
    return jsonify(ok=True, **DS.dictionary())


@dn_order_bp.route("/api/dn-order/candidates", methods=["GET"])
def candidates():
    """可拆合候选：open + 未拣货 + 无 dn_pick；带 owner / 客户 / 行数与数量。"""
    db = get_db()
    where, params = ["d.status = ?"], list(DS.C.SPLIT_ALLOWED_STATUS[:1])
    S.scoped_where(where, params, table="d")
    clause = " AND ".join(where)
    rows = db.execute(
        f"""SELECT d.id, d.code, d.owner_id, d.customer_id, d.doc_type,
                   d.total_qty, d.category_count, d.created_at,
                   o.code AS owner_code, o.name AS owner_name,
                   c.code AS customer_code, c.name AS customer_name,
                   (SELECT COUNT(*) FROM dn_line dl WHERE dl.dn_id=d.id
                      AND dl.picked_qty > ?) AS picked_lines,
                   (SELECT COUNT(*) FROM dn_pick dp WHERE dp.dn_id=d.id) AS pick_rows
            FROM dn d
            JOIN owner o ON o.id = d.owner_id
            LEFT JOIN customer c ON c.id = d.customer_id
            WHERE {clause}
            ORDER BY d.id DESC LIMIT 200""",
        [DS.C.SPLIT_QTY_EPS] + params,
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["splittable"] = d["picked_lines"] == 0 and d["pick_rows"] == 0
        out.append(d)
    return jsonify(ok=True, candidates=out)


@dn_order_bp.route("/api/dn-order/split", methods=["POST"])
def split():
    body = request_payload() or {}
    result = _run(DS.split_order, body.get("source_id"), body.get("parts"),
                  operator=_operator(), note=str(body.get("note") or ""),
                  scope=S.current_scope())
    return result


@dn_order_bp.route("/api/dn-order/merge", methods=["POST"])
def merge():
    body = request_payload() or {}
    result = _run(DS.merge_orders, body.get("source_ids"),
                  operator=_operator(), note=str(body.get("note") or ""),
                  scope=S.current_scope())
    return result


@dn_order_bp.route("/api/dn-order/log", methods=["GET"])
def log():
    args = request.args
    page, size = _page_args()
    if page is None:
        return jsonify(ok=False, error="page/page_size 必须为整数"), 400
    where, params = [], []
    action = (args.get("action") or "").strip()
    if action:
        where.append("l.action = ?")
        params.append(action)
    keyword = (args.get("keyword") or "").strip()
    if keyword:
        where.append("(l.source_codes LIKE ? OR l.target_codes LIKE ?)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])
    S.scoped_where(where, params, table="l")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    db = get_db()
    total = db.execute(
        f"SELECT COUNT(*) AS n FROM dn_order_log l {clause}", params
    ).fetchone()["n"]
    rows = db.execute(
        f"""SELECT l.*, o.code AS owner_code, o.name AS owner_name
            FROM dn_order_log l LEFT JOIN owner o ON o.id = l.owner_id
            {clause} ORDER BY l.id DESC LIMIT ? OFFSET ?""",
        params + [size, (page - 1) * size],
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["detail_rows"] = __import__("json").loads(d.get("detail") or "[]")
        except (ValueError, TypeError):
            d["detail_rows"] = []
        out.append(d)
    return jsonify(ok=True, total=total, page=page, page_size=size, logs=out)


@dn_order_bp.route("/api/dn-order/<int:dn_id>/lineage", methods=["GET"])
def lineage(dn_id):
    db = get_db()
    row = db.execute("SELECT owner_id FROM dn WHERE id=?", (dn_id,)).fetchone()
    if row is None:
        return jsonify(ok=False, error="出库单不存在"), 404
    S.assert_owner_allowed(S.current_scope(), row["owner_id"])
    # 作为源单被拆 / 被合，或作为目标单生成（ID 串为逗号分隔，首尾补逗号精确匹配）
    rows = db.execute(
        """SELECT * FROM dn_order_log
           WHERE (',' || source_dn_ids || ',') LIKE ? OR (',' || target_dn_ids || ',') LIKE ?
           ORDER BY id DESC""",
        (f"%,{dn_id},%", f"%,{dn_id},%"),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["detail_rows"] = __import__("json").loads(d.get("detail") or "[]")
        except (ValueError, TypeError):
            d["detail_rows"] = []
        out.append(d)
    return jsonify(ok=True, dn_id=dn_id, logs=out)
