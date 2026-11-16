"""M35 出库波次分配 + FEFO 服务层（用户 2026-11-10 3A 定稿）。

职责：把 DN 明细行的待拣数量，按「先专属后通用 + 组内 FEFO」自动分摊到
``stock_batch`` 明细行，真实占用 ``allocated_qty``，并把批次维度写进 ``dn_pick``。

与 M25/M26 的关系（3A 共用表）：

* 出库规则**不另建表**，复用 ``putaway_rule.rule_type='outbound'``；
  ``condition`` 用同一套 13 维条件组、``priority`` 升序命中即停，命中信息写日志与响应，
  便于按规则排查；``steps`` 只解释「候选库位范围」，``score`` 只作可解释权重，
  ``limits`` 不放松 M35 的质量/专属红线（详见 ``docs/OPEN_QUESTIONS.md`` Q02）。
* 零启用 outbound 规则时 ``engine='legacy'``，仍按安全默认分配（FEFO + 专属 +
  不合格/冻结过滤），**不会**因为没配规则就允许不合格库存出库。

三层不变式：分配动作 ``qty_delta=0``，只改 ``allocated_qty``；主账 ``stock.qty``
一次都不 UPDATE；显式传 ``batches=`` 精确落到目标明细行，I1/I2/I3 全部保持。
所有库存写入一律经 ``inventory_service.adjust_stock()``，路由不直写明细层。
"""
import datetime
import math

import constants as C
import putaway_rules as PR
from inventory_service import EPS, BATCH_DIMS, adjust_stock
from reservation_rules import check_customer_reservation

# 流水类型：取消分配是分配动作的反向，qty_delta 恒 0、allocated_delta < 0
MOVEMENT_DN_UNPICK = "dn_unpick"

_DN_SELECT = "SELECT * FROM dn WHERE id = ?"
_LINE_SELECT = """SELECT dl.*, p.sku, p.name AS product_name, p.category_l1, p.category
                  FROM dn_line dl JOIN product p ON p.id = dl.product_id
                  WHERE dl.dn_id = ? ORDER BY dl.id"""


class AllocationError(Exception):
    """分配参数/库存异常，由路由层转 4xx。"""


def _as_int(value):
    if value in (None, "", 0, "0", False):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _j(raw, default):
    """复用 PR 的宽松 JSON 解析（规则行字段可能是字符串或已解析对象）。"""
    import json
    if isinstance(raw, (dict, list)):
        return raw
    if raw in (None, ""):
        return default() if callable(default) else default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default() if callable(default) else default


def next_wave_no(db):
    """轻波次号 ``WV-YYYYMMDD-NNN``；不建波次主表，按当日已有波次数 +1。"""
    day = datetime.date.today().strftime("%Y%m%d")
    like = f"{C.WAVE_PREFIX}-{day}-%"
    n = db.execute(
        "SELECT COUNT(DISTINCT wave_no) AS n FROM outbound_allocation_log "
        "WHERE wave_no LIKE ?", (like,),
    ).fetchone()["n"]
    return f"{C.WAVE_PREFIX}-{day}-{n + 1:03d}"


def build_context(db, dn_id, line_id):
    """构造与上架规则同形的 13 维上下文，供 ``PR.matches()`` / ``PR.match_rule()`` 复用。"""
    dn = db.execute(_DN_SELECT, (dn_id,)).fetchone()
    if dn is None:
        raise AllocationError(f"DN 不存在: id={dn_id}")
    line = db.execute(
        "SELECT * FROM dn_line WHERE id = ? AND dn_id = ?", (line_id, dn_id)
    ).fetchone()
    if line is None:
        raise AllocationError(f"明细行不存在或不属于该 DN: id={line_id}")
    product = db.execute("SELECT * FROM product WHERE id = ?",
                         (line["product_id"],)).fetchone()
    if product is None:
        raise AllocationError("货品不存在")
    d, p = dict(dn), dict(product)
    ctx = {
        "doc_type": str(d.get("doc_type") or "销售"),
        "goods_property": "合格",           # 出库单不带货品属性，按合格品申请
        "is_virtual": int(d.get("is_virtual") or 0),
        "is_direct": int(d.get("is_direct") or 0),
        "is_cold_chain": int(d.get("is_cold_chain") or 0),
        "is_hazard": int(d.get("is_hazard") or 0),
        "external_doc_type": str(d.get("external_doc_type") or ""),
        "abc_class": "",                    # 暂无 ABC 数据源（M26 同一边界）
        "owner_id": d.get("owner_id"),
        "is_designated": 1 if d.get("customer_id") else 0,
        "zone_id": None,
        "product_id": line["product_id"],
        "category_l1": str(p.get("category_l1") or p.get("category") or ""),
    }
    return {
        "ctx": ctx, "dn": d, "line": dict(line), "product": p,
        "owner_id": d["owner_id"], "product_id": line["product_id"],
        "customer_id": d.get("customer_id"), "dn_id": dn_id, "line_id": line_id,
        # PR.resolve_rule_set() 读 ctx["asn"]["source_warehouse"]，出库用同形结构兼容
        "asn": {"source_warehouse": d.get("source_warehouse")},
    }


def resolve_rule(db, info, strategy=None):
    """命中 outbound 规则行，返回 ``(rule, set_row, strategy, engine, limits)``。

    零启用规则 → ``engine='legacy'``、``rule=None``；命中规则时 ``engine='rules'``，
    ``limits`` 只用于可解释输出与审计标记，**不放松**质量/专属红线。
    """
    chosen = strategy if strategy in C.OUTBOUND_STRATEGIES else C.OUTBOUND_STRATEGY_OWN_FIRST
    if not PR.has_enabled_rules(db, C.RULE_TYPE_OUTBOUND):
        return None, None, chosen, C.OUTBOUND_ENGINE_LEGACY, {}
    set_row = PR.resolve_rule_set(db, info, C.RULE_TYPE_OUTBOUND)
    if set_row is None:
        return None, None, chosen, C.OUTBOUND_ENGINE_LEGACY, {}
    # match_rule 需要 ctx["ctx"] / ctx["owner_id"] 等同形结构
    rule = PR.match_rule(db, set_row["id"], info, C.RULE_TYPE_OUTBOUND)
    if rule is None:
        return None, set_row, chosen, C.OUTBOUND_ENGINE_LEGACY, {}
    limits = _j(rule["limits"], dict)
    score = _j(rule["score"], dict)
    if not isinstance(limits, dict):
        limits = {}
    if not isinstance(score, dict):
        score = {}
    # score.strategy 是配置化扩展位：只认白名单值，非法值忽略不影响默认
    cfg = score.get("strategy")
    if cfg in C.OUTBOUND_STRATEGIES:
        chosen = cfg
    return rule, set_row, chosen, C.OUTBOUND_ENGINE_RULES, limits


def _step_scope(_db, rule):
    """解释规则行 steps：只认第一个可解释的库位范围步骤，其余忽略（Q02）。

    返回 ``(kind, value)``；``all_active`` / 不认识的 step 一律返回 ``("all", None)``。
    """
    if rule is None:
        return "all", None
    steps = _j(rule["steps"], list)
    for s in steps if isinstance(steps, list) else []:
        if not isinstance(s, dict):
            continue
        kind = str(s.get("type") or "")
        try:
            if kind == "zone_by_code" and s.get("zone_id"):
                return "zone", int(s["zone_id"])
            if kind == "fixed_location" and s.get("location_id"):
                return "location", int(s["location_id"])
        except (TypeError, ValueError):
            return "all", None
        if kind == "all_active":
            return "all", None
    return "all", None


def _skip_reason(row, customer_id):
    """返回跳批原因码；None 表示可分配。质量/专属红线在这里统一收口。"""
    gp = str(row["goods_property"] or "").strip()
    if gp and gp not in C.OUTBOUND_ALLOCATABLE_GOODS:
        return C.OUTBOUND_SKIP_QUALITY
    st = str(row["stock_status"] or "").strip()
    if st in C.OUTBOUND_BLOCKED_STATUSES:
        return C.OUTBOUND_SKIP_STATUS
    if str(row["loc_status"] or "") != "active" or str(row["loc_type"] or "") not in ("storage", "picking"):
        return C.OUTBOUND_SKIP_LOCATION
    verdict = check_customer_reservation(row["designated_customer_id"], customer_id)
    if not verdict["allowed"]:
        return C.OUTBOUND_SKIP_RESERVED
    return None


def _sort_key(row, customer_id, strategy):
    """先专属后通用（默认）或严格 FEFO；空效期永远排最后。"""
    exp = str(row["expiry_date"] or "")
    fefo = (0 if exp else 1, exp, row["location_id"], row["id"])
    if strategy == C.OUTBOUND_STRATEGY_STRICT_FEFO:
        return fefo
    own = 0 if (_as_int(row["designated_customer_id"]) is not None
                and _as_int(row["designated_customer_id"]) == _as_int(customer_id)) else 1
    return (own,) + fefo


def _candidates(db, info, scope):
    """读取可用批次明细快照（可写 dict，供试算在本地递减，不写库）。"""
    sql = """SELECT sb.*, l.code AS location_code, l.status AS loc_status,
                    l.loc_type, l.zone_id,
                    (sb.qty - sb.allocated_qty) AS available
             FROM stock_batch sb JOIN location l ON l.id = sb.location_id
             WHERE sb.owner_id = ? AND sb.product_id = ?
               AND (sb.qty - sb.allocated_qty) > ?"""
    params = [info["owner_id"], info["product_id"], EPS]
    kind, value = scope
    if kind == "zone":
        sql += " AND l.zone_id = ?"
        params.append(value)
    elif kind == "location":
        sql += " AND sb.location_id = ?"
        params.append(value)
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def _batch_dims(row):
    d = {k: row[k] for k in BATCH_DIMS}
    d["production_date"] = row.get("production_date") or ""
    return d


def _log(db, *, action, wave_no, dn_id, line_id=None, row=None, qty=0.0,
         reason_code="", reason="", rule=None, strategy="", engine="",
         operator="", product_id=None, location_id=None):
    r = row or {}
    db.execute(
        """INSERT INTO outbound_allocation_log
           (wave_no, dn_id, line_id, product_id, location_id, batch_no, batch_key,
            designated_customer_id, goods_property, stock_status, expiry_date,
            qty, action, reason_code, reason, rule_id, rule_priority,
            strategy, engine, operator)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (wave_no, dn_id, line_id,
         product_id if product_id is not None else r.get("product_id"),
         location_id if location_id is not None else r.get("location_id"),
         r.get("batch_no") or "", r.get("batch_key") or "",
         r.get("designated_customer_id"), r.get("goods_property") or "",
         r.get("stock_status") or "", r.get("expiry_date") or "",
         float(qty), action, reason_code, reason,
         rule["id"] if rule else None, rule["priority"] if rule else None,
         strategy, engine, operator),
    )


def _validate_dn_ids(db, dn_ids):
    if not isinstance(dn_ids, (list, tuple)) or not dn_ids:
        raise AllocationError("dn_ids 必须为非空数组")
    ids, seen = [], set()
    for raw in dn_ids:
        try:
            n = int(raw)
        except (TypeError, ValueError):
            raise AllocationError(f"dn_ids 含非法值: {raw}")
        if n in seen:
            continue
        seen.add(n)
        ids.append(n)
    out = []
    for n in ids:
        dn = db.execute(_DN_SELECT, (n,)).fetchone()
        if dn is None:
            raise AllocationError(f"DN 不存在: id={n}")
        if dn["status"] in (C.DN_SHIPPED, C.DN_CANCELLED):
            raise AllocationError(f"DN {dn['code']} 状态为 {dn['status']}，不可分配")
        out.append(dict(dn))
    return out


def plan(db, dn_ids, *, line_ids=None, strategy=None, operator="",
         wave_no=None, dry_run=False, apply=False):
    """生成（或执行）一次波次分配。

    ``dry_run=True`` 只读试算：返回与实分配同形的结果，不写任何表。
    ``apply=True`` 才真正占用库存、写 ``dn_pick`` / 日志 / 波次号。
    两者互斥；调用方负责开启 ``BEGIN IMMEDIATE``（apply）或普通只读连接（dry_run）。
    """
    dns = _validate_dn_ids(db, dn_ids)
    chosen_ids = None
    if line_ids:
        try:
            chosen_ids = {int(x) for x in line_ids}
        except (TypeError, ValueError):
            raise AllocationError("line_ids 必须为整数数组")

    wave = (str(wave_no or "").strip() or ("" if dry_run else next_wave_no(db)))
    if wave and len(wave) > C.WAVE_NO_MAXLEN:
        raise AllocationError(f"wave_no 最长 {C.WAVE_NO_MAXLEN} 字符")

    allocations, skipped, shortages, dns_out = [], [], [], []
    # 试算时同一波次内多张 DN 会争同一批次：用本地快照按 (owner, product) 共享余额
    snapshot = {}
    for dn in dns:
        lines = [dict(r) for r in db.execute(_LINE_SELECT, (dn["id"],)).fetchall()]
        dn_alloc, dn_skipped, dn_short = [], [], []
        for line in lines:
            if chosen_ids is not None and line["id"] not in chosen_ids:
                continue
            remaining = float(line["ordered_qty"] - line["picked_qty"])
            if remaining <= EPS:
                continue
            info = build_context(db, dn["id"], line["id"])
            rule, _set, strat, engine, limits = resolve_rule(db, info, strategy)
            scope = _step_scope(db, rule)
            key = (info["owner_id"], info["product_id"])
            if dry_run:
                if key not in snapshot:
                    snapshot[key] = _candidates(db, info, scope)
                rows = snapshot[key]
            else:
                rows = _candidates(db, info, scope)

            ordered = sorted(rows, key=lambda r: _sort_key(r, dn["customer_id"], strat))
            # 跳批留痕与「剩余量」解耦：先全量扫描红线候选，再按排序分配。
            # 否则需求被前面的批次满足后 break，排在后面的不合格/专属批次会静默消失，
            # 排查时看不到「这批为什么没被选中」。
            skip_map = {}
            for row in ordered:
                avail = float(row.get("available", row["qty"] - row["allocated_qty"]))
                if avail <= EPS:
                    continue
                code = _skip_reason(row, dn["customer_id"])
                if code:
                    skip_map[row["id"]] = (code, avail)
            for row in ordered:
                if row["id"] not in skip_map:
                    continue
                code, avail = skip_map[row["id"]]
                rec = {"dn_id": dn["id"], "line_id": line["id"],
                       "location_id": row["location_id"],
                       "location_code": row["location_code"],
                       "batch_no": row["batch_no"], "expiry_date": row["expiry_date"],
                       "available": avail, "reason_code": code,
                       "reason": C.OUTBOUND_SKIP_LABELS.get(code, code)}
                # 同一批次在多行里重复出现时，跳批日志只记一次
                if not any(s["batch_no"] == rec["batch_no"]
                           and s["location_id"] == rec["location_id"]
                           and s["line_id"] == rec["line_id"] for s in dn_skipped):
                    dn_skipped.append(rec)
                    if apply:
                        _log(db, action="skipped", wave_no=wave, dn_id=dn["id"],
                             line_id=line["id"], row=row, qty=avail,
                             reason_code=code, reason=rec["reason"], rule=rule,
                             strategy=strat, engine=engine, operator=operator,
                             product_id=info["product_id"])

            picked_any = False
            for row in ordered:
                if remaining <= EPS:
                    break
                avail = float(row.get("available", row["qty"] - row["allocated_qty"]))
                if avail <= EPS or row["id"] in skip_map:
                    continue
                take = min(avail, remaining)
                dims = _batch_dims(row)
                rec = {"dn_id": dn["id"], "dn_code": dn["code"], "line_id": line["id"],
                       "sku": line["sku"], "product_id": info["product_id"],
                       "location_id": row["location_id"], "location_code": row["location_code"],
                       "batch_no": row["batch_no"], "designated_customer_id": row["designated_customer_id"],
                       "goods_property": row["goods_property"], "stock_status": row["stock_status"],
                       "expiry_date": row["expiry_date"], "qty": take,
                       "engine": engine, "strategy": strat,
                       "rule_id": rule["id"] if rule else None,
                       "rule_priority": rule["priority"] if rule else None,
                       "limits_quality": bool((limits or {}).get("quality_state")),
                       }
                dn_alloc.append(rec)
                remaining -= take
                row["available"] = avail - take
                picked_any = True
                if apply:
                    item = dict(dims, qty=0.0, allocated=take)
                    adjust_stock(db, info["owner_id"], info["product_id"],
                                 row["location_id"], 0, C.MOVEMENT_DN_PICK, dn["id"],
                                 allocated_delta=take, batches=[item],
                                 note=f"DN {dn['code']} 波次 {wave} 自动分配",
                                 operator=operator)
                    db.execute(
                        """INSERT INTO dn_pick
                           (dn_id, line_id, location_id, qty, batch_no, batch_key,
                            internal_batch_no, designated_customer_id, goods_property,
                            stock_status, expiry_date, production_date, wave_no, rule_id)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (dn["id"], line["id"], row["location_id"], take,
                         dims["batch_no"], dims["batch_key"], dims["internal_batch_no"],
                         dims["designated_customer_id"], dims["goods_property"],
                         dims["stock_status"], dims["expiry_date"], dims["production_date"],
                         wave, rule["id"] if rule else None),
                    )
                    db.execute("UPDATE dn_line SET picked_qty = picked_qty + ? WHERE id = ?",
                               (take, line["id"]))
                    _log(db, action="allocated", wave_no=wave, dn_id=dn["id"],
                         line_id=line["id"], row=row, qty=take, rule=rule,
                         strategy=strat, engine=engine, operator=operator,
                         product_id=info["product_id"])
            if remaining > EPS:
                rec = {"dn_id": dn["id"], "dn_code": dn["code"], "line_id": line["id"],
                       "sku": line["sku"], "short_qty": remaining,
                       "reason_code": "SHORTAGE",
                       "reason": C.OUTBOUND_SKIP_LABELS["SHORTAGE"]}
                dn_short.append(rec)
                if apply:
                    _log(db, action="short", wave_no=wave, dn_id=dn["id"],
                         line_id=line["id"], qty=remaining, reason_code="SHORTAGE",
                         reason=rec["reason"], rule=rule, strategy=strat,
                         engine=engine, operator=operator,
                         product_id=info["product_id"])
            if apply and picked_any:
                lines_now = db.execute(
                    "SELECT picked_qty FROM dn_line WHERE dn_id = ?", (dn["id"],)
                ).fetchall()
                status = C.DN_PICKING if any(r["picked_qty"] > 0 for r in lines_now) else C.DN_OPEN
                db.execute(
                    "UPDATE dn SET status = ?, wave_no = ?, "
                    "updated_at = datetime('now','localtime') WHERE id = ?",
                    (status, wave, dn["id"]),
                )
        allocations.extend(dn_alloc)
        skipped.extend(dn_skipped)
        shortages.extend(dn_short)
        dns_out.append({"dn_id": dn["id"], "dn_code": dn["code"],
                        "allocated_qty": round(sum(a["qty"] for a in dn_alloc), 6),
                        "skipped": len(dn_skipped), "shortages": len(dn_short)})
    return {
        "ok": True, "wave_no": wave, "dry_run": bool(dry_run),
        "strategy": strategy or C.OUTBOUND_STRATEGY_OWN_FIRST,
        "dns": dns_out, "allocations": allocations, "skipped": skipped,
        "shortages": shortages,
        "totals": {"dn_count": len(dns), "allocated_lines": len(allocations),
                   "allocated_qty": round(sum(a["qty"] for a in allocations), 6),
                   "skipped_batches": len(skipped), "shortages": len(shortages)},
    }


def unallocate(db, dn_id, pick_ids=None, operator="", wave_no=""):
    """取消分配：对称释放 ``allocated_qty``、删 ``dn_pick``、回退 ``picked_qty``。

    新行按记录的批次维度精确释放；历史行（无批号）回退到 M21 自动释放路径。
    已发货/已取消的单据不允许取消分配。
    """
    dn = db.execute(_DN_SELECT, (dn_id,)).fetchone()
    if dn is None:
        raise AllocationError(f"DN 不存在: id={dn_id}")
    if dn["status"] in (C.DN_SHIPPED, C.DN_CANCELLED):
        raise AllocationError(f"状态为 {dn['status']}，不可取消分配")
    sql = """SELECT dp.*, dl.product_id FROM dn_pick dp
              JOIN dn_line dl ON dl.id = dp.line_id WHERE dp.dn_id = ?"""
    params = [dn_id]
    if pick_ids:
        try:
            ids = [int(x) for x in pick_ids]
        except (TypeError, ValueError):
            raise AllocationError("pick_ids 必须为整数数组")
        sql += " AND dp.id IN (" + ",".join("?" for _ in ids) + ")"
        params += ids
    picks = [dict(r) for r in db.execute(sql + " ORDER BY dp.id", params).fetchall()]
    released = 0.0
    for p in picks:
        dims = None
        if str(p["batch_no"] or "").strip():
            dims = {k: p[k] for k in BATCH_DIMS}
            dims["production_date"] = p.get("production_date") or ""
            dims = [dict(dims, qty=0.0, allocated=-float(p["qty"]))]
        adjust_stock(db, dn["owner_id"], p["product_id"], p["location_id"],
                     0, MOVEMENT_DN_UNPICK, dn_id, allocated_delta=-float(p["qty"]),
                     batches=dims, note=f"DN {dn['code']} 取消分配",
                     operator=operator)
        db.execute("DELETE FROM dn_pick WHERE id = ?", (p["id"],))
        db.execute("UPDATE dn_line SET picked_qty = picked_qty - ? WHERE id = ?",
                   (p["qty"], p["line_id"]))
        _log(db, action="unallocated", wave_no=str(p.get("wave_no") or wave_no),
             dn_id=dn_id, line_id=p["line_id"], row=p, qty=float(p["qty"]),
             reason_code="UNALLOCATED", reason="人工取消分配，对称释放占用",
             engine=C.OUTBOUND_ENGINE_LEGACY, operator=operator,
             product_id=p["product_id"])
        released += float(p["qty"])
    lines = db.execute("SELECT picked_qty FROM dn_line WHERE dn_id = ?", (dn_id,)).fetchall()
    status = C.DN_PICKING if any(r["picked_qty"] > 0 for r in lines) else C.DN_OPEN
    db.execute("UPDATE dn SET status = ?, updated_at = datetime('now','localtime') "
               "WHERE id = ?", (status, dn_id))
    return {"ok": True, "dn_id": dn_id, "status": status,
            "released_qty": round(released, 6), "released_picks": len(picks)}


def list_logs(db, *, wave_no="", dn_id=None, action="", date_from="", date_to="",
              page=1, page_size=20):
    where, params = [], []
    if wave_no:
        where.append("wave_no = ?")
        params.append(wave_no)
    if dn_id:
        where.append("dn_id = ?")
        params.append(dn_id)
    if action:
        where.append("action = ?")
        params.append(action)
    if date_from:
        where.append("date(created_at) >= date(?)")
        params.append(date_from)
    if date_to:
        where.append("date(created_at) <= date(?)")
        params.append(date_to)
    sql = "WHERE " + " AND ".join(where) if where else ""
    total = db.execute(
        f"SELECT COUNT(*) AS n FROM outbound_allocation_log {sql}", params).fetchone()["n"]
    rows = [dict(r) for r in db.execute(
        f"SELECT * FROM outbound_allocation_log {sql} "
        "ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size]).fetchall()]
    stats = {}
    for r in db.execute(
        f"SELECT action, COUNT(*) AS n FROM outbound_allocation_log {sql} GROUP BY action",
        params).fetchall():
        stats[r["action"]] = r["n"]
    return {"ok": True, "total": total, "page": page, "page_size": page_size,
            "logs": rows, "stats": stats}


def list_waves(db, limit=20):
    rows = [dict(r) for r in db.execute(
        """SELECT wave_no, COUNT(*) AS log_count,
                  COUNT(DISTINCT dn_id) AS dn_count,
                  SUM(CASE WHEN action = 'allocated' THEN qty ELSE 0 END) AS allocated_qty,
                  MIN(created_at) AS created_at
           FROM outbound_allocation_log
           WHERE wave_no <> '' GROUP BY wave_no
           ORDER BY wave_no DESC LIMIT ?""", (limit,)).fetchall()]
    return {"ok": True, "waves": rows}


def dictionary():
    return {
        "ok": True,
        "strategies": [{"value": x, "label": C.OUTBOUND_STRATEGY_LABELS[x]}
                       for x in C.OUTBOUND_STRATEGIES],
        "actions": [{"value": x, "label": C.OUTBOUND_ACTION_LABELS[x]}
                    for x in C.OUTBOUND_ALLOC_ACTIONS],
        "skip_reasons": [{"value": x, "label": C.OUTBOUND_SKIP_LABELS[x]}
                         for x in C.OUTBOUND_SKIP_CODES],
        "engines": [C.OUTBOUND_ENGINE_RULES, C.OUTBOUND_ENGINE_LEGACY],
        "allocatable_goods": list(C.OUTBOUND_ALLOCATABLE_GOODS),
        "blocked_statuses": list(C.OUTBOUND_BLOCKED_STATUSES),
        "rule_types": dict(PR.rule_types()),
        "permissions": list(C.OUTBOUND_PERMISSIONS),
    }
