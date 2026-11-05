"""MiniWMS 认证与权限基础（零依赖，仅用标准库）。

设计目标（呼应「需要有角色用户，后续功能要联动」）：
- 用户（user）归属一个角色（role），角色持有一份权限清单（JSON 数组）
- 密码用 salt + sha256 哈希存储，不存明文
- 登录签发 HMAC-SHA256 签名的无状态 token（无外部依赖、无需会话表）
- 提供 `require_permission(perm)` 装饰器，后续任何接口想做权限控制，
  只需在函数上加一行装饰器即可联动本基础（详见 DEVELOPMENT.md「用户与角色」）

权限约定：用 `资源:动作` 形式，如 `product:read` / `product:write`；
通配 `*` 或 `资源:*` 表示全部。**不再按角色名硬编码放行**：全部权限由角色
`permissions` 里的 `*` 显式给出，与 scope.py 的「ALL 必须显式」保持同一纪律。

用户 / 角色属于控制面，一律从中央库读取（`get_control_db()`），
这样按仓分库后登录与鉴权不会被业务库里的副本干扰。
"""
import hashlib
import hmac
import json
import os
import time
from functools import wraps

from flask import jsonify, request

SECRET = os.environ.get("MINIWMS_AUTH_SECRET", "miniwms-dev-secret-change-me")
TOKEN_TTL = 60 * 60 * 24 * 7  # 7 天


def hash_password(password):
    """返回 (salt, hash) —— 密码 + 随机盐 sha256。"""
    salt = os.urandom(8).hex()
    h = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return salt, h


def verify_password(password, salt, expected_hash):
    if not salt or not expected_hash:
        return False
    h = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return hmac.compare_digest(h, expected_hash)


def make_token(user_id, ttl=TOKEN_TTL):
    payload = json.dumps({"uid": user_id, "exp": int(time.time()) + ttl})
    sig = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_token(token):
    if not token or "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    expected = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        data = json.loads(payload)
    except Exception:
        return None
    if int(data.get("exp", 0)) < int(time.time()):
        return None
    return data.get("uid")


def get_current_user():
    """从 Authorization: Bearer <token> 还原当前用户（含角色权限），无效返回 None。

    查的是中央库（控制面）：账号、角色、授权范围在按仓分库时仍只有一份权威数据。
    """
    from main import get_control_db

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    uid = verify_token(auth[7:])
    if not uid:
        return None
    db = get_control_db()
    row = db.execute(
        "SELECT u.*, r.name AS role_name, r.permissions AS role_permissions "
        "FROM user u LEFT JOIN role r ON r.id = u.role_id WHERE u.id = ?",
        (uid,),
    ).fetchone()
    if row is None or row["status"] != "active":
        return None
    u = dict(row)
    try:
        u["permissions"] = json.loads(u.get("role_permissions") or "[]")
    except Exception:
        u["permissions"] = []
    return u


def has_permission(user, perm):
    if not user:
        return False
    # 全部权限只认角色 permissions 里显式的 '*'，不靠角色名做隐式提权。
    perms = user.get("permissions") or []
    return perm in perms or any(
        p.endswith("*") and perm.startswith(p[:-1]) for p in perms
    )


def require_permission(perm=None):
    """装饰器：要求登录（且具备 perm 权限）。用法：
    @user_bp.route("/x"); @require_permission("user:manage"); def handler(): ...
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = get_current_user()
            if not user:
                return jsonify(ok=False, error="未登录或登录已过期"), 401
            if perm and not has_permission(user, perm):
                return jsonify(ok=False, error="权限不足"), 403
            request.user = user  # 供后续逻辑读取操作人
            return fn(*args, **kwargs)

        return wrapper

    return decorator
