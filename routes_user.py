"""用户与角色管理（M9 基础）：/api/auth、/api/roles、/api/users。

- 登录签发无状态 token（见 auth.py）
- 角色 / 用户 CRUD 全部要求 `user:manage` 权限（管理员默认具备）
- 后续任何功能要做权限控制，直接给对应路由加 `@require_permission("xxx")` 即可联动本模块
"""
import json

from flask import Blueprint, jsonify, request

from main import get_db, request_payload
from auth import (
    hash_password,
    verify_password,
    make_token,
    require_permission,
)

user_bp = Blueprint("user", __name__, url_prefix="/api")


# ───────────────────────────── 认证 ─────────────────────────────

@user_bp.route("/auth/login", methods=["POST"])
def login():
    payload = request_payload()
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    db = get_db()
    row = db.execute("SELECT * FROM user WHERE username = ?", (username,)).fetchone()
    if row is None or row["status"] != "active":
        return jsonify(ok=False, error="用户名或密码错误"), 401
    if not verify_password(password, row["password_salt"], row["password_hash"]):
        return jsonify(ok=False, error="用户名或密码错误"), 401
    token = make_token(row["id"])
    role = db.execute("SELECT name, permissions FROM role WHERE id = ?", (row["role_id"],)).fetchone()
    perms = []
    try:
        perms = json.loads(role["permissions"]) if role else []
    except Exception:
        perms = []
    return jsonify(ok=True, token=token, user={
        "id": row["id"],
        "username": row["username"],
        "role_name": role["name"] if role else None,
        "owner_id": row["owner_id"],
        "permissions": perms,
        "status": row["status"],
    })


# ───────────────────────────── 角色 ─────────────────────────────

def _role_out(db, rid):
    r = db.execute("SELECT * FROM role WHERE id = ?", (rid,)).fetchone()
    if r is None:
        return None
    d = dict(r)
    try:
        d["permissions"] = json.loads(d["permissions"] or "[]")
    except Exception:
        d["permissions"] = []
    # 引用该角色的用户数
    d["user_count"] = db.execute(
        "SELECT COUNT(*) AS n FROM user WHERE role_id = ?", (rid,)
    ).fetchone()["n"]
    return d


@user_bp.route("/roles", methods=["GET"])
@require_permission("user:manage")
def list_roles():
    db = get_db()
    rows = db.execute("SELECT * FROM role ORDER BY id").fetchall()
    return jsonify(ok=True, roles=[_role_out(db, r["id"]) for r in rows])


@user_bp.route("/roles", methods=["POST"])
@require_permission("user:manage")
def create_role():
    payload = request_payload()
    name = str(payload.get("name") or "").strip()
    if not name:
        return jsonify(ok=False, error="name 必填"), 400
    perms = payload.get("permissions", [])
    if isinstance(perms, list):
        perms = json.dumps(perms)
    elif isinstance(perms, str):
        try:
            json.loads(perms)
        except Exception:
            return jsonify(ok=False, error="permissions 不是合法 JSON 数组"), 400
    else:
        perms = "[]"
    db = get_db()
    if db.execute("SELECT id FROM role WHERE name = ?", (name,)).fetchone():
        return jsonify(ok=False, error="角色名已存在"), 409
    cur = db.execute(
        "INSERT INTO role (name, permissions, created_at, updated_at) "
        "VALUES (?, ?, datetime('now','localtime'), datetime('now','localtime'))",
        (name, perms),
    )
    db.commit()
    return jsonify(ok=True, role=_role_out(db, cur.lastrowid)), 201


@user_bp.route("/roles/<int:rid>", methods=["GET", "PUT"])
@require_permission("user:manage")
def role_detail(rid):
    db = get_db()
    if request.method == "GET":
        r = _role_out(db, rid)
        if r is None:
            return jsonify(ok=False, error="角色不存在"), 404
        return jsonify(ok=True, role=r)
    # PUT
    payload = request_payload()
    name = str(payload.get("name") or "").strip()
    if not name:
        return jsonify(ok=False, error="name 必填"), 400
    perms = payload.get("permissions", [])
    if isinstance(perms, list):
        perms = json.dumps(perms)
    elif isinstance(perms, str):
        try:
            json.loads(perms)
        except Exception:
            return jsonify(ok=False, error="permissions 不是合法 JSON 数组"), 400
    else:
        perms = None
    if db.execute("SELECT id FROM role WHERE name = ? AND id != ?", (name, rid)).fetchone():
        return jsonify(ok=False, error="角色名已存在"), 409
    if db.execute("SELECT id FROM role WHERE id = ?", (rid,)).fetchone() is None:
        return jsonify(ok=False, error="角色不存在"), 404
    db.execute(
        "UPDATE role SET name = ?, permissions = COALESCE(?, permissions), "
        "updated_at = datetime('now','localtime') WHERE id = ?",
        (name, perms, rid),
    )
    db.commit()
    return jsonify(ok=True, role=_role_out(db, rid))


@user_bp.route("/roles/<int:rid>", methods=["DELETE"])
@require_permission("user:manage")
def delete_role(rid):
    db = get_db()
    if db.execute("SELECT id FROM role WHERE id = ?", (rid,)).fetchone() is None:
        return jsonify(ok=False, error="角色不存在"), 404
    if db.execute("SELECT id FROM user WHERE role_id = ?", (rid,)).fetchone():
        return jsonify(ok=False, error="该角色下仍有用户，不能删除"), 409
    db.execute("DELETE FROM role WHERE id = ?", (rid,))
    db.commit()
    return jsonify(ok=True)


# ───────────────────────────── 用户 ─────────────────────────────

def _user_out(db, uid):
    u = db.execute(
        "SELECT u.id, u.username, u.role_id, r.name AS role_name, u.owner_id, "
        "o.code AS owner_code, u.status, u.created_at, u.updated_at "
        "FROM user u LEFT JOIN role r ON r.id = u.role_id "
        "LEFT JOIN owner o ON o.id = u.owner_id WHERE u.id = ?",
        (uid,),
    ).fetchone()
    return dict(u) if u else None


@user_bp.route("/users", methods=["GET"])
@require_permission("user:manage")
def list_users():
    db = get_db()
    rows = db.execute(
        "SELECT u.id, u.username, u.role_id, r.name AS role_name, u.owner_id, "
        "o.code AS owner_code, u.status, u.created_at "
        "FROM user u LEFT JOIN role r ON r.id = u.role_id "
        "LEFT JOIN owner o ON o.id = u.owner_id ORDER BY u.id"
    ).fetchall()
    return jsonify(ok=True, users=[dict(r) for r in rows])


@user_bp.route("/users", methods=["POST"])
@require_permission("user:manage")
def create_user():
    payload = request_payload()
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    role_id = payload.get("role_id")
    owner_id = payload.get("owner_id") or None
    status = str(payload.get("status") or "active")
    if not username:
        return jsonify(ok=False, error="username 必填"), 400
    if not password:
        return jsonify(ok=False, error="password 必填"), 400
    try:
        role_id = int(role_id)
    except (TypeError, ValueError):
        return jsonify(ok=False, error="role_id 必填且为整数"), 400
    db = get_db()
    if db.execute("SELECT id FROM user WHERE username = ?", (username,)).fetchone():
        return jsonify(ok=False, error="用户名已存在"), 409
    if db.execute("SELECT id FROM role WHERE id = ?", (role_id,)).fetchone() is None:
        return jsonify(ok=False, error="角色不存在"), 400
    if owner_id is not None:
        try:
            owner_id = int(owner_id)
        except (TypeError, ValueError):
            return jsonify(ok=False, error="owner_id 必须为整数"), 400
        if db.execute("SELECT id FROM owner WHERE id = ?", (owner_id,)).fetchone() is None:
            return jsonify(ok=False, error="货主不存在"), 400
    salt, h = hash_password(password)
    cur = db.execute(
        "INSERT INTO user (username, password_salt, password_hash, role_id, owner_id, status, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, datetime('now','localtime'), datetime('now','localtime'))",
        (username, salt, h, role_id, owner_id, status),
    )
    db.commit()
    return jsonify(ok=True, user=_user_out(db, cur.lastrowid)), 201


@user_bp.route("/users/<int:uid>", methods=["GET", "PUT"])
@require_permission("user:manage")
def user_detail(uid):
    db = get_db()
    if request.method == "GET":
        u = _user_out(db, uid)
        if u is None:
            return jsonify(ok=False, error="用户不存在"), 404
        return jsonify(ok=True, user=u)
    # PUT
    payload = request_payload()
    username = str(payload.get("username") or "").strip()
    role_id = payload.get("role_id")
    owner_id = payload.get("owner_id")  # None 表示清空；也可显式传 null
    status = payload.get("status")
    password = payload.get("password")
    if not username:
        return jsonify(ok=False, error="username 必填"), 400
    if db.execute("SELECT id FROM user WHERE username = ? AND id != ?", (username, uid)).fetchone():
        return jsonify(ok=False, error="用户名已存在"), 409
    if db.execute("SELECT id FROM user WHERE id = ?", (uid,)).fetchone() is None:
        return jsonify(ok=False, error="用户不存在"), 404

    sets, params = ["username = ?"], [username]
    if role_id is not None:
        try:
            role_id = int(role_id)
        except (TypeError, ValueError):
            return jsonify(ok=False, error="role_id 必须为整数"), 400
        if db.execute("SELECT id FROM role WHERE id = ?", (role_id,)).fetchone() is None:
            return jsonify(ok=False, error="角色不存在"), 400
        sets.append("role_id = ?"); params.append(role_id)
    if "owner_id" in payload:
        if owner_id is None or owner_id == "":
            sets.append("owner_id = NULL")
        else:
            try:
                owner_id = int(owner_id)
            except (TypeError, ValueError):
                return jsonify(ok=False, error="owner_id 必须为整数"), 400
            if db.execute("SELECT id FROM owner WHERE id = ?", (owner_id,)).fetchone() is None:
                return jsonify(ok=False, error="货主不存在"), 400
            sets.append("owner_id = ?"); params.append(owner_id)
    if status is not None:
        sets.append("status = ?"); params.append(status)
    if password:
        salt, h = hash_password(str(password))
        sets.append("password_salt = ?"); params.append(salt)
        sets.append("password_hash = ?"); params.append(h)
    sets.append("updated_at = datetime('now','localtime')")
    params.append(uid)
    db.execute("UPDATE user SET " + ", ".join(sets) + " WHERE id = ?", params)
    db.commit()
    return jsonify(ok=True, user=_user_out(db, uid))


@user_bp.route("/users/<int:uid>", methods=["DELETE"])
@require_permission("user:manage")
def delete_user(uid):
    db = get_db()
    if db.execute("SELECT id FROM user WHERE id = ?", (uid,)).fetchone() is None:
        return jsonify(ok=False, error="用户不存在"), 404
    # 不允许删除最后一个管理员，避免锁死
    if db.execute(
        "SELECT COUNT(*) AS n FROM user u JOIN role r ON r.id = u.role_id "
        "WHERE r.name = '管理员' AND u.status = 'active'"
    ).fetchone()["n"] <= 1:
        target = db.execute(
            "SELECT r.name FROM user u JOIN role r ON r.id = u.role_id WHERE u.id = ?", (uid,)
        ).fetchone()
        if target and target["name"] == "管理员":
            return jsonify(ok=False, error="不能删除最后一个管理员账户"), 409
    db.execute("DELETE FROM user WHERE id = ?", (uid,))
    db.commit()
    return jsonify(ok=True)
