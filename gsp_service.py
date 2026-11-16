"""M39 GSP 合规报表服务层（纯只读）。

四类报表 + 概览 + 字典，全部复用现有表，不新建表、不改库存、不写任何流水：
  1. 批次追溯 trace —— 从 batch_no / batch_key 反查收货验收(batch_receipt)、
     在库批次(stock_batch)、库存流水(stock_movement)三段链路。
  2. 效期预警 expiry —— 按 stock_batch.expiry_date 与今天比较分四级
     (expired / soon / normal / none)，近效期阈值 days 可覆盖。
  3. 养护留痕 care —— care_log（异常 / 处置 / 正常全链路）。
  4. 验收留痕 receipt —— batch_receipt（含 M23 缺外部批号告警 warning）。
  5. 复核差异 review —— dn_review_diff_log（M36 差异只留痕不改库存）。

**纪律**：所有 SQL 走 scope.scoped_where() 注入货主条件；上下文缺失时由
scope 的 403 错误处理器统一拒绝，绝不返回 ALL 兜底。本模块只依赖标准库、
flask.g 与 scope，不反向 import main，避免循环依赖。
"""
import datetime

import constants as C
import scope as S


class GspError(Exception):
    """报表参数错误。"""

    def __init__(self, message, code="GSP_BAD_PARAM"):
        super().__init__(message)
        self.code = code


def _int(value, name, *, default=None, minimum=None, maximum=None):
    if value is None or str(value).strip() == "":
        if default is not None:
            return default
        raise GspError(f"{name} 必填")
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        raise GspError(f"{name} 必须是整数")
    if minimum is not None and n < minimum:
        raise GspError(f"{name} 不能小于 {minimum}")
    if maximum is not None and n > maximum:
        raise GspError(f"{name} 不能大于 {maximum}")
    return n


def paging(args):
    """统一分页参数解析。"""
    page = _int(args.get("page"), "page", default=1, minimum=1)
    page_size = _int(args.get("page_size"), "page_size", default=20, minimum=1, maximum=200)
    return page, page_size


def dictionary():
    """报表字典：报表类型 / 效期分级 / 追溯环节 / 复用原因码 / 近效期默认天数。"""
    return {
        "reports": [{"code": c, "label": C.GSP_REPORT_LABELS[c]} for c in C.GSP_REPORTS],
        "expiry_levels": [{"code": c, "label": C.GSP_EXPIRY_LABELS[c]}
                          for c in C.GSP_EXPIRY_LEVELS],
        "trace_stages": [{"code": c, "label": C.GSP_TRACE_STAGE_LABELS[c]}
                         for c in C.GSP_TRACE_STAGES],
        "care_projects": [{"code": c, "label": C.CARE_PROJECT_LABELS[c]}
                          for c in C.CARE_PROJECTS],
        "care_reasons": [{"code": c, "label": C.CARE_REASON_LABELS[c]}
                         for c in C.CARE_REASONS],
        "review_reasons": [{"code": c, "label": C.REVIEW_DIFF_LABELS[c]}
                           for c in C.REVIEW_DIFF_REASONS],
        "soon_days_default": C.GSP_EXPIRY_SOON_DAYS_DEFAULT,
        "soon_days_max": C.GSP_EXPIRY_SOON_DAYS_MAX,
    }


# ─────────────────────────────── 效期分级 ───────────────────────────────

def _expiry_level(expiry_date, today, soon_days):
    """把 expiry_date 分成四级。空串 / NULL 视为无有效期。"""
    raw = (expiry_date or "").strip() if isinstance(expiry_date, str) else ""
    if not raw:
        return C.GSP_EXPIRY_NONE, None
    try:
        exp = datetime.date.fromisoformat(raw[:10])
    except (TypeError, ValueError):
        return C.GSP_EXPIRY_NONE, None
    days_left = (exp - today).days
    if days_left < 0:
        return C.GSP_EXPIRY_EXPIRED, days_left
    if days_left <= soon_days:
        return C.GSP_EXPIRY_SOON, days_left
    return C.GSP_EXPIRY_NORMAL, days_left


def expiry_report(db, args):
    """效期预警：按 stock_batch 逐行分级，支持 level / days / product_id 过滤。

    返回分级统计（四级数量）+ 分级后的批次余额列表（分页）。仅统计 qty > 0
    的在库批次；零行保留（qty=0）不进入效期预警。
    """
    page, page_size = paging(args)
    soon_days = _int(args.get("days"), "days",
                     default=C.GSP_EXPIRY_SOON_DAYS_DEFAULT,
                     minimum=0, maximum=C.GSP_EXPIRY_SOON_DAYS_MAX)
    level_filter = (args.get("level") or "").strip()
    if level_filter and level_filter not in C.GSP_EXPIRY_LEVELS:
        raise GspError(f"level 无效：{level_filter}")

    where = ["sb.qty > 0"]
    params = []
    product_id = args.get("product_id")
    if str(product_id or "").strip():
        where.append("sb.product_id = ?")
        params.append(_int(product_id, "product_id", minimum=1))
    S.scoped_where(where, params, table="sb")
    where_sql = " AND ".join(where)

    rows = db.execute(
        f"""SELECT sb.id, sb.owner_id, sb.product_id, sb.location_id,
                   sb.batch_no, sb.internal_batch_no, sb.expiry_date,
                   sb.production_date, sb.qty, sb.allocated_qty,
                   o.code AS owner_code, p.sku, p.name AS product_name,
                   l.code AS location_code, l.name AS location_name
            FROM stock_batch sb
            JOIN owner o ON o.id = sb.owner_id
            JOIN product p ON p.id = sb.product_id
            JOIN location l ON l.id = sb.location_id
            WHERE {where_sql}
            ORDER BY sb.expiry_date ASC, sb.id ASC""",
        params,
    ).fetchall()

    today = datetime.date.today()
    items = []
    stats = {lv: {"count": 0, "qty": 0.0} for lv in C.GSP_EXPIRY_LEVELS}
    for r in rows:
        d = dict(r)
        level, days_left = _expiry_level(d["expiry_date"], today, soon_days)
        d["level"] = level
        d["level_label"] = C.GSP_EXPIRY_LABELS[level]
        d["days_left"] = days_left
        stats[level]["count"] += 1
        stats[level]["qty"] += d["qty"] or 0
        if not level_filter or level == level_filter:
            items.append(d)

    total = len(items)
    return {
        "ok": True, "total": total, "page": page, "page_size": page_size,
        "days": soon_days, "today": today.isoformat(),
        "stats": [{"level": lv, "label": C.GSP_EXPIRY_LABELS[lv], **stats[lv]}
                  for lv in C.GSP_EXPIRY_LEVELS],
        "batches": items[(page - 1) * page_size: page * page_size],
    }


# ─────────────────────────────── 批次追溯 ───────────────────────────────

def trace_report(db, args):
    """批次追溯：按 batch_no / batch_key / product_id 反查三段链路。

    先定位符合条件的批次键（batch_no → batch_key），再分别取收货验收、
    在库批次与库存流水。所有子查询独立注入货主隔离。
    """
    page, page_size = paging(args)
    batch_no = (args.get("batch_no") or "").strip()
    batch_key = (args.get("batch_key") or "").strip()
    product_id = args.get("product_id")
    if not batch_no and not batch_key and not str(product_id or "").strip():
        raise GspError("batch_no / batch_key / product_id 至少填一项")
    pid = None
    if str(product_id or "").strip():
        pid = _int(product_id, "product_id", minimum=1)

    # ① 定位批次档案（batch 表：owner_id + product_id + batch_key）
    w, p = [], []
    if batch_key:
        w.append("b.batch_key = ?")
        p.append(batch_key)
    if pid is not None:
        w.append("b.product_id = ?")
        p.append(pid)
    if batch_no:
        w.append("EXISTS (SELECT 1 FROM batch_receipt br "
                 "WHERE br.batch_id = b.id AND br.batch_no = ?)")
        p.append(batch_no)
    S.scoped_where(w, p, table="b")
    wsql = (" WHERE " + " AND ".join(w)) if w else ""

    total = db.execute(f"SELECT COUNT(*) AS n FROM batch b {wsql}", p).fetchone()["n"]
    batches = db.execute(
        f"""SELECT b.id, b.code, b.batch_key, b.owner_id, b.product_id,
                   b.key_info, b.info, b.receipt_count, b.created_at,
                   o.code AS owner_code, p.sku, p.name AS product_name
            FROM batch b
            JOIN owner o ON o.id = b.owner_id
            JOIN product p ON p.id = b.product_id
            {wsql} ORDER BY b.id DESC LIMIT ? OFFSET ?""",
        p + [page_size, (page - 1) * page_size],
    ).fetchall()

    out = []
    for b in batches:
        d = dict(b)
        key = d["batch_key"]
        # 若按 batch_no 精确过滤，则明细也只取该批号，避免把同 batch_key 的其他批号混入
        br_w = ["br.batch_id = ?"]
        br_p = [d["id"]]
        if batch_no:
            br_w.append("br.batch_no = ?")
            br_p.append(batch_no)
        S.scoped_where(br_w, br_p, table="br")
        receipts = db.execute(
            f"""SELECT br.id, br.batch_no, br.external_batch_no, br.internal_batch_no,
                       br.doc_type, br.asn_id, br.qty, br.goods_property,
                       br.warning, br.created_at, a.code AS asn_code
                FROM batch_receipt br
                LEFT JOIN asn a ON a.id = br.asn_id
                WHERE {' AND '.join(br_w)} ORDER BY br.id""",
            br_p,
        ).fetchall()

        sb_w, sb_p = ["sb.batch_key = ?"], [key]
        S.scoped_where(sb_w, sb_p, table="sb")
        stocks = db.execute(
            f"""SELECT sb.id, sb.batch_no, sb.location_id, sb.qty, sb.allocated_qty,
                       sb.expiry_date, sb.production_date, sb.goods_property,
                       sb.stock_status, sb.designated_customer_id,
                       l.code AS location_code, l.name AS location_name
                FROM stock_batch sb
                JOIN location l ON l.id = sb.location_id
                WHERE {' AND '.join(sb_w)} ORDER BY sb.id""",
            sb_p,
        ).fetchall()

        mv_w, mv_p = ["m.batch_key = ?"], [key]
        S.scoped_where(mv_w, mv_p, table="m")
        movements = db.execute(
            f"""SELECT m.id, m.batch_no, m.ref_type, m.ref_id, m.qty_delta,
                       m.note, m.operator, m.created_at,
                       l.code AS location_code
                FROM stock_movement m
                LEFT JOIN location l ON l.id = m.location_id
                WHERE {' AND '.join(mv_w)} ORDER BY m.id""",
            mv_p,
        ).fetchall()

        d["receipts"] = [dict(r) for r in receipts]
        d["stocks"] = [dict(r) for r in stocks]
        d["movements"] = [dict(r) for r in movements]
        d["stage_counts"] = {
            C.GSP_TRACE_RECEIPT: len(receipts),
            C.GSP_TRACE_STOCK: len(stocks),
            C.GSP_TRACE_MOVEMENT: len(movements),
        }
        out.append(d)

    return {"ok": True, "total": total, "page": page, "page_size": page_size,
            "batches": out}


# ─────────────────────────── 养护 / 验收 / 复核留痕 ───────────────────────────

def _log_report(db, args, table, alias, extra_filters, order_by, result_key,
                date_col="created_at"):
    """留痕类报表通用查询：货主隔离 + 可选过滤 + 分页 + 统计。"""
    page, page_size = paging(args)
    where, params = [], []
    for col, arg in extra_filters:
        val = (args.get(arg) or "").strip()
        if val:
            where.append(f"{alias}.{col} = ?")
            params.append(val)
    date_from = (args.get("date_from") or "").strip()
    date_to = (args.get("date_to") or "").strip()
    if date_from:
        where.append(f"date({alias}.{date_col}) >= ?")
        params.append(date_from[:10])
    if date_to:
        where.append(f"date({alias}.{date_col}) <= ?")
        params.append(date_to[:10])
    S.scoped_where(where, params, table=alias)
    wsql = (" WHERE " + " AND ".join(where)) if where else ""

    total = db.execute(f"SELECT COUNT(*) AS n FROM {table} {alias} {wsql}", params).fetchone()["n"]
    rows = db.execute(
        f"SELECT {alias}.* FROM {table} {alias} {wsql} ORDER BY {order_by} LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()
    return {"ok": True, "total": total, "page": page, "page_size": page_size,
            result_key: [dict(r) for r in rows]}


def care_report(db, args):
    """养护留痕（M37 care_log）：项目 / 动作 / 结果 / 原因码 / 日期过滤。"""
    result = _log_report(
        db, args, "care_log", "cl",
        [("project", "project"), ("action", "action"), ("result", "result"),
         ("reason_code", "reason_code")],
        "cl.id DESC", "logs",
    )
    # 统计：异常数 / 已处置数（基于全量表按相同过滤，避免分页影响统计）
    where, params = [], []
    for col, arg in [("project", "project"), ("action", "action"),
                     ("result", "result"), ("reason_code", "reason_code")]:
        val = (args.get(arg) or "").strip()
        if val:
            where.append(f"cl.{col} = ?")
            params.append(val)
    S.scoped_where(where, params, table="cl")
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    stat = db.execute(
        f"""SELECT COUNT(*) AS total,
                   SUM(CASE WHEN action = 'abnormal' THEN 1 ELSE 0 END) AS abnormal,
                   SUM(CASE WHEN action = 'handled' THEN 1 ELSE 0 END) AS handled
            FROM care_log cl {wsql}""", params).fetchone()
    result["stats"] = {
        "total": stat["total"] or 0,
        "abnormal": stat["abnormal"] or 0,
        "handled": stat["handled"] or 0,
    }
    return result


def receipt_report(db, args):
    """验收留痕（batch_receipt）：含 M23 缺外部批号告警 warning。"""
    result = _log_report(
        db, args, "batch_receipt", "br",
        [("batch_no", "batch_no"), ("doc_type", "doc_type"),
         ("goods_property", "goods_property")],
        "br.id DESC", "logs",
    )
    where, params = [], []
    S.scoped_where(where, params, table="br")
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    stat = db.execute(
        f"""SELECT COUNT(*) AS total,
                   SUM(CASE WHEN warning IS NOT NULL AND warning <> '' THEN 1 ELSE 0 END) AS warned
            FROM batch_receipt br {wsql}""", params).fetchone()
    result["stats"] = {"total": stat["total"] or 0, "warned": stat["warned"] or 0}
    return result


def review_report(db, args):
    """复核差异留痕（M36 dn_review_diff_log）：差异只留痕、不改库存。"""
    result = _log_report(
        db, args, "dn_review_diff_log", "dl",
        [("reason_code", "reason_code"), ("action", "action"),
         ("dn_code", "dn_code"), ("batch_no", "batch_no")],
        "dl.id DESC", "logs",
    )
    where, params = [], []
    S.scoped_where(where, params, table="dl")
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    stat = db.execute(
        f"""SELECT COUNT(*) AS total,
                   SUM(CASE WHEN diff_qty < 0 THEN 1 ELSE 0 END) AS short,
                   SUM(CASE WHEN diff_qty > 0 THEN 1 ELSE 0 END) AS over
            FROM dn_review_diff_log dl {wsql}""", params).fetchone()
    result["stats"] = {"total": stat["total"] or 0, "short": stat["short"] or 0,
                       "over": stat["over"] or 0}
    return result


# ─────────────────────────────── 概览 ───────────────────────────────

def overview(db):
    """合规概览：各报表数据源总量 + 效期预警分级 + 异常计数（全部按货主隔离）。"""
    def count(table, alias, extra=""):
        w, p = [], []
        S.scoped_where(w, p, table=alias)
        wsql = (" WHERE " + " AND ".join(w)) if w else ""
        if extra:
            wsql = (wsql + " AND " + extra) if wsql else " WHERE " + extra
        return db.execute(f"SELECT COUNT(*) AS n FROM {table} {alias}{wsql}", p).fetchone()["n"]

    # 效期分级（复用与 expiry_report 相同口径，分页拿全量仅用于统计）
    exp = expiry_report(db, {"page": 1, "page_size": 1})
    return {
        "ok": True,
        "cards": [
            {"code": C.GSP_REPORT_TRACE, "label": C.GSP_REPORT_LABELS[C.GSP_REPORT_TRACE],
             "count": count("batch", "b")},
            {"code": C.GSP_REPORT_RECEIPT, "label": C.GSP_REPORT_LABELS[C.GSP_REPORT_RECEIPT],
             "count": count("batch_receipt", "br")},
            {"code": C.GSP_REPORT_CARE, "label": C.GSP_REPORT_LABELS[C.GSP_REPORT_CARE],
             "count": count("care_log", "cl")},
            {"code": C.GSP_REPORT_REVIEW, "label": C.GSP_REPORT_LABELS[C.GSP_REPORT_REVIEW],
             "count": count("dn_review_diff_log", "dl")},
        ],
        "expiry_stats": exp["stats"],
        "soon_days": exp["days"],
        "care_abnormal": count("care_log", "cl", "action = 'abnormal'"),
        "receipt_warned": count("batch_receipt", "br",
                                "warning IS NOT NULL AND warning <> ''"),
    }
