"""MiniWMS -- 轻量仓库管理系统（WMS）入口。

职责：
- Flask 应用创建与配置
- SQLite 连接管理（请求级连接，g 上下文）
- 数据库初始化（建表 + 列迁移）
- 聚合各业务 Blueprint

依赖：仅 Python 标准库 + Flask。
"""
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


def request_payload():
    """统一取参：优先 JSON，退回表单。"""
    return request.get_json(silent=True) or request.form or {}


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
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                code        TEXT    NOT NULL UNIQUE,
                owner_id    INTEGER NOT NULL REFERENCES owner(id),
                status      TEXT    NOT NULL DEFAULT 'open',
                note        TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                updated_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
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
            -- M5 出库单：主表 + 行表 + 拣货明细（拣货在哪些库位各分配多少，发货按此扣减）
            CREATE TABLE IF NOT EXISTS dn (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                code        TEXT    NOT NULL UNIQUE,
                owner_id    INTEGER NOT NULL REFERENCES owner(id),
                status      TEXT    NOT NULL DEFAULT 'open',
                note        TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
                updated_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
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
    _app.run(host="0.0.0.0", port=5100, debug=False)
else:
    from routes_location import location_bp  # noqa: E402
    from routes_owner import owner_bp  # noqa: E402
    from routes_product import product_bp  # noqa: E402
    from routes_asn import asn_bp  # noqa: E402
    from routes_dn import dn_bp  # noqa: E402
    from routes_stock import stock_bp  # noqa: E402

    app.register_blueprint(owner_bp)
    app.register_blueprint(product_bp)
    app.register_blueprint(location_bp)
    app.register_blueprint(asn_bp)
    app.register_blueprint(dn_bp)
    app.register_blueprint(stock_bp)
