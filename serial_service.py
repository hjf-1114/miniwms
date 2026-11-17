"""M33 序列号台账：序列号档案 + 收发绑定流水。

设计要点
--------
1. **台账独立于库存主账**：序列号不参与 ``stock.qty`` 计算，也不写
   ``stock_movement``；M33 只做「序列号台账」，进出库动作记在
   ``serial_transaction`` 流水里，库存账实关系不受影响（Q52）。
2. **档案 + 流水两张表**：``serial_no`` 存当前状态（一个序列号 = 一件实物，
   数量恒为 1，不需要 qty）；``serial_transaction`` 存每一次状态迁移的
   前后状态、位置、单据来源、操作人与时间，形成可追溯台账。
3. **状态机受控**：``SERIAL_TRANSITIONS`` 定义允许的流转；报废是终态，
   非法流转返回 400 ``SERIAL_BAD_TRANSITION``（Q50）。
4. **货主隔离**：``owner_id`` 可空，``NULL``＝全系统台账；列表
   ``allow_null=True``，写路径对非空 owner 过 ``assert_owner_allowed()``（Q49）。
5. **不强制拦截出入库**：M33 只提供**手工登记台账**与与 ASN/DN 的**只读关联**，
   不改动已稳定的收货 / 发货主流程，避免为序列号硬拦截而推翻 M1–M41
   的库存链路（Q51）。
"""
import sqlite3

import scope as S

import constants as C


class SerialError(Exception):
    """业务校验失败 → 路由层转 400。"""

    def __init__(self, message, code="SERIAL_BAD_PARAM"):
        super().__init__(message)
        self.message = message
        self.code = code


def dictionary():
    """字典：状态 / 流转 / 流水动作 / 单据来源。"""
    return {
        "statuses": [{"code": s, "label": C.SERIAL_STATUS_LABELS[s]}
                     for s in C.SERIAL_STATUSES],
        "transitions": {k: list(v) for k, v in C.SERIAL_TRANSITIONS.items()},
        "actions": list(C.SERIAL_ACTIONS),
        "ref_types": list(C.SERIAL_REF_TYPES),
        "owner_null_means_global": True,
        "affects_stock": False,   # 序列号台账不写库存账（Q52）
        "dn_binding": False,      # 不接发货硬拦截（Q51）
    }


# ── 工具 ────────────────────────────────────────────────────────────────────

def _row_dict(row, *, txns=None, product=None):
    d = dict(row)
    d["status_label"] = C.SERIAL_STATUS_LABELS.get(d.get("status"), d.get("status"))
    if product:
        d["product_code"] = product.get("sku")
        d["product_name"] = product.get("name")
        d["product_is_serial"] = product.get("is_serial")
    d["transactions"] = txns if txns is not None else []
    return d


def _product_of(db, pid):
    row = db.execute(
        "SELECT id, sku, name, is_serial, owner_id FROM product WHERE id = ?",
        (int(pid),),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def _allowed_next(status):
    return C.SERIAL_TRANSITIONS.get(status, ())


# ── 校验 ────────────────────────────────────────────────────────────────────

def _validate(db, payload, *, partial=False):
    errors = []
    cleaned = {}

    def has(key):
        return key in payload

    if has("serial_no") or not partial:
        no = str(payload.get("serial_no") or "").strip()
        if not no:
            errors.append("serial_no 必填")
        elif len(no) > C.SERIAL_NO_MAXLEN:
            errors.append(f"serial_no 最长 {C.SERIAL_NO_MAXLEN} 字符")
        else:
            cleaned["serial_no"] = no

    if has("product_id") or not partial:
        raw = payload.get("product_id")
        if raw in (None, ""):
            errors.append("product_id 必填")
        else:
            try:
                pid = int(raw)
            except (TypeError, ValueError):
                errors.append("product_id 必须为整数")
            else:
                prod = _product_of(db, pid)
                if prod is None:
                    errors.append("product_id 指向的货品不存在")
                else:
                    cleaned["product_id"] = pid

    if has("batch_no"):
        b = str(payload.get("batch_no") or "").strip()
        if len(b) > C.SERIAL_BATCH_NO_MAXLEN:
            errors.append(f"batch_no 最长 {C.SERIAL_BATCH_NO_MAXLEN} 字符")
        else:
            cleaned["batch_no"] = b

    if has("location_id"):
        raw = payload.get("location_id")
        if raw in (None, "", 0, "0"):
            cleaned["location_id"] = None
        else:
            try:
                lid = int(raw)
            except (TypeError, ValueError):
                errors.append("location_id 必须为整数或留空")
            else:
                hit = db.execute("SELECT id FROM location WHERE id = ?",
                                 (lid,)).fetchone()
                if hit is None:
                    errors.append("location_id 指向的库位不存在")
                else:
                    cleaned["location_id"] = lid

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
        if len(note) > C.SERIAL_NOTE_MAXLEN:
            errors.append(f"note 最长 {C.SERIAL_NOTE_MAXLEN} 字符")
        else:
            cleaned["note"] = note

    return errors, cleaned


def _check_serial_unique(db, serial_no, exclude_id=None):
    sql = "SELECT id FROM serial_no WHERE serial_no = ?"
    params = [serial_no]
    if exclude_id is not None:
        sql += " AND id != ?"
        params.append(exclude_id)
    if db.execute(sql, params).fetchone() is not None:
        raise SerialError(f"序列号已存在：{serial_no}", "SERIAL_DUPLICATE")


# ── 流水 ────────────────────────────────────────────────────────────────────

def _add_txn(db, serial_id, action, *, from_status="", to_status="",
             ref_type=C.SERIAL_REF_MANUAL, ref_id=None,
             from_location_id=None, to_location_id=None,
             operator="", note=""):
    cur = db.execute(
        "INSERT INTO serial_transaction (serial_id, action, from_status, to_status,"
        " ref_type, ref_id, from_location_id, to_location_id, operator, note,"
        " occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
        " datetime('now','localtime'))",
        (int(serial_id), action, from_status, to_status, ref_type, ref_id,
         from_location_id, to_location_id, operator, note),
    )
    return cur.lastrowid


def list_transactions(db, serial_id):
    rows = db.execute(
        "SELECT * FROM serial_transaction WHERE serial_id = ?"
        " ORDER BY id ASC", (int(serial_id),),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["action_label"] = {"in": "入库登记", "out": "出库",
                             "status": "状态变更"}.get(d.get("action"), d.get("action"))
        out.append(d)
    return out


# ── CRUD / 动作 ────────────────────────────────────────────────────────────

def get(db, rid):
    row = db.execute("SELECT * FROM serial_no WHERE id = ?", (int(rid),)).fetchone()
    if row is None:
        raise SerialError("序列号档案不存在", "SERIAL_NOT_FOUND")
    if row["owner_id"] is not None:
        S.assert_owner_allowed(S.current_scope(), row["owner_id"])
    prod = _product_of(db, row["product_id"]) if row["product_id"] else None
    return _row_dict(row, txns=list_transactions(db, rid), product=prod)


def create(db, payload):
    errors, data = _validate(db, payload, partial=False)
    if errors:
        raise SerialError("；".join(errors), "SERIAL_BAD_PARAM")
    _check_serial_unique(db, data["serial_no"])
    if data.get("owner_id") is not None:
        S.assert_owner_allowed(S.current_scope(), data["owner_id"])
    cols = list(data)
    cur = db.execute(
        f"INSERT INTO serial_no ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
        [data[c] for c in cols],
    )
    sid = cur.lastrowid
    _add_txn(db, sid, C.SERIAL_ACTION_IN,
             from_status="", to_status=C.SERIAL_STATUS_IN_STOCK,
             to_location_id=data.get("location_id"),
             operator=str(payload.get("operator") or ""),
             note="M33 台账建档")
    db.commit()
    return get(db, sid)


def update(db, rid, payload):
    """编辑档案的非状态字段（批次 / 库位 / 备注 / 归属）。

    状态**不能**在这里改 —— 状态必须走 ``change_status()`` 以留下流水。
    """
    row = db.execute("SELECT * FROM serial_no WHERE id = ?", (int(rid),)).fetchone()
    if row is None:
        raise SerialError("序列号档案不存在", "SERIAL_NOT_FOUND")
    if row["owner_id"] is not None:
        S.assert_owner_allowed(S.current_scope(), row["owner_id"])
    errors, data = _validate(db, payload, partial=True)
    if errors:
        raise SerialError("；".join(errors), "SERIAL_BAD_PARAM")
    if "serial_no" in data and data["serial_no"] != row["serial_no"]:
        _check_serial_unique(db, data["serial_no"], exclude_id=int(rid))
    if "owner_id" in data and data["owner_id"] is not None:
        S.assert_owner_allowed(S.current_scope(), data["owner_id"])
    if not data:
        return get(db, rid)
    sets = ", ".join(f"{c} = ?" for c in data)
    db.execute(
        f"UPDATE serial_no SET {sets}, updated_at = datetime('now','localtime')"
        " WHERE id = ?",
        [data[c] for c in data] + [int(rid)],
    )
    db.commit()
    return get(db, rid)


def change_status(db, rid, payload):
    """状态变更（受状态机约束），写流水。"""
    row = db.execute("SELECT * FROM serial_no WHERE id = ?", (int(rid),)).fetchone()
    if row is None:
        raise SerialError("序列号档案不存在", "SERIAL_NOT_FOUND")
    if row["owner_id"] is not None:
        S.assert_owner_allowed(S.current_scope(), row["owner_id"])
    target = str(payload.get("status") or "").strip()
    if target not in C.SERIAL_STATUSES:
        raise SerialError(f"status 只能是 {'/'.join(C.SERIAL_STATUSES)}")
    cur_status = row["status"]
    if target == cur_status:
        raise SerialError("目标状态与当前状态相同", "SERIAL_BAD_TRANSITION")
    if target not in _allowed_next(cur_status):
        raise SerialError(
            f"不允许从「{C.SERIAL_STATUS_LABELS.get(cur_status, cur_status)}」"
            f"流转到「{C.SERIAL_STATUS_LABELS.get(target, target)}」",
            "SERIAL_BAD_TRANSITION")
    location_id = payload.get("location_id", row["location_id"])
    operator = str(payload.get("operator") or "")
    note = str(payload.get("note") or "")
    fields = ["status = ?", "updated_at = datetime('now','localtime')"]
    params = [target]
    if target == C.SERIAL_STATUS_OUT:
        fields.append("out_date = datetime('now','localtime')")
        fields.append("location_id = NULL")
    elif target == C.SERIAL_STATUS_IN_STOCK:
        fields.append("location_id = ?")
        params.append(location_id)
    db.execute(f"UPDATE serial_no SET {', '.join(fields)} WHERE id = ?",
                params + [int(rid)])
    _add_txn(db, rid, C.SERIAL_ACTION_STATUS,
             from_status=cur_status, to_status=target,
             from_location_id=row["location_id"], to_location_id=location_id,
             operator=operator, note=note)
    db.commit()
    return get(db, rid)


def outbound(db, rid, payload):
    """出库：在库 / 冻结 → 已出库，记 out 流水。

    已出库 / 报废不可再出库（状态机约束）。
    """
    row = db.execute("SELECT * FROM serial_no WHERE id = ?", (int(rid),)).fetchone()
    if row is None:
        raise SerialError("序列号档案不存在", "SERIAL_NOT_FOUND")
    if row["owner_id"] is not None:
        S.assert_owner_allowed(S.current_scope(), row["owner_id"])
    if row["status"] != C.SERIAL_STATUS_IN_STOCK:
        raise SerialError(
            f"只有「{C.SERIAL_STATUS_LABELS[C.SERIAL_STATUS_IN_STOCK]}」的序列号可以出库",
            "SERIAL_BAD_TRANSITION")
    ref_type = str(payload.get("ref_type") or C.SERIAL_REF_MANUAL).strip()
    if ref_type not in C.SERIAL_REF_TYPES:
        raise SerialError(f"ref_type 只能是 {'/'.join(C.SERIAL_REF_TYPES)}")
    ref_id = payload.get("ref_id")
    if ref_id in ("", None):
        ref_id = None
    else:
        try:
            ref_id = int(ref_id)
        except (TypeError, ValueError):
            raise SerialError("ref_id 必须为整数")
    operator = str(payload.get("operator") or "")
    note = str(payload.get("note") or "")
    db.execute(
        "UPDATE serial_no SET status = ?, location_id = NULL,"
        " out_date = datetime('now','localtime'),"
        " updated_at = datetime('now','localtime') WHERE id = ?",
        (C.SERIAL_STATUS_OUT, int(rid)),
    )
    _add_txn(db, rid, C.SERIAL_ACTION_OUT,
             from_status=C.SERIAL_STATUS_IN_STOCK, to_status=C.SERIAL_STATUS_OUT,
             ref_type=ref_type, ref_id=ref_id,
             from_location_id=row["location_id"], to_location_id=None,
             operator=operator, note=note)
    db.commit()
    return get(db, rid)


# ── 查询 ────────────────────────────────────────────────────────────────────

def list_records(db, args):
    where, params = [], []
    keyword = str(args.get("keyword") or "").strip()
    status = str(args.get("status") or "").strip()
    owner_id = str(args.get("owner_id") or "").strip()
    product_id = str(args.get("product_id") or "").strip()
    batch_no = str(args.get("batch_no") or "").strip()

    if keyword:
        where.append("(serial_no LIKE ? OR batch_no LIKE ?)")
        params += [f"%{keyword}%", f"%{keyword}%"]
    if status:
        if status not in C.SERIAL_STATUSES:
            raise SerialError(f"status 只能是 {'/'.join(C.SERIAL_STATUSES)}")
        where.append("status = ?")
        params.append(status)
    if owner_id:
        if owner_id == "none":
            where.append("owner_id IS NULL")
        else:
            where.append("owner_id = ?")
            params.append(owner_id)
    if product_id:
        where.append("product_id = ?")
        params.append(product_id)
    if batch_no:
        where.append("batch_no = ?")
        params.append(batch_no)

    S.scoped_where(where, params, allow_null=True)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    try:
        page = max(1, int(args.get("page", 1)))
        page_size = min(C.SERIAL_PAGE_SIZE_MAX,
                        max(1, int(args.get("page_size", C.SERIAL_PAGE_SIZE_DEFAULT))))
    except (TypeError, ValueError):
        raise SerialError("page/page_size 必须为整数")

    total = db.execute(f"SELECT COUNT(*) AS n FROM serial_no {where_sql}",
                       params).fetchone()["n"]
    rows = db.execute(
        f"SELECT * FROM serial_no {where_sql}"
        " ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()

    out = []
    for r in rows:
        prod = _product_of(db, r["product_id"]) if r["product_id"] else None
        out.append(_row_dict(r, product=prod))
    return {"total": total, "page": page, "page_size": page_size, "serials": out}


def stats(db, args):
    """按货品聚合的台账统计：总数 / 在库 / 已出库 / 冻结 / 报废。

    「应有数量」取 ``stock`` 里该货品的当前库存（只读，不改），
    让用户能看出「这个货品还差几个序列号没登记」。
    """
    where, params = [], []
    product_id = str(args.get("product_id") or "").strip()
    owner_id = str(args.get("owner_id") or "").strip()
    if product_id:
        where.append("product_id = ?")
        params.append(product_id)
    if owner_id:
        if owner_id == "none":
            where.append("owner_id IS NULL")
        else:
            where.append("owner_id = ?")
            params.append(owner_id)
    S.scoped_where(where, params, allow_null=True)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    rows = db.execute(
        f"SELECT product_id, status, COUNT(*) AS n FROM serial_no {where_sql}"
        " GROUP BY product_id, status", params,
    ).fetchall()

    agg = {}
    for r in rows:
        item = agg.setdefault(r["product_id"], {
            "product_id": r["product_id"], "sku": "", "name": "",
            "total": 0, "in_stock": 0, "out": 0, "frozen": 0, "scrapped": 0,
            "stock_qty": 0, "gap": 0,
        })
        item[r["status"]] = r["n"]
        item["total"] += r["n"]

    for pid, item in agg.items():
        prod = _product_of(db, pid)
        if prod:
            item["sku"] = prod["sku"]
            item["name"] = prod["name"]
        row = db.execute(
            "SELECT COALESCE(SUM(qty), 0) AS q FROM stock WHERE product_id = ?",
            (pid,),
        ).fetchone()
        item["stock_qty"] = row["q"]
        # 「缺口」＝当前在库库存 − 已在库台账数（只读提示，不写任何数据）
        item["gap"] = round((item["stock_qty"] or 0) - (item["in_stock"] or 0), 4)

    return {"items": sorted(agg.values(), key=lambda x: x["product_id"]),
            "total": sum(i["total"] for i in agg.values())}


def stats_batch(db, product_ids):
    """批量取多个货品的台账摘要（货品列表页一次拿当前页，避免 N+1 查询）。

    返回 ``{product_id: 摘要}``；没有台账的货品不出现在结果里（由调用方按缺省值渲染）。
    """
    ids = []
    for raw in product_ids or []:
        try:
            rid = int(raw)
        except (TypeError, ValueError):
            continue
        if rid > 0 and rid not in ids:
            ids.append(rid)
    if not ids:
        return {}
    ph = ",".join("?" for _ in ids)
    rows = db.execute(
        f"SELECT product_id, status, COUNT(*) AS n FROM serial_no"
        f" WHERE product_id IN ({ph}) GROUP BY product_id, status", ids,
    ).fetchall()
    out = {}
    for r in rows:
        item = out.setdefault(r["product_id"], {
            "product_id": r["product_id"], "total": 0, "in_stock": 0,
            "out": 0, "frozen": 0, "scrapped": 0,
        })
        item[r["status"]] = r["n"]
        item["total"] += r["n"]
    return out
