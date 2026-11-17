"""M31 司机与车辆主数据服务层。

- 两张独立表 ``driver`` / ``vehicle``，都带可空 ``owner_id``：
  `NULL = 全系统共享运力`，非空 = 该货主专属；查询统一 ``S.scoped_where(allow_null=True)``，
  写路径对非空 owner 调 ``S.assert_owner_allowed()``（Q41）。
- 一名司机 / 一辆车最多关联一个承运商 ``carrier_id``（Q42），不建多对多关系；
  本里程碑只做主数据，**不接 ASN/DN 的运力派工**。
- 证照效期（驾驶证 / 车辆保险 / 道路运输证）只读分级预警，默认 30 天，
  不拦截单据、不生成任务（Q43）。
- 编码全局唯一；车牌号 / 驾驶证号非空时唯一，空串允许多个（Q44）。
"""
import datetime

import constants as C
import scope as S


class FleetError(Exception):
    def __init__(self, message, code="FLEET_BAD_PARAM"):
        super().__init__(message)
        self.code = code


DRIVER_COLUMNS = ("code", "name", "phone", "id_no", "license_no", "license_type",
                  "license_expiry", "carrier_id", "owner_id", "status", "note")
VEHICLE_COLUMNS = ("code", "plate_no", "vehicle_type", "max_load_kg", "length_cm",
                   "width_cm", "height_cm", "insurance_expiry", "transport_expiry",
                   "carrier_id", "owner_id", "status", "note")


def dictionary():
    return {
        "ok": True,
        "statuses": [{"code": s, "label": C.FLEET_STATUS_LABELS[s]}
                     for s in C.FLEET_STATUSES],
        "license_types": [{"code": t, "label": C.DRIVER_LICENSE_TYPE_LABELS[t]}
                          for t in C.DRIVER_LICENSE_TYPES],
        "vehicle_types": [{"code": t, "label": t} for t in C.VEHICLE_TYPES],
        "expiry_levels": [{"code": lv, "label": C.FLEET_EXPIRY_LABELS[lv]}
                          for lv in C.FLEET_EXPIRY_LEVELS],
        "expiry_kinds": [{"code": k, "label": C.FLEET_EXPIRY_KIND_LABELS[k]}
                         for k in C.FLEET_EXPIRY_KINDS],
        "soon_days_default": C.FLEET_EXPIRY_SOON_DAYS_DEFAULT,
        "soon_days_max": C.FLEET_EXPIRY_SOON_DAYS_MAX,
        "owner_null_means_shared": True,
        "dn_binding": False,
    }


def _int(value, name, *, default=None, minimum=None, maximum=None):
    if value is None or str(value).strip() == "":
        if default is not None:
            return default
        raise FleetError(f"{name} 必填")
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        raise FleetError(f"{name} 必须是整数")
    if minimum is not None and n < minimum:
        raise FleetError(f"{name} 不能小于 {minimum}")
    if maximum is not None and n > maximum:
        raise FleetError(f"{name} 不能大于 {maximum}")
    return n


def _date(value, name):
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return datetime.date.fromisoformat(raw[:10]).isoformat()
    except (TypeError, ValueError):
        raise FleetError(f"{name} 必须是 YYYY-MM-DD 日期")


def _number(value, name):
    raw = str(value if value is not None else "").strip()
    if not raw:
        return 0.0
    try:
        n = float(raw)
    except (TypeError, ValueError):
        raise FleetError(f"{name} 必须是数字")
    if n < 0:
        raise FleetError(f"{name} 不能小于 0")
    return n


def _nullable_id(db, value, table, name):
    raw = value
    if raw in (None, "", "null", "0", 0):
        return None
    oid = _int(raw, name, minimum=1)
    if db.execute(f"SELECT id FROM {table} WHERE id=?", (oid,)).fetchone() is None:
        raise FleetError(f"{name} 不存在：{oid}")
    return oid


def _validate(db, kind, payload, *, partial=False, existing=None):
    cols = DRIVER_COLUMNS if kind == "driver" else VEHICLE_COLUMNS
    data, errors = {}, []
    merged = {**(existing or {}), **payload}

    def need(key, label, maxlen=None):
        val = str(merged.get(key) or "").strip()
        if not val:
            errors.append(f"{label} 必填")
        elif maxlen and len(val) > maxlen:
            errors.append(f"{label} 最长 {maxlen} 字符")
        return val

    if not partial or "code" in payload:
        data["code"] = need("code", "编码", C.FLEET_CODE_MAXLEN)
    if kind == "driver":
        if not partial or "name" in payload:
            data["name"] = need("name", "司机姓名", 64)
        for key in ("phone", "id_no", "license_no", "note"):
            if key in payload:
                data[key] = str(payload.get(key) or "").strip()
        if not partial or "license_type" in payload:
            lt = str(merged.get("license_type") or "C1").strip()
            if lt not in C.DRIVER_LICENSE_TYPES:
                errors.append("license_type 无效")
            else:
                data["license_type"] = lt
        if "license_expiry" in payload or (not partial and "license_expiry" in merged):
            data["license_expiry"] = _date(merged.get("license_expiry"), "license_expiry")
    else:
        if not partial or "plate_no" in payload:
            data["plate_no"] = need("plate_no", "车牌号", 24)
        if not partial or "vehicle_type" in payload:
            vt = str(merged.get("vehicle_type") or C.VEHICLE_TYPES[0]).strip()
            if vt not in C.VEHICLE_TYPES:
                errors.append("vehicle_type 无效")
            else:
                data["vehicle_type"] = vt
        for key in ("max_load_kg", "length_cm", "width_cm", "height_cm"):
            if key in payload:
                data[key] = _number(payload.get(key), key)
        for key in ("insurance_expiry", "transport_expiry"):
            if key in payload or (not partial and key in merged):
                data[key] = _date(merged.get(key), key)
        if "note" in payload:
            data["note"] = str(payload.get("note") or "").strip()

    if not partial or "status" in payload:
        st = str(merged.get("status") or C.FLEET_STATUS_ACTIVE).strip()
        if st not in C.FLEET_STATUSES:
            errors.append("status 只能是 active/inactive")
        else:
            data["status"] = st
    for key, table, label in (("carrier_id", "carrier", "carrier_id"),
                              ("owner_id", "owner", "owner_id")):
        if key in payload or (not partial and key in merged):
            data[key] = _nullable_id(db, merged.get(key), table, label)
    if errors:
        raise FleetError("；".join(errors))
    return data, cols


def _unique(db, kind, data, exclude_id=None):
    table = kind
    code = data.get("code")
    sql = f"SELECT id FROM {table} WHERE code=?"
    params = [code]
    if exclude_id is not None:
        sql += " AND id<>?"
        params.append(exclude_id)
    if db.execute(sql, params).fetchone():
        raise FleetError(f"编码已存在：{code}", "FLEET_DUPLICATE_CODE")
    key = "license_no" if kind == "driver" else "plate_no"
    val = (data.get(key) or "").strip()
    if val:
        sql = f"SELECT id FROM {table} WHERE {key}=?"
        params = [val]
        if exclude_id is not None:
            sql += " AND id<>?"
            params.append(exclude_id)
        if db.execute(sql, params).fetchone():
            raise FleetError(f"{key} 已存在：{val}", "FLEET_DUPLICATE_VALUE")


def _row_dict(kind, row):
    d = dict(row)
    if kind == "vehicle":
        for key in ("max_load_kg", "length_cm", "width_cm", "height_cm"):
            d[key] = float(d.get(key) or 0)
    return d


def get(db, kind, rid):
    if kind not in ("driver", "vehicle"):
        raise FleetError(f"类型无效：{kind}")
    row = db.execute(f"SELECT * FROM {kind} WHERE id=?", (rid,)).fetchone()
    if row is None:
        raise FleetError("记录不存在", "FLEET_NOT_FOUND")
    # 详情 / 编辑 / 删除都走这里：非空 owner 必须过货主守门，
    # NULL（全系统共享）对所有已解析上下文可见（与 list_records 的 allow_null 一致）。
    if row["owner_id"] is not None:
        S.assert_owner_allowed(S.current_scope(), row["owner_id"])
    return _row_dict(kind, row)


def create(db, kind, payload):
    data, _ = _validate(db, kind, payload)
    _unique(db, kind, data)
    if data.get("owner_id") is not None:
        S.assert_owner_allowed(S.current_scope(), data["owner_id"])
    cols = list(data)
    cur = db.execute(
        f"INSERT INTO {kind} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
        [data[c] for c in cols],
    )
    db.commit()
    return get(db, kind, cur.lastrowid)


def update(db, kind, rid, payload):
    old = get(db, kind, rid)
    data, _ = _validate(db, kind, payload, partial=True, existing=old)
    if not data:
        return old
    _unique(db, kind, {**old, **data}, exclude_id=rid)
    owner_id = data.get("owner_id", old.get("owner_id"))
    if owner_id is not None:
        S.assert_owner_allowed(S.current_scope(), owner_id)
    db.execute(
        f"UPDATE {kind} SET {','.join(k + '=?' for k in data)}, "
        "updated_at=datetime('now','localtime') WHERE id=?",
        list(data.values()) + [rid],
    )
    db.commit()
    return get(db, kind, rid)


def delete(db, kind, rid):
    get(db, kind, rid)
    db.execute(f"DELETE FROM {kind} WHERE id=?", (rid,))
    db.commit()
    return {"ok": True, "deleted": rid, "kind": kind}


def list_records(db, kind, args):
    if kind not in ("driver", "vehicle"):
        raise FleetError(f"类型无效：{kind}")
    page = _int(args.get("page"), "page", default=1, minimum=1)
    page_size = _int(args.get("page_size"), "page_size",
                     default=C.FLEET_PAGE_SIZE_DEFAULT, minimum=1,
                     maximum=C.FLEET_PAGE_SIZE_MAX)
    where, params = [], []
    keyword = (args.get("keyword") or "").strip()
    if keyword:
        cols = ("code", "name", "phone", "license_no") if kind == "driver" \
            else ("code", "plate_no", "vehicle_type", "note")
        where.append("(" + " OR ".join(f"{c} LIKE ?" for c in cols) + ")")
        params += [f"%{keyword}%"] * len(cols)
    for key in ("status", "carrier_id", "owner_id"):
        val = str(args.get(key) or "").strip()
        if not val:
            continue
        if key == "status":
            if val not in C.FLEET_STATUSES:
                raise FleetError(f"status 无效：{val}")
            where.append("status=?")
            params.append(val)
        else:
            where.append(f"{key}=?")
            params.append(_int(val, key, minimum=1))
    S.scoped_where(where, params, allow_null=True)
    sql = f"SELECT * FROM {kind}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC"
    rows = db.execute(sql, params).fetchall()
    items = [_row_dict(kind, r) for r in rows]

    level = (args.get("expiry_level") or "").strip()
    if level:
        if level not in C.FLEET_EXPIRY_LEVELS:
            raise FleetError(f"expiry_level 无效：{level}")
        days = _int(args.get("days"), "days", default=C.FLEET_EXPIRY_SOON_DAYS_DEFAULT,
                    minimum=0, maximum=C.FLEET_EXPIRY_SOON_DAYS_MAX)
        today = datetime.date.today()
        key = "license_expiry" if kind == "driver" else "insurance_expiry"
        items = [x for x in items if _expiry_level(x.get(key), today, days) == level]
    total = len(items)
    return {"ok": True, "total": total, "page": page, "page_size": page_size,
            (kind + "s"): items[(page - 1) * page_size: page * page_size]}


def _expiry_level(raw, today, days):
    text = str(raw or "").strip()
    if not text:
        return C.FLEET_EXPIRY_NONE
    try:
        d = datetime.date.fromisoformat(text[:10])
    except ValueError:
        return C.FLEET_EXPIRY_NONE
    left = (d - today).days
    if left < 0:
        return C.FLEET_EXPIRY_EXPIRED
    if left <= days:
        return C.FLEET_EXPIRY_SOON
    return C.FLEET_EXPIRY_NORMAL


def expiry_report(db, args):
    """证照效期预警（只读）：驾驶证 / 车辆保险 / 道路运输证三类。"""
    page = _int(args.get("page"), "page", default=1, minimum=1)
    page_size = _int(args.get("page_size"), "page_size",
                     default=C.FLEET_PAGE_SIZE_DEFAULT, minimum=1,
                     maximum=C.FLEET_PAGE_SIZE_MAX)
    days = _int(args.get("days"), "days", default=C.FLEET_EXPIRY_SOON_DAYS_DEFAULT,
                minimum=0, maximum=C.FLEET_EXPIRY_SOON_DAYS_MAX)
    level = (args.get("level") or "").strip()
    if level and level not in C.FLEET_EXPIRY_LEVELS:
        raise FleetError(f"level 无效：{level}")
    kind = (args.get("kind") or "").strip()
    if kind and kind not in C.FLEET_EXPIRY_KINDS:
        raise FleetError(f"kind 无效：{kind}")
    today = datetime.date.today()
    items = []

    def collect(table, alias, date_col, kind_code, id_col):
        w, p = [], []
        S.scoped_where(w, p, table=alias, allow_null=True)
        sql = f"SELECT {alias}.* FROM {table} {alias}"
        if w:
            sql += " WHERE " + " AND ".join(w)
        for row in db.execute(sql, p).fetchall():
            d = dict(row)
            raw = d.get(date_col) or ""
            lv = _expiry_level(raw, today, days)
            try:
                left = (datetime.date.fromisoformat(str(raw)[:10]) - today).days if raw else None
            except ValueError:
                left = None
            if level and lv != level:
                continue
            items.append({
                "kind": kind_code, "kind_label": C.FLEET_EXPIRY_KIND_LABELS[kind_code],
                "id": d["id"], "code": d["code"],
                "name": d.get("name") or d.get("plate_no") or "",
                "carrier_id": d.get("carrier_id"), "owner_id": d.get("owner_id"),
                "expiry_date": raw, "level": lv,
                "level_label": C.FLEET_EXPIRY_LABELS[lv], "days_left": left,
                "source_id": d[id_col] if id_col != "id" else d["id"],
            })

    if kind in ("", C.FLEET_EXPIRY_KINDS[0]):
        collect("driver", "d", "license_expiry", C.FLEET_EXPIRY_KINDS[0], "id")
    if kind in ("", C.FLEET_EXPIRY_KINDS[1]):
        collect("vehicle", "v", "insurance_expiry", C.FLEET_EXPIRY_KINDS[1], "id")
    if kind in ("", C.FLEET_EXPIRY_KINDS[2]):
        collect("vehicle", "v", "transport_expiry", C.FLEET_EXPIRY_KINDS[2], "id")
    items.sort(key=lambda x: (x["expiry_date"] or "9999-12-31", x["kind"], x["id"]))
    stats = {lv: {"count": 0, "items": []} for lv in C.FLEET_EXPIRY_LEVELS}
    for x in items:
        stats[x["level"]]["count"] += 1
    return {"ok": True, "total": len(items), "page": page, "page_size": page_size,
            "days": days, "today": today.isoformat(),
            "stats": [{"level": lv, "label": C.FLEET_EXPIRY_LABELS[lv],
                       "count": stats[lv]["count"]} for lv in C.FLEET_EXPIRY_LEVELS],
            "items": items[(page - 1) * page_size: page * page_size]}
