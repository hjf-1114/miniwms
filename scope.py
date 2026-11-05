"""MiniWMS 仓库 / 货主双维数据隔离（MANDT 式上下文切面，零依赖实现）。

设计对齐 SAP MANDT 与 SaaS WMS 的「租户上下文切面」，分三层落地：

  ① 连接层  main.get_db() 按 ``warehouse.db_path`` 选库（仓库容器隔离）
  ② SQL 层  本模块 ``owner_where()`` / ``assert_owner_allowed()`` 注入货主条件
  ③ 数据层  业务表带 ``owner_id`` 标识列；公共表走白名单不注入
  ④ 业务层  ``apply_owner_warehouse_grid()`` 把「当前仓已登记的货主」与账号授权取交集，
            消除「可见仓 × 可见货主」笛卡尔积里那些业务上并不存在的组合

**核心纪律**：``ALL`` 必须是授权解析后**显式**给出的标志。上下文缺失一律拒绝，
绝不用「没解析到就不加条件」来放行 —— 那是多租户系统最常见的越权漏洞。
写路径（新建单据 / 主数据）同样要过 ``assert_owner_allowed()``。

本模块只依赖标准库与 flask.g，不反向 import main，避免循环依赖。
"""
import os

from flask import g

# 需要按货主注入的业务表（用于文档与守门断言，注入点由各路由显式调用）
OWNER_SCOPED_TABLES = (
    "owner",          # 货主主数据本身按 id 注入
    "product", "customer", "supplier", "carrier",
    "asn", "dn", "stock", "stock_movement",
    "transfer", "stocktake", "stocktake_line",
    "batch", "batch_receipt",
)
# 公共表：不按货主隔离（仓库靠选库隔离，布局/权限表属于控制面）
PUBLIC_TABLES = (
    "warehouse", "zone", "location", "product_location",
    "role", "user", "user_warehouse_grant", "user_owner_grant",
    "owner_warehouse",
    "owner_cert", "batch_config", "asn_line", "dn_line", "dn_pick",
    "transfer_line", "putaway_rule_set", "putaway_rule",
)

# 下列路径不参与数据权限门禁（登录前 / 上下文自检）
OPEN_API_PATHS = ("/api/auth/login", "/api/scope/context")


class ScopeError(Exception):
    """数据权限解析失败。status 决定 HTTP 状态码。"""

    def __init__(self, message, status=403):
        super().__init__(message)
        self.status = status


def allow_anonymous():
    """匿名演示模式开关：默认开，保证「下载下来就能用」；生产部署设为 0 即强制登录。

    注意这不是「上下文缺失就放行」：开启时 ``resolve_scope`` 会**显式**返回
    ``mode='anonymous-demo'`` 的 ALL 上下文，关闭时直接 401。
    """
    return os.environ.get("MINIWMS_ALLOW_ANONYMOUS", "1") == "1"


def anonymous_scope():
    return {
        "mode": "anonymous-demo",
        "user_id": None,
        "warehouse_all": True, "warehouse_ids": [],
        "owner_all": True, "owner_ids": [],
    }


def denied_scope(reason):
    """上下文自检端点用的「最小权限」上下文：任何业务查询都会返回空集。"""
    return {
        "mode": "denied", "reason": reason, "user_id": None,
        "warehouse_all": False, "warehouse_ids": [],
        "owner_all": False, "owner_ids": [],
    }


def _grants(db, table, column, uid):
    try:
        return [int(r[0]) for r in db.execute(
            f"SELECT {column} FROM {table} WHERE user_id = ? ORDER BY {column}", (uid,)
        ).fetchall()]
    except Exception:
        # 表缺失 / 旧库未迁移时按「无授权」处理，绝不退化成 ALL
        return []


def resolve_scope(user, control_db):
    """解析当前请求的数据权限上下文；解析不出可用上下文时抛 ScopeError。

    - 匿名：由 ``allow_anonymous()`` 显式放行（演示模式）或 401
    - 登录：``user.warehouse_scope`` / ``user.owner_scope`` 为 ``all`` 时显式 ALL；
      否则读授权表，授权行必须存在且非空，否则 403 拒绝（不是放行）
    """
    if user is None:
        if not allow_anonymous():
            raise ScopeError("未登录或登录已过期", 401)
        return anonymous_scope()

    uid = int(user["id"])
    wh_all = str(user.get("warehouse_scope") or "grant") == "all"
    owner_all = str(user.get("owner_scope") or "grant") == "all"
    wh_ids = [] if wh_all else _grants(control_db, "user_warehouse_grant", "warehouse_id", uid)
    owner_ids = [] if owner_all else _grants(control_db, "user_owner_grant", "owner_id", uid)

    if not wh_all and not wh_ids:
        raise ScopeError("账号未配置任何仓库权限，请联系管理员在数据授权中设置")
    if not owner_all and not owner_ids:
        raise ScopeError("账号未配置任何货主权限，请联系管理员在数据授权中设置")

    return {
        "mode": "granted", "user_id": uid,
        "warehouse_all": wh_all, "warehouse_ids": wh_ids,
        "owner_all": owner_all, "owner_ids": owner_ids,
    }


def current_scope():
    """取当前请求上下文；缺失时拒绝，绝不返回 ALL 兜底。"""
    s = getattr(g, "scope", None)
    if s is None:
        raise ScopeError("数据权限上下文缺失，拒绝执行", 403)
    return s


def owner_where(scope, column="owner_id", table=None, allow_null=False):
    """生成货主过滤条件，返回 ``(condition, params)``；ALL 时返回 ``(None, [])``。

    ``table`` 用于多表 JOIN 时限定列名，如 ``table='a'`` → ``a.owner_id``。
    ``allow_null`` 用于 ``product.owner_id`` / ``carrier.owner_id`` 这类
    「NULL = 公共/未指定货主」的可空列：受限账号仍可见公共数据。
    """
    s = scope or current_scope()
    if s.get("owner_all"):
        return None, []
    q = f"{table}.{column}" if table else column
    ids = s.get("owner_ids") or []
    if not ids:
        return "1=0", []
    marks = ",".join("?" for _ in ids)
    cond = f"{q} IN ({marks})"
    if allow_null:
        cond = f"({cond} OR {q} IS NULL)"
    return cond, list(ids)


def scoped_where(where, params, column="owner_id", table=None,
                 allow_null=False, scope=None):
    """把货主条件追加进既有 WHERE 片段列表（原地修改并返回，便于链式调用）。

    用法：``cond, p = owner_where(...)`` 的等价简写
    ``scoped_where(where, params, table="a")``。
    """
    cond, p = owner_where(scope or current_scope(), column, table, allow_null)
    if cond:
        where.append(cond)
        params.extend(p)
    return where, params


def assert_owner_allowed(scope, owner_id):
    """写路径守门：校验目标货主在授权集合内，否则抛 ScopeError。"""
    s = scope or current_scope()
    if s.get("owner_all"):
        return
    try:
        oid = int(owner_id)
    except (TypeError, ValueError):
        raise ScopeError("owner_id 无效，无法校验数据权限", 400)
    if oid not in (s.get("owner_ids") or []):
        raise ScopeError("无权操作该货主的数据", 403)


def owner_warehouse_grid(control_db, warehouse_id):
    """读取某个仓已登记（``status='active'``）的货主 id 列表。

    **空列表 = 该仓未启用格子管控**，行为与本次增强前完全一致。这是「功能开关」，
    不是授权上下文：账号授权层仍由 ``resolve_scope()`` 严格执行，表缺失 / 旧库未迁移
    只表示「还没配置过业务关系」，不会把账号权限放大成 ALL。

    关系表是控制面数据，始终查中央库；业务库里的同名表只是 DDL 幂等带出来的空壳。
    """
    if warehouse_id in (None, "", 0, "0"):
        return []
    try:
        return [int(r[0]) for r in control_db.execute(
            "SELECT owner_id FROM owner_warehouse "
            "WHERE warehouse_id = ? AND status = 'active' ORDER BY owner_id",
            (int(warehouse_id),)
        ).fetchall()]
    except Exception:
        return []


def apply_owner_warehouse_grid(scope, control_db, warehouse_id):
    """把「当前仓有业务关系的货主」与账号授权取交集，原地写回 scope。

    启用条件：该仓至少有一条 active 登记行，且请求明确带了当前仓（A 作业视角）。
    启用后 ``owner_all`` 强制置 False —— 即使账号被授予「全部货主」，也只能看
    该仓登记过的货主；这样 13 个列表接口、写路径守门、按 ID 守门全部自动生效，
    不需要逐个路由改代码。

    未指定仓 / 未配置格子时不改 scope，保留向下兼容（clone 即用、老库零迁移）。
    """
    s = scope or current_scope()
    grid = owner_warehouse_grid(control_db, warehouse_id)
    s["grid_enabled"] = bool(grid)
    s["grid_warehouse_id"] = int(warehouse_id) if warehouse_id else None
    s["grid_owner_ids"] = grid
    if not grid:
        return s
    allowed = set(grid)
    if not s.get("owner_all"):
        allowed &= set(s.get("owner_ids") or [])
    s["owner_all"] = False
    s["owner_ids"] = sorted(allowed)
    return s


def check_warehouse_access(scope, warehouse_id, control_db):
    """校验当前账号能否切到指定仓库，并返回仓库行（含 db_path）。"""
    if warehouse_id in (None, "", 0, "0"):
        return None
    s = scope or current_scope()
    try:
        wid = int(warehouse_id)
    except (TypeError, ValueError):
        raise ScopeError("仓库标识无效", 400)
    if not s.get("warehouse_all") and wid not in (s.get("warehouse_ids") or []):
        raise ScopeError("无权访问该仓库", 403)
    row = control_db.execute(
        "SELECT * FROM warehouse WHERE id = ?", (wid,)
    ).fetchone()
    if row is None:
        raise ScopeError("仓库不存在", 404)
    return row


def visible_warehouses(scope, control_db):
    """A 作业视角：账号可切换的仓库清单（一次只操作一个仓）。"""
    s = scope or current_scope()
    sql = "SELECT id, code, name, status, db_path FROM warehouse"
    params = []
    if not s.get("warehouse_all"):
        ids = s.get("warehouse_ids") or []
        if not ids:
            return []
        sql += f" WHERE id IN ({','.join('?' for _ in ids)})"
        params = list(ids)
    sql += " ORDER BY id"
    return [dict(r) for r in control_db.execute(sql, params).fetchall()]


def visible_owners(scope, db):
    """当前账号可见的货主清单，供前端下拉与授权配置页使用。"""
    s = scope or current_scope()
    sql = "SELECT id, code, name, status FROM owner"
    params = []
    if not s.get("owner_all"):
        ids = s.get("owner_ids") or []
        if not ids:
            return []
        sql += f" WHERE id IN ({','.join('?' for _ in ids)})"
        params = list(ids)
    sql += " ORDER BY id"
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def scope_summary(scope):
    """给前端的上下文摘要（不含敏感信息）。"""
    s = scope or current_scope()
    return {
        "mode": s.get("mode"),
        "warehouse_scope": "ALL" if s.get("warehouse_all") else "PARTIAL",
        "warehouse_ids": list(s.get("warehouse_ids") or []),
        "owner_scope": "ALL" if s.get("owner_all") else "PARTIAL",
        "owner_ids": list(s.get("owner_ids") or []),
        "grid_enabled": bool(s.get("grid_enabled")),
        "grid_warehouse_id": s.get("grid_warehouse_id"),
        "grid_owner_ids": list(s.get("grid_owner_ids") or []),
        "reason": s.get("reason", ""),
    }
