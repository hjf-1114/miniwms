"""M32 产品类型字典：独立主数据 + 货品引用 + 老数据回填。

设计要点
--------
1. **单一真值源**：新增独立表 ``product_type``；``product.product_type`` 文本列
   继续存在，但语义从「自由文本」升级为「引用字典 code」。这样：
   - 不用改 ``product`` 表结构（不破坏 M1–M41 既有迁移）；
   - ``stocktake_service`` 里 ``p.product_type`` 作为盘点维度的匹配口径不变，
     历史盘点方案里存的类型文本仍能命中。
2. **老数据回填零破坏**：迁移时扫描 ``product.product_type`` 的非空取值，
   字典里没有的**原文本直接当 code 建行**（name 同为原文本），
   这样货品上的引用值一个字符都不改，报表与盘点维度继续工作。
3. **写入兼容**：货品新建 / 编辑时传 ``product_type``：
   - 命中字典 code → 直接写；
   - 未命中 → 自动 get-or-create 建一行（字典随业务逐步收敛，不阻塞录入）；
   - 传空 → 置空。
4. **删除保护**：字典行仍被货品引用时拒绝删除（``PT_IN_USE``），
   不静默改写货品、不留悬空引用。
5. **货主隔离**：`owner_id` 可空，``NULL``＝全局字典；列表 ``allow_null=True``，
   写路径对非空 owner 过 ``assert_owner_allowed()``（Q45）。
6. **层级**：``parent_id`` 自引用单父级，最多两级（子类型不能再挂子类型，
   避免出现难用的无限树）；环路与自引用一律 400（Q46）。
"""
import sqlite3

import scope as S

import constants as C


class ProductTypeError(Exception):
    """业务校验失败 → 路由层转 400。"""

    def __init__(self, message, code="PT_BAD_PARAM"):
        super().__init__(message)
        self.message = message
        self.code = code


COLUMNS = ("code", "name", "parent_id", "sort_no", "status", "owner_id", "note")


# ── 基础工具 ────────────────────────────────────────────────────────────────

def dictionary():
    """字典：状态枚举 + 层级深度上限 + 种子类型（供前端建层级下拉）。"""
    return {
        "statuses": [{"code": s, "label": C.PT_STATUS_LABELS[s]} for s in C.PT_STATUSES],
        "max_depth": 2,
        "seed_types": [{"code": c, "name": n, "sort_no": s} for c, n, s in C.PT_SEED_TYPES],
    }


def _row_dict(row, *, ref_count=0, children=None):
    d = dict(row)
    d["status_label"] = C.PT_STATUS_LABELS.get(d.get("status"), d.get("status"))
    d["ref_count"] = ref_count
    d["children"] = children if children is not None else []
    return d


def _code_of(value):
    return str(value or "").strip()


def _exists(db, table, rid):
    return db.execute(f"SELECT id FROM {table} WHERE id = ?", (int(rid),)).fetchone() is not None


# ── 引用解析 / 回填 ─────────────────────────────────────────────────────────

def ensure_type(db, value, *, owner_id=None):
    """把一个类型取值解析成字典 code；字典里没有则自动建行。

    - 空值 → 返回空串（表示不指定类型）。
    - 命中字典 → 返回该 code。
    - 未命中 → 以原文本建行（code = 原文本），返回新 code。

    这是 M32「老数据回填」与「写入兼容」的共同入口：货品写路径与
    迁移回填都走它，保证同一套语义。
    """
    text = _code_of(value)
    if not text:
        return ""
    row = db.execute("SELECT code FROM product_type WHERE code = ?", (text,)).fetchone()
    if row is not None:
        return row["code"]
    name = text
    if len(name) > C.PT_NAME_MAXLEN:
        name = name[: C.PT_NAME_MAXLEN]
    try:
        db.execute(
            "INSERT INTO product_type (code, name, parent_id, sort_no, status, owner_id, note)"
            " VALUES (?, ?, NULL, 0, ?, ?, ?)",
            (text, name, C.PT_STATUS_ACTIVE, owner_id,
             "M32 老数据回填 / 写入自动建行"),
        )
    except sqlite3.IntegrityError:
        # 并发下同名插入：回读即可
        row = db.execute("SELECT code FROM product_type WHERE code = ?", (text,)).fetchone()
        if row is None:
            raise ProductTypeError(f"产品类型建行失败：{text}", "PT_CREATE_FAILED")
        return row["code"]
    return text


def backfill_from_products(db):
    """把货品上的历史自由文本回填成字典行；返回回填条数。

    回填后 ``product.product_type`` 的**取值不变**（老文本即 code），
    因此历史盘点方案维度、报表分组都继续匹配。
    """
    rows = db.execute(
        "SELECT DISTINCT product_type FROM product"
        " WHERE product_type IS NOT NULL AND TRIM(product_type) <> ''"
    ).fetchall()
    made = 0
    for r in rows:
        text = _code_of(r["product_type"])
        if not text:
            continue
        hit = db.execute(
            "SELECT id FROM product_type WHERE code = ?", (text,)
        ).fetchone()
        if hit is None:
            ensure_type(db, text)
            made += 1
    return made


# ── 校验 ────────────────────────────────────────────────────────────────────

def _validate(db, payload, *, partial=False, existing=None):
    """校验产品类型载荷，返回 (errors, cleaned)。"""
    errors = []
    cleaned = {}

    def has(key):
        return key in payload

    if has("code") or not partial:
        code = _code_of(payload.get("code"))
        if not code:
            errors.append("code 必填")
        elif len(code) > C.PT_CODE_MAXLEN:
            errors.append(f"code 最长 {C.PT_CODE_MAXLEN} 字符")
        else:
            cleaned["code"] = code

    if has("name") or not partial:
        name = str(payload.get("name") or "").strip()
        if not name:
            errors.append("name 必填")
        elif len(name) > C.PT_NAME_MAXLEN:
            errors.append(f"name 最长 {C.PT_NAME_MAXLEN} 字符")
        else:
            cleaned["name"] = name

    if has("parent_id"):
        raw = payload.get("parent_id")
        if raw in (None, "", 0, "0"):
            cleaned["parent_id"] = None
        else:
            try:
                pid = int(raw)
            except (TypeError, ValueError):
                errors.append("parent_id 必须为整数或留空")
            else:
                if not _exists(db, "product_type", pid):
                    errors.append("parent_id 指向的产品类型不存在")
                else:
                    cleaned["parent_id"] = pid

    if has("sort_no"):
        try:
            cleaned["sort_no"] = int(payload.get("sort_no") or 0)
        except (TypeError, ValueError):
            errors.append("sort_no 必须为整数")

    if has("status"):
        st = str(payload.get("status") or "").strip()
        if st not in C.PT_STATUSES:
            errors.append(f"status 只能是 {'/'.join(C.PT_STATUSES)}")
        else:
            cleaned["status"] = st

    if has("owner_id"):
        raw = payload.get("owner_id")
        if raw in (None, ""):
            cleaned["owner_id"] = None
        else:
            try:
                cleaned["owner_id"] = int(raw)
            except (TypeError, ValueError):
                errors.append("owner_id 必须为整数")

    if has("note"):
        note = str(payload.get("note") or "").strip()
        if len(note) > C.PT_NOTE_MAXLEN:
            errors.append(f"note 最长 {C.PT_NOTE_MAXLEN} 字符")
        else:
            cleaned["note"] = note

    return errors, cleaned


def _check_unique(db, data, exclude_id=None):
    code = data.get("code")
    if code:
        sql = "SELECT id FROM product_type WHERE code = ?"
        params = [code]
        if exclude_id is not None:
            sql += " AND id != ?"
            params.append(exclude_id)
        if db.execute(sql, params).fetchone() is not None:
            raise ProductTypeError(f"产品类型编码已存在：{code}", "PT_DUPLICATE_CODE")


def _check_hierarchy(db, rid, parent_id):
    """校验层级：父级必须存在（已在 _validate 校验），且父级不能是自身 / 自身子孙。"""
    if parent_id is None:
        return
    if rid is not None and int(parent_id) == int(rid):
        raise ProductTypeError("上级产品类型不能是自己", "PT_HIERARCHY_CYCLE")
    # 最多两级：父级的父级必须为空
    row = db.execute(
        "SELECT parent_id FROM product_type WHERE id = ?", (int(parent_id),)
    ).fetchone()
    if row is None:
        raise ProductTypeError("上级产品类型不存在", "PT_PARENT_NOT_FOUND")
    if row["parent_id"]:
        raise ProductTypeError(
            "产品类型最多支持两级（父级不能再有上级）", "PT_MAX_DEPTH"
        )


# ── CRUD ────────────────────────────────────────────────────────────────────

def get(db, rid):
    row = db.execute("SELECT * FROM product_type WHERE id = ?", (int(rid),)).fetchone()
    if row is None:
        raise ProductTypeError("产品类型不存在", "PT_NOT_FOUND")
    # 详情 / 编辑 / 删除都走这里：非空 owner 必须过货主守门；
    # NULL（全局字典）对所有已解析上下文可见（与 list_records 的 allow_null 一致）。
    if row["owner_id"] is not None:
        S.assert_owner_allowed(S.current_scope(), row["owner_id"])
    ref = db.execute(
        "SELECT COUNT(*) AS n FROM product WHERE product_type = ?", (row["code"],)
    ).fetchone()["n"]
    return _row_dict(row, ref_count=ref)


def create(db, payload):
    errors, data = _validate(db, payload, partial=False)
    if errors:
        raise ProductTypeError("；".join(errors), "PT_BAD_PARAM")
    _check_unique(db, data)
    _check_hierarchy(db, None, data.get("parent_id"))
    if data.get("owner_id") is not None:
        S.assert_owner_allowed(S.current_scope(), data["owner_id"])
    cols = list(data)
    cur = db.execute(
        f"INSERT INTO product_type ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
        [data[c] for c in cols],
    )
    db.commit()
    return get(db, cur.lastrowid)


def update(db, rid, payload):
    row = db.execute("SELECT * FROM product_type WHERE id = ?", (int(rid),)).fetchone()
    if row is None:
        raise ProductTypeError("产品类型不存在", "PT_NOT_FOUND")
    # 原记录归属守门：不能改别人货主的类型
    if row["owner_id"] is not None:
        S.assert_owner_allowed(S.current_scope(), row["owner_id"])
    errors, data = _validate(db, payload, partial=True, existing=row)
    if errors:
        raise ProductTypeError("；".join(errors), "PT_BAD_PARAM")
    if "code" in data and data["code"] != row["code"]:
        used = db.execute(
            "SELECT COUNT(*) AS n FROM product WHERE product_type = ?", (row["code"],)
        ).fetchone()["n"]
        if used:
            raise ProductTypeError(
                "该编码仍被货品引用，不能改编码（请先调整货品）", "PT_IN_USE"
            )
    _check_unique(db, data, exclude_id=int(rid))
    parent_id = data.get("parent_id", row["parent_id"])
    _check_hierarchy(db, int(rid), parent_id)
    if "owner_id" in data and data["owner_id"] is not None:
        S.assert_owner_allowed(S.current_scope(), data["owner_id"])
    if not data:
        return get(db, rid)
    sets = ", ".join(f"{c} = ?" for c in data)
    db.execute(
        f"UPDATE product_type SET {sets} WHERE id = ?",
        [data[c] for c in data] + [int(rid)],
    )
    db.commit()
    return get(db, rid)


def delete(db, rid):
    row = db.execute("SELECT * FROM product_type WHERE id = ?", (int(rid),)).fetchone()
    if row is None:
        raise ProductTypeError("产品类型不存在", "PT_NOT_FOUND")
    # 归属守门：不能删别人货主的类型
    if row["owner_id"] is not None:
        S.assert_owner_allowed(S.current_scope(), row["owner_id"])
    used = db.execute(
        "SELECT COUNT(*) AS n FROM product WHERE product_type = ?", (row["code"],)
    ).fetchone()["n"]
    if used:
        raise ProductTypeError(C.PT_DELETE_BLOCKED_MSG, C.PT_DELETE_BLOCKED_CODE)
    kids = db.execute(
        "SELECT COUNT(*) AS n FROM product_type WHERE parent_id = ?", (int(rid),)
    ).fetchone()["n"]
    if kids:
        raise ProductTypeError("该产品类型下还有子类型，请先删除子类型", "PT_HAS_CHILDREN")
    db.execute("DELETE FROM product_type WHERE id = ?", (int(rid),))
    db.commit()
    return True


def list_records(db, args):
    """列表：支持 keyword / status / owner_id / parent_id 过滤 + 分页。

    返回带 ``ref_count``（被货品引用数）与 ``children``（子类型 id 列表），
    前端据此渲染两级树。
    """
    where, params = [], []
    keyword = str(args.get("keyword") or "").strip()
    status = str(args.get("status") or "").strip()
    owner_id = str(args.get("owner_id") or "").strip()
    parent_raw = args.get("parent_id")

    if keyword:
        where.append("(code LIKE ? OR name LIKE ?)")
        params += [f"%{keyword}%", f"%{keyword}%"]
    if status:
        if status not in C.PT_STATUSES:
            raise ProductTypeError(f"status 只能是 {'/'.join(C.PT_STATUSES)}")
        where.append("status = ?")
        params.append(status)
    if owner_id:
        if owner_id == "none":
            where.append("owner_id IS NULL")
        else:
            where.append("owner_id = ?")
            params.append(owner_id)
    if parent_raw not in (None, ""):
        if str(parent_raw) == "none":
            where.append("parent_id IS NULL")
        elif str(parent_raw) == "0":
            where.append("parent_id IS NULL")
        else:
            where.append("parent_id = ?")
            params.append(parent_raw)

    S.scoped_where(where, params, allow_null=True)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    try:
        page = max(1, int(args.get("page", 1)))
        page_size = min(C.PT_PAGE_SIZE_MAX,
                        max(1, int(args.get("page_size", C.PT_PAGE_SIZE_DEFAULT))))
    except (TypeError, ValueError):
        raise ProductTypeError("page/page_size 必须为整数")

    total = db.execute(f"SELECT COUNT(*) AS n FROM product_type {where_sql}",
                       params).fetchone()["n"]
    rows = db.execute(
        f"SELECT * FROM product_type {where_sql}"
        " ORDER BY sort_no ASC, id ASC LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()

    out = []
    for r in rows:
        ref = db.execute(
            "SELECT COUNT(*) AS n FROM product WHERE product_type = ?", (r["code"],)
        ).fetchone()["n"]
        kids = db.execute(
            "SELECT id FROM product_type WHERE parent_id = ? ORDER BY sort_no, id",
            (r["id"],),
        ).fetchall()
        out.append(_row_dict(r, ref_count=ref, children=[k["id"] for k in kids]))
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "product_types": out,
    }


def ref_counts(db):
    """全量引用计数（字典页顶部统计用）：{code: 货品数}。"""
    rows = db.execute(
        "SELECT product_type, COUNT(*) AS n FROM product"
        " WHERE product_type IS NOT NULL AND TRIM(product_type) <> ''"
        " GROUP BY product_type"
    ).fetchall()
    return {r["product_type"]: r["n"] for r in rows}


def resolve_name(db, code):
    """code → 显示名；查不到返回原值（老数据兜底，不吞信息）。"""
    text = _code_of(code)
    if not text:
        return "", False
    row = db.execute(
        "SELECT name FROM product_type WHERE code = ?", (text,)
    ).fetchone()
    if row is None:
        return text, False
    return row["name"], True
