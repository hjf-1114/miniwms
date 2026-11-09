"""M23 补充：专属库存风险放行日志查询（只读，带货主隔离）。

用户 2026-11-09 二次确认：风险操作允许继续，但必须留痕且可追溯。
本 Blueprint 只提供查询，**没有任何写端点**；日志由 ``ship()`` 在同一事务内写入。
"""
from flask import Blueprint, jsonify, request

import scope as S
from main import get_db

log_bp = Blueprint("reservation_log", __name__,
                   url_prefix="/api/reservation-logs")


@log_bp.route("", methods=["GET"])
def list_logs():
    args = request.args
    where, params = [], []

    dn_id = (args.get("dn_id") or "").strip()
    if dn_id:
        try:
            where.append("rl.dn_id = ?")
            params.append(int(dn_id))
        except ValueError:
            return jsonify(ok=False, error="dn_id 必须为整数"), 400
    keyword = (args.get("keyword") or "").strip()
    if keyword:
        where.append("(rl.dn_code LIKE ? OR rl.batch_no LIKE ?)")
        params.extend((f"%{keyword}%", f"%{keyword}%"))

    # 日志按货主归属隔离：与业务列表同一套授权集合，不另开口径
    cond, p = S.owner_where(S.current_scope(), table="rl")
    if cond:
        where.append(cond)
        params.extend(p)

    clause = " WHERE " + " AND ".join(where) if where else ""
    try:
        page = max(1, int(args.get("page") or 1))
        page_size = min(200, max(1, int(args.get("page_size") or 20)))
    except ValueError:
        return jsonify(ok=False, error="page/page_size 必须为整数"), 400

    db = get_db()
    total = db.execute(
        "SELECT COUNT(*) FROM customer_reservation_log rl" + clause, params
    ).fetchone()[0]
    rows = db.execute(
        """SELECT rl.*, p.sku, p.name AS product_name,
                  l.code AS location_code,
                  c1.code AS designated_customer_code,
                  c1.name AS designated_customer_name,
                  c2.code AS dn_customer_code, c2.name AS dn_customer_name
           FROM customer_reservation_log rl
           LEFT JOIN product p  ON p.id = rl.product_id
           LEFT JOIN location l ON l.id = rl.location_id
           LEFT JOIN customer c1 ON c1.id = rl.designated_customer_id
           LEFT JOIN customer c2 ON c2.id = rl.dn_customer_id"""
        + clause + " ORDER BY rl.id DESC LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()
    return jsonify(ok=True, logs=[dict(r) for r in rows], total=total,
                   page=page, page_size=page_size)
