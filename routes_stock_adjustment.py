"""库存调整单（Stock Adjustment）模块：/api/stock-adjustments。

M22 起，手工报损 / 报溢 / 数量修正、盘点差异、冲销都必须经调整单审核过账；
路由层不再有任何直接调用 ``adjust_stock()`` 改数量的旁路。
"""
from flask import Blueprint, jsonify, request

import constants as C
import scope as S
from adjustment_service import (AdjustmentError, approve_adjustment,
                                cancel_adjustment, create_adjustment,
                                get_adjustment, reverse_adjustment)
from inventory_service import StockError
from main import get_db, request_payload

adj_bp = Blueprint("stock_adjustment", __name__, url_prefix="/api/stock-adjustments")


def _current_user_name():
    from flask import g
    user = getattr(g, "current_user", None) or {}
    return user.get("username", "") if isinstance(user, dict) else ""


def _list_where(args):
    where, params = [], []
    status = (args.get("status") or "").strip()
    source_type = (args.get("source_type") or "").strip()
    keyword = (args.get("keyword") or "").strip()
    if status:
        where.append("sa.status = ?")
        params.append(status)
    if source_type:
        where.append("sa.source_type = ?")
        params.append(source_type)
    if keyword:
        where.append("sa.code LIKE ?")
        params.append(f"%{keyword}%")
    # 单据头无 owner_id：按明细行的货主归属做隔离（与授权集合一致）
    cond, p = S.owner_where(S.current_scope(), table="al")
    if cond:
        where.append(
            "EXISTS (SELECT 1 FROM stock_adjustment_line al "
            f"WHERE al.adjustment_id = sa.id AND {cond})"
        )
        params.extend(p)
    return where, params


@adj_bp.route("", methods=["GET"])
def list_adjustments():
    args = request.args
    try:
        page = max(1, int(args.get("page", 1)))
        page_size = min(100, max(1, int(args.get("page_size", 20))))
    except ValueError:
        return jsonify(ok=False, error="page/page_size 必须为整数"), 400
    where, params = _list_where(args)
    sql = ("WHERE " + " AND ".join(where)) if where else ""
    db = get_db()
    total = db.execute(
        f"SELECT COUNT(*) AS n FROM stock_adjustment sa {sql}", params
    ).fetchone()["n"]
    rows = db.execute(
        f"SELECT sa.*, (SELECT COUNT(*) FROM stock_adjustment_line al "
        f"WHERE al.adjustment_id = sa.id) AS line_count "
        f"FROM stock_adjustment sa {sql} "
        f"ORDER BY sa.id DESC LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()
    return jsonify(ok=True, total=total, page=page, page_size=page_size,
                   adjustments=[dict(r) for r in rows])


@adj_bp.route("", methods=["POST"])
def create_manual_adjustment():
    """手工制单：只生成 draft，绝不在此处过账。"""
    payload = request_payload()
    lines = payload.get("lines")
    reason_code = str(payload.get("reason_code") or "").strip()
    note = str(payload.get("note") or "")
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        adj_id, code, n = create_adjustment(
            db, source_type=C.ADJUSTMENT_MANUAL, reason_code=reason_code,
            lines=lines, creator=_current_user_name(), note=note,
        )
        db.commit()
    except (AdjustmentError, ValueError) as e:
        db.rollback()
        return jsonify(ok=False, error=str(e)), 400
    except S.ScopeError as e:
        db.rollback()
        return jsonify(ok=False, error=str(e)), e.status
    return jsonify(ok=True, id=adj_id, code=code, line_count=n,
                   status=C.ADJUSTMENT_DRAFT,
                   adjustment=get_adjustment(db, adj_id)), 201


@adj_bp.route("/<int:adj_id>", methods=["GET"])
def detail_adjustment(adj_id):
    db = get_db()
    try:
        adj = get_adjustment(db, adj_id)
    except AdjustmentError as e:
        return jsonify(ok=False, error=str(e)), 404
    return jsonify(ok=True, adjustment=adj)


@adj_bp.route("/<int:adj_id>/approve", methods=["POST"])
def approve(adj_id):
    """审核过账：事务里逐行过账，成功后来源盘点单自动结案。"""
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        adj = approve_adjustment(db, adj_id, _current_user_name())
        db.commit()
    except (AdjustmentError, StockError, ValueError) as e:
        db.rollback()
        return jsonify(ok=False, error=str(e)), 400
    except S.ScopeError as e:
        db.rollback()
        return jsonify(ok=False, error=str(e)), e.status
    return jsonify(ok=True, adjustment=adj, status=C.ADJUSTMENT_APPROVED)


@adj_bp.route("/<int:adj_id>/cancel", methods=["POST"])
def cancel(adj_id):
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        adj = cancel_adjustment(db, adj_id)
        db.commit()
    except AdjustmentError as e:
        db.rollback()
        return jsonify(ok=False, error=str(e)), 400
    return jsonify(ok=True, adjustment=adj, status=C.ADJUSTMENT_CANCELLED)


@adj_bp.route("/<int:adj_id>/reverse", methods=["POST"])
def reverse(adj_id):
    """冲销：生成反向 draft 单，仍需人工审核，原单保持 approved。"""
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        new_id, code, n = reverse_adjustment(db, adj_id, _current_user_name())
        db.commit()
    except (AdjustmentError, ValueError) as e:
        db.rollback()
        return jsonify(ok=False, error=str(e)), 400
    except S.ScopeError as e:
        db.rollback()
        return jsonify(ok=False, error=str(e)), e.status
    return jsonify(ok=True, id=new_id, code=code, line_count=n,
                   status=C.ADJUSTMENT_DRAFT,
                   adjustment=get_adjustment(db, new_id)), 201
