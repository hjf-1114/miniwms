"""数据权限与切仓上下文：/api/scope、/api/users/<uid>/grants。

对应用户确认的三个诉求：
  1. 不同仓库的数据隔离 —— 连接层按 ``warehouse.db_path`` 选库 + 账号可见仓授权
  2. 不同货主的数据隔离 —— SQL 层按 ``user_owner_grant`` 注入 owner 条件
  3. 一个账号看全部 / 部分仓库、全部 / 部分货主 —— ``*_scope='all'|'grant'`` + 授权行

「看所有仓库」采用 **A 作业视角**：账号可切换到任意有权限的仓，但一次只操作一个仓；
跨仓汇总报表不在本模块范围，未来需要独立的只读汇总通道。
"""
from flask import Blueprint, g, jsonify

from main import WAREHOUSE_HEADER, get_control_db, request_payload
from auth import require_permission
import scope as S

scope_bp = Blueprint("scope", __name__)

VALID_SCOPES = ("all", "grant")


def _warehouse_row(control, wid):
    row = control.execute(
        "SELECT id, code, name, status, db_path FROM warehouse WHERE id = ?", (wid,)
    ).fetchone()
    return dict(row) if row else None


# ───────────────────────────── 当前上下文 ─────────────────────────────

@scope_bp.route("/api/scope/context", methods=["GET"])
def scope_context():
    """前端启动时拉取：权限摘要 + 可切换仓库清单 + 当前仓。

    属于软解析端点：账号未配置授权时返回 ``mode='denied'`` 与空清单，
    让界面能显示明确提示，而不是只看到一个 403。
    """
    control = get_control_db()
    s = S.current_scope()
    warehouses = S.visible_warehouses(s, control)
    current_id = getattr(g, "warehouse_id", None)
    return jsonify(ok=True, **S.scope_summary(s), warehouses=warehouses,
                   current_warehouse_id=current_id,
                   current_warehouse=_warehouse_row(control, current_id) if current_id else None,
                   header=WAREHOUSE_HEADER)


@scope_bp.route("/api/scope/warehouses", methods=["GET"])
def scope_warehouses():
    """A 作业视角：当前账号可切换到的仓库清单。"""
    return jsonify(ok=True, warehouses=S.visible_warehouses(S.current_scope(), get_control_db()))


@scope_bp.route("/api/scope/owners", methods=["GET"])
def scope_owners():
    """当前账号可见的货主清单（供前端下拉与授权配置页参照）。"""
    return jsonify(ok=True, owners=S.visible_owners(S.current_scope(), get_control_db()))


@scope_bp.route("/api/scope/warehouse", methods=["POST"])
def switch_warehouse():
    """校验一次切仓请求是否合法并回显目标仓。

    真正的切换由前端把 ``X-WMS-Warehouse`` header 附加到后续每个请求完成，
    服务端无会话状态，因此不需要「登录态里存当前仓」。
    """
    wid = request_payload().get("warehouse_id")
    row = S.check_warehouse_access(S.current_scope(), wid, get_control_db())
    if row is None:
        return jsonify(ok=False, error="warehouse_id 必填"), 400
    return jsonify(ok=True, warehouse=_warehouse_row(get_control_db(), row["id"]),
                   header=WAREHOUSE_HEADER)


# ───────────────────────── 账号数据授权配置 ─────────────────────────

def _user_exists(db, uid):
    return db.execute("SELECT id FROM user WHERE id = ?", (uid,)).fetchone() is not None


def _parse_ids(payload, key):
    raw = payload.get(key, [])
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{key} 必须是数组")
    out = []
    for v in raw:
        try:
            n = int(v)
        except (TypeError, ValueError):
            raise ValueError(f"{key} 元素必须是整数")
        if n not in out:
            out.append(n)
    return out


@scope_bp.route("/api/users/<int:uid>/grants", methods=["GET"])
@require_permission("user:manage")
def get_grants(uid):
    db = get_control_db()
    if not _user_exists(db, uid):
        return jsonify(ok=False, error="用户不存在"), 404
    u = db.execute(
        "SELECT id, username, warehouse_scope, owner_scope FROM user WHERE id = ?", (uid,)
    ).fetchone()
    wh = [r[0] for r in db.execute(
        "SELECT warehouse_id FROM user_warehouse_grant WHERE user_id = ? ORDER BY warehouse_id", (uid,)
    )]
    ow = [r[0] for r in db.execute(
        "SELECT owner_id FROM user_owner_grant WHERE user_id = ? ORDER BY owner_id", (uid,)
    )]
    warehouses = [dict(r) for r in db.execute(
        "SELECT id, code, name, status FROM warehouse ORDER BY id"
    )]
    owners = [dict(r) for r in db.execute(
        "SELECT id, code, name, status FROM owner ORDER BY id"
    )]
    return jsonify(ok=True, user=dict(u), warehouse_ids=wh, owner_ids=ow,
                   warehouses=warehouses, owners=owners)


@scope_bp.route("/api/users/<int:uid>/grants", methods=["PUT"])
@require_permission("user:manage")
def save_grants(uid):
    """配置账号的仓库 / 货主可见范围（全部或自定义部分）。

    ``*_scope='all'`` 时授权行会被清空（ALL 由标志显式给出，不靠空集合表示）；
    ``'grant'`` 时按传入的 id 列表写入授权行。写入前逐个校验 id 存在，
    避免把不存在的仓库 / 货主写进授权表。
    """
    db = get_control_db()
    if not _user_exists(db, uid):
        return jsonify(ok=False, error="用户不存在"), 404
    payload = request_payload()
    wh_scope = str(payload.get("warehouse_scope") or "grant").strip()
    ow_scope = str(payload.get("owner_scope") or "grant").strip()
    if wh_scope not in VALID_SCOPES or ow_scope not in VALID_SCOPES:
        return jsonify(ok=False, error="scope 只能是 all/grant"), 400
    try:
        wh_ids = _parse_ids(payload, "warehouse_ids")
        ow_ids = _parse_ids(payload, "owner_ids")
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400

    for wid in wh_ids:
        if db.execute("SELECT id FROM warehouse WHERE id = ?", (wid,)).fetchone() is None:
            return jsonify(ok=False, error=f"仓库 {wid} 不存在"), 400
    for oid in ow_ids:
        if db.execute("SELECT id FROM owner WHERE id = ?", (oid,)).fetchone() is None:
            return jsonify(ok=False, error=f"货主 {oid} 不存在"), 400

    db.execute(
        "UPDATE user SET warehouse_scope = ?, owner_scope = ?, "
        "updated_at = datetime('now','localtime') WHERE id = ?",
        (wh_scope, ow_scope, uid),
    )
    db.execute("DELETE FROM user_warehouse_grant WHERE user_id = ?", (uid,))
    db.execute("DELETE FROM user_owner_grant WHERE user_id = ?", (uid,))
    if wh_scope == "grant":
        db.executemany(
            "INSERT INTO user_warehouse_grant (user_id, warehouse_id) VALUES (?, ?)",
            [(uid, w) for w in wh_ids],
        )
    if ow_scope == "grant":
        db.executemany(
            "INSERT INTO user_owner_grant (user_id, owner_id) VALUES (?, ?)",
            [(uid, o) for o in ow_ids],
        )
    db.commit()
    return get_grants(uid)
