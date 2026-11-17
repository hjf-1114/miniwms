"""M34 货位组主数据：独立表替代 ``location.location_group`` 文本字段。

设计要点
--------
1. **不动 ``location`` 表结构**：`location.location_group` 仍是 TEXT 列，语义从
   「自由文本」升级为「引用 ``location_group.code``」。与 M32 产品类型字典同款
   零破坏策略：老文本原样当 code，库位上的取值一个字符都不改（Q55）。
2. **回填**：`main._migrate_m34()` 扫描库位上的历史分组文本，字典里没有的
   **原文本直接建行**（name 同为原文本），库位取值不变。
3. **组级属性**：拣货顺序 `pick_seq`、容积上限 `capacity_volume`（cm³）、
   重量上限 `capacity_weight`（kg）、三档混放标志（产品 / 批号 / 货主）。
   混放标志与容积只是**组级默认提示**，库位自身的 `allow_mix_*` 优先，
   不覆盖 M20 / M25 已有的混放规则引擎口径（Q54）。
4. **归属仓库**：货位组属仓库布局控制面（与 ``zone`` / ``location`` 一致），
   **不按货主隔离**，字段为可空 ``warehouse_id`` / ``zone_id``（Q53）。
5. **盘点维度口径**：``stocktake_service`` 的 ``location_group`` 维度改为
   「优先取真实货位组，为空回落 ``location.zone`` 文本」——
   存量库位（分组非空）的老盘点方案取值仍能命中，空分组的库位沿用旧口径，
   历史方案零失配（Q56）。
"""
import sqlite3

import scope as S

import constants as C


class LocationGroupError(Exception):
    """业务校验失败 → 路由层转 400。"""

    def __init__(self, message, code="LOC_GROUP_BAD_PARAM"):
        super().__init__(message)
        self.message = message
        self.code = code


COLUMNS = ("code", "name", "warehouse_id", "zone_id", "pick_seq",
           "capacity_volume", "capacity_weight", "allow_mix_product",
           "allow_mix_batch", "allow_mix_owner", "status", "note")


# ── 基础工具 ────────────────────────────────────────────────────────────────

def dictionary():
    """字典：状态 / 混放标志 / 容积语义。"""
    return {
        "statuses": [{"code": s, "label": C.LOC_GROUP_STATUS_LABELS[s]}
                     for s in C.LOC_GROUP_STATUSES],
        "mix_fields": [{"code": f, "label": C.LOC_GROUP_MIX_LABELS[f]}
                       for f in C.LOC_GROUP_MIX_FIELDS],
        "capacity_unlimited": C.LOC_GROUP_CAPACITY_UNLIMITED,
        "owner_isolated": False,   # 仓库布局控制面，不按货主隔离（Q53）
    }


def _row_dict(row, *, location_count=0, locations=None):
    d = dict(row)
    d["status_label"] = C.LOC_GROUP_STATUS_LABELS.get(d.get("status"),
                                                       d.get("status"))
    d["location_count"] = location_count
    d["locations"] = locations if locations is not None else []
    d["volume_unlimited"] = not d.get("capacity_volume")
    d["weight_unlimited"] = not d.get("capacity_weight")
    return d


def _exists(db, table, rid):
    return db.execute(f"SELECT id FROM {table} WHERE id = ?",
                      (int(rid),)).fetchone() is not None


def _warehouse_code(db, wid):
    if not wid:
        return ""
    row = db.execute("SELECT code FROM warehouse WHERE id = ?",
                     (int(wid),)).fetchone()
    return row["code"] if row else ""


def _zone_code(db, zid):
    if not zid:
        return ""
    row = db.execute("SELECT code FROM zone WHERE id = ?",
                     (int(zid),)).fetchone()
    return row["code"] if row else ""


# ── 引用解析 / 回填 ─────────────────────────────────────────────────────────

def ensure_group(db, value, *, warehouse_id=None, zone_id=None):
    """把一个分组取值解析成字典 code；字典里没有则自动建行。

    与 M32 ``ensure_type()`` 同款：老文本即 code，保证库位取值不变。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    row = db.execute("SELECT code FROM location_group WHERE code = ?",
                     (text,)).fetchone()
    if row is not None:
        return row["code"]
    name = text[: C.LOC_GROUP_NAME_MAXLEN]
    try:
        db.execute(
            "INSERT INTO location_group (code, name, warehouse_id, zone_id,"
            " pick_seq, capacity_volume, capacity_weight, allow_mix_product,"
            " allow_mix_batch, allow_mix_owner, status, note)"
            " VALUES (?, ?, ?, ?, 0, 0, 0, 0, 0, 0, ?, ?)",
            (text, name, warehouse_id, zone_id, C.LOC_GROUP_STATUS_ACTIVE,
             "M34 老数据回填 / 写入自动建行"),
        )
    except sqlite3.IntegrityError:
        row = db.execute("SELECT code FROM location_group WHERE code = ?",
                         (text,)).fetchone()
        if row is None:
            raise LocationGroupError(f"货位组建行失败：{text}", "LOC_GROUP_CREATE_FAILED")
        return row["code"]
    return text


def backfill_from_locations(db):
    """把库位上的历史分组文本回填成货位组行；返回回填条数。

    回填后 ``location.location_group`` 的**取值不变**（老文本即 code）。
    """
    rows = db.execute(
        "SELECT DISTINCT location_group FROM location"
        " WHERE location_group IS NOT NULL AND TRIM(location_group) <> ''"
    ).fetchall()
    made = 0
    for r in rows:
        text = str(r["location_group"] or "").strip()
        if not text:
            continue
        hit = db.execute("SELECT id FROM location_group WHERE code = ?",
                         (text,)).fetchone()
        if hit is None:
            ensure_group(db, text)
            made += 1
    return made


# ── 校验 ────────────────────────────────────────────────────────────────────

def _validate(db, payload, *, partial=False):
    errors = []
    cleaned = {}

    def has(key):
        return key in payload

    if has("code") or not partial:
        code = str(payload.get("code") or "").strip()
        if not code:
            errors.append("code 必填")
        elif len(code) > C.LOC_GROUP_CODE_MAXLEN:
            errors.append(f"code 最长 {C.LOC_GROUP_CODE_MAXLEN} 字符")
        else:
            cleaned["code"] = code

    if has("name") or not partial:
        name = str(payload.get("name") or "").strip()
        if not name:
            errors.append("name 必填")
        elif len(name) > C.LOC_GROUP_NAME_MAXLEN:
            errors.append(f"name 最长 {C.LOC_GROUP_NAME_MAXLEN} 字符")
        else:
            cleaned["name"] = name

    for key, table, label in (("warehouse_id", "warehouse", "warehouse_id"),
                              ("zone_id", "zone", "zone_id")):
        if has(key):
            raw = payload.get(key)
            if raw in (None, "", 0, "0"):
                cleaned[key] = None
            else:
                try:
                    rid = int(raw)
                except (TypeError, ValueError):
                    errors.append(f"{label} 必须为整数或留空")
                else:
                    if not _exists(db, table, rid):
                        errors.append(f"{label} 指向的记录不存在")
                    else:
                        cleaned[key] = rid

    if has("pick_seq"):
        try:
            cleaned["pick_seq"] = int(payload.get("pick_seq") or 0)
        except (TypeError, ValueError):
            errors.append("pick_seq 必须为整数")

    for key in ("capacity_volume", "capacity_weight"):
        if has(key):
            raw = payload.get(key)
            try:
                val = float(raw or 0)
            except (TypeError, ValueError):
                errors.append(f"{key} 必须为数字")
                continue
            if val < 0:
                errors.append(f"{key} 不能为负数")
            else:
                cleaned[key] = val

    for key in C.LOC_GROUP_MIX_FIELDS:
        if has(key):
            raw = payload.get(key)
            cleaned[key] = 0 if raw in (None, "") else (1 if raw else 0)

    if has("status"):
        st = str(payload.get("status") or "").strip()
        if st not in C.LOC_GROUP_STATUSES:
            errors.append(f"status 只能是 {'/'.join(C.LOC_GROUP_STATUSES)}")
        else:
            cleaned["status"] = st

    if has("note"):
        note = str(payload.get("note") or "").strip()
        if len(note) > C.LOC_GROUP_NOTE_MAXLEN:
            errors.append(f"note 最长 {C.LOC_GROUP_NOTE_MAXLEN} 字符")
        else:
            cleaned["note"] = note

    return errors, cleaned


def _check_unique(db, code, exclude_id=None):
    sql = "SELECT id FROM location_group WHERE code = ?"
    params = [code]
    if exclude_id is not None:
        sql += " AND id != ?"
        params.append(exclude_id)
    if db.execute(sql, params).fetchone() is not None:
        raise LocationGroupError(f"货位组编码已存在：{code}", "LOC_GROUP_DUPLICATE_CODE")


# ── CRUD ────────────────────────────────────────────────────────────────────

def get(db, rid):
    row = db.execute("SELECT * FROM location_group WHERE id = ?",
                     (int(rid),)).fetchone()
    if row is None:
        raise LocationGroupError("货位组不存在", "LOC_GROUP_NOT_FOUND")
    locs = db.execute(
        "SELECT id, code, name FROM location WHERE location_group = ?"
        " ORDER BY code", (row["code"],),
    ).fetchall()
    return _row_dict(row, location_count=len(locs),
                     locations=[dict(x) for x in locs])


def create(db, payload):
    errors, data = _validate(db, payload, partial=False)
    if errors:
        raise LocationGroupError("；".join(errors), "LOC_GROUP_BAD_PARAM")
    _check_unique(db, data["code"])
    cols = list(data)
    cur = db.execute(
        f"INSERT INTO location_group ({','.join(cols)})"
        f" VALUES ({','.join('?' for _ in cols)})",
        [data[c] for c in cols],
    )
    db.commit()
    return get(db, cur.lastrowid)


def update(db, rid, payload):
    row = db.execute("SELECT * FROM location_group WHERE id = ?",
                     (int(rid),)).fetchone()
    if row is None:
        raise LocationGroupError("货位组不存在", "LOC_GROUP_NOT_FOUND")
    errors, data = _validate(db, payload, partial=True)
    if errors:
        raise LocationGroupError("；".join(errors), "LOC_GROUP_BAD_PARAM")
    if "code" in data and data["code"] != row["code"]:
        used = db.execute(
            "SELECT COUNT(*) AS n FROM location WHERE location_group = ?",
            (row["code"],),
        ).fetchone()["n"]
        if used:
            raise LocationGroupError(
                "该编码仍被库位引用，不能改编码（请先调整库位的货位组）",
                "LOC_GROUP_IN_USE")
    _check_unique(db, data["code"], exclude_id=int(rid)) if "code" in data else None
    if not data:
        return get(db, rid)
    sets = ", ".join(f"{c} = ?" for c in data)
    db.execute(
        f"UPDATE location_group SET {sets}, updated_at = datetime('now','localtime')"
        " WHERE id = ?",
        [data[c] for c in data] + [int(rid)],
    )
    db.commit()
    return get(db, rid)


def delete(db, rid):
    row = db.execute("SELECT * FROM location_group WHERE id = ?",
                     (int(rid),)).fetchone()
    if row is None:
        raise LocationGroupError("货位组不存在", "LOC_GROUP_NOT_FOUND")
    used = db.execute(
        "SELECT COUNT(*) AS n FROM location WHERE location_group = ?",
        (row["code"],),
    ).fetchone()["n"]
    if used:
        raise LocationGroupError(
            "该货位组仍被库位引用，请先调整库位的货位组", "LOC_GROUP_IN_USE")
    db.execute("DELETE FROM location_group WHERE id = ?", (int(rid),))
    db.commit()
    return True


# ── 查询 ────────────────────────────────────────────────────────────────────

def list_records(db, args):
    where, params = [], []
    keyword = str(args.get("keyword") or "").strip()
    status = str(args.get("status") or "").strip()
    warehouse_id = str(args.get("warehouse_id") or "").strip()

    if keyword:
        where.append("(code LIKE ? OR name LIKE ?)")
        params += [f"%{keyword}%", f"%{keyword}%"]
    if status:
        if status not in C.LOC_GROUP_STATUSES:
            raise LocationGroupError(f"status 只能是 {'/'.join(C.LOC_GROUP_STATUSES)}")
        where.append("status = ?")
        params.append(status)
    if warehouse_id:
        where.append("warehouse_id = ?")
        params.append(warehouse_id)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    try:
        page = max(1, int(args.get("page", 1)))
        page_size = min(C.LOC_GROUP_PAGE_SIZE_MAX,
                        max(1, int(args.get("page_size",
                                            C.LOC_GROUP_PAGE_SIZE_DEFAULT))))
    except (TypeError, ValueError):
        raise LocationGroupError("page/page_size 必须为整数")

    total = db.execute(f"SELECT COUNT(*) AS n FROM location_group {where_sql}",
                       params).fetchone()["n"]
    rows = db.execute(
        f"SELECT * FROM location_group {where_sql}"
        " ORDER BY pick_seq ASC, code ASC LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()

    out = []
    for r in rows:
        n = db.execute(
            "SELECT COUNT(*) AS n FROM location WHERE location_group = ?",
            (r["code"],),
        ).fetchone()["n"]
        out.append(_row_dict(r, location_count=n))
    return {"total": total, "page": page, "page_size": page_size,
            "location_groups": out}


def pick_order(db, args):
    """拣货顺序视图：按 ``pick_seq`` 排序列出货位组及其库位（只读）。

    这是 M34 的核心用途 —— 让拣货路径按组走，而不是按库位编码逐个找。
    """
    where, params = [], []
    warehouse_id = str(args.get("warehouse_id") or "").strip()
    if warehouse_id:
        where.append("(g.warehouse_id = ? OR g.warehouse_id IS NULL)")
        params.append(warehouse_id)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    rows = db.execute(
        f"SELECT g.* FROM location_group g {where_sql}"
        " ORDER BY g.pick_seq ASC, g.code ASC", params,
    ).fetchall()

    groups = []
    for r in rows:
        locs = db.execute(
            "SELECT id, code, name, location_group FROM location"
            " WHERE location_group = ? ORDER BY code", (r["code"],),
        ).fetchall()
        groups.append({
            "id": r["id"],
            "code": r["code"],
            "name": r["name"],
            "pick_seq": r["pick_seq"],
            "warehouse_code": _warehouse_code(db, r["warehouse_id"]),
            "zone_code": _zone_code(db, r["zone_id"]),
            "location_count": len(locs),
            "locations": [dict(x) for x in locs],
        })
    return {"total": len(groups), "groups": groups}


def resolve_name(db, code):
    """code → 显示名；查不到返回原值（老数据兜底，不吞信息）。"""
    text = str(code or "").strip()
    if not text:
        return "", False
    row = db.execute("SELECT name FROM location_group WHERE code = ?",
                     (text,)).fetchone()
    if row is None:
        return text, False
    return row["name"], True
