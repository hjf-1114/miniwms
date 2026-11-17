"""权限码目录与初始数据播种的回归测试。

锁三件事：
1. **目录不漂移** —— 目录里的码与各模块常量一致，模板权限全在目录内；
2. **目录端点可用** —— ``GET /api/permissions`` 返回分组与模板；
3. **账号授权真的可用** —— 受限账号登录后能访问业务、且**货主隔离生效**
   （只看得到被授权的货主），这是「账号能真正用起来」的关键。
"""
import contextlib
import os
import sqlite3
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
# 与其它测试模块共用同一份临时库（setdefault 不覆盖已设的值）——
# 指向演示库会与全量运行时的其它模块抢库，且演示数据不保证存在。
_TMP = os.path.join(tempfile.gettempdir(), "miniwms_unittest")
os.makedirs(_TMP, exist_ok=True)
os.environ.setdefault("MINIWMS_DB", os.path.join(_TMP, "test.db"))
# 注意：**不要**在模块级把 MINIWMS_ALLOW_ANONYMOUS 设成 0 ——
# 同进程内其它测试模块依赖匿名演示模式，模块级改动会污染它们
# （表现为几百个 401/403 失败）。需要「强制登录」时在用例内临时改并复原。

import main  # noqa: E402
import permission_catalog as PC  # noqa: E402
from auth import hash_password  # noqa: E402


def _conn():
    return sqlite3.connect(os.environ["MINIWMS_DB"])


class PermissionCatalogTest(unittest.TestCase):
    """权限码目录。"""

    def test_01_catalog_has_groups_and_codes(self):
        d = PC.dictionary()
        self.assertTrue(d["groups"], "目录不能为空")
        self.assertTrue(d["codes"])
        self.assertEqual(d["wildcard"], "*")
        # 每个分组项都要有 code 与中文标签
        for g in d["groups"]:
            self.assertTrue(g["name"], "分组必须有名称")
            for it in g["items"]:
                self.assertIn(":", it["code"])
                self.assertTrue(it["label"])

    def test_02_codes_are_unique(self):
        codes = PC.ALL_CODES
        self.assertEqual(len(codes), len(set(codes)), "权限码有重复")

    def test_03_module_constants_all_in_catalog(self):
        """各模块自己定义的权限码常量必须都在目录里（防漂移）。"""
        import constants as C
        consts = [
            C.IT_PERM_CREATE, C.IT_PERM_ALLOCATE, C.IT_PERM_UNALLOCATE,
            C.IT_PERM_APPROVE, C.IT_PERM_CLOSE, C.IT_PERM_DELETE,
            C.OUTBOUND_PERM_PLAN, C.OUTBOUND_PERM_ALLOCATE,
            C.OUTBOUND_PERM_UNALLOCATE, C.OUTBOUND_PERM_WAVE,
            C.OUTBOUND_PERM_LOG, C.OUTBOUND_PERM_CONFIG,
            C.REVIEW_PERM_CREATE, C.REVIEW_PERM_CHECK, C.REVIEW_PERM_COMPLETE,
            C.REVIEW_PERM_CANCEL, C.REVIEW_PERM_LOG, C.REVIEW_PERM_CONFIG,
            C.CARE_PERM_PLAN, C.CARE_PERM_GENERATE, C.CARE_PERM_EXECUTE,
            C.CARE_PERM_HANDLE, C.CARE_PERM_LOG, C.CARE_PERM_CONFIG,
            C.SPLIT_PERM_EXECUTE, C.SPLIT_PERM_LOG,
            C.GSP_PERM_VIEW, C.GSP_PERM_EXPORT, C.WL_PERM_VIEW,
            C.PRINT_PERM_CONFIG, C.PRINT_PERM_VIEW, C.PRINT_PERM_PDF,
            C.FLEET_PERM_VIEW, C.FLEET_PERM_CONFIG,
            C.PT_PERM_VIEW, C.PT_PERM_CONFIG,
            C.SERIAL_PERM_VIEW, C.SERIAL_PERM_CONFIG,
            C.LOC_GROUP_PERM_VIEW, C.LOC_GROUP_PERM_CONFIG,
            C.OP_REPORT_PERM_VIEW,
        ]
        for code in consts:
            self.assertIn(code, PC.ALL_CODES, f"权限码 {code} 不在目录里")

    def test_04_role_templates_use_catalog_codes_only(self):
        """模板里的每个权限码都必须在目录内 —— 否则「勾选面板」选不到它。"""
        codes = set(PC.ALL_CODES)
        for tpl in PC.ROLE_TEMPLATES:
            for c in tpl["permissions"]:
                self.assertTrue(c == "*" or c in codes,
                                f"模板 {tpl['name']} 用了目录外的码 {c}")

    def test_05_admin_template_is_wildcard(self):
        tpl = PC.template("系统管理员")
        self.assertIsNotNone(tpl)
        self.assertEqual(tpl["permissions"], ["*"])


class PermissionEndpointTest(unittest.TestCase):
    """GET /api/permissions 端点。"""

    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()
        # 端点挂了 require_permission → 必须带 token（不需要关匿名模式）


    def _admin_token(self):
        r = self.client.post("/api/auth/login",
                             json={"username": "admin", "password": "admin123"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:200])
        return r.get_json()["token"]

    def test_10_endpoint_requires_login(self):
        r = self.client.get("/api/permissions")
        self.assertEqual(r.status_code, 401)

    def test_11_endpoint_returns_catalog(self):
        h = {"Authorization": "Bearer " + self._admin_token()}
        r = self.client.get("/api/permissions", headers=h)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:200])
        d = r.get_json()
        self.assertTrue(d["ok"])
        self.assertTrue(d["groups"])
        self.assertTrue(d["templates"])
        self.assertIn("user:manage", d["codes"])

    def test_12_role_crud_with_catalog_codes(self):
        """用目录里的码建角色 → 读回一致。"""
        h = {"Authorization": "Bearer " + self._admin_token()}
        code = "TEST-PERM-CATALOG"
        r = self.client.post("/api/roles", json={
            "name": code, "permissions": ["stock:read", "gsp:view"]}, headers=h)
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True)[:200])
        rid = r.get_json()["role"]["id"]
        try:
            d = self.client.get(f"/api/roles/{rid}", headers=h).get_json()
            self.assertEqual(sorted(d["role"]["permissions"]),
                             ["gsp:view", "stock:read"])
        finally:
            self.client.delete(f"/api/roles/{rid}", headers=h)


class AccountGrantTest(unittest.TestCase):
    """账号 + 数据授权（这是「账号能不能真正用起来」的关键）。

    这里刻意**不开**匿名演示模式的测试隔离：登录后 scope 解析走的是
    「已登录 → 读授权表」，与匿名开关无关。
    """

    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()
        cls._mk_account()

    @classmethod
    def _mk_account(cls):
        """建一个受限账号：只授权 1 个仓库 + 1 个货主。"""
        with contextlib.closing(_conn()) as c:
            c.row_factory = sqlite3.Row
            # 自己造主数据，不依赖演示库内容
            for code in ("PERMTEST-OW-A", "PERMTEST-OW-B"):
                c.execute("DELETE FROM owner WHERE code = ?", (code,))
                c.execute(
                    "INSERT INTO owner (code, name, status, created_at, updated_at)"
                    " VALUES (?, ?, 'active', datetime('now','localtime'),"
                    " datetime('now','localtime'))", (code, code))
            c.execute("DELETE FROM warehouse WHERE code = 'PERMTEST-WH'")
            c.execute(
                "INSERT INTO warehouse (code, name, status, created_at, updated_at)"
                " VALUES ('PERMTEST-WH', 'PERMTEST-WH', 'active',"
                " datetime('now','localtime'), datetime('now','localtime'))")
            c.commit()
            rows = c.execute(
                "SELECT id, code FROM owner WHERE code LIKE 'PERMTEST-%' ORDER BY code"
            ).fetchall()
            cls.owner_id = rows[0]["id"]
            cls.other_owner_id = rows[1]["id"]
            cls.wh_id = c.execute(
                "SELECT id FROM warehouse WHERE code = 'PERMTEST-WH'").fetchone()["id"]
            rid = c.execute("SELECT id FROM role ORDER BY id LIMIT 1").fetchone()["id"]
            salt, h = hash_password("grant123456")
            c.execute("DELETE FROM user WHERE username = 'TEST-GRANT'")
            cur = c.execute(
                "INSERT INTO user (username, password_salt, password_hash, role_id,"
                " status, warehouse_scope, owner_scope, created_at, updated_at)"
                " VALUES ('TEST-GRANT', ?, ?, ?, 'active', 'grant', 'grant',"
                " datetime('now','localtime'), datetime('now','localtime'))",
                (salt, h, rid))
            cls.uid = cur.lastrowid
            c.execute("INSERT INTO user_warehouse_grant (user_id, warehouse_id)"
                      " VALUES (?, ?)", (cls.uid, cls.wh_id))
            c.execute("INSERT INTO user_owner_grant (user_id, owner_id)"
                      " VALUES (?, ?)", (cls.uid, cls.owner_id))
            c.commit()

    @classmethod
    def tearDownClass(cls):
        with contextlib.closing(_conn()) as c:
            c.execute("DELETE FROM user WHERE username = 'TEST-GRANT'")
            c.execute("DELETE FROM owner WHERE code LIKE 'PERMTEST-%'")
            c.execute("DELETE FROM warehouse WHERE code = 'PERMTEST-WH'")
            c.commit()

    def _token(self):
        r = self.client.post("/api/auth/login",
                             json={"username": "TEST-GRANT",
                                   "password": "grant123456"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:200])
        return r.get_json()["token"]

    def test_20_login_and_business_access(self):
        """受限账号登录后能访问业务（不被 scope 拦成 403）。"""
        tok = self._token()
        h = {"Authorization": "Bearer " + tok}
        for path in ("/api/products", "/api/stock", "/api/owners"):
            r = self.client.get(path, headers=h)
            self.assertEqual(r.status_code, 200,
                             f"{path} → {r.status_code} {r.get_data(as_text=True)[:120]}")

    def test_21_owner_isolation_effective(self):
        """受限账号只看得到被授权的货主 —— 隔离真的生效。"""
        tok = self._token()
        r = self.client.get("/api/owners?page_size=100",
                            headers={"Authorization": "Bearer " + tok})
        self.assertEqual(r.status_code, 200)
        codes = {o["code"] for o in r.get_json()["owners"]}
        with contextlib.closing(_conn()) as c:
            c.row_factory = sqlite3.Row
            mine = c.execute("SELECT code FROM owner WHERE id = ?",
                             (self.owner_id,)).fetchone()["code"]
            other = c.execute("SELECT code FROM owner WHERE id = ?",
                              (self.other_owner_id,)).fetchone()["code"]
        self.assertNotEqual(mine, other)
        self.assertIn(mine, codes)
        self.assertNotIn(other, codes, "未授权的货主不应出现")

    def test_22_missing_grant_is_rejected(self):
        """授权表为空 + scope=grant → 403（不退化成 ALL）。

        这是「hjf 账号原本的坏状态」：scope 写着 grant 但授权表 0 行，
        登录后任何业务都 403。resolve_scope 必须显式拒绝。
        """
        with contextlib.closing(_conn()) as c:
            c.execute("DELETE FROM user_owner_grant WHERE user_id = ?", (self.uid,))
            c.commit()
        try:
            tok = self._token()
            r = self.client.get("/api/products",
                                headers={"Authorization": "Bearer " + tok})
            self.assertEqual(r.status_code, 403, r.get_data(as_text=True)[:200])
            body = r.get_json()
            self.assertIn("货主", body.get("error", ""))
        finally:
            with contextlib.closing(_conn()) as c:
                c.execute("INSERT INTO user_owner_grant (user_id, owner_id)"
                          " VALUES (?, ?)", (self.uid, self.owner_id))
                c.commit()


if __name__ == "__main__":
    unittest.main()
