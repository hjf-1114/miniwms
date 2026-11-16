"""M40 工作量统计服务层（纯只读聚合：按人 / 按节点 / 按时间段）。

现有系统没有独立工时表，用户也没拍板「工时」来源（Q35），因此本模块把
**已有操作留痕**统一抽成事件流，再按三个视角聚合：

  - 按人   operator（匿名 / 老数据为空时归并到「未记录」，Q37）
  - 按节点 node（收货 / 上架 / 拣货 / 发货 / 复核 / 养护 / 拆合 …，Q34）
  - 按时间段 period（day / week / month，按 created_at 本地时间，Q36）

**纪律**：
  - 全 SELECT，不写任何业务表、不改库存、不写流水（延续 M39 只读边界）；
  - 每条事件都带 owner_id（无字段的控制面日志从父单据推导），统一经
    ``scope.scoped_where(table="e")`` 注入货主条件，上下文缺失一律 403；
  - 不反向 import main，只依赖 constants / scope / flask.g，避免循环依赖。
"""
import datetime

import constants as C
import scope as S


class WorkloadError(Exception):
    """统计参数错误。"""

    def __init__(self, message, code="WORKLOAD_BAD_PARAM"):
        super().__init__(message)
        self.code = code


def _int(value, name, *, default=None, minimum=None, maximum=None):
    if value is None or str(value).strip() == "":
        if default is not None:
            return default
        raise WorkloadError(f"{name} 必填")
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        raise WorkloadError(f"{name} 必须是整数")
    if minimum is not None and n < minimum:
        raise WorkloadError(f"{name} 不能小于 {minimum}")
    if maximum is not None and n > maximum:
        raise WorkloadError(f"{name} 不能大于 {maximum}")
    return n


def paging(args):
    page = _int(args.get("page"), "page", default=1, minimum=1)
    page_size = _int(args.get("page_size"), "page_size",
                     default=C.WORKLOAD_PAGE_SIZE_DEFAULT,
                     minimum=1, maximum=C.WORKLOAD_PAGE_SIZE_MAX)
    return page, page_size


# ─────────────────────────── 事件流（唯一口径）───────────────────────────
# 每个 SELECT 产出相同列：event_key / node / source / source_id / action /
# operator / owner_id / occurred_at / qty / note。
# qty 是「涉及数量」而非库存变动：qty_delta 优先，纯分配流水取 allocated_delta。
_MOVEMENT_NODE_SQL = f"""CASE
        WHEN m.ref_type = '{C.MOVEMENT_ASN_RECEIVE}' THEN '{C.WL_NODE_RECEIVE}'
        WHEN m.ref_type IN ('{C.MOVEMENT_ASN_PUTAWAY_OUT}', '{C.MOVEMENT_ASN_PUTAWAY_IN}')
             THEN '{C.WL_NODE_PUTAWAY}'
        WHEN m.ref_type = '{C.MOVEMENT_DN_PICK}' THEN '{C.WL_NODE_PICK}'
        WHEN m.ref_type = '{C.MOVEMENT_DN_SHIP}' THEN '{C.WL_NODE_SHIP}'
        WHEN m.ref_type IN ('{C.MOVEMENT_MOVE_OUT}', '{C.MOVEMENT_MOVE_IN}')
             THEN '{C.WL_NODE_MOVE}'
        WHEN m.ref_type = '{C.MOVEMENT_ADJUSTMENT}' THEN '{C.WL_NODE_ADJUST}'
        WHEN m.ref_type LIKE 'it_%' THEN '{C.WL_NODE_IT}'
        ELSE '{C.WL_NODE_OTHER}'
    END"""

_MOVEMENT_QTY_SQL = """CASE WHEN ABS(COALESCE(m.qty_delta, 0)) > 1e-9
        THEN ABS(m.qty_delta) ELSE ABS(COALESCE(m.allocated_delta, 0)) END"""

EVENTS_SQL = f"""
WITH events AS (
    SELECT 'sm-' || m.id AS event_key, {_MOVEMENT_NODE_SQL} AS node,
           'stock_movement' AS source, m.id AS source_id,
           m.ref_type AS action,
           COALESCE(NULLIF(m.operator, ''), '{C.WORKLOAD_UNKNOWN_OPERATOR}') AS operator,
           m.owner_id AS owner_id, m.created_at AS occurred_at,
           {_MOVEMENT_QTY_SQL} AS qty, COALESCE(m.note, '') AS note
      FROM stock_movement m

    UNION ALL
    SELECT 'cl-' || cl.id,
           CASE WHEN cl.action IN ('{C.CARE_LOG_ABNORMAL}', '{C.CARE_LOG_HANDLED}')
                THEN '{C.WL_NODE_CARE_HANDLE}' ELSE '{C.WL_NODE_CARE}' END,
           'care_log', cl.id, cl.action,
           COALESCE(NULLIF(cl.operator, ''), '{C.WORKLOAD_UNKNOWN_OPERATOR}'),
           cl.owner_id, cl.created_at, 0, COALESCE(cl.note, '')
      FROM care_log cl

    UNION ALL
    SELECT 'rl-' || rl.id, '{C.WL_NODE_REVIEW}', 'dn_review_line', rl.id,
           COALESCE(NULLIF(rl.result, ''), 'reviewed'),
           COALESCE(NULLIF(rl.operator, ''), '{C.WORKLOAD_UNKNOWN_OPERATOR}'),
           r.owner_id, COALESCE(rl.reviewed_at, r.updated_at, r.created_at),
           ABS(COALESCE(rl.actual_qty, 0)), COALESCE(rl.note, '')
      FROM dn_review_line rl
      JOIN dn_review r ON r.id = rl.review_id
     WHERE rl.result IS NOT NULL AND rl.result <> ''

    UNION ALL
    SELECT 'df-' || dl.id, '{C.WL_NODE_REVIEW_DIFF}', 'dn_review_diff_log', dl.id,
           COALESCE(NULLIF(dl.reason_code, ''), 'DIFF'),
           COALESCE(NULLIF(dl.operator, ''), '{C.WORKLOAD_UNKNOWN_OPERATOR}'),
           dl.owner_id, dl.created_at, ABS(COALESCE(dl.diff_qty, 0)),
           COALESCE(dl.note, '')
      FROM dn_review_diff_log dl

    UNION ALL
    SELECT 'od-' || ol.id, '{C.WL_NODE_DN_ORDER}', 'dn_order_log', ol.id, ol.action,
           COALESCE(NULLIF(ol.operator, ''), '{C.WORKLOAD_UNKNOWN_OPERATOR}'),
           ol.owner_id, ol.created_at, 0, COALESCE(ol.note, '')
      FROM dn_order_log ol

    UNION ALL
    SELECT 'pr-' || pr.id, '{C.WL_NODE_PUTAWAY_REC}', 'putaway_recommendation', pr.id,
           pr.action,
           COALESCE(NULLIF(pr.operator, ''), '{C.WORKLOAD_UNKNOWN_OPERATOR}'),
           (SELECT a.owner_id FROM asn a WHERE a.id = pr.asn_id),
           pr.created_at, 0, COALESCE(pr.override_reason, '')
      FROM putaway_recommendation pr

    UNION ALL
    SELECT 'oa-' || oal.id, '{C.WL_NODE_OUTBOUND}', 'outbound_allocation_log', oal.id,
           oal.action,
           COALESCE(NULLIF(oal.operator, ''), '{C.WORKLOAD_UNKNOWN_OPERATOR}'),
           (SELECT d.owner_id FROM dn d WHERE d.id = oal.dn_id),
           oal.created_at, ABS(COALESCE(oal.qty, 0)), COALESCE(oal.reason, '')
      FROM outbound_allocation_log oal

    UNION ALL
    SELECT 'ic-' || icl.id, '{C.WL_NODE_IT_CHANGE}', 'inventory_change_log', icl.id,
           COALESCE(NULLIF(icl.reason_code, ''), icl.field_name),
           COALESCE(NULLIF(icl.operator, ''), '{C.WORKLOAD_UNKNOWN_OPERATOR}'),
           COALESCE(
               (SELECT itl.owner_id FROM inventory_transfer_line itl
                 WHERE itl.id = icl.line_id),
               (SELECT itl2.owner_id FROM inventory_transfer_line itl2
                 WHERE itl2.transfer_id = icl.transfer_id LIMIT 1)),
           COALESCE(NULLIF(icl.system_at, ''), icl.business_at), 0,
           COALESCE(icl.new_value, '')
      FROM inventory_change_log icl
)
SELECT e.* FROM events e
"""


def _parse_date(value, name):
    raw = (value or "").strip()
    if not raw:
        return ""
    try:
        return datetime.date.fromisoformat(raw[:10]).isoformat()
    except (TypeError, ValueError):
        raise WorkloadError(f"{name} 必须是 YYYY-MM-DD 日期")


def filtered_events(db, args):
    """取符合过滤条件的全部事件（只读）；所有视图共用这一份口径。"""
    where, params = [], []
    date_from = _parse_date(args.get("date_from"), "date_from")
    date_to = _parse_date(args.get("date_to"), "date_to")
    if date_from:
        where.append("date(e.occurred_at) >= ?")
        params.append(date_from)
    if date_to:
        where.append("date(e.occurred_at) <= ?")
        params.append(date_to)
    node = (args.get("node") or "").strip()
    if node:
        if node not in C.WORKLOAD_NODES:
            raise WorkloadError(f"node 无效：{node}")
        where.append("e.node = ?")
        params.append(node)
    operator = (args.get("operator") or "").strip()
    if operator:
        where.append("e.operator = ?")
        params.append(operator)
    action = (args.get("action") or "").strip()
    if action:
        where.append("e.action = ?")
        params.append(action)
    source = (args.get("source") or "").strip()
    if source:
        where.append("e.source = ?")
        params.append(source)
    if str(args.get("owner_id") or "").strip():
        where.append("e.owner_id = ?")
        params.append(_int(args.get("owner_id"), "owner_id", minimum=1))
    S.scoped_where(where, params, table="e")
    sql = EVENTS_SQL
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY e.occurred_at DESC, e.event_key DESC"
    rows = db.execute(sql, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["qty"] = float(d.get("qty") or 0)
        d["node_label"] = C.WORKLOAD_NODE_LABELS.get(
            d["node"], C.WORKLOAD_NODE_LABELS[C.WL_NODE_OTHER])
        d["occurred_date"] = str(d.get("occurred_at") or "")[:10]
        out.append(d)
    return out


# ─────────────────────────── 三个视角 ────────────────────────────────

def events_report(db, args):
    """事件明细：过滤 + 分页（供前端下钻核对聚合结果）。"""
    page, page_size = paging(args)
    rows = filtered_events(db, args)
    return {
        "ok": True, "total": len(rows), "page": page, "page_size": page_size,
        "events": rows[(page - 1) * page_size: page * page_size],
    }


def _sorted(groups, key=lambda x: (-x[1]["count"], x[0])):
    return sorted(groups.items(), key=key)


def by_operator(db, args):
    """按人：次数 / 涉及数量 / 覆盖节点 / 活跃天数 / 最近作业时间。"""
    rows = filtered_events(db, args)
    groups = {}
    for r in rows:
        g = groups.setdefault(r["operator"], {
            "operator": r["operator"], "count": 0, "qty": 0.0,
            "nodes": set(), "days": set(), "last_at": "",
        })
        g["count"] += 1
        g["qty"] += r["qty"]
        g["nodes"].add(r["node"])
        if r["occurred_date"]:
            g["days"].add(r["occurred_date"])
        if r["occurred_at"] and r["occurred_at"] > g["last_at"]:
            g["last_at"] = r["occurred_at"]
    out = []
    for _, g in _sorted(groups):
        item = {k: v for k, v in g.items() if k != "days"}
        item["nodes"] = sorted(g["nodes"])
        item["node_labels"] = [C.WORKLOAD_NODE_LABELS.get(n, n) for n in g["nodes"]]
        item["active_days"] = len(g["days"])
        out.append(item)
    return {"ok": True, "total": len(rows), "operators": out}


def by_node(db, args):
    """按节点：次数 / 涉及数量 / 操作人数 / 货主数 / 最近作业时间。"""
    rows = filtered_events(db, args)
    groups = {}
    for r in rows:
        g = groups.setdefault(r["node"], {
            "node": r["node"],
            "label": C.WORKLOAD_NODE_LABELS.get(r["node"], r["node"]),
            "count": 0, "qty": 0.0, "operators": set(), "owners": set(),
            "last_at": "",
        })
        g["count"] += 1
        g["qty"] += r["qty"]
        if r["operator"]:
            g["operators"].add(r["operator"])
        if r["owner_id"]:
            g["owners"].add(r["owner_id"])
        if r["occurred_at"] and r["occurred_at"] > g["last_at"]:
            g["last_at"] = r["occurred_at"]
    out = []
    for _, g in _sorted(groups):
        item = {k: v for k, v in g.items() if k != "owners"}
        item["operators"] = sorted(g["operators"])
        item["operator_count"] = len(g["operators"])
        item["owner_count"] = len(g["owners"])
        out.append(item)
    return {"ok": True, "total": len(rows), "nodes": out}


def _period_key(occurred_at, period):
    raw = str(occurred_at or "")[:10]
    if not raw:
        return "_unknown", "未记录时间"
    try:
        d = datetime.date.fromisoformat(raw)
    except ValueError:
        return "_unknown", "未记录时间"
    if period == C.WL_PERIOD_WEEK:
        iso = d.isocalendar()
        monday = d - datetime.timedelta(days=d.weekday())
        return f"{iso[0]}-W{iso[1]:02d}", f"{iso[0]}-W{iso[1]:02d}（{monday.isoformat()} 起）"
    if period == C.WL_PERIOD_MONTH:
        return d.strftime("%Y-%m"), d.strftime("%Y-%m")
    return d.isoformat(), d.isoformat()


def by_period(db, args):
    """按时间段：day / week（ISO 周一起）/ month，含节点分布。"""
    period = (args.get("period") or C.WL_PERIOD_DAY).strip()
    if period not in C.WORKLOAD_PERIODS:
        raise WorkloadError(f"period 无效：{period}")
    rows = filtered_events(db, args)
    groups = {}
    for r in rows:
        key, label = _period_key(r["occurred_at"], period)
        g = groups.setdefault(key, {
            "key": key, "label": label, "period": period, "count": 0,
            "qty": 0.0, "operators": set(), "nodes": {},
        })
        g["count"] += 1
        g["qty"] += r["qty"]
        if r["operator"]:
            g["operators"].add(r["operator"])
        g["nodes"][r["node"]] = g["nodes"].get(r["node"], 0) + 1
    series = []
    for key in sorted(groups):
        g = groups[key]
        series.append({**g, "operators": sorted(g["operators"]),
                       "operator_count": len(g["operators"])})
    return {"ok": True, "total": len(rows), "period": period,
            "period_label": C.WORKLOAD_PERIOD_LABELS[period], "series": series}


def operators(db, args):
    """操作人下拉：从当前可见事件里去重，带次数便于前端识别。"""
    rows = filtered_events(db, args)
    counts = {}
    for r in rows:
        counts[r["operator"]] = counts.get(r["operator"], 0) + 1
    return {"ok": True, "total": len(counts),
            "operators": [{"operator": k, "count": v}
                          for k, v in sorted(counts.items(), key=lambda x: (-x[1], x[0]))]}


def overview(db, args):
    """概览卡 + Top N + 近 14 天日趋势（均复用同一份过滤事件）。"""
    rows = filtered_events(db, args)
    op_counts, node_counts, day_counts, source_counts = {}, {}, {}, {}
    for r in rows:
        op_counts[r["operator"]] = op_counts.get(r["operator"], 0) + 1
        node_counts[r["node"]] = node_counts.get(r["node"], 0) + 1
        source_counts[r["source"]] = source_counts.get(r["source"], 0) + 1
        if r["occurred_date"]:
            day_counts[r["occurred_date"]] = day_counts.get(r["occurred_date"], 0) + 1

    def top(counter, n=5):
        return [{"code": k, "label": C.WORKLOAD_NODE_LABELS.get(k, k), "count": v}
                for k, v in sorted(counter.items(), key=lambda x: (-x[1], x[0]))[:n]]

    daily = [{"key": d, "label": d, "count": day_counts[d]}
             for d in sorted(day_counts)[-14:]]
    return {
        "ok": True,
        "cards": [
            {"code": "events", "label": "操作事件数", "value": len(rows)},
            {"code": "operators", "label": "操作人数", "value": len(op_counts)},
            {"code": "nodes", "label": "覆盖作业节点", "value": len(node_counts)},
            {"code": "active_days", "label": "活跃天数", "value": len(day_counts)},
        ],
        "total_qty": round(sum(r["qty"] for r in rows), 4),
        "top_operators": [
            {"operator": k, "count": v}
            for k, v in sorted(op_counts.items(), key=lambda x: (-x[1], x[0]))[:5]],
        "top_nodes": top(node_counts),
        "sources": [{"code": k, "count": v}
                    for k, v in sorted(source_counts.items(), key=lambda x: (-x[1], x[0]))],
        "daily": daily,
    }


def dictionary():
    """节点 / 时间段 / 数据源字典，供前端下拉与口径说明。"""
    return {
        "ok": True,
        "nodes": [{"code": n, "label": C.WORKLOAD_NODE_LABELS[n]}
                  for n in C.WORKLOAD_NODES],
        "periods": [{"code": p, "label": C.WORKLOAD_PERIOD_LABELS[p]}
                    for p in C.WORKLOAD_PERIODS],
        "sources": [
            {"code": "stock_movement", "label": "库存流水", "nodes": [
                C.WL_NODE_RECEIVE, C.WL_NODE_PUTAWAY, C.WL_NODE_PICK,
                C.WL_NODE_SHIP, C.WL_NODE_MOVE, C.WL_NODE_ADJUST,
                C.WL_NODE_IT, C.WL_NODE_OTHER]},
            {"code": "care_log", "label": "养护留痕",
             "nodes": [C.WL_NODE_CARE, C.WL_NODE_CARE_HANDLE]},
            {"code": "dn_review_line", "label": "出库复核行", "nodes": [C.WL_NODE_REVIEW]},
            {"code": "dn_review_diff_log", "label": "复核差异留痕",
             "nodes": [C.WL_NODE_REVIEW_DIFF]},
            {"code": "dn_order_log", "label": "拆单合单留痕", "nodes": [C.WL_NODE_DN_ORDER]},
            {"code": "putaway_recommendation", "label": "上架推荐决策",
             "nodes": [C.WL_NODE_PUTAWAY_REC]},
            {"code": "outbound_allocation_log", "label": "波次分配留痕",
             "nodes": [C.WL_NODE_OUTBOUND]},
            {"code": "inventory_change_log", "label": "转移变更文档",
             "nodes": [C.WL_NODE_IT_CHANGE]},
        ],
        "unknown_operator": C.WORKLOAD_UNKNOWN_OPERATOR,
        "page_size_default": C.WORKLOAD_PAGE_SIZE_DEFAULT,
        "page_size_max": C.WORKLOAD_PAGE_SIZE_MAX,
    }
