"""MiniWMS -- 轻量仓库管理系统（WMS）入口。

职责：
- Flask 应用创建与配置
- SQLite 连接管理（请求级连接，g 上下文）
- 数据库初始化（建表 + 列迁移）
- 聚合各业务 Blueprint

依赖：仅 Python 标准库 + Flask。
"""
import json
import os
import sqlite3

from flask import Flask, g, request

DB_PATH = os.environ.get(
    "MINIWMS_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "miniwms.db"),
)

app = Flask(__name__, static_folder="web_app", static_url_path="")


@app.route("/")
def index():
    """首页：前端 demo 导航页（web_app/index.html）。"""
    return app.send_static_file("index.html")


def get_db():
    """获取当前请求的数据库连接（懒创建，请求结束自动关闭）。"""
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _seed_auth(conn):
    """初始化默认角色（管理员/操作员/只读）与 admin 账号（仅空库时）。"""
    from auth import hash_password

    if conn.execute("SELECT COUNT(*) FROM role").fetchone()[0] == 0:
        roles = {
            "管理员": ["*"],
            "操作员": ["owner:*", "product:*", "location:*", "customer:*", "asn:*", "dn:*", "stock:read"],
            "只读": ["owner:read", "product:read", "location:read", "customer:read", "asn:read", "dn:read", "stock:read"],
        }
        for name, perms in roles.items():
            conn.execute(
                "INSERT INTO role (name, permissions, created_at, updated_at) "
                "VALUES (?, ?, datetime('now','localtime'), datetime('now','localtime'))",
                (name, json.dumps(perms)),
            )
        conn.commit()
    if conn.execute("SELECT COUNT(*) FROM user").fetchone()[0] == 0:
        salt, h = hash_password("admin123")
        admin_role = conn.execute("SELECT id FROM role WHERE name = ?", ("管理员",)).fetchone()[0]
        conn.execute(
            "INSERT INTO user (username, password_salt, password_hash, role_id, status, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, 'active', datetime('now','localtime'), datetime('now','localtime'))",
            ("admin", salt, h, admin_role),
        )
        conn.commit()


def request_payload():
    """统一取参：优先 JSON，退回表单。"""
    return request.get_json(silent=True) or request.form or {}


def gen_doc_no(db, table, prefix):
    """生成业务单号：前缀-YYYYMMDD-NNN（当日前缀下计数 +1，3 位补零）。

    留空单号时调用；创建接口传入 code 则优先用传入值，实现「英文开头 + 日期流水号」。
    """
    import datetime

    day = datetime.date.today().strftime("%Y%m%d")
    like = f"{prefix}-{day}-%"
    n = db.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE code LIKE ?", (like,)).fetchone()["n"]
    return f"{prefix}-{day}-{n + 1:03d}"


def _migrate_m20_columns(conn):
    """M20 幂等列迁移：库位挂仓库/储区 + 混放开关、货品加推荐储区、
    ASN/DN 加 4 个单据标识位与 external_doc_type（阶段 B）。

    混放字段在 location 上允许 NULL，表示「继承所属储区」；zone 上是 0/1 实值。
    单据标识位一律 0/1 并带默认值，老库补列后旧单据自动为 0 / 空串。
    """
    doc_cols = [
        ("is_virtual", "INTEGER NOT NULL DEFAULT 0"),
        ("is_direct", "INTEGER NOT NULL DEFAULT 0"),
        ("is_cold_chain", "INTEGER NOT NULL DEFAULT 0"),
        ("is_hazard", "INTEGER NOT NULL DEFAULT 0"),
        ("external_doc_type", "TEXT NOT NULL DEFAULT ''"),
    ]
    wanted = {
        "location": [
            ("zone_id", "INTEGER REFERENCES zone(id)"),
            ("warehouse_id", "INTEGER REFERENCES warehouse(id)"),
            ("allow_mix_product", "INTEGER"),
            ("allow_mix_batch", "INTEGER"),
            ("allow_mix_owner", "INTEGER"),
        ],
        "product": [("default_zone_id", "INTEGER REFERENCES zone(id)")],
        "asn": doc_cols,
        "dn": doc_cols,
    }
    for table, cols in wanted.items():
        exists = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, ddl in cols:
            if name not in exists:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_location_zone_id ON location(zone_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_location_warehouse ON location(warehouse_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_product_default_zone ON product(default_zone_id)")


def _seed_zones(conn):
    """播种预置储区：3 个全仓共享 + 每个仓库一个常温默认区（幂等，不覆盖用户改动）。"""
    shared = (
        ("ZONE-SHARED", "全仓共享默认区", "常温"),
        ("ZONE-COLD", "冷藏区", "冷藏"),
        ("ZONE-NG", "不合格隔离区", "常温"),
    )
    for code, name, temp in shared:
        conn.execute(
            "INSERT OR IGNORE INTO zone (code, name, warehouse_id, temp_class, "
            "allow_mix_product, allow_mix_batch, allow_mix_owner, status, note) "
            "VALUES (?, ?, NULL, ?, 0, 0, 0, 'active', '')",
            (code, name, temp),
        )
    for wh in conn.execute("SELECT id, code FROM warehouse").fetchall():
        conn.execute(
            "INSERT OR IGNORE INTO zone (code, name, warehouse_id, temp_class, "
            "allow_mix_product, allow_mix_batch, allow_mix_owner, status, note) "
            "VALUES (?, ?, ?, '常温', 0, 0, 0, 'active', '')",
            (f"ZONE-{wh[1]}-DEFAULT", f"{wh[1]} 默认储区", wh[0]),
        )


def init_db():
    """初始化数据库：建表与列迁移（幂等，可重复执行）。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS owner (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                code        TEXT    NOT NULL UNIQUE,
                name        TEXT    NOT NULL,
                contact     TEXT    NOT NULL DEFAULT '',
                phone       TEXT    NOT NULL DEFAULT '',
                email       TEXT    NOT NULL DEFAULT '',
                address     TEXT    NOT NULL DEFAULT '',
                start_date  TEXT    NOT NULL DEFAULT '',
                end_date    TEXT    NOT NULL DEFAULT '',
                status      TEXT    NOT NULL DEFAULT 'active',
                note        TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                updated_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_owner_status ON owner(status);
            CREATE TABLE IF NOT EXISTS product (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                sku             TEXT    NOT NULL UNIQUE,
                name            TEXT    NOT NULL,
                category        TEXT    NOT NULL DEFAULT '未分类',
                unit            TEXT    NOT NULL DEFAULT '件',
                barcode         TEXT    NOT NULL DEFAULT '',
                brand           TEXT    NOT NULL DEFAULT '',
                spec            TEXT    NOT NULL DEFAULT '',
                weight_kg       REAL    NOT NULL DEFAULT 0,
                length_cm       REAL    NOT NULL DEFAULT 0,
                width_cm        REAL    NOT NULL DEFAULT 0,
                height_cm       REAL    NOT NULL DEFAULT 0,
                volume          REAL    NOT NULL DEFAULT 0,
                shelf_life_days INTEGER NOT NULL DEFAULT 0,
                purchase_price  REAL    NOT NULL DEFAULT 0,
                sale_price      REAL    NOT NULL DEFAULT 0,
                stock_qty       REAL    NOT NULL DEFAULT 0,
                min_stock       REAL    NOT NULL DEFAULT 0,
                status          TEXT    NOT NULL DEFAULT 'active',
                note            TEXT    NOT NULL DEFAULT '',
                created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                updated_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_product_category ON product(category);
            CREATE INDEX IF NOT EXISTS idx_product_status   ON product(status);
            CREATE TABLE IF NOT EXISTS location (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                code        TEXT    NOT NULL UNIQUE,
                name        TEXT    NOT NULL,
                zone        TEXT    NOT NULL DEFAULT '默认区',
                loc_type    TEXT    NOT NULL DEFAULT 'storage',
                capacity    REAL    NOT NULL DEFAULT 0,
                aisle       TEXT    NOT NULL DEFAULT '',
                bay         TEXT    NOT NULL DEFAULT '',
                level       TEXT    NOT NULL DEFAULT '',
                barcode     TEXT    NOT NULL DEFAULT '',
                status      TEXT    NOT NULL DEFAULT 'active',
                note        TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                updated_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_location_zone  ON location(zone);
            CREATE INDEX IF NOT EXISTS idx_location_type  ON location(loc_type);
            CREATE INDEX IF NOT EXISTS idx_location_status ON location(status);
            -- 库存账（按 货主×物料×库位 唯一）：M3 建表供库位删除保护使用，
            -- M4 收货上架开始写入，M6 提供库存账查询
            CREATE TABLE IF NOT EXISTS stock (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id      INTEGER NOT NULL REFERENCES owner(id),
                product_id    INTEGER NOT NULL REFERENCES product(id),
                location_id   INTEGER NOT NULL REFERENCES location(id),
                qty           REAL    NOT NULL DEFAULT 0,
                allocated_qty REAL    NOT NULL DEFAULT 0,
                updated_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                UNIQUE(owner_id, product_id, location_id)
            );
            CREATE INDEX IF NOT EXISTS idx_stock_location ON stock(location_id);
            """
        )
        # M16 供应商主数据（收货单的上游来源，归属货主，多货主隔离）
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS supplier (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                code        TEXT    NOT NULL UNIQUE,
                name        TEXT    NOT NULL,
                owner_id    INTEGER NOT NULL REFERENCES owner(id),
                contact     TEXT    NOT NULL DEFAULT '',
                phone       TEXT    NOT NULL DEFAULT '',
                address     TEXT    NOT NULL DEFAULT '',
                status      TEXT    NOT NULL DEFAULT 'active',
                note        TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                updated_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_supplier_owner  ON supplier(owner_id);
            CREATE INDEX IF NOT EXISTS idx_supplier_status ON supplier(status);
            """
        )
        # M16 仓库主数据（多仓库支持；仓库为运营方资产，全系统共享，不绑定货主）
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS warehouse (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                code        TEXT    NOT NULL UNIQUE,
                name        TEXT    NOT NULL,
                address     TEXT    NOT NULL DEFAULT '',
                contact     TEXT    NOT NULL DEFAULT '',
                phone       TEXT    NOT NULL DEFAULT '',
                status      TEXT    NOT NULL DEFAULT 'active',
                note        TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                updated_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_warehouse_status ON warehouse(status);
            """
        )
        # 列迁移：product.owner_id（M2 多货主；NULL 表示自营/未指定货主）
        cols = {r[1] for r in conn.execute("PRAGMA table_info(product)")}
        if "owner_id" not in cols:
            conn.execute(
                "ALTER TABLE product ADD COLUMN owner_id INTEGER"
                " REFERENCES owner(id)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_product_owner ON product(owner_id)")
        # 列迁移：stock.allocated_qty（M4，参考 Sentry WMS 三数量字段设计；
        # 旧库的 stock 表无此列，新库 DDL 已自带则跳过）
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if "stock" in tables:
            stock_cols = {r[1] for r in conn.execute("PRAGMA table_info(stock)")}
            if "allocated_qty" not in stock_cols:
                conn.execute(
                    "ALTER TABLE stock ADD COLUMN allocated_qty REAL NOT NULL DEFAULT 0"
                )
        # M4 收货单：ASN 主表 + 行表 + 库存动作流水
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS asn (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                code            TEXT    NOT NULL UNIQUE,
                owner_id        INTEGER NOT NULL REFERENCES owner(id),
                doc_type        TEXT    NOT NULL DEFAULT '采购',
                external_no     TEXT    NOT NULL DEFAULT '',
                order_source    TEXT    NOT NULL DEFAULT '',
                is_virtual      INTEGER NOT NULL DEFAULT 0,
                is_direct       INTEGER NOT NULL DEFAULT 0,
                is_cold_chain   INTEGER NOT NULL DEFAULT 0,
                is_hazard       INTEGER NOT NULL DEFAULT 0,
                external_doc_type TEXT  NOT NULL DEFAULT '',
                source_warehouse INTEGER REFERENCES warehouse(id),
                supplier_id     INTEGER REFERENCES supplier(id),
                customer_name   TEXT    NOT NULL DEFAULT '',
                customer_id     INTEGER REFERENCES customer(id),
                customer_contact TEXT   NOT NULL DEFAULT '',
                customer_phone  TEXT    NOT NULL DEFAULT '',
                customer_address TEXT   NOT NULL DEFAULT '',
                carrier_name    TEXT    NOT NULL DEFAULT '',
                supplier_name   TEXT    NOT NULL DEFAULT '',
                supplier_contact TEXT   NOT NULL DEFAULT '',
                supplier_phone  TEXT    NOT NULL DEFAULT '',
                supplier_address TEXT   NOT NULL DEFAULT '',
                total_qty       REAL    NOT NULL DEFAULT 0,
                category_count  INTEGER NOT NULL DEFAULT 0,
                status          TEXT    NOT NULL DEFAULT 'open',
                extra1          TEXT    NOT NULL DEFAULT '',
                extra2          TEXT    NOT NULL DEFAULT '',
                extra3          TEXT    NOT NULL DEFAULT '',
                note            TEXT    NOT NULL DEFAULT '',
                created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                updated_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_asn_owner  ON asn(owner_id);
            CREATE INDEX IF NOT EXISTS idx_asn_status ON asn(status);
            CREATE TABLE IF NOT EXISTS asn_line (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                asn_id       INTEGER NOT NULL REFERENCES asn(id),
                product_id   INTEGER NOT NULL REFERENCES product(id),
                expected_qty REAL    NOT NULL,
                received_qty REAL    NOT NULL DEFAULT 0,
                putaway_qty  REAL    NOT NULL DEFAULT 0,
                extra        TEXT    NOT NULL DEFAULT '',
                UNIQUE(asn_id, product_id)
            );
            CREATE INDEX IF NOT EXISTS idx_asn_line_asn ON asn_line(asn_id);
            CREATE TABLE IF NOT EXISTS stock_movement (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id    INTEGER NOT NULL REFERENCES owner(id),
                product_id  INTEGER NOT NULL REFERENCES product(id),
                location_id INTEGER NOT NULL REFERENCES location(id),
                qty_delta   REAL    NOT NULL,
                ref_type    TEXT    NOT NULL,
                ref_id      INTEGER,
                note        TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_movement_owner ON stock_movement(owner_id);
            CREATE INDEX IF NOT EXISTS idx_movement_ref   ON stock_movement(ref_type, ref_id);
            -- M7 客户主数据：客户归属货主（多货主下客户是货主的客户），出库单关联客户
            CREATE TABLE IF NOT EXISTS customer (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                code        TEXT    NOT NULL UNIQUE,
                name        TEXT    NOT NULL,
                owner_id    INTEGER NOT NULL REFERENCES owner(id),
                contact     TEXT    NOT NULL DEFAULT '',
                phone       TEXT    NOT NULL DEFAULT '',
                address     TEXT    NOT NULL DEFAULT '',
                email       TEXT    NOT NULL DEFAULT '',
                level       TEXT    NOT NULL DEFAULT '普通',
                region      TEXT    NOT NULL DEFAULT '',
                status      TEXT    NOT NULL DEFAULT 'active',
                note        TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                updated_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_customer_owner  ON customer(owner_id);
            CREATE INDEX IF NOT EXISTS idx_customer_status ON customer(status);
            -- M5 出库单：主表 + 行表 + 拣货明细（拣货在哪些库位各分配多少，发货按此扣减）
            CREATE TABLE IF NOT EXISTS dn (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                code            TEXT    NOT NULL UNIQUE,
                owner_id        INTEGER NOT NULL REFERENCES owner(id),
                customer_id     INTEGER REFERENCES customer(id),
                doc_type        TEXT    NOT NULL DEFAULT '销售',
                external_no     TEXT    NOT NULL DEFAULT '',
                order_source    TEXT    NOT NULL DEFAULT '',
                is_virtual      INTEGER NOT NULL DEFAULT 0,
                is_direct       INTEGER NOT NULL DEFAULT 0,
                is_cold_chain   INTEGER NOT NULL DEFAULT 0,
                is_hazard       INTEGER NOT NULL DEFAULT 0,
                external_doc_type TEXT  NOT NULL DEFAULT '',
                source_warehouse INTEGER REFERENCES warehouse(id),
                carrier_name    TEXT    NOT NULL DEFAULT '',
                total_qty       REAL    NOT NULL DEFAULT 0,
                category_count  INTEGER NOT NULL DEFAULT 0,
                status          TEXT    NOT NULL DEFAULT 'open',
                extra1          TEXT    NOT NULL DEFAULT '',
                extra2          TEXT    NOT NULL DEFAULT '',
                extra3          TEXT    NOT NULL DEFAULT '',
                note            TEXT    NOT NULL DEFAULT '',
                created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                updated_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_dn_owner  ON dn(owner_id);
            CREATE INDEX IF NOT EXISTS idx_dn_status ON dn(status);
            CREATE TABLE IF NOT EXISTS dn_line (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                dn_id        INTEGER NOT NULL REFERENCES dn(id),
                product_id   INTEGER NOT NULL REFERENCES product(id),
                ordered_qty  REAL    NOT NULL,
                picked_qty   REAL    NOT NULL DEFAULT 0,
                UNIQUE(dn_id, product_id)
            );
            CREATE INDEX IF NOT EXISTS idx_dn_line_dn ON dn_line(dn_id);
            CREATE TABLE IF NOT EXISTS dn_pick (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                dn_id       INTEGER NOT NULL REFERENCES dn(id),
                line_id     INTEGER NOT NULL REFERENCES dn_line(id),
                location_id INTEGER NOT NULL REFERENCES location(id),
                qty         REAL    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_dn_pick_dn ON dn_pick(dn_id);
            """
        )
        # M7 客户主数据：customer 表（新库 DDL 已带则跳过）+ dn.customer_id 列迁移
        if "customer" not in tables:
            conn.executescript(
                """                   CREATE TABLE IF NOT EXISTS customer (
                       id          INTEGER PRIMARY KEY AUTOINCREMENT,
                       code        TEXT    NOT NULL UNIQUE,
                       name        TEXT    NOT NULL,
                       owner_id    INTEGER NOT NULL REFERENCES owner(id),
                       contact     TEXT    NOT NULL DEFAULT '',
                       phone       TEXT    NOT NULL DEFAULT '',
                       address     TEXT    NOT NULL DEFAULT '',
                       email       TEXT    NOT NULL DEFAULT '',
                       level       TEXT    NOT NULL DEFAULT '普通',
                       region      TEXT    NOT NULL DEFAULT '',
                       status      TEXT    NOT NULL DEFAULT 'active',
                       note        TEXT    NOT NULL DEFAULT '',
                       created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                       updated_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
                   );
                   CREATE INDEX IF NOT EXISTS idx_customer_owner  ON customer(owner_id);
                   CREATE INDEX IF NOT EXISTS idx_customer_status ON customer(status);"""
            )
        if "dn" in tables:
            dn_cols = {r[1] for r in conn.execute("PRAGMA table_info(dn)")}
            if "customer_id" not in dn_cols:
                conn.execute(
                    "ALTER TABLE dn ADD COLUMN customer_id INTEGER REFERENCES customer(id)"
                )
        # M16 单据字段增强：ASN/DN 新增单据类型/外部单号/来源仓库/供应商/承运商/自定义字段/汇总字段
        if "asn" in tables:
            asn_cols = {r[1] for r in conn.execute("PRAGMA table_info(asn)")}
            for col, ddl in (
                ("doc_type", "ALTER TABLE asn ADD COLUMN doc_type TEXT NOT NULL DEFAULT '采购'"),
                ("external_no", "ALTER TABLE asn ADD COLUMN external_no TEXT NOT NULL DEFAULT ''"),
                ("order_source", "ALTER TABLE asn ADD COLUMN order_source TEXT NOT NULL DEFAULT ''"),
                ("source_warehouse", "ALTER TABLE asn ADD COLUMN source_warehouse INTEGER REFERENCES warehouse(id)"),
                ("supplier_id", "ALTER TABLE asn ADD COLUMN supplier_id INTEGER REFERENCES supplier(id)"),
                ("customer_name", "ALTER TABLE asn ADD COLUMN customer_name TEXT NOT NULL DEFAULT ''"),
                ("carrier_name", "ALTER TABLE asn ADD COLUMN carrier_name TEXT NOT NULL DEFAULT ''"),
                ("supplier_name", "ALTER TABLE asn ADD COLUMN supplier_name TEXT NOT NULL DEFAULT ''"),
                ("supplier_contact", "ALTER TABLE asn ADD COLUMN supplier_contact TEXT NOT NULL DEFAULT ''"),
                ("supplier_phone", "ALTER TABLE asn ADD COLUMN supplier_phone TEXT NOT NULL DEFAULT ''"),
                ("supplier_address", "ALTER TABLE asn ADD COLUMN supplier_address TEXT NOT NULL DEFAULT ''"),
                ("total_qty", "ALTER TABLE asn ADD COLUMN total_qty REAL NOT NULL DEFAULT 0"),
                ("category_count", "ALTER TABLE asn ADD COLUMN category_count INTEGER NOT NULL DEFAULT 0"),
                ("extra1", "ALTER TABLE asn ADD COLUMN extra1 TEXT NOT NULL DEFAULT ''"),
                ("extra2", "ALTER TABLE asn ADD COLUMN extra2 TEXT NOT NULL DEFAULT ''"),
                ("extra3", "ALTER TABLE asn ADD COLUMN extra3 TEXT NOT NULL DEFAULT ''"),
                # 收货单往来单位联系方式（可在下单时覆盖主数据快照）
                ("customer_id", "ALTER TABLE asn ADD COLUMN customer_id INTEGER REFERENCES customer(id)"),
                ("customer_contact", "ALTER TABLE asn ADD COLUMN customer_contact TEXT NOT NULL DEFAULT ''"),
                ("customer_phone", "ALTER TABLE asn ADD COLUMN customer_phone TEXT NOT NULL DEFAULT ''"),
                ("customer_address", "ALTER TABLE asn ADD COLUMN customer_address TEXT NOT NULL DEFAULT ''"),
            ):
                if col not in asn_cols:
                    conn.execute(ddl)
        # 收货明细商品档案快照（JSON：规格/货号/批号/效期/注册证等，选商品时自动带出）
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='asn_line'"
        ).fetchone():
            line_cols = {r[1] for r in conn.execute("PRAGMA table_info(asn_line)")}
            if "extra" not in line_cols:
                conn.execute("ALTER TABLE asn_line ADD COLUMN extra TEXT NOT NULL DEFAULT ''")
        if "dn" in tables:
            dn_cols = {r[1] for r in conn.execute("PRAGMA table_info(dn)")}
            for col, ddl in (
                ("doc_type", "ALTER TABLE dn ADD COLUMN doc_type TEXT NOT NULL DEFAULT '销售'"),
                ("external_no", "ALTER TABLE dn ADD COLUMN external_no TEXT NOT NULL DEFAULT ''"),
                ("order_source", "ALTER TABLE dn ADD COLUMN order_source TEXT NOT NULL DEFAULT ''"),
                ("source_warehouse", "ALTER TABLE dn ADD COLUMN source_warehouse INTEGER REFERENCES warehouse(id)"),
                ("carrier_name", "ALTER TABLE dn ADD COLUMN carrier_name TEXT NOT NULL DEFAULT ''"),
                ("total_qty", "ALTER TABLE dn ADD COLUMN total_qty REAL NOT NULL DEFAULT 0"),
                ("category_count", "ALTER TABLE dn ADD COLUMN category_count INTEGER NOT NULL DEFAULT 0"),
                ("extra1", "ALTER TABLE dn ADD COLUMN extra1 TEXT NOT NULL DEFAULT ''"),
                ("extra2", "ALTER TABLE dn ADD COLUMN extra2 TEXT NOT NULL DEFAULT ''"),
                ("extra3", "ALTER TABLE dn ADD COLUMN extra3 TEXT NOT NULL DEFAULT ''"),
            ):
                if col not in dn_cols:
                    conn.execute(ddl)
        # M8 主数据字段增强：电商场景补充字段（旧库 ALTER 迁移；新库 DDL 已带则跳过）
        if "owner" in tables:
            owner_cols = {r[1] for r in conn.execute("PRAGMA table_info(owner)")}
            for col, ddl in (
                ("email", "ALTER TABLE owner ADD COLUMN email TEXT NOT NULL DEFAULT ''"),
                ("address", "ALTER TABLE owner ADD COLUMN address TEXT NOT NULL DEFAULT ''"),
            ):
                if col not in owner_cols:
                    conn.execute(ddl)
        # M11 货主主数据深化：省市区/备用地址/授权仓库/经营范围/备用字段 + 证照子表
        if "owner" in tables:
            owner_cols = {r[1] for r in conn.execute("PRAGMA table_info(owner)")}
            for col, ddl in (
                ("province", "ALTER TABLE owner ADD COLUMN province TEXT NOT NULL DEFAULT ''"),
                ("city", "ALTER TABLE owner ADD COLUMN city TEXT NOT NULL DEFAULT ''"),
                ("district", "ALTER TABLE owner ADD COLUMN district TEXT NOT NULL DEFAULT ''"),
                ("backup_address", "ALTER TABLE owner ADD COLUMN backup_address TEXT NOT NULL DEFAULT ''"),
                ("authorized_warehouse", "ALTER TABLE owner ADD COLUMN authorized_warehouse TEXT NOT NULL DEFAULT ''"),
                ("business_scope", "ALTER TABLE owner ADD COLUMN business_scope TEXT NOT NULL DEFAULT ''"),
                ("start_date", "ALTER TABLE owner ADD COLUMN start_date TEXT NOT NULL DEFAULT ''"),
                ("end_date", "ALTER TABLE owner ADD COLUMN end_date TEXT NOT NULL DEFAULT ''"),
                ("extra1", "ALTER TABLE owner ADD COLUMN extra1 TEXT NOT NULL DEFAULT ''"),
                ("extra2", "ALTER TABLE owner ADD COLUMN extra2 TEXT NOT NULL DEFAULT ''"),
            ):
                if col not in owner_cols:
                    conn.execute(ddl)
        # M11 货主证照子表（营业执照/医疗器械经营许可等，可多证）
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS owner_cert (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id      INTEGER NOT NULL REFERENCES owner(id),
                cert_type     TEXT    NOT NULL DEFAULT '',
                cert_no       TEXT    NOT NULL DEFAULT '',
                issuer        TEXT    NOT NULL DEFAULT '',
                valid_from    TEXT    NOT NULL DEFAULT '',
                valid_to      TEXT    NOT NULL DEFAULT '',
                note          TEXT    NOT NULL DEFAULT '',
                created_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_owner_cert_owner ON owner_cert(owner_id);
            """
        )
        # 注意：customer 表在本函数稍早才创建，不能用函数开头的 tables 快照判断，
        # 否则全新库会跳过列迁移导致 /api/customers 插入缺列（实测新库 bug）。
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='customer'"
        ).fetchone():
            cust_cols = {r[1] for r in conn.execute("PRAGMA table_info(customer)")}
            for col, ddl in (
                ("email", "ALTER TABLE customer ADD COLUMN email TEXT NOT NULL DEFAULT ''"),
                ("level", "ALTER TABLE customer ADD COLUMN level TEXT NOT NULL DEFAULT '普通'"),
                ("region", "ALTER TABLE customer ADD COLUMN region TEXT NOT NULL DEFAULT ''"),
            ):
                if col not in cust_cols:
                    conn.execute(ddl)
        # M12 客户主数据深化：省市区 + 详细地址 + 经营范围（同样用实时表判断）
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='customer'"
        ).fetchone():
            cust_cols = {r[1] for r in conn.execute("PRAGMA table_info(customer)")}
            for col, ddl in (
                ("province", "ALTER TABLE customer ADD COLUMN province TEXT NOT NULL DEFAULT ''"),
                ("city", "ALTER TABLE customer ADD COLUMN city TEXT NOT NULL DEFAULT ''"),
                ("district", "ALTER TABLE customer ADD COLUMN district TEXT NOT NULL DEFAULT ''"),
                ("detail_address", "ALTER TABLE customer ADD COLUMN detail_address TEXT NOT NULL DEFAULT ''"),
                ("business_scope", "ALTER TABLE customer ADD COLUMN business_scope TEXT NOT NULL DEFAULT ''"),
            ):
                if col not in cust_cols:
                    conn.execute(ddl)
        if "product" in tables:
            prod_cols = {r[1] for r in conn.execute("PRAGMA table_info(product)")}
            for col, ddl in (
                ("barcode", "ALTER TABLE product ADD COLUMN barcode TEXT NOT NULL DEFAULT ''"),
                ("brand", "ALTER TABLE product ADD COLUMN brand TEXT NOT NULL DEFAULT ''"),
                ("spec", "ALTER TABLE product ADD COLUMN spec TEXT NOT NULL DEFAULT ''"),
                ("weight_kg", "ALTER TABLE product ADD COLUMN weight_kg REAL NOT NULL DEFAULT 0"),
                ("length_cm", "ALTER TABLE product ADD COLUMN length_cm REAL NOT NULL DEFAULT 0"),
                ("width_cm", "ALTER TABLE product ADD COLUMN width_cm REAL NOT NULL DEFAULT 0"),
                ("height_cm", "ALTER TABLE product ADD COLUMN height_cm REAL NOT NULL DEFAULT 0"),
                ("shelf_life_days", "ALTER TABLE product ADD COLUMN shelf_life_days INTEGER NOT NULL DEFAULT 0"),
            ):
                if col not in prod_cols:
                    conn.execute(ddl)
        # M10 物料主数据深化：电商/医药级字段（旧库 ALTER 迁移；新库 DDL 已带则跳过）
        if "product" in tables:
            prod_cols = {r[1] for r in conn.execute("PRAGMA table_info(product)")}
            for col, ddl in (
                ("article_no", "ALTER TABLE product ADD COLUMN article_no TEXT NOT NULL DEFAULT ''"),
                ("category_l1", "ALTER TABLE product ADD COLUMN category_l1 TEXT NOT NULL DEFAULT ''"),
                ("category_l2", "ALTER TABLE product ADD COLUMN category_l2 TEXT NOT NULL DEFAULT ''"),
                ("category_l3", "ALTER TABLE product ADD COLUMN category_l3 TEXT NOT NULL DEFAULT ''"),
                ("category_l4", "ALTER TABLE product ADD COLUMN category_l4 TEXT NOT NULL DEFAULT ''"),
                ("product_type", "ALTER TABLE product ADD COLUMN product_type TEXT NOT NULL DEFAULT ''"),
                ("origin", "ALTER TABLE product ADD COLUMN origin TEXT NOT NULL DEFAULT ''"),
                ("business_scope", "ALTER TABLE product ADD COLUMN business_scope TEXT NOT NULL DEFAULT ''"),
                ("reg_cert_no", "ALTER TABLE product ADD COLUMN reg_cert_no TEXT NOT NULL DEFAULT ''"),
                ("registrant", "ALTER TABLE product ADD COLUMN registrant TEXT NOT NULL DEFAULT ''"),
                ("mfg_enterprise", "ALTER TABLE product ADD COLUMN mfg_enterprise TEXT NOT NULL DEFAULT ''"),
                ("mfg_license", "ALTER TABLE product ADD COLUMN mfg_license TEXT NOT NULL DEFAULT ''"),
                ("external_code", "ALTER TABLE product ADD COLUMN external_code TEXT NOT NULL DEFAULT ''"),
                ("main_category", "ALTER TABLE product ADD COLUMN main_category TEXT NOT NULL DEFAULT ''"),
                ("temp_range", "ALTER TABLE product ADD COLUMN temp_range TEXT NOT NULL DEFAULT ''"),
                ("humidity_range", "ALTER TABLE product ADD COLUMN humidity_range TEXT NOT NULL DEFAULT ''"),
                ("hazard_type", "ALTER TABLE product ADD COLUMN hazard_type TEXT NOT NULL DEFAULT ''"),
                ("is_serial", "ALTER TABLE product ADD COLUMN is_serial INTEGER NOT NULL DEFAULT 0"),
                ("is_shelf_control", "ALTER TABLE product ADD COLUMN is_shelf_control INTEGER NOT NULL DEFAULT 0"),
                ("extra1", "ALTER TABLE product ADD COLUMN extra1 TEXT NOT NULL DEFAULT ''"),
                ("extra2", "ALTER TABLE product ADD COLUMN extra2 TEXT NOT NULL DEFAULT ''"),
                ("extra3", "ALTER TABLE product ADD COLUMN extra3 TEXT NOT NULL DEFAULT ''"),
            ):
                if col not in prod_cols:
                    conn.execute(ddl)
        # M15 物料体积（可手填；未填则按长宽高自动换算 cm³）
        if "product" in tables:
            if "volume" not in {r[1] for r in conn.execute("PRAGMA table_info(product)")}:
                conn.execute("ALTER TABLE product ADD COLUMN volume REAL NOT NULL DEFAULT 0")
        # M10 物料条码/包装子表（多条码扫码、多层级包装规格；新库 CREATE IF NOT EXISTS 自带，旧库执行一次）
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS product_barcode (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id  INTEGER NOT NULL REFERENCES product(id),
                barcode     TEXT    NOT NULL,
                is_primary  INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_pb_product ON product_barcode(product_id);
            CREATE INDEX IF NOT EXISTS idx_pb_barcode ON product_barcode(barcode);
            CREATE TABLE IF NOT EXISTS product_package (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id       INTEGER NOT NULL REFERENCES product(id),
                pkg_level        TEXT    NOT NULL DEFAULT '主箱',
                length_cm        REAL    NOT NULL DEFAULT 0,
                width_cm         REAL    NOT NULL DEFAULT 0,
                height_cm        REAL    NOT NULL DEFAULT 0,
                volume           REAL    NOT NULL DEFAULT 0,
                weight_kg        REAL    NOT NULL DEFAULT 0,
                qty_per_package  REAL    NOT NULL DEFAULT 0,
                created_at       TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_pp_product ON product_package(product_id);
            """
        )
        if "location" in tables:
            loc_cols = {r[1] for r in conn.execute("PRAGMA table_info(location)")}
            for col, ddl in (
                ("aisle", "ALTER TABLE location ADD COLUMN aisle TEXT NOT NULL DEFAULT ''"),
                ("bay", "ALTER TABLE location ADD COLUMN bay TEXT NOT NULL DEFAULT ''"),
                ("level", "ALTER TABLE location ADD COLUMN level TEXT NOT NULL DEFAULT ''"),
                ("barcode", "ALTER TABLE location ADD COLUMN barcode TEXT NOT NULL DEFAULT ''"),
            ):
                if col not in loc_cols:
                    conn.execute(ddl)
        # M13 库位主数据深化：库位组 + 长宽高 + 体积库容（按体积计算）
        if "location" in tables:
            loc_cols = {r[1] for r in conn.execute("PRAGMA table_info(location)")}
            for col, ddl in (
                ("location_group", "ALTER TABLE location ADD COLUMN location_group TEXT NOT NULL DEFAULT ''"),
                ("length_cm", "ALTER TABLE location ADD COLUMN length_cm REAL NOT NULL DEFAULT 0"),
                ("width_cm", "ALTER TABLE location ADD COLUMN width_cm REAL NOT NULL DEFAULT 0"),
                ("height_cm", "ALTER TABLE location ADD COLUMN height_cm REAL NOT NULL DEFAULT 0"),
                ("capacity_volume", "ALTER TABLE location ADD COLUMN capacity_volume REAL NOT NULL DEFAULT 0"),
            ):
                if col not in loc_cols:
                    conn.execute(ddl)
        # M14 承运商（物流履约资源，全系统共享，独立于多货主）
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS carrier (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                code          TEXT    NOT NULL UNIQUE,
                name          TEXT    NOT NULL,
                contact       TEXT    NOT NULL DEFAULT '',
                phone         TEXT    NOT NULL DEFAULT '',
                api_code      TEXT    NOT NULL DEFAULT '',
                address       TEXT    NOT NULL DEFAULT '',
                carrier_type  TEXT    NOT NULL DEFAULT '快递',
                status        TEXT    NOT NULL DEFAULT 'active',
                note          TEXT    NOT NULL DEFAULT '',
                created_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_carrier_code   ON carrier(code);
            CREATE INDEX IF NOT EXISTS idx_carrier_type   ON carrier(carrier_type);
            CREATE INDEX IF NOT EXISTS idx_carrier_status ON carrier(status);
            """
        )
        # M16 移库单（Stock Transfer）：单据驱动库位间一减一增，同事务原子完成
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS transfer (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                code        TEXT    NOT NULL UNIQUE,
                owner_id    INTEGER NOT NULL REFERENCES owner(id),
                status      TEXT    NOT NULL DEFAULT 'open',
                note        TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                updated_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_transfer_owner  ON transfer(owner_id);
            CREATE INDEX IF NOT EXISTS idx_transfer_status ON transfer(status);
            CREATE TABLE IF NOT EXISTS transfer_line (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                transfer_id     INTEGER NOT NULL REFERENCES transfer(id),
                product_id      INTEGER NOT NULL REFERENCES product(id),
                from_location_id INTEGER NOT NULL REFERENCES location(id),
                to_location_id  INTEGER NOT NULL REFERENCES location(id),
                qty             REAL    NOT NULL,
                UNIQUE(transfer_id, product_id, from_location_id, to_location_id)
            );
            CREATE INDEX IF NOT EXISTS idx_transfer_line_transfer ON transfer_line(transfer_id);
            """
        )
        # M17 盘点单（Stocktake）：单据驱动账实核对——冻结快照 → 盲盘 → 差异调整 → 解冻
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS stocktake (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                code        TEXT    NOT NULL UNIQUE,
                owner_id    INTEGER NOT NULL REFERENCES owner(id),
                status      TEXT    NOT NULL DEFAULT 'open',
                note        TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                updated_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_stocktake_owner  ON stocktake(owner_id);
            CREATE INDEX IF NOT EXISTS idx_stocktake_status ON stocktake(status);
            CREATE TABLE IF NOT EXISTS stocktake_line (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                stocktake_id     INTEGER NOT NULL REFERENCES stocktake(id),
                owner_id         INTEGER NOT NULL REFERENCES owner(id),
                product_id       INTEGER NOT NULL REFERENCES product(id),
                location_id      INTEGER NOT NULL REFERENCES location(id),
                book_qty         REAL    NOT NULL DEFAULT 0,
                counted_qty      REAL    DEFAULT NULL,
                UNIQUE(stocktake_id, product_id, location_id)
            );
            CREATE INDEX IF NOT EXISTS idx_stocktake_line_st ON stocktake_line(stocktake_id);
            """
        )
        # M19 批次（医疗器械/GSP 口径）：批次字段配置 / 批次档案 / 每次入库唯一批次流水
        # 级联删除仅用于保持历史数据一致性；批次流水本身只能由收货单验收写入，不提供手工新增接口
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS batch_config (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                field_name    TEXT    NOT NULL UNIQUE,
                label         TEXT    NOT NULL DEFAULT '',
                input_type    TEXT    NOT NULL DEFAULT 'text',
                is_key        INTEGER NOT NULL DEFAULT 0,
                enabled       INTEGER NOT NULL DEFAULT 1,
                default_value TEXT    NOT NULL DEFAULT '',
                sort_order    INTEGER NOT NULL DEFAULT 0,
                note          TEXT    NOT NULL DEFAULT '',
                updated_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS batch (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                code          TEXT    NOT NULL UNIQUE,
                batch_key     TEXT    NOT NULL UNIQUE,
                owner_id      INTEGER NOT NULL REFERENCES owner(id) ON DELETE CASCADE,
                product_id    INTEGER NOT NULL REFERENCES product(id) ON DELETE CASCADE,
                key_info      TEXT    NOT NULL DEFAULT '{}',
                info          TEXT    NOT NULL DEFAULT '{}',
                receipt_count INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_batch_owner   ON batch(owner_id);
            CREATE INDEX IF NOT EXISTS idx_batch_product ON batch(product_id);
            CREATE TABLE IF NOT EXISTS batch_receipt (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_no          TEXT    NOT NULL UNIQUE,
                batch_id          INTEGER NOT NULL REFERENCES batch(id) ON DELETE CASCADE,
                internal_batch_no TEXT    NOT NULL DEFAULT '',
                internal_source   TEXT    NOT NULL DEFAULT 'first_in',
                owner_id          INTEGER NOT NULL REFERENCES owner(id) ON DELETE CASCADE,
                product_id        INTEGER NOT NULL REFERENCES product(id) ON DELETE CASCADE,
                location_id       INTEGER NOT NULL REFERENCES location(id) ON DELETE CASCADE,
                asn_id            INTEGER NOT NULL REFERENCES asn(id) ON DELETE CASCADE,
                asn_line_id       INTEGER NOT NULL REFERENCES asn_line(id) ON DELETE CASCADE,
                qty               REAL    NOT NULL,
                doc_type          TEXT    NOT NULL DEFAULT '',
                goods_property    TEXT    NOT NULL DEFAULT '',
                info              TEXT    NOT NULL DEFAULT '{}',
                created_at        TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_batch_receipt_batch    ON batch_receipt(batch_id);
            CREATE INDEX IF NOT EXISTS idx_batch_receipt_asn      ON batch_receipt(asn_id);
            CREATE INDEX IF NOT EXISTS idx_batch_receipt_line     ON batch_receipt(asn_line_id);
            CREATE INDEX IF NOT EXISTS idx_batch_receipt_internal ON batch_receipt(internal_batch_no);
            """
        )
        # 批次字段配置播种（幂等，不覆盖用户已改的配置）
        from batch_fields import seed_batch_config  # noqa: E402
        seed_batch_config(conn)
        # 用户与角色（M9 基础：后续功能通过 require_permission 联动）
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS role (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT    NOT NULL UNIQUE,
                permissions  TEXT    NOT NULL DEFAULT '[]',
                created_at   TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                updated_at   TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            CREATE TABLE IF NOT EXISTS user (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                username       TEXT    NOT NULL UNIQUE,
                password_salt  TEXT    NOT NULL DEFAULT '',
                password_hash  TEXT    NOT NULL DEFAULT '',
                role_id        INTEGER REFERENCES role(id),
                owner_id       INTEGER REFERENCES owner(id),
                status         TEXT    NOT NULL DEFAULT 'active',
                created_at     TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                updated_at     TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_user_role   ON user(role_id);
            CREATE INDEX IF NOT EXISTS idx_user_status ON user(status);
            """
        )
        # ── M20：储区主数据 + 仓库维度 + 混放开关 + 货品绑定货位 ──
        # zone.warehouse_id 为空表示全仓共享；库位 warehouse_id 与储区 warehouse_id
        # 必须一致（或储区共享），由 routes_location 校验。
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS zone (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                code              TEXT    NOT NULL UNIQUE,
                name              TEXT    NOT NULL,
                warehouse_id      INTEGER REFERENCES warehouse(id),
                temp_class        TEXT    NOT NULL DEFAULT '常温',
                allow_mix_product INTEGER NOT NULL DEFAULT 0,
                allow_mix_batch   INTEGER NOT NULL DEFAULT 0,
                allow_mix_owner   INTEGER NOT NULL DEFAULT 0,
                status            TEXT    NOT NULL DEFAULT 'active',
                note              TEXT    NOT NULL DEFAULT '',
                created_at        TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at        TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_zone_warehouse ON zone(warehouse_id);
            CREATE INDEX IF NOT EXISTS idx_zone_status    ON zone(status);
            CREATE TABLE IF NOT EXISTS product_location (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id  INTEGER NOT NULL REFERENCES product(id) ON DELETE CASCADE,
                location_id INTEGER NOT NULL REFERENCES location(id) ON DELETE CASCADE,
                priority    INTEGER NOT NULL DEFAULT 0,
                note        TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
                UNIQUE(product_id, location_id)
            );
            CREATE INDEX IF NOT EXISTS idx_pl_product   ON product_location(product_id);
            CREATE INDEX IF NOT EXISTS idx_pl_location ON product_location(location_id);
            """
        )
        _migrate_m20_columns(conn)
        _seed_zones(conn)
        _seed_auth(conn)
        conn.commit()
    finally:
        conn.close()


# ── 业务模块注册（须在 app/get_db 定义之后导入，供蓝图反向引用）──
# 仅在被 import 时注册蓝图；若以 `python main.py` 直接运行，__main__ 与 main
# 会形成双实例循环导入（routes_* → from main import ... → 再次执行本文件的
# 蓝图导入），因此入口模式下改为以 main 模块身份重新导入自身。
if __name__ == "__main__":
    from main import app as _app, init_db as _init_db  # noqa: E402

    _init_db()
    # 端口可用 MINIWMS_PORT 覆盖，便于本机多实例/端到端测试（默认仍为 5100）
    _app.run(host="0.0.0.0", port=int(os.environ.get("MINIWMS_PORT", "5100")), debug=False)
else:
    from routes_location import location_bp  # noqa: E402
    from routes_owner import owner_bp  # noqa: E402
    from routes_product import product_bp  # noqa: E402
    from routes_asn import asn_bp  # noqa: E402
    from routes_dn import dn_bp  # noqa: E402
    from routes_stock import stock_bp  # noqa: E402
    from routes_customer import customer_bp
    from routes_user import user_bp  # noqa: E402
    from routes_carrier import carrier_bp  # noqa: E402
    from routes_supplier import supplier_bp  # noqa: E402
    from routes_warehouse import warehouse_bp  # noqa: E402
    from routes_transfer import transfer_bp  # noqa: E402
    from routes_stocktake import stocktake_bp  # noqa: E402
    from routes_batch import batch_bp  # noqa: E402
    from routes_zone import zone_bp  # noqa: E402
    from routes_product_location import product_location_bp  # noqa: E402
    from routes_putaway import putaway_bp  # noqa: E402

    app.register_blueprint(owner_bp)
    app.register_blueprint(product_bp)
    app.register_blueprint(location_bp)
    app.register_blueprint(asn_bp)
    app.register_blueprint(dn_bp)
    app.register_blueprint(stock_bp)
    app.register_blueprint(customer_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(carrier_bp)
    app.register_blueprint(supplier_bp)
    app.register_blueprint(warehouse_bp)
    app.register_blueprint(transfer_bp)
    app.register_blueprint(stocktake_bp)
    app.register_blueprint(batch_bp)
    app.register_blueprint(zone_bp)
    app.register_blueprint(product_location_bp)
    app.register_blueprint(putaway_bp)
