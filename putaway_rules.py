"""M25 上架推荐规则引擎（v3.3 配置化；用户 2026-11-10 1A/2A/3A 定稿）。

执行模型（``docs/PUTAWAY_RECOMMENDATION.md`` §10.1）：

    ① 策略包匹配：货主+仓库精确 > 仓库默认(owner 空) > 系统默认
    ② 条件组匹配：13 维 AND，按 priority 升序，第一行命中即停
    ③ 该行 fallback 链：按 steps 顺序，第一个产出「通过过滤后非空候选」的步骤生效
    ④ 硬约束过滤：按命中规则行 limits 的三态执行（没配就不拦）
       · enabled=true        推荐排除候选 + 上架过账拦截
       · recommend_only=true 只排除推荐候选，过账放行（M20 混放口径）
    ⑤ 候选内排序：9 条评分规则（R07–R15）加权，取 Top-N
    ⑥ 全部失败 → 兜底行 priority 999 + NO_CANDIDATE

**v3.3 铁律**：拦截内容全部来自 ``putaway_rule.limits``，本模块不保留任何
「绕不过配置的 hardcoded 红线」；删掉规则行即不再拦截。命中的约束按过滤执行，
**不参与打分**（约束不能被权重买通）。

**3A**：``putaway_rule`` 带 ``rule_type``，出库分配规则（M35）复用同一张表，
本模块的条件组 / 优先级 / limits 骨架对两类规则通用。
"""
import json

import constants as C


class RuleError(ValueError):
    """配置非法 / 单据不存在；继承 ValueError 以复用现有路由的 400 捕获。"""

    def __init__(self, message, code=""):
        super().__init__(message)
        self.code = code


def _j(value, default):
    """容忍 NULL / 空串 / 非法 JSON，永远返回期望类型。"""
    if value in (None, ""):
        return default() if callable(default) else default
    if isinstance(value, (dict, list)):
        return value
    try:
        out = json.loads(value)
    except (TypeError, ValueError):
        return default() if callable(default) else default
    return out if isinstance(out, type(default() if callable(default) else default)) else (
        default() if callable(default) else default
    )


def _dict(row):
    return dict(row) if row is not None else {}


def _list(rows):
    return [dict(r) for r in rows]


# ── 13 维单据上下文 ────────────────────────────────────────────────────
def build_context(db, asn_id, line_id, qty=0.0):
    """从 ASN 单据 / 明细 / 货品构造 13 维 ctx，并带回推荐需要的辅助信息。"""
    asn = db.execute("SELECT * FROM asn WHERE id = ?", (asn_id,)).fetchone()
    if asn is None:
        raise RuleError("ASN 不存在")
    line = db.execute(
        "SELECT * FROM asn_line WHERE id = ? AND asn_id = ?", (line_id, asn_id)
    ).fetchone()
    if line is None:
        raise RuleError("行不存在或不属于该 ASN")
    product = db.execute("SELECT * FROM product WHERE id = ?",
                         (line["product_id"],)).fetchone()
    if product is None:
        raise RuleError("货品不存在")

    extra = _j(line["extra"], dict)
    asn_d = _dict(asn)
    prod_d = _dict(product)
    batch_no = str(extra.get("batch_no") or "").strip()
    goods_property = str(extra.get("goods_property") or "合格").strip() or "合格"
    dcid = extra.get("designated_customer_id")

    ctx = {
        "doc_type": str(asn_d.get("doc_type") or "采购"),
        "goods_property": goods_property,
        "is_virtual": int(asn_d.get("is_virtual") or 0),
        "is_direct": int(asn_d.get("is_direct") or 0),
        "is_cold_chain": int(asn_d.get("is_cold_chain") or 0),
        "is_hazard": int(asn_d.get("is_hazard") or 0),
        "external_doc_type": str(asn_d.get("external_doc_type") or ""),
        "abc_class": "",                       # ABC 由近 30 天出库频次聚合，暂无缓存字段
        "owner_id": asn_d.get("owner_id"),
        "is_designated": 1 if dcid else 0,
        "zone_id": None,                       # 由规则行指定，条件组里通常不限
        "product_id": line["product_id"],
        "category_l1": str(prod_d.get("category_l1") or prod_d.get("category") or ""),
    }
    return {
        "ctx": ctx, "asn": asn_d, "line": dict(line), "product": prod_d,
        "extra": extra, "batch_no": batch_no,
        "goods_property": goods_property,
        "internal_batch_no": str(extra.get("internal_batch_no") or ""),
        "batch_key": str(extra.get("batch_key") or ""),
        "designated_customer_id": dcid,
        "owner_id": asn_d.get("owner_id"), "product_id": line["product_id"],
        "qty": float(qty or 0), "asn_id": asn_id, "line_id": line_id,
    }


def matches(cond, ctx):
    """每维单选、维度间 AND；``null`` = 通配；列表值 = 该维多选（``in``）。"""
    for key, want in (cond or {}).items():
        if want is None or want == "":
            continue
        got = ctx.get(key)
        if isinstance(want, (list, tuple)):
            if got not in want and str(got) not in {str(x) for x in want}:
                return False
        elif isinstance(want, bool):
            if int(got or 0) != int(want):
                return False
        elif isinstance(want, (int, float)) and isinstance(got, (int, float)):
            if got != want:
                return False
        elif str(got if got is not None else "") != str(want):
            return False
    return True


# ── 策略包 / 规则行 ────────────────────────────────────────────────────
def resolve_rule_set(db, ctx, rule_type=C.RULE_TYPE_PUTAWAY):
    """货主+仓库精确 > 仓库默认(owner 空) > 系统默认（warehouse 空）。"""
    wh = (ctx.get("asn") or {}).get("source_warehouse")
    owner = ctx.get("owner_id")
    rows = _list(db.execute(
        "SELECT * FROM putaway_rule_set WHERE status = ?",
        (C.RULE_STATUS_ACTIVE,),
    ).fetchall())
    for exact in (True, False):
        for r in rows:
            if exact:
                if r["warehouse_id"] == wh and r["owner_id"] == owner and wh:
                    return r
            elif r["warehouse_id"] == wh and r["owner_id"] in (None, "") and wh:
                return r
    for r in rows:
        if r["warehouse_id"] in (None, "") and r["owner_id"] in (None, ""):
            return r
    return None


def list_rules(db, set_id, rule_type, enabled_only=True):
    sql = ("SELECT * FROM putaway_rule WHERE rule_set_id = ? AND rule_type = ?")
    params = [set_id, rule_type]
    if enabled_only:
        sql += " AND enabled = 1"
    sql += " ORDER BY priority, id"
    return _list(db.execute(sql, params).fetchall())


def match_rule(db, set_id, ctx, rule_type=C.RULE_TYPE_PUTAWAY):
    """按 priority 升序，第一行命中即停；兜底行条件组为空。"""
    for r in list_rules(db, set_id, rule_type):
        if matches(_j(r["condition"], dict), ctx["ctx"]):
            return r
    return None


def has_enabled_rules(db, rule_type=C.RULE_TYPE_PUTAWAY):
    row = db.execute(
        "SELECT COUNT(*) AS n FROM putaway_rule r "
        "JOIN putaway_rule_set s ON s.id = r.rule_set_id "
        "WHERE r.rule_type = ? AND r.enabled = 1 AND s.status = ?",
        (rule_type, C.RULE_STATUS_ACTIVE),
    ).fetchone()
    return bool(row and row["n"])


# ── 库位与占用快照 ─────────────────────────────────────────────────────
def _loc_rows(db, where="", params=()):
    sql = """
        SELECT l.*, z.code AS zone_code, z.temp_class AS zone_temp,
               z.name AS zone_name, z.allow_mix_product AS z_mp,
               z.allow_mix_batch AS z_mb, z.allow_mix_owner AS z_mo,
               w.code AS warehouse_code
        FROM location l
        LEFT JOIN zone z      ON z.id = l.zone_id
        LEFT JOIN warehouse w ON w.id = l.warehouse_id
        {w} ORDER BY l.code
    """.format(w=where)
    return _list(db.execute(sql, params).fetchall())


def _occupancy(db):
    stock, batches, designated, batchkeys = {}, {}, {}, {}
    for r in db.execute(
        "SELECT location_id, product_id, owner_id, SUM(qty) AS q FROM stock "
        "WHERE qty > 0 GROUP BY location_id, product_id, owner_id"
    ).fetchall():
        stock.setdefault(r["location_id"], []).append(dict(r))
    for r in db.execute("SELECT location_id, info FROM batch_receipt").fetchall():
        try:
            bn = str((json.loads(r["info"] or "{}") or {}).get("batch_no") or "").strip()
        except (TypeError, ValueError):
            bn = ""
        if bn:
            batches.setdefault(r["location_id"], set()).add(bn)
    for r in db.execute(
        "SELECT location_id, batch_no, batch_key, designated_customer_id, "
        "expiry_date, qty FROM stock_batch WHERE qty > 0"
    ).fetchall():
        if r["batch_no"]:
            batches.setdefault(r["location_id"], set()).add(str(r["batch_no"]))
        if r["batch_key"]:
            batchkeys.setdefault(r["location_id"], set()).add(str(r["batch_key"]))
        if r["designated_customer_id"]:
            designated.setdefault(r["location_id"], set()).add(
                int(r["designated_customer_id"]))
    return {"stock": stock, "batches": batches,
            "designated": designated, "batchkeys": batchkeys}


def _used(occ, loc_id):
    return sum(float(r["q"] or 0) for r in occ["stock"].get(loc_id, []))


def _temp_required(product):
    """从货品 temp_range 粗判温区要求（M20 同名逻辑迁入，只在配置启用时生效）。"""
    s = str((product or {}).get("temp_range") or "")
    for t in ("冷冻", "冷藏", "阴凉"):
        if t in s:
            return t
    return None


def _effective_mix(loc):
    out = {}
    for f in C.MIX_FIELDS:
        own = loc.get(f)
        zb = {"allow_mix_product": "z_mp", "allow_mix_batch": "z_mb",
              "allow_mix_owner": "z_mo"}[f]
        val = own if own is not None else loc.get(zb)
        out[f] = int(val or 0)
    return out


def _step_candidates(db, step, info, occ):
    """9 种寻址（8 预设 + all_active）；返回候选库位 dict 列表（未过 limits）。"""
    typ = str(step.get("type") or "")
    ctx, prod = info["ctx"], info["product"]
    all_locs = _loc_rows(db)
    by_id = {l["id"]: l for l in all_locs}

    def pick(ids):
        return [by_id[i] for i in ids if i in by_id]

    if typ == "all_active":
        return [l for l in all_locs
                if l["status"] == "active" and l["loc_type"] in ("storage", "picking")]

    if typ == "fixed_location":
        code = str(step.get("location_code") or "")
        return [l for l in all_locs if l["code"] == code]

    if typ == "zone_by_code":
        code = str(step.get("zone_code") or "")
        return [l for l in all_locs if (l["zone_code"] or "") == code]

    if typ == "zone_by_product":
        zid = prod.get("default_zone_id")
        return [l for l in all_locs if zid and l["zone_id"] == zid]

    if typ == "empty_in_zone":
        code = str(step.get("zone_code") or "")
        return [l for l in all_locs
                if (not code or (l["zone_code"] or "") == code)
                and _used(occ, l["id"]) <= 0]

    if typ == "same_product":
        ids = [lid for lid, rows in occ["stock"].items()
               if any(r["product_id"] == info["product_id"] for r in rows)]
        if str(step.get("order")) == "empty_first":
            ids.sort(key=lambda i: (_used(occ, i), by_id[i]["code"] if i in by_id else ""))
        else:
            ids.sort(key=lambda i: (-_used(occ, i), by_id[i]["code"] if i in by_id else ""))
        return pick(ids)

    if typ == "same_category":
        cat = ctx.get("category_l1") or ""
        if not cat:
            return []
        pids = {r["id"] for r in db.execute(
            "SELECT id FROM product WHERE category_l1 = ? OR category = ?", (cat, cat)
        ).fetchall()}
        ids = [lid for lid, rows in occ["stock"].items()
               if any(r["product_id"] in pids for r in rows)]
        return pick(sorted(ids, key=lambda i: by_id[i]["code"] if i in by_id else ""))

    if typ == "last_putaway":
        r = db.execute(
            "SELECT to_location_id FROM batch_location "
            "WHERE product_id = ? ORDER BY id DESC LIMIT 1", (info["product_id"],)
        ).fetchone()
        return pick([r["to_location_id"]]) if r else []

    if typ == "batch_location":
        ib = info["internal_batch_no"]
        if not ib:
            return []
        ids = [r["to_location_id"] for r in db.execute(
            "SELECT DISTINCT to_location_id FROM batch_location "
            "WHERE batch_no = ? OR ref_id = ?", (ib, info["asn_id"])
        ).fetchall()]
        return pick(ids)

    return []


# ── 8 步硬约束过滤（每项 enabled 缺省 False = 不检查） ───────────────────
def _reject(key, message, loc):
    return False, C.PUTAWAY_REJECT_CODES[key], message, {
        "location_id": loc["id"], "code": loc["code"],
        "reason_code": C.PUTAWAY_REJECT_CODES[key],
        "message": message, "reason": message,
    }


def filter_location(db, loc, info, limits, occ, qty, for_post=False):
    """按 §9.4 流水线顺序执行已启用的检查项；返回 (ok, code, message, rejected_row)。

    检查项三态全部来自规则行配置（代码不写死任何业务红线）：

    * ``enabled=true``       → 推荐阶段排除候选 **且** 上架过账拦截（enforce）
    * ``recommend_only=true`` → 只在推荐阶段排除候选，过账放行（M20 混放口径）
    * 两者皆假              → 不检查

    ``for_post=True`` 由 ``validate_location()`` 传入；推荐阶段走默认的 False。
    """
    limits = limits or {}
    ctx = info["ctx"]
    held = occ["stock"].get(loc["id"], [])
    used = _used(occ, loc["id"])
    mix = _effective_mix(loc)

    def _active(cfg):
        return bool(cfg.get("enabled")) or (
            not for_post and bool(cfg.get("recommend_only")))

    cfg = limits.get("loc_status") or {}
    if _active(cfg):
        allowed = cfg.get("loc_types") or ["storage", "picking"]
        if loc["status"] != "active" or loc["loc_type"] not in allowed:
            return _reject("loc_status", "库位停用或类型不符", loc)
        if cfg.get("warehouse_id") and loc["warehouse_id"] != cfg["warehouse_id"]:
            return _reject("loc_status", "库位不属于规则指定仓库", loc)

    cfg = limits.get("storage_class") or {}
    if _active(cfg):
        zones = cfg.get("allowed_zone_codes") or []
        temps = list(cfg.get("temp_classes") or [])
        if cfg.get("temp_class_from_product"):
            need = _temp_required(info["product"])
            if need and need not in temps:
                temps.append(need)
        if zones and (loc["zone_code"] or "") not in zones:
            return _reject("storage_class",
                           f"储区 {loc['zone_code'] or '未分配'} 不在规则允许范围", loc)
        if temps and (loc["zone_temp"] or "常温") not in temps:
            shown = temps[0] if len(temps) == 1 else "/".join(temps)
            return _reject("storage_class", f"温区不匹配（需 {shown}）", loc)
        if cfg.get("require_empty") and used > 0:
            return _reject("storage_class", "该规则要求空库位", loc)

    cfg = limits.get("quality_state") or {}
    if _active(cfg):
        gp = cfg.get("allowed_goods_property") or []
        ss = cfg.get("allowed_stock_status") or []
        if gp and ctx.get("goods_property") not in gp:
            return _reject("quality_state",
                           f"货品属性 {ctx.get('goods_property')} 不允许上架", loc)
        if ss and str(info["extra"].get("stock_status") or "") not in ss:
            return _reject("quality_state", "库存状态不允许上架", loc)
        if cfg.get("forbid_mixed_quality"):
            for r in db.execute(
                "SELECT DISTINCT goods_property FROM stock_batch "
                "WHERE location_id = ? AND qty > 0", (loc["id"],)
            ).fetchall():
                if (r["goods_property"] or "") != ctx.get("goods_property"):
                    return _reject("quality_state", "该货位已存不同质量状态库存", loc)

    cfg = limits.get("capacity") or {}
    if _active(cfg):
        cap = float(loc["capacity"] or 0)
        if cap > 0 and used + float(qty or 0) > cap + 1e-9:
            return _reject("capacity", f"剩余容量不足（已用 {used}，容量 {cap}）", loc)

    other_product = [r for r in held if r["product_id"] != info["product_id"]]
    other_owner = [r for r in held if r["owner_id"] != info["owner_id"]]
    other_batches = {b for b in occ["batches"].get(loc["id"], set())
                     if b and b != info["batch_no"]}

    cfg = limits.get("mix_product") or {}
    if _active(cfg):
        if other_product and not mix["allow_mix_product"]:
            return _reject("mix_product", C.PUTAWAY_REJECT_LABELS["MIX_PRODUCT_FORBIDDEN"], loc)
        max_sku = int(cfg.get("max_sku") or 0)
        if max_sku and len({r["product_id"] for r in held} | {info["product_id"]}) > max_sku:
            return _reject("mix_product", f"该货位 SKU 数超过上限 {max_sku}", loc)

    cfg = limits.get("mix_batch") or {}
    if _active(cfg):
        by = str(cfg.get("by") or "batch_no")
        pool = occ["batchkeys"].get(loc["id"], set()) if by == "batch_key" else other_batches
        if pool and not mix["allow_mix_batch"]:
            return _reject("mix_batch", C.PUTAWAY_REJECT_LABELS["MIX_BATCH_FORBIDDEN"], loc)

    cfg = limits.get("mix_owner") or {}
    if _active(cfg) and other_owner and not mix["allow_mix_owner"]:
        return _reject("mix_owner", C.PUTAWAY_REJECT_LABELS["MIX_OWNER_FORBIDDEN"], loc)

    cfg = limits.get("designated_customer") or {}
    if _active(cfg) and not cfg.get("allow_mixed"):
        existing = occ["designated"].get(loc["id"], set())
        want = int(info["designated_customer_id"] or 0)
        if existing and (want == 0 or want not in existing or len(existing) > 1):
            return _reject("designated_customer",
                           C.PUTAWAY_REJECT_LABELS["DESIGNATED_CUSTOMER_CONFLICT"], loc)

    return True, "", "", None


# ── 9 条评分（只用于同一步骤内候选排序） ─────────────────────────────────
def _default_weights():
    """v3.3 §10.4 九条评分的**建议权重**（供 M26 编辑器展示）。

    默认不叠加到推荐结果上：规则行 score 显式配置才启用，避免 M25 上线
    就改变 M20 已验证的排序口径（老测试与演示数据都依赖它）。
    """
    return {k: v[1] for k, v in C.PUTAWAY_SCORE_RULES.items()}


def _extension_hit(rule, db, loc, info, occ, used):
    """R07–R15 命中判定；取不到数据的规则一律返回 False（不臆造分数）。"""
    held = occ["stock"].get(loc["id"], [])
    cap = float(loc["capacity"] or 0)
    bnos = occ["batches"].get(loc["id"], set())
    if rule == "R07":
        return bool(info["batch_key"] and
                    info["batch_key"] in occ["batchkeys"].get(loc["id"], set()))
    if rule == "R08":
        return bool((info["batch_no"] and info["batch_no"] in bnos)
                    or (info["internal_batch_no"] and info["internal_batch_no"] in bnos))
    if rule == "R09":
        return any(r["product_id"] == info["product_id"] for r in held)
    if rule == "R10":
        cat = info["ctx"].get("category_l1") or ""
        if not cat:
            return False
        pids = {r["id"] for r in db.execute(
            "SELECT id FROM product WHERE category_l1 = ? OR category = ?", (cat, cat)
        ).fetchall()}
        return any(r["product_id"] in pids and r["product_id"] != info["product_id"]
                   for r in held)
    if rule == "R11":
        return any(r["product_id"] == info["product_id"] for r in held) and bool(
            info["extra"].get("expiry_date"))
    # R12 ABC 周转需近 30 天出库频次聚合，当前无缓存字段 → 不臆造分数
    if rule == "R13":
        return cap > 0 and used + info["qty"] <= cap
    if rule == "R14":
        return bool(loc.get("aisle") or loc.get("level"))
    if rule == "R15":
        return 0 < used < (cap * 0.5 if cap else 0)
    return False


def score_location(db, loc, info, occ, weights=None):
    """M20 六项打分（默认口径）+ v3.3 R07–R15 可配置扩展。

    ``reasons`` 用 M20 的英文理由码，``breakdown`` 用中文键，保持前端与
    既有测试兼容；只有规则行 ``score`` 显式配置权重时才叠加扩展评分。
    """
    held = occ["stock"].get(loc["id"], [])
    used = _used(occ, loc["id"])
    cap = float(loc["capacity"] or 0)
    prod = info["product"]
    score, breakdown, reasons = 0.0, {}, []

    def add(code, label, pts, hit):
        nonlocal score
        if hit:
            score += pts
            breakdown[label] = pts
            reasons.append(code)

    add(C.PUTAWAY_R_ZONE, "推荐储区", 60,
        bool(prod.get("default_zone_id") and loc["zone_id"] == prod["default_zone_id"]))
    add(C.PUTAWAY_R_SAME_PRODUCT, "同产品聚拢", 30,
        any(r["product_id"] == info["product_id"] for r in held))
    add(C.PUTAWAY_R_SAME_BATCH, "同批号聚拢", 20,
        bool(info["batch_no"] and info["batch_no"] in occ["batches"].get(loc["id"], set())))
    add(C.PUTAWAY_R_EMPTY, "空库位", 10, used <= 0)
    add(C.PUTAWAY_R_CAPACITY, "容量贴合", 5, cap > 0 and used + info["qty"] <= cap)
    for rule, weight in (weights or {}).items():
        if rule in C.PUTAWAY_SCORE_RULES and weight and _extension_hit(
                rule, db, loc, info, occ, used):
            pts = round(float(weight), 2)
            score += pts
            breakdown[rule] = pts
            reasons.append(rule)
    return round(score, 2), breakdown, reasons


# ── 推荐主入口 ─────────────────────────────────────────────────────────
def recommend(db, asn_id, line_id, qty=None, top_n=5):
    """规则驱动推荐；返回值兼容 M20 前端字段，并附 M25 可解释信息。"""
    info = build_context(db, asn_id, line_id, qty)
    ctx = info["ctx"]
    s = resolve_rule_set(db, info)
    rule = match_rule(db, s["id"], info) if s else None
    occ = _occupancy(db)
    q = info["qty"]

    binds = {r["location_id"]: r["priority"] for r in db.execute(
        "SELECT location_id, priority FROM product_location WHERE product_id = ?",
        (info["product_id"],),
    ).fetchall()}

    base = {
        "asn_id": asn_id, "line_id": line_id,
        "product_id": info["product_id"], "sku": info["product"]["sku"],
        "product_name": info["product"]["name"], "owner_id": info["owner_id"],
        "batch_no": info["batch_no"], "qty": q,
        "default_zone_id": info["product"].get("default_zone_id"),
        "bound_location_count": len(binds),
        "engine": "rules",
        "rule_set": s["code"] if s else "",
        "rule_set_id": s["id"] if s else None,
        "hit_rule": rule["id"] if rule else None,
        "hit_priority": rule["priority"] if rule else None,
        "hit_condition": _j(rule["condition"], dict) if rule else {},
        "hit_step": None, "step_name": "", "fallback": False,
        "top_n": top_n,
    }

    steps = _j(rule["steps"], list) if rule else []
    limits = _j(rule["limits"], dict) if rule else {}
    weights = _j(rule["score"], dict) if rule else {}
    rejected, top, used_ids = [], [], set()
    hit_step = None

    for idx, step in enumerate(steps, 1):
        candidates = _step_candidates(db, step, info, occ)
        passed = []
        for loc in candidates:
            if loc["id"] in used_ids:
                continue
            ok, code, msg, row = filter_location(db, loc, info, limits, occ, q)
            if not ok:
                if row:
                    rejected.append(dict(row, hit_step=idx))
                continue
            s_score, breakdown, reasons = score_location(db, loc, info, occ, weights)
            if loc["id"] in binds:
                pts = 100 + int(binds[loc["id"]] or 0)
                s_score += pts
                reasons.append(C.PUTAWAY_R_BIND)
                breakdown["绑定货位"] = pts
            passed.append({
                "location_id": loc["id"], "code": loc["code"], "name": loc["name"],
                "loc_type": loc["loc_type"], "zone_id": loc["zone_id"],
                "zone_code": loc["zone_code"] or "", "zone_name": loc["zone_name"] or "",
                "warehouse_id": loc["warehouse_id"],
                "warehouse_code": loc["warehouse_code"] or "",
                "capacity": float(loc["capacity"] or 0), "used_qty": _used(occ, loc["id"]),
                "score": round(s_score, 2), "breakdown": breakdown,
                "reasons": reasons,
                "reason_labels": [C.PUTAWAY_REASON_LABELS.get(r, r) for r in reasons],
                "effective_mix": _effective_mix(loc),
            })
        if passed:
            passed.sort(key=lambda x: (-x["score"], x["code"]))
            top = passed[:top_n]
            used_ids.update(x["location_id"] for x in passed)
            hit_step = idx
            break

    if hit_step is None and rule and rule["priority"] == C.RULE_FALLBACK_PRIORITY:
        base["fallback"] = True

    base.update({
        "recommendations": top,
        "rejected": rejected,
        "candidate_count": len(_loc_rows(db)),
        "hit_step": hit_step,
        "step_name": (steps[hit_step - 1].get("type") if hit_step else ""),
        "error_code": "NO_CANDIDATE" if not top else "",
        "error_message": C.PUTAWAY_REJECT_LABELS["NO_CANDIDATE"] if not top else "",
    })
    base["recommendation_id"] = log_recommendation(
        db, info, base, action=C.PUTAWAY_RECOMMEND_ACTIONS[0])
    return base


def log_recommendation(db, info, result, action="preview", operator="",
                       chosen_location_id=None, override_reason=""):
    """写推荐日志（命中率 / 驳回率的数据来源），不参与过账。"""
    cur = db.execute(
        """INSERT INTO putaway_recommendation
           (asn_id, asn_line_id, rule_set_id, rule_id, hit_priority, hit_step,
            top_json, chosen_location_id, action, operator, override_reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (info["asn_id"], info["line_id"], result.get("rule_set_id"),
         result.get("hit_rule"), result.get("hit_priority"), result.get("hit_step"),
         json.dumps(result.get("recommendations") or [], ensure_ascii=False),
         chosen_location_id, action, operator, override_reason),
    )
    return cur.lastrowid


def validate_location(db, asn_id, line_id, to_location_id, qty):
    """上架过账前的硬约束校验入口。返回 (ok, reason_code, message)。

    没有启用规则 / 规则行 limits 未启用任何检查项 → 直接放行（v3.3 3B：没配就不拦）。
    """
    info = build_context(db, asn_id, line_id, qty)
    s = resolve_rule_set(db, info)
    rule = match_rule(db, s["id"], info) if s else None
    if not rule:
        return True, "", ""
    rows = _loc_rows(db, "WHERE l.id = ?", (to_location_id,))
    if not rows:
        return True, "", ""          # 库位不存在由调用方 _check_location 负责报错
    ok, code, msg, _ = filter_location(
        db, rows[0], info, _j(rule["limits"], dict), _occupancy(db), info["qty"],
        for_post=True)   # 只执行 enforce 级检查项；recommend_only 不参与过账拦截
    return ok, code, msg


# ── 配置 CRUD（M26 编辑器使用；M25 先落端点与校验） ─────────────────────
def list_sets(db):
    return _list(db.execute(
        "SELECT * FROM putaway_rule_set ORDER BY id").fetchall())


def save_set(db, payload):
    code = str(payload.get("code") or "").strip()
    name = str(payload.get("name") or "").strip()
    if not code or not name:
        raise RuleError("策略包编码与名称必填")
    status = str(payload.get("status") or C.RULE_STATUS_ACTIVE)
    if status not in C.RULE_STATUSES:
        raise RuleError(f"status 非法: {status}")
    values = (code, name, payload.get("warehouse_id") or None,
              payload.get("owner_id") or None, status, str(payload.get("note") or ""))
    if payload.get("id"):
        db.execute(
            "UPDATE putaway_rule_set SET code=?, name=?, warehouse_id=?, owner_id=?, "
            "status=?, note=? WHERE id=?", values + (int(payload["id"]),))
        return int(payload["id"])
    cur = db.execute(
        "INSERT INTO putaway_rule_set (code, name, warehouse_id, owner_id, status, note) "
        "VALUES (?, ?, ?, ?, ?, ?)", values)
    return cur.lastrowid


def list_rule_rows(db, set_id=None, rule_type=None):
    sql, params = "SELECT * FROM putaway_rule WHERE 1=1", []
    if set_id:
        sql += " AND rule_set_id = ?"
        params.append(int(set_id))
    if rule_type:
        sql += " AND rule_type = ?"
        params.append(rule_type)
    sql += " ORDER BY rule_set_id, priority, id"
    return _list(db.execute(sql, params).fetchall())


def _json_field(payload, key, kind, label):
    raw = payload.get(key)
    if raw in (None, ""):
        return "{}" if kind is dict else "[]"
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raise RuleError(f"{label} 不是合法 JSON")
    if not isinstance(raw, kind):
        raise RuleError(f"{label} 必须是 {'对象' if kind is dict else '数组'}")
    return json.dumps(raw, ensure_ascii=False)


def save_rule(db, payload):
    rule_type = str(payload.get("rule_type") or C.RULE_TYPE_PUTAWAY)
    if rule_type not in C.RULE_TYPES:
        raise RuleError(f"rule_type 非法: {rule_type}")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise RuleError("规则行名称必填")
    try:
        priority = int(payload.get("priority") or 10)
        set_id = int(payload.get("rule_set_id") or 0)
    except (TypeError, ValueError):
        raise RuleError("priority / rule_set_id 必须为整数")
    if not set_id:
        raise RuleError("rule_set_id 必填")
    condition = _json_field(payload, "condition", dict, "condition 条件组")
    steps = _json_field(payload, "steps", list, "steps fallback 链")
    limits = _json_field(payload, "limits", dict, "limits 硬约束")
    score = _json_field(payload, "score", dict, "score 评分权重")
    unknown = [k for k in _j(condition, dict) if k not in C.PUTAWAY_CONDITION_DIMS]
    if unknown:
        raise RuleError(f"条件组含未知维度: {unknown}")
    bad = [s.get("type") for s in _j(steps, list)
           if isinstance(s, dict) and s.get("type") not in C.PUTAWAY_STEP_TYPES]
    if bad:
        raise RuleError(f"fallback 链含未知寻址类型: {bad}")
    values = (set_id, rule_type, priority, name, condition, steps, limits, score,
              1 if payload.get("enabled", 1) else 0, str(payload.get("note") or ""))
    if payload.get("id"):
        db.execute(
            "UPDATE putaway_rule SET rule_set_id=?, rule_type=?, priority=?, name=?, "
            "condition=?, steps=?, limits=?, score=?, enabled=?, note=? WHERE id=?",
            values + (int(payload["id"]),))
        return int(payload["id"])
    cur = db.execute(
        "INSERT INTO putaway_rule (rule_set_id, rule_type, priority, name, "
        "condition, steps, limits, score, enabled, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
    return cur.lastrowid


def delete_rule(db, rule_id):
    db.execute("DELETE FROM putaway_rule WHERE id = ?", (int(rule_id),))


def rule_types():
    """M26 编辑器的数据驱动字典。"""
    return {
        "rule_types": [{"value": x, "label": "上架推荐" if x == C.RULE_TYPE_PUTAWAY
                        else "出库分配"} for x in C.RULE_TYPES],
        "condition_dims": [{"value": x, "label": C.PUTAWAY_CONDITION_LABELS[x]}
                           for x in C.PUTAWAY_CONDITION_DIMS],
        "step_types": [{"value": x, "label": C.PUTAWAY_STEP_LABELS[x]}
                       for x in C.PUTAWAY_STEP_TYPES],
        "limit_keys": [{"value": x, "reason_code": C.PUTAWAY_REJECT_CODES[x],
                        "label": C.PUTAWAY_REJECT_LABELS[C.PUTAWAY_REJECT_CODES[x]]}
                       for x in C.PUTAWAY_LIMIT_KEYS],
        "score_rules": [{"value": k, "label": v[0], "weight": v[1]}
                        for k, v in C.PUTAWAY_SCORE_RULES.items()],
    }


# ── 种子规则（v3.3 §10.3，只在策略包首次创建时插入，用户删行后重启不复活） ──
def _limits(**kw):
    out = {}
    for key in C.PUTAWAY_LIMIT_KEYS:
        out[key] = dict(kw.get(key) or {}, enabled=bool((kw.get(key) or {}).get("enabled")))
    return out


def seed_rules(conn):
    """11 行预置规则，初期只启用 7 行核心路径（5/8/10/20/25/50/999）。

    种子 storages 用**实际存在的储区编码**（ZONE-COLD / ZONE-NG / ZONE-SHARED），
    不引用设计 §10.3 的占位 code（COLD-01 / HAZ-01 / …），避免 fallback 全空。
    混放 / 温区等更严的检查项在种子里**配置但默认 enabled=false**，由 M26 编辑器
    一键开启；这既满足「拦截内容全部来自配置」，又不会让既有演示数据大面积被拒。
    """
    row = conn.execute("SELECT id FROM putaway_rule_set WHERE code = ?",
                       (C.RULE_SET_DEFAULT_CODE,)).fetchone()
    if row:
        # init_db() 是裸连接，row_factory 未必是 sqlite3.Row，一律按位置取值
        return row[0]
    cur = conn.execute(
        "INSERT INTO putaway_rule_set (code, name, warehouse_id, owner_id, status, note) "
        "VALUES (?, ?, NULL, NULL, ?, ?)",
        (C.RULE_SET_DEFAULT_CODE, "系统默认上架策略包", C.RULE_STATUS_ACTIVE,
         "M25 种子，可在 M26 规则配置页增删改；删掉规则行即不再拦截"),
    )
    sid = cur.lastrowid

    # 常规行把 M20 简版的硬过滤**搬进配置**，并保留 M20 的推荐口径：
    # 三类混放在 M20 只在*推荐阶段排除候选*，上架过账从不拦截（用户可手工绕过推荐）。
    # 若 M25 上线就把它们设成 enforce，既有演示与老单据（同一货位放不同货品 /
    # 不同货主）会大面积 400，同时 M20 已验证的推荐排序也会随之改变。
    # 因此种子对混放用 ``recommend_only=true``（推荐排除、过账放行）；M26 编辑器
    # 把 ``enabled`` 打开即升级为过账硬拦截。「库位状态 / 温区 / 容量」则是
    # enforce 级，两处都生效。全部强度来自配置，代码零 hardcoded 红线。
    normal = _limits(
        loc_status={"enabled": True, "loc_types": ["storage", "picking"]},
        storage_class={"enabled": True, "temp_class_from_product": True,
                       "allowed_zone_codes": [], "temp_classes": []},
        capacity={"enabled": True},
        quality_state={"enabled": False, "allowed_goods_property": ["合格", ""]},
        mix_product={"enabled": False, "recommend_only": True, "max_sku": 0},
        # M21 起 stock_batch 明细层本身就支持同库位多批次（M22 按批号盘点、
        # M23 专属与通用分明细都依赖它），故批号混放连推荐阶段也不排除。
        mix_batch={"enabled": False, "by": "batch_no"},
        mix_owner={"enabled": False, "recommend_only": True},
        designated_customer={"enabled": False, "allow_mixed": False},
    )
    isolated = _limits(
        loc_status={"enabled": True, "loc_types": ["storage", "picking"]},
        storage_class={"enabled": False, "require_empty": True},
        capacity={"enabled": True},
        quality_state={"enabled": False, "forbid_mixed_quality": True},
        mix_product={"enabled": False}, mix_batch={"enabled": False},
        mix_owner={"enabled": False}, designated_customer={"enabled": False},
    )
    cold = dict(isolated)
    cold["storage_class"] = {
        "enabled": True, "allowed_zone_codes": ["ZONE-COLD"],
        "temp_classes": ["冷藏", "冷冻"], "require_empty": False,
    }
    hazard = dict(isolated)
    hazard["storage_class"] = {
        "enabled": True, "allowed_zone_codes": ["ZONE-NG"],
        "temp_classes": [], "require_empty": False,
    }

    steps = lambda *items: [dict(x) for x in items]  # noqa: E731
    catalog = [
        (5, "冷链分流", {"is_cold_chain": 1},
         steps({"type": "zone_by_code", "zone_code": "ZONE-COLD"},
               {"type": "empty_in_zone", "zone_code": "ZONE-COLD"}), cold, 1),
        (8, "危险分流", {"is_hazard": 1},
         steps({"type": "zone_by_code", "zone_code": "ZONE-NG"},
               {"type": "empty_in_zone", "zone_code": "ZONE-NG"}), hazard, 1),
        (10, "不合格隔离", {"goods_property": "不合格"},
         steps({"type": "zone_by_code", "zone_code": "ZONE-NG"},
               {"type": "empty_in_zone", "zone_code": "ZONE-NG"}), isolated, 1),
        (20, "虚拟货位", {"is_virtual": 1},
         steps({"type": "empty_in_zone", "zone_code": "ZONE-SHARED"}), isolated, 1),
        (25, "直调暂存", {"is_direct": 1},
         steps({"type": "fixed_location", "location_code": "XDOCK-01"},
               {"type": "empty_in_zone"}), isolated, 1),
        (30, "待检冻结隔离", {"goods_property": ["待检", "冻结", "质检"]},
         steps({"type": "zone_by_code", "zone_code": "ZONE-NG"}), isolated, 0),
        (35, "外部单据类别分流", {"external_doc_type": "EDI"},
         steps({"type": "zone_by_code", "zone_code": "ZONE-SHARED"}), isolated, 0),
        (40, "销退良品回流", {"doc_type": "销退", "goods_property": "合格"},
         steps({"type": "batch_location"}, {"type": "same_product"},
               {"type": "empty_in_zone"}), isolated, 0),
        (50, "常规上架（默认）",
         {"is_virtual": 0, "is_direct": 0, "is_cold_chain": 0,
          "is_hazard": 0, "goods_property": "合格"},
         steps({"type": "all_active"},          # M20 兼容：全库位一起打分
               {"type": "zone_by_product"},     # 删掉首步即启用 v3.3 递降 fallback
               {"type": "same_product"},
               {"type": "same_category", "level": "category_l1"},
               {"type": "empty_in_zone"}), normal, 1),
        (60, "A 类黄金拣货位", {"abc_class": "A"},
         steps({"type": "same_product", "order": "occupied_first"},
               {"type": "empty_in_zone"}), normal, 0),
        (999, "兜底异常区", {},
         steps({"type": "empty_in_zone"},
               {"type": "zone_by_code", "zone_code": "ZONE-SHARED"}), isolated, 1),
    ]
    for priority, name, cond, st, lim, enabled in catalog:
        conn.execute(
            """INSERT INTO putaway_rule
               (rule_set_id, rule_type, priority, name, condition, steps,
                limits, score, enabled, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)""",
            (sid, C.RULE_TYPE_PUTAWAY, priority, name,
             json.dumps(cond, ensure_ascii=False),
             json.dumps(st, ensure_ascii=False),
             json.dumps(lim, ensure_ascii=False), enabled,
             "M25 种子规则；limits 检查项可在 M26 编辑器启用"),
        )
    return sid
