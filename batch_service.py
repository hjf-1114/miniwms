"""批次服务层：验收入库时在同一事务内登记「批次档案 + 唯一批次流水」。

设计（医疗器械 / GSP 口径，M19）：
- `batch`         批次档案＝关键批次信息主档，按 (货主, 物料, 配置的关键字段值) 指纹去重
- `batch_receipt` 每次验收入库一条，`batch_no` 全局唯一 —— 每次入库都有唯一批次号
- 内部批次号：外部数据传入优先；首次入库 = 本次批次号；销退 = 沿用原批次内部批次号
- 路由层不直接写批次 SQL，统一经本服务；由 `routes_asn.receive()` 已有的
  `BEGIN IMMEDIATE` 事务调用，库存增加与批次写入原子完成

用户口径（2026-11-04）：「每次入库都要有唯一的批次号，内部批次号的含义是
如果是第一次入库和批次号一致，如果是退货的按出库的内部批次号一致，一般都是数据传过来的」。
"""
import datetime
import json

import constants as C
from batch_fields import FIELD_NAMES, build_batch_key, clean_info, ensure_config


def _next_no(db, table, column, prefix):
    """生成 前缀-YYYYMMDD-NNN；按当日已存在编号集合推进，删除中间行也不会重号。"""
    day = datetime.date.today().strftime("%Y%m%d")
    like = f"{prefix}-{day}-%"
    used = {r[0] for r in db.execute(
        f"SELECT {column} FROM {table} WHERE {column} LIKE ?", (like,)
    ).fetchall()}
    seq = len(used) + 1
    while f"{prefix}-{day}-{seq:03d}" in used:
        seq += 1
    return f"{prefix}-{day}-{seq:03d}"


def prepare_info(db, line_info):
    """规整明细快照，并套用批次配置里启用的默认值。"""
    ensure_config(db)
    info = clean_info(line_info)
    rows = db.execute(
        "SELECT field_name, default_value FROM batch_config "
        "WHERE enabled = 1 AND default_value != ''"
    ).fetchall()
    defaults = {r[0]: r[1] for r in rows}
    for name in FIELD_NAMES:
        if not str(info.get(name) or "").strip() and defaults.get(name):
            info[name] = str(defaults[name]).strip()
    return info


def validate_external_batch_no(doc_type, value):
    """M23 销退口径（用户 2026-11-09 确认）。

    * 非销退：可留空；填 ``/`` 归一为空串，不产生告警。
    * 销退：**外部批次号（原内部批次号）必填**，缺失直接拒绝；
      确实没有时显式填 ``/`` 作为合法占位，允许通过但写告警
      ``MISSING_EXTERNAL_BATCH_NO``，仍走历史回溯兜底。

    返回 ``(normalized, warning_code, error)``；``error`` 非空时调用方应返回 400。
    """
    raw = str(value or "").strip()
    if len(raw) > C.EXTERNAL_BATCH_NO_MAXLEN:
        return "", "", f"外部批次号最长 {C.EXTERNAL_BATCH_NO_MAXLEN} 字符"
    if doc_type != "销退":
        return ("" if raw == C.EXTERNAL_BATCH_NO_ABSENT else raw), "", ""
    if not raw:
        return "", "", ("销退单必须填写外部批次号（原内部批次号）；"
                        "确实没有请填 / 表示无")
    if raw == C.EXTERNAL_BATCH_NO_ABSENT:
        return C.EXTERNAL_BATCH_NO_ABSENT, C.WARN_MISSING_EXTERNAL_BATCH_NO, ""
    return raw, "", ""


def _resolve_internal(db, batch_id, batch_no, doc_type, provided, info):
    """内部批次号规则：数据传入 > 销退沿用原批次 > 首次入库 = 批次号。"""
    if provided:
        return provided, C.INTERNAL_BATCH_EXTERNAL
    from_line = str(info.get("internal_batch_no") or "").strip()
    if from_line:
        return from_line, C.INTERNAL_BATCH_EXTERNAL
    if doc_type == "销退":
        prior = db.execute(
            "SELECT internal_batch_no FROM batch_receipt WHERE batch_id = ? "
            "ORDER BY id DESC LIMIT 1", (batch_id,)
        ).fetchone()
        if prior and prior[0]:
            return prior[0], C.INTERNAL_BATCH_RETURN
    return batch_no, C.INTERNAL_BATCH_FIRST_IN


def record_receipt(db, *, owner_id, product_id, location_id, asn_id, asn_line_id,
                   qty, doc_type, line_info, internal_batch_no="",
                   designated_customer_id=None, external_batch_no=""):
    """登记一次验收入库。调用方须已开启事务（库存增加与批次写入同事务）。

    M23：``designated_customer_id`` 是验收时的「指定客户」录入值（默认空＝通用库存）；
    ``external_batch_no`` 是销退单外部传入的原内部批次号，``/`` 表示显式声明没有。
    """
    info = prepare_info(db, line_info)
    key_hash, key_info = build_batch_key(db, owner_id, product_id, info)

    profile = db.execute(
        "SELECT id, code FROM batch WHERE batch_key = ?", (key_hash,)
    ).fetchone()
    if profile is None:
        cur = db.execute(
            "INSERT INTO batch (code, batch_key, owner_id, product_id, key_info, info, receipt_count) "
            "VALUES (?, ?, ?, ?, ?, ?, 0)",
            (_next_no(db, "batch", "code", C.BATCH_PROFILE_PREFIX),
             key_hash, owner_id, product_id,
             json.dumps(key_info, ensure_ascii=False), json.dumps(info, ensure_ascii=False)),
        )
        profile_id, profile_code = cur.lastrowid, db.execute(
            "SELECT code FROM batch WHERE id = ?", (cur.lastrowid,)
        ).fetchone()[0]
    else:
        profile_id, profile_code = profile[0], profile[1]

    batch_no = _next_no(db, "batch_receipt", "batch_no", C.BATCH_RECEIPT_PREFIX)
    ext_norm, warning, ext_err = validate_external_batch_no(doc_type, external_batch_no)
    if ext_err:
        raise ValueError(ext_err)
    # 外部批次号（销退单必填）优先作为内部批次号来源；显式 '/' 时不采用，
    # 交给 _resolve_internal 的历史回溯兜底，并保留告警标记。
    provided = str(internal_batch_no or "").strip()
    if not provided and ext_norm and ext_norm != C.EXTERNAL_BATCH_NO_ABSENT:
        provided = ext_norm
    internal, source = _resolve_internal(
        db, profile_id, batch_no, doc_type, provided, info,
    )
    cur = db.execute(
        """INSERT INTO batch_receipt
           (batch_no, batch_id, internal_batch_no, internal_source, owner_id, product_id,
            location_id, asn_id, asn_line_id, qty, doc_type, goods_property, info,
            designated_customer_id, external_batch_no, warning)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (batch_no, profile_id, internal, source, owner_id, product_id,
         location_id, asn_id, asn_line_id, qty, doc_type,
         info.get("goods_property", ""), json.dumps(info, ensure_ascii=False),
         designated_customer_id, ext_norm, warning),
    )
    db.execute(
        "UPDATE batch SET receipt_count = receipt_count + 1, "
        "updated_at = datetime('now','localtime') WHERE id = ?", (profile_id,)
    )
    return {
        "receipt_id": cur.lastrowid,
        "batch_no": batch_no,
        "batch_id": profile_id,
        "batch_code": profile_code,
        "batch_key": key_hash,
        "key_info": key_info,
        "info": info,
        "internal_batch_no": internal,
        "internal_source": source,
        "designated_customer_id": designated_customer_id,
        "external_batch_no": ext_norm,
        "warning": warning,
    }


def create_profile(db, owner_id, product_id, info):
    """手动预配置批次档案；关键批次信息已存在时返回已存在档案 id（不重复建）。"""
    clean = prepare_info(db, info)
    key_hash, key_info = build_batch_key(db, owner_id, product_id, clean)
    exists = db.execute("SELECT id FROM batch WHERE batch_key = ?", (key_hash,)).fetchone()
    if exists:
        return exists[0], False
    cur = db.execute(
        "INSERT INTO batch (code, batch_key, owner_id, product_id, key_info, info, receipt_count) "
        "VALUES (?, ?, ?, ?, ?, ?, 0)",
        (_next_no(db, "batch", "code", C.BATCH_PROFILE_PREFIX),
         key_hash, owner_id, product_id,
         json.dumps(key_info, ensure_ascii=False), json.dumps(clean, ensure_ascii=False)),
    )
    return cur.lastrowid, True
