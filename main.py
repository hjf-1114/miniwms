"""MiniWMS -- 轻量仓库管理系统（WMS）入口。

职责：
- Flask 应用创建与配置
- **中央库 / 业务库双层连接**（控制面 + 按仓容器隔离，见 scope.py）
- SQLite 连接管理（请求级连接，g 上下文）
- 数据库初始化（建表 + 列迁移）
- 聚合各业务 Blueprint

依赖：仅 Python 标准库 + Flask。
"""
import json
import os
import sqlite3

from flask import Flask, g, jsonify, request

import scope as S

DB_PATH = os.environ.get(
    "MINIWMS_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "miniwms.db"),
)
# 中央库（控制面）：仓库主数据、货主主数据、用户角色、数据授权表。
# 业务库（数据面）：默认直接复用中央库，保证「下载下来就能用」；
# 在 warehouse.db_path 配置独立文件后，该仓的业务数据物理隔离到另一个 SQLite 文件。
CONTROL_DB_PATH = DB_PATH
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WAREHOUSE_HEADER = "X-WMS-Warehouse"

# 按 ID 访问的资源 → (业务表, 归属列, owner_id 为 NULL 是否视为公共数据放行)。
# 集中在 before_request 做归属守门，覆盖详情 / 更新 / 删除 / 单据动作等所有端点，
# 避免逐个路由漏挂装饰器（这正是上下文切面相对「手写 WHERE」的价值）。
RESOURCE_OWNER_TABLES = {
    "owners": ("owner", "id", False),
    "products": ("product", "owner_id", True),
    "customers": ("customer", "owner_id", False),
    "suppliers": ("supplier", "owner_id", False),
    "carriers": ("carrier", "owner_id", True),
    "asns": ("asn", "owner_id", False),
    "dns": ("dn", "owner_id", False),
    "transfers": ("transfer", "owner_id", False),
    "stocktakes": ("stocktake", "owner_id", False),
}
# /api/batch/<子资源>/<id> 的二级路径映射
BATCH_RESOURCE_TABLES = {
    "profiles": ("batch", "owner_id", False),
    "receipts": ("batch_receipt", "owner_id", False),
}

app = Flask(__name__, static_folder="web_app", static_url_path="")


@app.route("/")
def index():
    """首页：前端 demo 导航页（web_app/index.html）。"""
    return app.send_static_file("index.html")


def _connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_control_db():
    """中央库连接（控制面）。所有账号、角色、仓库主数据与授权表只认这一份。"""
    if "cdb" not in g:
        g.cdb = _connect(CONTROL_DB_PATH)
    return g.cdb


_BUSINESS_INITIALIZED = set()


def resolve_warehouse_db_path(raw):
    """把 warehouse.db_path 解析成安全绝对路径。

    空串 = 共用中央库（默认演示模式）；非空时只允许项目目录内、以 .db 结尾的文件，
    避免把任意路径配置成数据库文件。
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    p = os.path.abspath(raw if os.path.isabs(raw) else os.path.join(BASE_DIR, raw))
    try:
        inside = os.path.commonpath([BASE_DIR, p]) == BASE_DIR
    except ValueError:
        inside = False
    if not inside:
        raise S.ScopeError("仓库数据文件路径必须位于项目目录内", 400)
    if not p.lower().endswith(".db"):
        raise S.ScopeError("仓库数据文件必须以 .db 结尾", 400)
    return p


def normalize_warehouse_db_path(raw):
    """校验并返回可入库的**相对路径**（相对项目根目录），空串表示共用中央库。

    用相对路径是为了保证「换台机器 clone 下来直接能用」：绝对路径会绑死本机目录。
    """
    p = resolve_warehouse_db_path(raw)
    if not p:
        return ""
    return os.path.relpath(p, BASE_DIR).replace(os.sep, "/")


def all_business_db_paths(control=None):
    """列出需要检查的已存在库文件：中央库 + 各仓配置的独立库。

    用于仓库删除前的引用检查 —— 分库后单据可能只存在于某个仓的库里，
    只看中央库会漏判。
    """
    db = control or get_control_db()
    paths = [CONTROL_DB_PATH]
    try:
        for row in db.execute("SELECT db_path FROM warehouse WHERE db_path != ''"):
            try:
                p = resolve_warehouse_db_path(row["db_path"])
            except S.ScopeError:
                continue
            if p not in paths:
                paths.append(p)
    except Exception:
        pass
    return [p for p in paths if os.path.exists(p)]


def sync_control_tables(conn, control):
    """把中央库的「跨仓主数据」（货主 / 仓库）同步进业务库副本。

    业务表带 ``owner_id`` / ``warehouse_id`` 外键，独立库里必须存在对应主数据行，
    否则新建单据会触发外键失败。中央库是权威来源，这里只补齐缺失行、不覆盖已有行。
    """
    for table in ("owner", "warehouse"):
        local_cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        center_cols = {r[1] for r in control.execute(f"PRAGMA table_info({table})")}
        common = [c for c in local_cols if c in center_cols]
        if not common:
            continue
        collist = ",".join(common)
        marks = ",".join("?" for _ in common)
        sql = f"INSERT OR IGNORE INTO {table} ({collist}) VALUES ({marks})"
        for row in control.execute(f"SELECT {collist} FROM {table}").fetchall():
            conn.execute(sql, tuple(row))
    conn.commit()


def ensure_business_db(path):
    """首次连到某个独立仓时，把完整 DDL 幂等地建一遍（每个进程只做一次）。"""
    if path in _BUSINESS_INITIALIZED:
        return
    init_db(path)
    _BUSINESS_INITIALIZED.add(path)


def init_warehouse_db(db_path_rel):
    """配置 ``warehouse.db_path`` 时立即建库并同步跨仓主数据，返回绝对路径。

    之后切仓请求仍走 ``get_db()`` 的同一套初始化逻辑（幂等），这里只是让
    「改完配置马上能用、文件马上可见」，不必等第一次业务请求触发懒加载。
    """
    path = resolve_warehouse_db_path(db_path_rel)
    if not path or os.path.abspath(path) == os.path.abspath(CONTROL_DB_PATH):
        return ""
    ensure_business_db(path)
    conn = _connect(path)
    try:
        sync_control_tables(conn, get_control_db())
    finally:
        conn.close()
    return path


def get_db():
    """业务库连接：默认复用中央库连接；仓库配置了 db_path 时按仓选库。

    这是「连接层隔离」的唯一挂点 —— 现有 17 个 routes_* 全部只依赖 get_db()，
    因此按仓分库不需要改任何业务路由。
    """
    if "db" not in g:
        raw_path = getattr(g, "warehouse_db_path", "") or ""
        if not raw_path:
            g.db = get_control_db()
        else:
            path = resolve_warehouse_db_path(raw_path)
            if os.path.abspath(path) == os.path.abspath(CONTROL_DB_PATH):
                g.db = get_control_db()
            else:
                ensure_business_db(path)
                conn = _connect(path)
                sync_control_tables(conn, get_control_db())
                g.db = conn
    return g.db


def current_scope():
    """供业务路由读取当前请求的数据权限上下文（缺失时由 scope.py 拒绝）。"""
    return S.current_scope()


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    cdb = g.pop("cdb", None)
    if db is not None and db is not cdb:
        db.close()
    if cdb is not None:
        cdb.close()


@app.errorhandler(S.ScopeError)
def _scope_error(e):
    return jsonify(ok=False, error=str(e)), e.status


@app.before_request
def _bind_scope():
    """每个 API 请求绑定数据权限上下文与当前仓库（A 作业视角：一次只看一个仓）。

    静态页面不参与门禁，保证登录页 / 工作台始终可打开；登录与上下文自检端点
    使用「软解析」，受限时返回最小权限上下文而不是直接报错。
    """
    if not request.path.startswith("/api/"):
        g.scope = None
        g.warehouse_id = None
        g.warehouse_db_path = ""
        return

    from auth import get_current_user

    control = get_control_db()
    user = get_current_user()
    try:
        s = S.resolve_scope(user, control)
    except S.ScopeError as e:
        if request.path in S.OPEN_API_PATHS:
            s = S.denied_scope(str(e))
        else:
            return jsonify(ok=False, error=str(e)), e.status
    g.scope = s
    g.current_user = user
    try:
        row = S.check_warehouse_access(s, request.headers.get(WAREHOUSE_HEADER), control)
    except S.ScopeError as e:
        # 自检 / 登录端点允许带着「已失效的旧仓」进来，由前端据此重选；
        # 业务端点一律拒绝，避免用失效上下文绕过仓库校验。
        if request.path in S.OPEN_API_PATHS:
            row = None
        else:
            return jsonify(ok=False, error=str(e)), e.status
    g.warehouse_id = row["id"] if row is not None else None
    g.warehouse_db_path = (row["db_path"] if row is not None else "") or ""

    # ── 货主 × 仓库业务关系（格子）：与账号授权取交集，原地收紧 g.scope ──
    # s 与 g.scope 是同一 dict；授权层先解析、格子层再收紧，顺序不能反。
    g.scope = S.apply_owner_warehouse_grid(s, control, g.warehouse_id)

    # ── 写路径守门：请求体里带 owner_id 时，必须落在授权货主集合内 ──
    if request.method in ("POST", "PUT", "PATCH"):
        body = request.get_json(silent=True)
        if not isinstance(body, dict) and request.form:
            body = dict(request.form)
        if isinstance(body, dict) and body.get("owner_id") not in (None, ""):
            try:
                S.assert_owner_allowed(s, body["owner_id"])
            except S.ScopeError as e:
                return jsonify(ok=False, error=str(e)), e.status

    # ── 按 ID 访问的资源归属守门 ──
    parts = request.path.strip("/").split("/")
    spec, doc_id = None, None
    if len(parts) >= 3 and parts[0] == "api" and parts[2].isdigit():
        spec, doc_id = RESOURCE_OWNER_TABLES.get(parts[1]), parts[2]
    elif (len(parts) >= 4 and parts[0] == "api" and parts[1] == "batch"
          and parts[2] in BATCH_RESOURCE_TABLES and parts[3].isdigit()):
        spec, doc_id = BATCH_RESOURCE_TABLES[parts[2]], parts[3]
    if spec:
        table, column, allow_null = spec
        # 货主主数据是控制面权威数据，归属守门也查中央库，避免业务库副本滞后导致漏判
        reader = get_control_db() if parts[1] == "owners" else get_db()
        try:
            rec = reader.execute(
                f"SELECT {column} AS v FROM {table} WHERE id = ?", (int(doc_id),)
            ).fetchone()
        except Exception:
            rec = None  # 表不存在等异常交给路由自身返回 404，不在守门层改语义
        if rec is not None and not s.get("owner_all"):
            v = rec["v"]
            if v is None and not allow_null:
                return jsonify(ok=False, error="无权操作该货主的数据"), 403
            try:
                if v is not None:
                    S.assert_owner_allowed(s, v)
            except S.ScopeError as e:
                return jsonify(ok=False, error=str(e)), e.status


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
        # 管理员显式授予 ALL：ALL 必须是授权解析后显式给出的标志，
        # 不能靠「角色名叫管理员」或「上下文缺失」隐式放行。
        conn.execute(
            "INSERT INTO user (username, password_salt, password_hash, role_id, status, "
            "warehouse_scope, owner_scope, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'active', 'all', 'all', "
            "datetime('now','localtime'), datetime('now','localtime'))",
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


def _migrate_scope_columns(conn):
    """数据权限升级（幂等）：仓库数据文件路径、承运商归属货主、用户授权范围。

    - ``warehouse.db_path``：空 = 共用中央库；非空 = 该仓业务数据物理隔离到独立文件
    - ``carrier.owner_id``：NULL = 全系统共享物流资源；非空 = 归属货主，可按货主管控
    - ``user.warehouse_scope / owner_scope``：``all``=全部、``grant``=按授权表
    - 两张授权表：账号可看哪些仓 / 哪些货主（支持「部分」也支持「全部」）
    - ``owner_warehouse``：货主在哪些仓**有业务关系**（格子），用来消掉
      「可见仓 × 可见货主」里业务上并不存在的组合；空表 = 功能未启用，老库零影响

    老库升级时只给既有 admin 一次性显式 ALL，其余账号保持 ``grant`` 最小权限；
    列已存在时不再 UPDATE，避免覆盖管理员后续收紧的授权。
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(warehouse)")}
    if "db_path" not in cols:
        conn.execute("ALTER TABLE warehouse ADD COLUMN db_path TEXT NOT NULL DEFAULT ''")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(carrier)")}
    if "owner_id" not in cols:
        conn.execute("ALTER TABLE carrier ADD COLUMN owner_id INTEGER REFERENCES owner(id)")
    # 新库 CREATE 自带该列、旧库刚 ALTER 补列，两种情况下索引都在此统一创建
    conn.execute("CREATE INDEX IF NOT EXISTS idx_carrier_owner ON carrier(owner_id)")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(user)")}
    upgraded = False
    if "warehouse_scope" not in cols:
        conn.execute(
            "ALTER TABLE user ADD COLUMN warehouse_scope TEXT NOT NULL DEFAULT 'grant'"
        )
        upgraded = True
    if "owner_scope" not in cols:
        conn.execute(
            "ALTER TABLE user ADD COLUMN owner_scope TEXT NOT NULL DEFAULT 'grant'"
        )
        upgraded = True
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS user_warehouse_grant (
            user_id      INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
            warehouse_id INTEGER NOT NULL REFERENCES warehouse(id) ON DELETE CASCADE,
            PRIMARY KEY (user_id, warehouse_id)
        );
        CREATE TABLE IF NOT EXISTS user_owner_grant (
            user_id  INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
            owner_id INTEGER NOT NULL REFERENCES owner(id) ON DELETE CASCADE,
            PRIMARY KEY (user_id, owner_id)
        );
        CREATE TABLE IF NOT EXISTS owner_warehouse (
            owner_id     INTEGER NOT NULL REFERENCES owner(id) ON DELETE CASCADE,
            warehouse_id INTEGER NOT NULL REFERENCES warehouse(id) ON DELETE CASCADE,
            status       TEXT    NOT NULL DEFAULT 'active',
            note         TEXT    NOT NULL DEFAULT '',
            created_at   TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
            PRIMARY KEY (owner_id, warehouse_id)
        );
        CREATE INDEX IF NOT EXISTS idx_owner_warehouse_wh
            ON owner_warehouse(warehouse_id, status);
        """
    )
    if upgraded:
        conn.execute(
            "UPDATE user SET warehouse_scope = 'all', owner_scope = 'all' "
            "WHERE username = 'admin'"
        )


def _migrate_m21_columns(conn):
    """M21 批次级库存明细层：新表 + 流水 9 列 + 一次性回填（幂等）。

    - ``stock_batch`` 明细层；``stock`` 汇总层列与唯一键**永不修改**（I1 原样保持）
    - ``batch_location``：上架拆多库位时的批次实物分布
    - ``stock_movement`` 扩 9 列，全部有默认值，老流水自动填 ``''`` / ``0`` / ``NULL``
    - 回填只在明细层为空时执行一次：历史流水统一归到 ``LEGACY-UNKNOWN`` 占位维度，
      按流水聚合生成明细行，再把汇总层与明细层的差额补齐，保证 I2 / I3 立即通过。
      **不做灰度豁免**，任何不一致都会被 ``/api/stock/batch-reconcile`` 判为 false。
    """
    # init_db() 用裸 sqlite3.connect（tuple），回填与明细 upsert 需要按列名取值
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS stock_batch (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id                INTEGER NOT NULL REFERENCES owner(id),
            product_id              INTEGER NOT NULL REFERENCES product(id),
            location_id             INTEGER NOT NULL REFERENCES location(id),
            batch_no                TEXT    NOT NULL DEFAULT '',
            batch_key               TEXT    NOT NULL DEFAULT '',
            internal_batch_no       TEXT    NOT NULL DEFAULT '',
            designated_customer_id  INTEGER,
            goods_property          TEXT    NOT NULL DEFAULT '',
            stock_status            TEXT    NOT NULL DEFAULT '',
            expiry_date             TEXT    NOT NULL DEFAULT '',
            production_date         TEXT    NOT NULL DEFAULT '',
            qty                     REAL    NOT NULL DEFAULT 0,
            allocated_qty           REAL    NOT NULL DEFAULT 0,
            updated_at              TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_stock_batch ON stock_batch (
            owner_id, product_id, location_id, batch_no, batch_key,
            IFNULL(designated_customer_id, 0), goods_property, stock_status
        );
        CREATE INDEX IF NOT EXISTS idx_stock_batch_loc ON stock_batch (
            owner_id, product_id, location_id
        );
        CREATE INDEX IF NOT EXISTS idx_stock_batch_no ON stock_batch (batch_no);
        CREATE TABLE IF NOT EXISTS batch_location (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_no         TEXT    NOT NULL,
            owner_id         INTEGER NOT NULL,
            product_id       INTEGER NOT NULL,
            from_location_id INTEGER,
            to_location_id   INTEGER NOT NULL,
            qty              REAL    NOT NULL,
            asn_id           INTEGER,
            asn_line_id      INTEGER,
            ref_type         TEXT    NOT NULL DEFAULT '',
            ref_id           INTEGER,
            created_at       TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_batch_location_no ON batch_location (batch_no);
        """
    )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(stock_movement)")}
    additions = (
        ("batch_no", "TEXT NOT NULL DEFAULT ''"),
        ("batch_key", "TEXT NOT NULL DEFAULT ''"),
        ("internal_batch_no", "TEXT NOT NULL DEFAULT ''"),
        ("designated_customer_id", "INTEGER"),
        ("goods_property", "TEXT NOT NULL DEFAULT ''"),
        ("stock_status", "TEXT NOT NULL DEFAULT ''"),
        ("expiry_date", "TEXT NOT NULL DEFAULT ''"),
        ("allocated_delta", "REAL NOT NULL DEFAULT 0"),
        ("operator", "TEXT NOT NULL DEFAULT ''"),
    )
    for name, decl in additions:
        if name not in cols:
            conn.execute(f"ALTER TABLE stock_movement ADD COLUMN {name} {decl}")

    # ── 一次性回填 ──
    from inventory_service import (LEGACY_BATCH_NO, align_stock_batch,
                                   legacy_dims, upsert_stock_batch)

    if conn.execute("SELECT COUNT(*) FROM stock_batch").fetchone()[0] == 0:
        conn.execute(
            "UPDATE stock_movement SET batch_no = ? WHERE batch_no = ''",
            (LEGACY_BATCH_NO,),
        )
        for r in conn.execute(
            """SELECT owner_id, product_id, location_id, batch_no, batch_key,
                      internal_batch_no, designated_customer_id, goods_property,
                      stock_status, expiry_date,
                      SUM(qty_delta) AS q, SUM(allocated_delta) AS a
               FROM stock_movement
               GROUP BY owner_id, product_id, location_id, batch_no, batch_key,
                        internal_batch_no, designated_customer_id, goods_property,
                        stock_status, expiry_date"""
        ).fetchall():
            upsert_stock_batch(
                conn, r["owner_id"], r["product_id"], r["location_id"],
                {k: r[k] for k in (
                    "batch_no", "batch_key", "internal_batch_no",
                    "designated_customer_id", "goods_property",
                    "stock_status", "expiry_date")},
                r["q"], r["a"],
            )
        # 无流水但有余额（含零行保留）也要有明细，并让 allocated 双层对齐
        for s in conn.execute(
            "SELECT owner_id, product_id, location_id, qty, allocated_qty FROM stock"
        ).fetchall():
            key = (s["owner_id"], s["product_id"], s["location_id"])
            align_stock_batch(conn, *key, s["qty"], s["allocated_qty"])
            # 明细层同样零行保留：qty=0 的汇总行也要有一条占位明细
            has = conn.execute(
                "SELECT 1 FROM stock_batch WHERE owner_id = ? AND product_id = ? "
                "AND location_id = ? LIMIT 1", key,
            ).fetchone()
            if has is None:
                upsert_stock_batch(conn, key[0], key[1], key[2], legacy_dims(), 0, 0)


def _ensure_columns(conn, table, additions):
    """幂等补列：老库与新建库执行同一段迁移，不依赖「库是否是新版创建」。"""
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    for name, decl in additions:
        if name not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def _migrate_m22(conn):
    """M22 库存调整单 / 盘点方案 DDL + 盘点明细行批次维度（幂等）。

    ``stocktake_line`` 老表的表级 UNIQUE 只有 3 列，SQLite 无法 ALTER CONSTRAINT；
    检测到旧定义时按「建新表 → 复制（batch_no=''）→ 删旧表」重建，历史行不丢。
    """
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS stock_adjustment (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            code         TEXT    NOT NULL UNIQUE,
            source_type  TEXT    NOT NULL DEFAULT 'manual',
            source_id    INTEGER,
            reason_code  TEXT    NOT NULL DEFAULT '',
            status       TEXT    NOT NULL DEFAULT 'draft',
            creator      TEXT    NOT NULL DEFAULT '',
            approver     TEXT    NOT NULL DEFAULT '',
            created_at   TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
            approved_at  TEXT    NOT NULL DEFAULT '',
            note         TEXT    NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_adjustment_status ON stock_adjustment(status);
        CREATE INDEX IF NOT EXISTS idx_adjustment_source ON stock_adjustment(source_type, source_id);
        CREATE TABLE IF NOT EXISTS stock_adjustment_line (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            adjustment_id  INTEGER NOT NULL REFERENCES stock_adjustment(id) ON DELETE CASCADE,
            owner_id       INTEGER NOT NULL,
            product_id     INTEGER NOT NULL,
            location_id    INTEGER NOT NULL,
            batch_no       TEXT    NOT NULL DEFAULT '',
            delta_qty      REAL    NOT NULL,
            qty_before     REAL    NOT NULL DEFAULT 0,
            reason         TEXT    NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_adjustment_line_adj ON stock_adjustment_line(adjustment_id);
        CREATE TABLE IF NOT EXISTS stocktake_plan (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            code          TEXT    NOT NULL UNIQUE,
            name          TEXT    NOT NULL,
            scope_type    TEXT    NOT NULL DEFAULT 'full',
            scope         TEXT    NOT NULL DEFAULT '{}',
            blind_mode    TEXT    NOT NULL DEFAULT 'hide_book',
            dynamic_baseline TEXT NOT NULL DEFAULT 'last_closed',
            lookback_days INTEGER NOT NULL DEFAULT 7,
            movement_from TEXT    NOT NULL DEFAULT '',
            movement_to   TEXT    NOT NULL DEFAULT '',
            movement_types TEXT   NOT NULL DEFAULT '[]',
            auto_adjust   INTEGER NOT NULL DEFAULT 0,
            include_zero  INTEGER NOT NULL DEFAULT 0,
            enabled       INTEGER NOT NULL DEFAULT 1,
            note          TEXT    NOT NULL DEFAULT '',
            created_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
        );
        """
    )
    _ensure_columns(conn, "stocktake", (
        ("plan_id", "INTEGER"),
        ("scope_type", "TEXT NOT NULL DEFAULT 'full'"),
        ("blind_mode", "TEXT NOT NULL DEFAULT 'none'"),
        ("config_override", "TEXT NOT NULL DEFAULT '{}'"),
        ("scope_snapshot", "TEXT NOT NULL DEFAULT '{}'"),
        ("adjustment_id", "INTEGER"),
        ("closed_at", "TEXT NOT NULL DEFAULT ''"),
    ))
    old = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='stocktake_line'"
    ).fetchone()
    if old is not None and "batch_no" not in (old[0] or ""):
        conn.executescript(
            """
            ALTER TABLE stocktake_line RENAME TO stocktake_line_m22_old;
            CREATE TABLE stocktake_line (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                stocktake_id     INTEGER NOT NULL REFERENCES stocktake(id),
                owner_id         INTEGER NOT NULL REFERENCES owner(id),
                product_id       INTEGER NOT NULL REFERENCES product(id),
                location_id      INTEGER NOT NULL REFERENCES location(id),
                batch_no         TEXT    NOT NULL DEFAULT '',
                book_qty         REAL    NOT NULL DEFAULT 0,
                counted_qty      REAL    DEFAULT NULL,
                UNIQUE(stocktake_id, product_id, location_id, batch_no)
            );
            INSERT INTO stocktake_line
                (id, stocktake_id, owner_id, product_id, location_id,
                 batch_no, book_qty, counted_qty)
            SELECT id, stocktake_id, owner_id, product_id, location_id,
                   '', book_qty, counted_qty FROM stocktake_line_m22_old;
            DROP TABLE stocktake_line_m22_old;
            CREATE INDEX IF NOT EXISTS idx_stocktake_line_st ON stocktake_line(stocktake_id);
            """
        )


def init_db(path=None):
    """初始化数据库：建表与列迁移（幂等，可重复执行）。

    ``path`` 为空时初始化中央库；按仓独立库时由 ``ensure_business_db(path)`` 传入，
    保证每个业务库拥有一套完整表结构（外键与前端下拉在分库模式下同样可用）。
    """
    conn = sqlite3.connect(path or DB_PATH)
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
                db_path     TEXT    NOT NULL DEFAULT '',
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
                owner_id      INTEGER REFERENCES owner(id),
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
                warehouse_scope TEXT  NOT NULL DEFAULT 'grant',
                owner_scope    TEXT  NOT NULL DEFAULT 'grant',
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
        _migrate_scope_columns(conn)
        _migrate_m21_columns(conn)
        _migrate_m22(conn)
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
    from routes_scope import scope_bp  # noqa: E402
    from routes_owner_warehouse import ow_bp  # noqa: E402
    from routes_stock_adjustment import adj_bp  # noqa: E402
    from routes_stocktake_plan import plan_bp  # noqa: E402

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
    app.register_blueprint(scope_bp)
    app.register_blueprint(ow_bp)
    app.register_blueprint(adj_bp)
    app.register_blueprint(plan_bp)
