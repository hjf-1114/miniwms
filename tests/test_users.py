"""用户与角色管理接口测试（M9 基础）。

测试隔离：复用共享临时库（MINIWMS_DB）。init_db 在 setUpClass 调一次，
空库时播种默认角色（管理员/操作员/只读）与 admin 账户（admin/admin123）。
本文件不删除 role/user 表，避免破坏种子数据；用户名均带唯一后缀防重复。
"""
import os
import sys
import tempfile
import unittest

_TMP = os.path.join(tempfile.gettempdir(), "miniwms_unittest")
os.makedirs(_TMP, exist_ok=True)
os.environ.setdefault("MINIWMS_DB", os.path.join(_TMP, "test.db"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
from auth import make_token  # noqa: E402


class AuthUserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()
        # 确保种子角色与 admin 存在
        db = __import__("sqlite3").connect(os.environ["MINIWMS_DB"])
        try:
            if db.execute("SELECT COUNT(*) FROM user WHERE username='admin'").fetchone()[0] == 0:
                main._seed_auth(db)
        finally:
            db.close()

    def _login(self, username, password):
        r = self.client.post("/api/auth/login", json={"username": username, "password": password})
        return r

    def _admin_token(self):
        r = self._login("admin", "admin123")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()["token"]

    # ── 登录 ──
    def test_login_ok(self):
        r = self._login("admin", "admin123")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])
        self.assertIn("token", r.get_json())

    def test_login_wrong_password(self):
        r = self._login("admin", "wrong")
        self.assertEqual(r.status_code, 401)
        self.assertFalse(r.get_json()["ok"])

    def test_login_unknown_user(self):
        r = self._login("nobody", "x")
        self.assertEqual(r.status_code, 401)

    # ── 权限门禁 ──
    def test_roles_requires_auth(self):
        r = self.client.get("/api/roles")
        self.assertEqual(r.status_code, 401)

    def test_roles_with_admin_token(self):
        tok = self._admin_token()
        r = self.client.get("/api/roles", headers={"Authorization": "Bearer " + tok})
        self.assertEqual(r.status_code, 200)
        names = {x["name"] for x in r.get_json()["roles"]}
        self.assertIn("管理员", names)
        self.assertIn("操作员", names)
        self.assertIn("只读", names)

    # ── 角色 CRUD ──
    def test_role_crud(self):
        tok = self._admin_token()
        h = {"Authorization": "Bearer " + tok}
        create = self.client.post("/api/roles", json={"name": "盘点员", "permissions": ["stock:read", "asn:read"]}, headers=h)
        self.assertEqual(create.status_code, 201, create.get_data(as_text=True))
        rid = create.get_json()["role"]["id"]
        # 重复名 409
        dup = self.client.post("/api/roles", json={"name": "盘点员"}, headers=h)
        self.assertEqual(dup.status_code, 409)
        # 更新
        upd = self.client.put(f"/api/roles/{rid}", json={"name": "盘点员2", "permissions": ["stock:read"]}, headers=h)
        self.assertEqual(upd.status_code, 200)
        self.assertEqual(upd.get_json()["role"]["name"], "盘点员2")
        # 删除
        d = self.client.delete(f"/api/roles/{rid}", headers=h)
        self.assertEqual(d.status_code, 200)

    def test_delete_role_with_users_blocked(self):
        tok = self._admin_token()
        h = {"Authorization": "Bearer " + tok}
        # 获取管理员角色 id
        roles = self.client.get("/api/roles", headers=h).get_json()["roles"]
        admin_role = next(r for r in roles if r["name"] == "管理员")
        d = self.client.delete(f"/api/roles/{admin_role['id']}", headers=h)
        self.assertEqual(d.status_code, 409)  # 仍有 admin 用户引用

    # ── 用户 CRUD + 登录联动 ──
    def test_user_crud_and_login(self):
        tok = self._admin_token()
        h = {"Authorization": "Bearer " + tok}
        roles = self.client.get("/api/roles", headers=h).get_json()["roles"]
        op_role = next(r for r in roles if r["name"] == "操作员")
        create = self.client.post("/api/users", json={
            "username": "alice", "password": "secret1", "role_id": op_role["id"], "status": "active",
        }, headers=h)
        self.assertEqual(create.status_code, 201, create.get_data(as_text=True))
        uid = create.get_json()["user"]["id"]
        # 用新账号登录成功
        login = self._login("alice", "secret1")
        self.assertEqual(login.status_code, 200)
        alice_tok = login.get_json()["token"]
        # 操作员无 user:manage 权限 → 访问 /api/roles 应 403
        forbidden = self.client.get("/api/roles", headers={"Authorization": "Bearer " + alice_tok})
        self.assertEqual(forbidden.status_code, 403)
        # 改密码后旧密码失效、新密码可用
        upd = self.client.put(f"/api/users/{uid}", json={"username": "alice", "password": "newsec", "role_id": op_role["id"]}, headers=h)
        self.assertEqual(upd.status_code, 200)
        self.assertEqual(self._login("alice", "secret1").status_code, 401)
        self.assertEqual(self._login("alice", "newsec").status_code, 200)
        # 删除
        d = self.client.delete(f"/api/users/{uid}", headers=h)
        self.assertEqual(d.status_code, 200)

    def test_create_user_duplicate(self):
        tok = self._admin_token()
        h = {"Authorization": "Bearer " + tok}
        roles = self.client.get("/api/roles", headers=h).get_json()["roles"]
        op_role = next(r for r in roles if r["name"] == "操作员")
        self.client.post("/api/users", json={"username": "bob", "password": "x", "role_id": op_role["id"]}, headers=h)
        dup = self.client.post("/api/users", json={"username": "bob", "password": "y", "role_id": op_role["id"]}, headers=h)
        self.assertEqual(dup.status_code, 409)
        # 清理 bob
        bob = self.client.get("/api/users", headers=h).get_json()["users"]
        bob_id = next(u["id"] for u in bob if u["username"] == "bob")
        self.client.delete(f"/api/users/{bob_id}", headers=h)

    def test_delete_last_admin_blocked(self):
        tok = self._admin_token()
        h = {"Authorization": "Bearer " + tok}
        users = self.client.get("/api/users", headers=h).get_json()["users"]
        admin_user = next(u for u in users if u["username"] == "admin")
        d = self.client.delete(f"/api/users/{admin_user['id']}", headers=h)
        self.assertEqual(d.status_code, 409)

    def test_invalid_token_rejected(self):
        r = self.client.get("/api/roles", headers={"Authorization": "Bearer garbage.token"})
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()
