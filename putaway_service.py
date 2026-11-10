"""简版上架推荐引擎（M20 阶段 A）。

优先级（硬过滤 → 打分）：
  硬过滤  启用状态 / 目标位类型 / 温区匹配 / 剩余容量 / 三类混放策略
  打分    货品绑定货位 > 货品推荐储区 > 同产品聚拢 > 同批号聚拢 > 空库位 > 容量贴合

混放生效值：库位自身非 NULL 优先，其次继承储区，最后默认禁止（保守口径）。
本版不做条件组 / fallback 链 / 权重配置，那属于 M25。
"""
import json

import constants as C


def _effective_mix(loc, zone_vals):
    out = {}
    for f in C.MIX_FIELDS:
        own = loc[f]
        val = own if own is not None else zone_vals.get(f)
        out[f] = int(val or 0)
    return out


def _temp_required(product):
    """从货品 temp_range 粗判温区要求；无要求返回 None（不限制）。"""
    s = str(product["temp_range"] or "")
    for t in ("冷冻", "冷藏", "阴凉"):
        if t in s:
            return t
    return None


def _legacy_recommend(db, asn_id, line_id, qty=None, top_n=5):
    """M20 简版推荐：仅在「零启用规则」时作为兜底，不再承担任何强制拦截。"""
    asn = db.execute("SELECT * FROM asn WHERE id = ?", (asn_id,)).fetchone()
    if asn is None:
        raise ValueError("ASN 不存在")
    line = db.execute(
        "SELECT * FROM asn_line WHERE id = ? AND asn_id = ?", (line_id, asn_id)
    ).fetchone()
    if line is None:
        raise ValueError("行不存在或不属于该 ASN")
    product = db.execute("SELECT * FROM product WHERE id = ?", (line["product_id"],)).fetchone()
    if product is None:
        raise ValueError("货品不存在")

    try:
        extra = json.loads(line["extra"] or "{}")
    except (TypeError, ValueError):
        extra = {}
    batch_no = str(extra.get("batch_no") or "").strip()

    # 货品绑定货位（含优先级）
    binds = {
        r["location_id"]: r["priority"]
        for r in db.execute(
            "SELECT location_id, priority FROM product_location WHERE product_id = ?",
            (product["id"],),
        ).fetchall()
    }

    # 各货位已有库存（按 产品×货主 聚合，用于混放判断与聚拢打分）
    stock_by_loc = {}
    for r in db.execute(
        "SELECT location_id, product_id, owner_id, SUM(qty) AS q FROM stock "
        "WHERE qty > 0 GROUP BY location_id, product_id, owner_id"
    ).fetchall():
        stock_by_loc.setdefault(r["location_id"], []).append(r)

    # 各货位出现过的批号（简版：从批次流水 info 快照提取）。
    # 只有在流水里明确出现「不同批号」时才算混批，不能因为「有库存但查不到批次」
    # 就一刀切拒绝——那样同产品聚拢与空库位排序都会失效。
    batch_by_loc = {}
    for r in db.execute("SELECT location_id, info FROM batch_receipt").fetchall():
        try:
            info = json.loads(r["info"] or "{}")
        except (TypeError, ValueError):
            info = {}
        bn = str(info.get("batch_no") or "").strip()
        if bn:
            batch_by_loc.setdefault(r["location_id"], set()).add(bn)
    batch_locs = {
        lid for lid, bns in batch_by_loc.items() if batch_no and batch_no in bns
    }

    rows = db.execute(
        """
        SELECT l.*, z.name AS zone_name, z.temp_class AS zone_temp,
               z.allow_mix_product AS z_mp, z.allow_mix_batch AS z_mb,
               z.allow_mix_owner AS z_mo, w.code AS warehouse_code
        FROM location l
        LEFT JOIN zone z      ON z.id = l.zone_id
        LEFT JOIN warehouse w ON w.id = l.warehouse_id
        WHERE l.status = 'active' AND l.loc_type IN ('storage', 'picking')
        ORDER BY l.code
        """
    ).fetchall()

    need_temp = _temp_required(product)
    accepted, rejected = [], []
    q = float(qty or 0)

    for loc in rows:
        lid = loc["id"]
        zone_vals = {
            "allow_mix_product": loc["z_mp"],
            "allow_mix_batch": loc["z_mb"],
            "allow_mix_owner": loc["z_mo"],
        }
        mix = _effective_mix(loc, zone_vals)
        base = {
            "location_id": lid, "code": loc["code"], "name": loc["name"],
            "loc_type": loc["loc_type"], "zone_id": loc["zone_id"],
            "zone_name": loc["zone_name"] or "",
            "warehouse_id": loc["warehouse_id"],
            "warehouse_code": loc["warehouse_code"] or "",
            "capacity": float(loc["capacity"] or 0),
        }
        held = stock_by_loc.get(lid, [])
        used = sum(float(r["q"] or 0) for r in held)

        # ── 硬过滤 ──
        if need_temp and (loc["zone_temp"] or "常温") != need_temp:
            rejected.append(dict(base, reason=f"温区不匹配（需 {need_temp}）"))
            continue
        if base["capacity"] > 0 and used + q > base["capacity"]:
            rejected.append(dict(base, reason="剩余容量不足"))
            continue
        other_product = [r for r in held if r["product_id"] != product["id"]]
        other_owner = [r for r in held if r["owner_id"] != asn["owner_id"]]
        other_batch_bns = (
            {b for b in batch_by_loc.get(lid, set()) if b != batch_no}
            if batch_no else set()
        )
        if other_product and not mix["allow_mix_product"]:
            rejected.append(dict(base, reason="该货位禁止产品混放"))
            continue
        if other_owner and not mix["allow_mix_owner"]:
            rejected.append(dict(base, reason="该货位禁止货主混放"))
            continue
        if other_batch_bns and not mix["allow_mix_batch"]:
            rejected.append(dict(base, reason="该货位禁止批号混放"))
            continue

        # ── 打分 ──
        score, reasons, breakdown = 0, [], {}
        if lid in binds:
            s = 100 + int(binds[lid] or 0)
            score += s
            reasons.append(C.PUTAWAY_R_BIND)
            breakdown["绑定货位"] = s
        if product["default_zone_id"] and loc["zone_id"] == product["default_zone_id"]:
            score += 60
            reasons.append(C.PUTAWAY_R_ZONE)
            breakdown["推荐储区"] = 60
        if any(r["product_id"] == product["id"] for r in held):
            score += 30
            reasons.append(C.PUTAWAY_R_SAME_PRODUCT)
            breakdown["同产品聚拢"] = 30
        if lid in batch_locs:
            score += 20
            reasons.append(C.PUTAWAY_R_SAME_BATCH)
            breakdown["同批号聚拢"] = 20
        if used == 0:
            score += 10
            reasons.append(C.PUTAWAY_R_EMPTY)
            breakdown["空库位"] = 10
        if base["capacity"] > 0 and used + q <= base["capacity"]:
            score += 5
            reasons.append(C.PUTAWAY_R_CAPACITY)
            breakdown["容量贴合"] = 5
        accepted.append(dict(base, score=score, used_qty=used,
                             reasons=reasons,
                             reason_labels=[C.PUTAWAY_REASON_LABELS[r] for r in reasons],
                             breakdown=breakdown, effective_mix=mix))

    accepted.sort(key=lambda x: (-x["score"], x["code"]))
    return {
        "asn_id": asn_id, "line_id": line_id,
        "product_id": product["id"], "sku": product["sku"],
        "product_name": product["name"], "owner_id": asn["owner_id"],
        "batch_no": batch_no, "qty": q,
        "default_zone_id": product["default_zone_id"],
        "bound_location_count": len(binds),
        # 与 M25 规则引擎分支保持同一可解释字段；前端据此区分引擎，不参与排序。
        "engine": "legacy",
        "recommendations": accepted[:top_n],
        "rejected": rejected,
        "candidate_count": len(rows),
    }


def recommend(db, asn_id, line_id, qty=None, top_n=5):
    """M25 分发：有启用规则 → 规则引擎；零配置 → 沿用 M20 简版推荐。

    设计 §12「无策略时沿用现有行为」与 v3.3 3B「没配规则就不拦」在这里合流：
    老库若把种子规则行全删掉，推荐退回简版、过账也不再拦截，代码不二次兜底。
    """
    import putaway_rules as PR
    if PR.has_enabled_rules(db, C.RULE_TYPE_PUTAWAY):
        return PR.recommend(db, asn_id, line_id, qty, top_n)
    return _legacy_recommend(db, asn_id, line_id, qty, top_n)
