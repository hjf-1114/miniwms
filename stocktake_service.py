"""M22 盘点方案服务层：生效配置、范围条件组编译、动碰集合、盘点行试算。

设计口径见 ``docs/PUTAWAY_RECOMMENDATION.md`` §18.3 / §18.4 / §18.5：

- 方案只是模板：创建盘点单可一次性覆盖；``freeze()`` 把生效范围落进
  ``stocktake.scope_snapshot``，之后改方案不影响历史单据（与 M19 同一原则）。
- 部分盘点的维度间 AND，数组走 IN、标量走 ``=``，``null`` / 空数组 = 不限。
- 只有出现批次维度时才以 ``stock_batch`` 作为主表，避免全盘多一次联表。
- 动碰集合来自 ``stock_movement``（M21 已带批次维度），排除 ``qty_delta = 0``
  的纯分配流水；交易类型白名单与时间窗都是方案级默认值。
"""
import datetime
import json

import constants as C

EPS = 1e-9

# 部分盘点范围维度 → SQL 列。a = 主表（stock 或 stock_batch）。
SCOPE_COLUMNS = {
    "warehouse_id": "l.warehouse_id",
    "zone_id": "l.zone_id",
    "location_group": "l.zone",       # M34 货位组落地前按库位区文本近似
    "location_ids": "l.id",
    "loc_type": "l.loc_type",
    "product_ids": "p.id",
    "category_l1": "p.category_l1",
    "category_l2": "p.category_l2",
    "main_category": "p.main_category",
    "product_type": "p.product_type",
    "batch_no": "a.batch_no",
    "batch_key": "a.batch_key",
    "goods_property": "a.goods_property",
}
BATCH_KEYS = ("batch_no", "batch_key", "goods_property")


class PlanError(Exception):
    """盘点方案配置非法，由路由层转成 4xx。"""


def loads(text, default):
    """宽容解析 JSON 字段（老库可能为空串或脏数据）。"""
    try:
        value = json.loads(text) if text else default
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def clean_scope(raw):
    """只保留规格允许的条件维度；空值 / 空数组视为不限。"""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key in C.SCOPE_DIMENSIONS:
        val = raw.get(key)
        if val is None or val == "" or val == [] or val == ():
            continue
        if key == "has_stock":
            out[key] = 1 if val in (1, True, "1") else 0
        elif isinstance(val, (list, tuple)):
            out[key] = list(val)
        else:
            out[key] = val
    # 货主维度不接受范围覆盖：一律以盘点单的 owner_id 为准，防止越权扩范围。
    out.pop("owner_id", None)
    return out


def effective_config(db, plan_id=None, override=None):
    """解析生效配置：方案默认值 ← 创建盘点单时的一次性覆盖。

    返回 dict 可直接写入 ``scope_snapshot``；``plan_code`` 只用于展示。
    """
    ov = override if isinstance(override, dict) else {}
    cfg = {
        "scope_type": C.SCOPE_FULL,
        "scope": {},
        "blind_mode": C.BLIND_NONE,
        "dynamic_baseline": C.BASELINE_LAST_CLOSED,
        "lookback_days": 7,
        "movement_from": "",
        "movement_to": "",
        "movement_types": [],
        "auto_adjust": 0,
        "include_zero": 0,
        "baseline_at": "",
        "plan_id": None,
        "plan_code": "",
    }
    if plan_id:
        row = db.execute(
            "SELECT * FROM stocktake_plan WHERE id = ?", (int(plan_id),)
        ).fetchone()
        if row is None:
            raise PlanError(f"盘点方案不存在: id={plan_id}")
        cfg.update({
            "scope_type": row["scope_type"],
            "scope": clean_scope(loads(row["scope"], {})),
            "blind_mode": row["blind_mode"],
            "dynamic_baseline": row["dynamic_baseline"],
            "lookback_days": row["lookback_days"],
            "movement_from": row["movement_from"] or "",
            "movement_to": row["movement_to"] or "",
            "movement_types": loads(row["movement_types"], []),
            "auto_adjust": row["auto_adjust"],
            "include_zero": row["include_zero"],
            "plan_id": row["id"],
            "plan_code": row["code"],
        })
    allowed = ("scope_type", "blind_mode", "dynamic_baseline", "lookback_days",
               "movement_from", "movement_to", "movement_types", "auto_adjust",
               "include_zero", "baseline_at")
    for key in allowed:
        if key in ov and ov[key] is not None:
            cfg[key] = ov[key]
    if "scope" in ov:
        cfg["scope"] = clean_scope(ov["scope"])
    if cfg["scope_type"] not in C.SCOPE_TYPES:
        raise PlanError(f"scope_type 非法: {cfg['scope_type']}")
    if cfg["blind_mode"] not in C.BLIND_MODES:
        raise PlanError(f"blind_mode 非法: {cfg['blind_mode']}")
    if cfg["dynamic_baseline"] not in C.BASELINE_TYPES:
        raise PlanError(f"dynamic_baseline 非法: {cfg['dynamic_baseline']}")
    try:
        cfg["lookback_days"] = max(0, int(cfg["lookback_days"]))
    except (TypeError, ValueError):
        raise PlanError("lookback_days 必须为整数")
    cfg["auto_adjust"] = 1 if cfg["auto_adjust"] in (1, True, "1") else 0
    cfg["include_zero"] = 1 if cfg["include_zero"] in (1, True, "1") else 0
    types = cfg["movement_types"]
    if isinstance(types, str):
        types = loads(types, [])
    if not isinstance(types, list):
        raise PlanError("movement_types 必须为数组")
    bad = [t for t in types if t not in C.DYNAMIC_MOVEMENT_TYPES]
    if bad:
        raise PlanError(f"movement_types 含未知交易类型: {bad}")
    cfg["movement_types"] = types
    # 带批次维度的范围必须按批次生成盘点行
    if cfg["scope_type"] != C.SCOPE_FULL and any(cfg["scope"].get(k) for k in BATCH_KEYS):
        cfg["batch_mode"] = True
    else:
        cfg["batch_mode"] = False
    return cfg


def resolve_baseline(db, cfg, owner_id):
    """动碰时间窗：手工窗 > 创建时 baseline_at > since_days > last_closed。"""
    frm = str(cfg.get("movement_from") or "").strip()
    to = str(cfg.get("movement_to") or "").strip()
    if frm:
        return frm, to
    explicit = str(cfg.get("baseline_at") or "").strip()
    if explicit:
        return explicit, ""
    if cfg.get("dynamic_baseline") == C.BASELINE_SINCE_DAYS:
        day = datetime.date.today() - datetime.timedelta(days=cfg["lookback_days"])
        return day.strftime("%Y-%m-%d 00:00:00"), ""
    row = db.execute(
        "SELECT MAX(closed_at) AS t FROM stocktake "
        "WHERE owner_id = ? AND status = ? AND closed_at <> ''",
        (owner_id, C.STOCKTAKE_CLOSED),
    ).fetchone()
    return (row["t"] or ""), ""


def _dynamic_keys(db, cfg, owner_id, frm, to):
    """返回动碰维度集合：非批次按 (o,p,l)，批次按 (o,p,l,batch_no)。"""
    where = ["owner_id = ?", "ABS(qty_delta) > ?"]
    params = [owner_id, EPS]
    if frm:
        where.append("created_at >= ?")
        params.append(frm)
    if to:
        where.append("created_at <= ?")
        params.append(to)
    types = cfg.get("movement_types") or []
    if types:
        where.append(f"ref_type IN ({','.join('?' for _ in types)})")
        params.extend(types)
    rows = db.execute(
        f"SELECT DISTINCT owner_id, product_id, location_id, batch_no "
        f"FROM stock_movement WHERE {' AND '.join(where)}", params,
    ).fetchall()
    if cfg.get("batch_mode"):
        return {(r["owner_id"], r["product_id"], r["location_id"], r["batch_no"])
                for r in rows}
    return {(r["owner_id"], r["product_id"], r["location_id"]) for r in rows}


def collect_lines(db, cfg, owner_id):
    """按生效配置生成待盘点行（不写库），供 freeze 与方案试算共用。"""
    batch_mode = bool(cfg.get("batch_mode"))
    if batch_mode:
        source = ("FROM stock_batch a JOIN product p ON p.id = a.product_id "
                  "JOIN location l ON l.id = a.location_id ")
        select = ("SELECT a.owner_id, a.product_id, a.location_id, a.batch_no, "
                  "SUM(a.qty) AS book_qty ")
        group = " GROUP BY a.owner_id, a.product_id, a.location_id, a.batch_no"
    else:
        source = ("FROM stock a JOIN product p ON p.id = a.product_id "
                  "JOIN location l ON l.id = a.location_id ")
        select = ("SELECT a.owner_id, a.product_id, a.location_id, '' AS batch_no, "
                  "a.qty AS book_qty ")
        group = ""

    where, params = ["a.owner_id = ?"], [owner_id]
    scope = cfg.get("scope") or {}
    for key, col in SCOPE_COLUMNS.items():
        val = scope.get(key)
        if val is None or val == "" or val == []:
            continue
        if col.startswith("a.batch") and not batch_mode:
            continue
        if isinstance(val, list):
            where.append(f"{col} IN ({','.join('?' for _ in val)})")
            params.extend(val)
        else:
            where.append(f"{col} = ?")
            params.append(val)
    include_zero = bool(cfg.get("include_zero")) or scope.get("has_stock") == 0
    sql = select + source + "WHERE " + " AND ".join(where) + group
    rows = db.execute(sql, params).fetchall()

    baseline = ("", "")
    if cfg.get("scope_type") == C.SCOPE_DYNAMIC:
        baseline = resolve_baseline(db, cfg, owner_id)
        touched = _dynamic_keys(db, cfg, owner_id, *baseline)

    lines = []
    for r in rows:
        qty = float(r["book_qty"] or 0)
        if not include_zero and abs(qty) <= EPS:
            continue
        bno = r["batch_no"] or ""
        if cfg.get("scope_type") == C.SCOPE_DYNAMIC:
            key = ((r["owner_id"], r["product_id"], r["location_id"], bno)
                   if batch_mode else (r["owner_id"], r["product_id"], r["location_id"]))
            if key not in touched:
                continue
        lines.append({
            "owner_id": r["owner_id"], "product_id": r["product_id"],
            "location_id": r["location_id"], "batch_no": bno, "book_qty": qty,
        })
    lines.sort(key=lambda x: (x["location_id"], x["product_id"], x["batch_no"]))
    return lines, baseline


def trial(db, cfg, owner_id, limit=20):
    """方案页「试算行数」：返回行数、前 N 行摘要与生效时间窗。"""
    lines, baseline = collect_lines(db, cfg, owner_id)
    return {
        "count": len(lines),
        "batch_mode": bool(cfg.get("batch_mode")),
        "baseline_from": baseline[0],
        "baseline_to": baseline[1],
        "preview": lines[:limit],
    }
