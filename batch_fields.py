"""批次信息字段字典（医疗器械 / GSP 口径）——前后端唯一口径来源。

后端用它播种「批次配置」、计算「关键批次信息」指纹、在验收入库时写入批次信息；
前端「批次配置」页通过 `/api/batch/fields` 取同一份字段清单，避免两处各写一套。

「关键批次信息」= 批次配置中 is_key=1 的字段值组合（再叠加货主 / 物料维度）。
两次验收入库若配置的关键字段值一致，则归入同一条批次档案，即用户所说的
「配置的部分批次信息内容一致，这个关键批次信息是一致的」。
"""
import hashlib
import json

# (字段名, 中文标签, 输入类型, 是否默认计入关键批次信息)
BATCH_INFO_FIELDS = (
    ("batch_no",               "批号",          "text",   1),
    ("production_date",        "生产日期",      "date",   1),
    ("expiry_date",            "失效日期",      "date",   1),
    ("sterilization_batch_no", "灭菌批号",      "text",   1),
    ("udi",                    "UDI",           "text",   1),
    ("origin",                 "产地",          "text",   1),
    ("production_enterprise",  "生产企业",      "text",   1),
    ("reg_cert_no",            "注册证号",      "text",   0),
    ("mfg_enterprise",         "厂家",          "text",   0),
    ("product_name",           "货品名称",      "text",   0),
    ("spec",                   "规格",          "text",   0),
    ("article_no",             "货号",          "text",   0),
    ("unit",                   "单位",          "text",   0),
    ("brand",                  "品牌",          "text",   0),
    ("registrant",             "注册人/备案人", "text",   0),
    ("mfg_license",            "生产许可证",    "text",   0),
    ("shelf_life_days",        "效期(天)",      "text",   0),
    ("patient_name",           "患者名称",      "text",   0),
    ("goods_property",         "货品属性",      "select", 0),
    ("internal_batch_no",      "内部批次号",    "text",   0),
)

FIELD_NAMES = tuple(f[0] for f in BATCH_INFO_FIELDS)
DEFAULT_KEY_FIELDS = tuple(f[0] for f in BATCH_INFO_FIELDS if f[3])

# 验收入库时字段为空则套用配置默认值（防止「合格/不合格」漏填）
FIELD_DEFAULTS = {"goods_property": "合格"}


def seed_batch_config(conn):
    """幂等播种批次字段配置：只补缺失行，不覆盖用户已改过的配置。"""
    for i, (name, label, typ, is_key) in enumerate(BATCH_INFO_FIELDS):
        conn.execute(
            "INSERT OR IGNORE INTO batch_config "
            "(field_name, label, input_type, is_key, enabled, default_value, sort_order) "
            "VALUES (?, ?, ?, ?, 1, ?, ?)",
            (name, label, typ, is_key, FIELD_DEFAULTS.get(name, ""), i + 1),
        )


def ensure_config(db):
    """配置表为空时补种（兼容老库 / 测试清表）；不提交事务，由调用方决定。"""
    if db.execute("SELECT COUNT(*) AS n FROM batch_config").fetchone()[0] == 0:
        seed_batch_config(db)


def key_fields(db):
    """当前生效的「关键批次信息」字段（按配置顺序）。"""
    rows = db.execute(
        "SELECT field_name FROM batch_config WHERE enabled = 1 AND is_key = 1 "
        "ORDER BY sort_order, id"
    ).fetchall()
    return [r[0] for r in rows]


def clean_info(info):
    """把任意输入规整成完整批次信息字典：只保留字典内字段，值统一为去空白字符串。"""
    src = info if isinstance(info, dict) else {}
    return {name: str(src.get(name) or "").strip() for name in FIELD_NAMES}


def build_batch_key(db, owner_id, product_id, info):
    """关键批次信息指纹：货主 + 物料 + 配置的关键字段值 → SHA-256。

    返回 (batch_key, key_info)；key_info 额外带 owner_id/product_id 便于页面展示与核对。
    """
    keys = key_fields(db) or list(DEFAULT_KEY_FIELDS)
    key_info = {k: str(info.get(k) or "").strip() for k in keys}
    key_info["owner_id"] = owner_id
    key_info["product_id"] = product_id
    raw = json.dumps(key_info, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest(), key_info
