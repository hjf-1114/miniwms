"""M28 经营报表：按货主的收发汇总 / 周转率 / 库位利用率（纯只读）。

设计要点
--------
1. **纯只读**：只查 ``stock_movement`` / ``stock`` / ``product`` / ``location``，
   不新建业务表、不写库存流水、不改任何单据状态。
2. **收发口径排除库位间转移**（Q57）：上架（``asn_putaway_in/out``）、移库
   （``move_in/out``）、库存转移（``it_transfer``）只是库位之间搬运，
   若计入会让收货量翻倍，故按 ``OP_REPORT_TRANSFER_REF_TYPES`` 排除；
   入库量＝``qty_delta > 0``、出库量＝``qty_delta < 0``，均取绝对值。
3. **成本口径用采购价**（Q57）：库存金额与出库成本都按
   ``product.purchase_price``（可用参数切到销售价）；未填价格的货品按 0 计，
   响应里返回 ``no_price_items`` 标注，避免报表看起来「有数其实缺价」。
4. **周转率口径**（Q58）：周转次数＝期间出库成本 ÷ 平均库存金额；
   平均库存金额＝（期初 + 期末）/ 2，期初＝期末 − 净变化金额；
   周转天数＝期间天数 ÷ 周转次数；平均库存为 0 时不算周转次数（返回 None）。
5. **库位利用率按库位数口径**（Q59）：占用库位数（``stock.qty <> 0`` 的库位）
   ÷ 启用库位总数；可按仓库 / 储区 / 货位组分组；不做体积利用率。
6. **时间区间**：支持 ``date_from`` / ``date_to``（ISO 日期，闭区间），
   缺省为最近 30 天；也支持 ``period=day|week|month`` 快捷覆盖（Q60）。
7. **货主隔离**：所有涉及 ``stock`` / ``stock_movement`` 的查询都过
   ``S.scoped_where()``，上下文缺失一律 403。
"""
import datetime

import scope as S

import constants as C

EPS = 1e-9


class OperationReportError(Exception):
    """业务校验失败 → 路由层转 400。"""

    def __init__(self, message, code="OP_REPORT_BAD_PARAM"):
        super().__init__(message)
        self.message = message
        self.code = code


def dictionary():
    """字典：期间快捷方式 / 成本字段 / 利用率维度 / 转移类流水口径。"""
    return {
        "periods": list(C.OP_REPORT_PERIODS),
        "default_days": C.OP_REPORT_DEFAULT_DAYS,
        "cost_fields": [{"code": k, "label": v}
                        for k, v in C.OP_REPORT_COST_FIELD_LABELS.items()],
        "util_dimensions": list(C.OP_REPORT_UTIL_DIMENSIONS),
        "transfer_ref_types": list(C.OP_REPORT_TRANSFER_REF_TYPES),
        "read_only": True,
    }


# ── 参数处理 ────────────────────────────────────────────────────────────────

def _parse_date(value, name):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.date.fromisoformat(text[:10])
    except ValueError:
        raise OperationReportError(f"{name} 必须是 YYYY-MM-DD")


def _period_range(period):
    """按 period 快捷方式算出 [from, to]；返回 None 表示沿用参数。"""
    today = datetime.date.today()
    if period == "day":
        return today, today
    if period == "week":
        monday = today - datetime.timedelta(days=today.weekday())
        return monday, today
    if period == "month":
        first = today.replace(day=1)
        return first, today
    return None


def _range(args):
    """解析统计区间 → (date_from, date_to, from_str, to_str, days)。"""
    period = str(args.get("period") or "").strip()
    if period and period not in C.OP_REPORT_PERIODS:
        raise OperationReportError(f"period 只能是 {'/'.join(C.OP_REPORT_PERIODS)}")
    d_from = _parse_date(args.get("date_from"), "date_from")
    d_to = _parse_date(args.get("date_to"), "date_to")
    if period:
        p_from, p_to = _period_range(period)
        d_from = d_from or p_from
        d_to = d_to or p_to
    today = datetime.date.today()
    d_to = d_to or today
    d_from = d_from or (d_to - datetime.timedelta(days=C.OP_REPORT_DEFAULT_DAYS))
    if d_from > d_to:
        raise OperationReportError("date_from 不能晚于 date_to")
    days = (d_to - d_from).days + 1
    return d_from, d_to, d_from.isoformat(), d_to.isoformat(), days


def _cost_field(args):
    field = str(args.get("cost_field") or C.OP_REPORT_COST_FIELD).strip()
    if field not in C.OP_REPORT_COST_FIELD_LABELS:
        raise OperationReportError(
            f"cost_field 只能是 {'/'.join(C.OP_REPORT_COST_FIELD_LABELS)}")
    return field


def _owner_filter(args):
    owner_id = str(args.get("owner_id") or "").strip()
    if not owner_id or owner_id == "all":
        return None
    return owner_id


# ── 收发汇总 ────────────────────────────────────────────────────────────────

def _movement_sql(args, from_str, to_str):
    """构造收发流水的 where + params（已排除库位间转移类）。"""
    where = [
        "m.created_at >= ? AND m.created_at <= ?",
        "m.ref_type NOT IN (%s)" % ",".join("?" for _ in C.OP_REPORT_TRANSFER_REF_TYPES),
    ]
    params = [f"{from_str} 00:00:00", f"{to_str} 23:59:59"]
    params += list(C.OP_REPORT_TRANSFER_REF_TYPES)
    owner_id = _owner_filter(args)
    if owner_id:
        if owner_id == "none":
            where.append("m.owner_id IS NULL")
        else:
            where.append("m.owner_id = ?")
            params.append(owner_id)
    S.scoped_where(where, params, table="m")
    return where, params


def io_summary(db, args):
    """按货主的收发汇总（数量口径，不含金额）。"""
    d_from, d_to, from_str, to_str, days = _range(args)
    where, params = _movement_sql(args, from_str, to_str)
    where_sql = "WHERE " + " AND ".join(where)

    rows = db.execute(
        f"""SELECT m.owner_id AS owner_id,
                   COALESCE(SUM(CASE WHEN m.qty_delta > 0 THEN m.qty_delta ELSE 0 END), 0)
                       AS in_qty,
                   COALESCE(SUM(CASE WHEN m.qty_delta < 0 THEN -m.qty_delta ELSE 0 END), 0)
                       AS out_qty,
                   COUNT(*) AS movements
            FROM stock_movement m {where_sql}
            GROUP BY m.owner_id""",
        params,
    ).fetchall()

    owners = {r["id"]: r for r in db.execute("SELECT id, code, name FROM owner")}
    items = []
    tot_in = tot_out = 0.0
    tot_mv = 0
    for r in rows:
        in_qty = round(r["in_qty"] or 0, 4)
        out_qty = round(r["out_qty"] or 0, 4)
        tot_in += in_qty
        tot_out += out_qty
        tot_mv += r["movements"] or 0
        o = owners.get(r["owner_id"])
        items.append({
            "owner_id": r["owner_id"],
            "owner_code": o["code"] if o else "",
            "owner_name": o["name"] if o else ("自营" if r["owner_id"] is None else ""),
            "in_qty": in_qty,
            "out_qty": out_qty,
            "net_qty": round(in_qty - out_qty, 4),
            "movements": r["movements"] or 0,
        })
    items.sort(key=lambda x: (-x["in_qty"], -x["out_qty"]))

    # 按货品看收发 Top（只读，限量）
    top_rows = db.execute(
        f"""SELECT m.product_id AS product_id,
                   COALESCE(SUM(CASE WHEN m.qty_delta > 0 THEN m.qty_delta ELSE 0 END), 0)
                       AS in_qty,
                   COALESCE(SUM(CASE WHEN m.qty_delta < 0 THEN -m.qty_delta ELSE 0 END), 0)
                       AS out_qty
            FROM stock_movement m {where_sql}
            GROUP BY m.product_id
            ORDER BY (in_qty + out_qty) DESC
            LIMIT 20""",
        params,
    ).fetchall()
    prods = {r["id"]: r for r in db.execute("SELECT id, sku, name FROM product")}
    top = []
    for r in top_rows:
        p = prods.get(r["product_id"])
        top.append({
            "product_id": r["product_id"],
            "sku": p["sku"] if p else "",
            "name": p["name"] if p else "",
            "in_qty": round(r["in_qty"] or 0, 4),
            "out_qty": round(r["out_qty"] or 0, 4),
        })

    return {
        "date_from": from_str,
        "date_to": to_str,
        "days": days,
        "items": items,
        "total": {
            "in_qty": round(tot_in, 4),
            "out_qty": round(tot_out, 4),
            "net_qty": round(tot_in - tot_out, 4),
            "movements": tot_mv,
        },
        "top_products": top,
        "excluded_ref_types": list(C.OP_REPORT_TRANSFER_REF_TYPES),
    }


# ── 周转率 ──────────────────────────────────────────────────────────────────

def turnover(db, args):
    """按货主的库存周转（成本口径）。"""
    d_from, d_to, from_str, to_str, days = _range(args)
    cost_field = _cost_field(args)

    # 期末库存金额（当前 stock）
    stock_where, stock_params = [], []
    owner_id = _owner_filter(args)
    if owner_id:
        if owner_id == "none":
            stock_where.append("s.owner_id IS NULL")
        else:
            stock_where.append("s.owner_id = ?")
            stock_params.append(owner_id)
    S.scoped_where(stock_where, stock_params, table="s")
    stock_sql = ("WHERE " + " AND ".join(stock_where)) if stock_where else ""

    end_rows = db.execute(
        f"""SELECT s.owner_id AS owner_id,
                   COALESCE(SUM(s.qty * COALESCE(p.{cost_field}, 0)), 0) AS amount,
                   COALESCE(SUM(s.qty), 0) AS qty
            FROM stock s LEFT JOIN product p ON p.id = s.product_id
            {stock_sql}
            GROUP BY s.owner_id""",
        stock_params,
    ).fetchall()

    # 期间出入库金额（同一套转移排除口径）
    mv_where, mv_params = _movement_sql(args, from_str, to_str)
    mv_sql = "WHERE " + " AND ".join(mv_where)
    flow_rows = db.execute(
        f"""SELECT m.owner_id AS owner_id,
                   COALESCE(SUM(CASE WHEN m.qty_delta > 0
                                     THEN m.qty_delta * COALESCE(p.{cost_field}, 0)
                                     ELSE 0 END), 0) AS in_amount,
                   COALESCE(SUM(CASE WHEN m.qty_delta < 0
                                     THEN -m.qty_delta * COALESCE(p.{cost_field}, 0)
                                     ELSE 0 END), 0) AS out_amount
            FROM stock_movement m LEFT JOIN product p ON p.id = m.product_id
            {mv_sql}
            GROUP BY m.owner_id""",
        mv_params,
    ).fetchall()

    owners = {r["id"]: r for r in db.execute("SELECT id, code, name FROM owner")}
    end_map = {r["owner_id"]: r for r in end_rows}
    flow_map = {r["owner_id"]: r for r in flow_rows}
    keys = set(end_map) | set(flow_map)

    # 未填价格的货品数量（标注口径缺失）
    no_price = db.execute(
        f"""SELECT COUNT(*) AS n FROM stock s
            LEFT JOIN product p ON p.id = s.product_id
            {stock_sql}
            {"AND" if stock_sql else "WHERE"} (p.{cost_field} IS NULL OR p.{cost_field} = 0)""",
        stock_params,
    ).fetchone()["n"]

    items = []
    tot_turn = tot_avg = 0.0
    for key in sorted(keys, key=lambda k: (k is None, k)):
        end = end_map.get(key)
        flow = flow_map.get(key)
        end_amount = round((end["amount"] if end else 0) or 0, 4)
        end_qty = round((end["qty"] if end else 0) or 0, 4)
        in_amount = round((flow["in_amount"] if flow else 0) or 0, 4)
        out_amount = round((flow["out_amount"] if flow else 0) or 0, 4)
        net_amount = round(in_amount - out_amount, 4)
        begin_amount = round(end_amount - net_amount, 4)
        avg_amount = round((begin_amount + end_amount) / 2, 4)
        turn_count = None
        turn_days = None
        if avg_amount > EPS:
            turn_count = round(out_amount / avg_amount, 4)
            if turn_count > EPS:
                turn_days = round(days / turn_count, 2)
        o = owners.get(key)
        items.append({
            "owner_id": key,
            "owner_code": o["code"] if o else "",
            "owner_name": o["name"] if o else ("自营" if key is None else ""),
            "begin_amount": begin_amount,
            "end_amount": end_amount,
            "avg_amount": avg_amount,
            "in_amount": in_amount,
            "out_amount": out_amount,
            "end_qty": end_qty,
            "turn_count": turn_count,
            "turn_days": turn_days,
        })
        if turn_count is not None:
            tot_turn += turn_count
            tot_avg += avg_amount

    total_turn = round(tot_turn, 4) if tot_avg > EPS else None
    return {
        "date_from": from_str,
        "date_to": to_str,
        "days": days,
        "cost_field": cost_field,
        "cost_field_label": C.OP_REPORT_COST_FIELD_LABELS[cost_field],
        "items": items,
        "total_turn_count": total_turn,
        "total_turn_days": (round(days / total_turn, 2)
                            if total_turn and total_turn > EPS else None),
        "no_price_items": no_price,
        "excluded_ref_types": list(C.OP_REPORT_TRANSFER_REF_TYPES),
    }


# ── 库位利用率 ──────────────────────────────────────────────────────────────

def _util_groups(db, args, dim):
    """按维度统计库位占用。"""
    if dim == "warehouse":
        key_sql = "COALESCE(w.code, '')"
        name_sql = "COALESCE(w.name, '')"
        join = "LEFT JOIN warehouse w ON w.id = l.warehouse_id"
        key_name = "warehouse"
    elif dim == "zone":
        key_sql = "COALESCE(z.code, '')"
        name_sql = "COALESCE(z.name, '')"
        join = "LEFT JOIN zone z ON z.id = l.zone_id"
        key_name = "zone"
    elif dim == "location_group":
        key_sql = "COALESCE(l.location_group, '')"
        name_sql = "COALESCE(l.location_group, '')"
        join = ""
        key_name = "location_group"
    else:
        key_sql = "''"
        name_sql = "''"
        join = ""
        key_name = "all"

    rows = db.execute(
        f"""SELECT {key_sql} AS k, {name_sql} AS n,
                   COUNT(*) AS total_locations,
                   SUM(CASE WHEN EXISTS (SELECT 1 FROM stock s
                                         WHERE s.location_id = l.id AND s.qty <> 0)
                            THEN 1 ELSE 0 END) AS used_locations
            FROM location l {join}
            WHERE l.status = 'active'
            GROUP BY k, n
            ORDER BY total_locations DESC, k""",
    ).fetchall()

    out = []
    t_total = t_used = 0
    for r in rows:
        total = r["total_locations"] or 0
        used = r["used_locations"] or 0
        t_total += total
        t_used += used
        out.append({
            "key": r["k"],
            "name": r["n"],
            "total_locations": total,
            "used_locations": used,
            "free_locations": total - used,
            "utilization": round(used / total, 4) if total else 0,
        })
    return key_name, out, t_total, t_used


def location_utilization(db, args):
    """库位利用率（按库位数口径）。"""
    dim = str(args.get("dimension") or "all").strip()
    if dim not in C.OP_REPORT_UTIL_DIMENSIONS:
        raise OperationReportError(
            f"dimension 只能是 {'/'.join(C.OP_REPORT_UTIL_DIMENSIONS)}")
    key_name, groups, t_total, t_used = _util_groups(db, args, dim)

    # 空库位明细（限量 50），方便定位闲置
    empty = db.execute(
        """SELECT l.id, l.code, l.name, l.zone, l.location_group
           FROM location l
           WHERE l.status = 'active'
             AND NOT EXISTS (SELECT 1 FROM stock s
                             WHERE s.location_id = l.id AND s.qty <> 0)
           ORDER BY l.code LIMIT 50""",
    ).fetchall()

    return {
        "dimension": key_name,
        "groups": groups,
        "total_locations": t_total,
        "used_locations": t_used,
        "free_locations": t_total - t_used,
        "utilization": round(t_used / t_total, 4) if t_total else 0,
        "empty_locations": [dict(r) for r in empty],
    }


# ── 总览 ────────────────────────────────────────────────────────────────────

def overview(db, args):
    """经营总览卡片（一次拿齐三张报表的关键数字）。"""
    io = io_summary(db, args)
    tv = turnover(db, args)
    lu = location_utilization(db, args)
    stock_where, stock_params = [], []
    owner_id = _owner_filter(args)
    if owner_id:
        if owner_id == "none":
            stock_where.append("s.owner_id IS NULL")
        else:
            stock_where.append("s.owner_id = ?")
            stock_params.append(owner_id)
    S.scoped_where(stock_where, stock_params, table="s")
    stock_sql = ("WHERE " + " AND ".join(stock_where)) if stock_where else ""
    totals = db.execute(
        f"""SELECT COUNT(*) AS n, COALESCE(SUM(s.qty), 0) AS qty
            FROM stock s {stock_sql}""", stock_params,
    ).fetchone()
    skus = db.execute(
        f"""SELECT COUNT(DISTINCT s.product_id) AS n FROM stock s {stock_sql}""",
        stock_params,
    ).fetchone()["n"]

    return {
        "date_from": io["date_from"],
        "date_to": io["date_to"],
        "days": io["days"],
        "in_qty": io["total"]["in_qty"],
        "out_qty": io["total"]["out_qty"],
        "net_qty": io["total"]["net_qty"],
        "movements": io["total"]["movements"],
        "turn_count": tv["total_turn_count"],
        "turn_days": tv["total_turn_days"],
        "cost_field": tv["cost_field"],
        "end_amount": round(sum(i["end_amount"] for i in tv["items"]), 4),
        "no_price_items": tv["no_price_items"],
        "stock_lines": totals["n"],
        "stock_qty": round(totals["qty"] or 0, 4),
        "sku_count": skus,
        "location_total": lu["total_locations"],
        "location_used": lu["used_locations"],
        "location_utilization": lu["utilization"],
    }
