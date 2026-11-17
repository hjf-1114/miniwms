"""M32 产品类型字典：CRUD / 层级 / 唯一性 / 引用保护 / 老数据回填 / 货主隔离。

共享物理库约定同 M31–M41：closing 包裸连接；setUp 只清 M32 前缀的数据与
本模块自建的 owner/货品，不动别的里程碑数据；setUpClass 显式 init_db。
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing

_TMP = os.path.join(tempfile.gettempdir(), "miniwms_unittest")
os.makedirs(_TMP, exist_ok=True)
os.environ.setdefault("MINIWMS_DB", os.path.join(_TMP, "test.db"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import constants as C  # noqa: E402
import main  # noqa: E402
import product_type_service as PTS  # noqa: E402
import scope as S  # noqa: E402
from flask import g  # noqa: E402

DB = os.environ["MINIWMS_DB"]


def _conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


class M32ProductTypeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        with closing(_conn()) as c:
            c.execute("PRAGMA foreign_keys=OFF")
            c.execute("DELETE FROM product_type WHERE code LIKE 'M32-%'")
            c.execute("DELETE FROM product WHERE sku LIKE 'M32-%'")
            c.execute("DELETE FROM owner WHERE code LIKE 'M32-%'")
            c.commit()
        self.owner = self._post("/api/owners", {
            "code": "M32-OW", "name": "M32 货主"})["owner"]["id"]
        self.other = self._post("/api/owners", {
            "code": "M32-OW9", "name": "M32 其他货主"})["owner"]["id"]

    # ---------- 辅助 ----------
    def _call(self, method, path, payload=None, status=200):
        fn = {"GET": self.client.get, "POST": self.client.post,
              "PUT": self.client.put, "DELETE": self.client.delete}[method]
        kw = {"json": payload} if payload is not None else {}
        r = fn(path, **kw)
        self.assertEqual(r.status_code, status, (method, path, r.get_data(as_text=True)[:300]))
        return r.get_json() or {}

    def _post(self, path, payload, status=201):
        return self._call("POST", path, payload, status)

    def _get(self, path, status=200):
        return self._call("GET", path, None, status)

    def _put(self, path, payload, status=200):
        return self._call("PUT", path, payload, status)

    def _delete(self, path, status=200):
        return self._call("DELETE", path, None, status)

    def _type(self, code="M32-T1", **kw):
        body = {"code": code, "name": "类型 " + code}
        body.update(kw)
        return self._post("/api/product-types", body)["product_type"]

    def _product(self, sku="M32-P1", **kw):
        body = {"sku": sku, "name": "货品 " + sku}
        body.update(kw)
        return self._post("/api/products", body)["product"]

    def _snapshot(self):
        with closing(_conn()) as c:
            return tuple(c.execute(
                "SELECT (SELECT COUNT(*) FROM stock), (SELECT COUNT(*) FROM stock_movement),"
                " (SELECT COUNT(*) FROM stock_adjustment), (SELECT COUNT(*) FROM stock_batch)"
            ).fetchone())

    # ---------- 用例 ----------
    def test_01_dict_endpoint(self):
        d = self._get("/api/product-types/dict")
        self.assertEqual(len(d["statuses"]), len(C.PT_STATUSES))
        self.assertEqual(d["max_depth"], 2)
        self.assertEqual(len(d["seed_types"]), len(C.PT_SEED_TYPES))

    def test_02_seed_types_present(self):
        d = self._get("/api/product-types?page_size=100")
        codes = {t["code"] for t in d["product_types"]}
        for code, _n, _s in C.PT_SEED_TYPES:
            self.assertIn(code, codes)

    def test_03_create_and_get(self):
        t = self._type("M32-T1", name="成品类")
        self.assertEqual(t["code"], "M32-T1")
        self.assertEqual(t["name"], "成品类")
        self.assertEqual(t["status"], C.PT_STATUS_ACTIVE)
        self.assertEqual(t["ref_count"], 0)
        got = self._get("/api/product-types/" + str(t["id"]))["product_type"]
        self.assertEqual(got["id"], t["id"])

    def test_04_duplicate_code_rejected(self):
        self._type("M32-DUP")
        bad = self._post("/api/product-types", {"code": "M32-DUP", "name": "重复"},
                         status=400)
        self.assertEqual(bad["code"], "PT_DUPLICATE_CODE")

    def test_05_required_fields(self):
        bad = self._post("/api/product-types", {"name": "没有编码"}, status=400)
        self.assertIn("code", bad["error"])
        bad2 = self._post("/api/product-types", {"code": "M32-NO"}, status=400)
        self.assertIn("name", bad2["error"])

    def test_06_update_partial_and_rename(self):
        t = self._type("M32-U1")
        r = self._put("/api/product-types/" + str(t["id"]), {"name": "改名了"})
        self.assertEqual(r["product_type"]["name"], "改名了")
        self.assertEqual(r["product_type"]["code"], "M32-U1")
        r2 = self._put("/api/product-types/" + str(t["id"]),
                       {"code": "M32-U1B", "sort_no": 5})
        self.assertEqual(r2["product_type"]["code"], "M32-U1B")
        self.assertEqual(r2["product_type"]["sort_no"], 5)

    def test_07_hierarchy_two_levels(self):
        p = self._type("M32-P")
        c = self._type("M32-C", parent_id=p["id"])
        self.assertEqual(c["parent_id"], p["id"])
        # 第三级被拒
        bad = self._post("/api/product-types",
                         {"code": "M32-G", "name": "第三级", "parent_id": c["id"]},
                         status=400)
        self.assertEqual(bad["code"], "PT_MAX_DEPTH")

    def test_08_hierarchy_self_and_missing_parent(self):
        t = self._type("M32-S")
        bad = self._put("/api/product-types/" + str(t["id"]),
                        {"parent_id": t["id"]}, status=400)
        self.assertEqual(bad["code"], "PT_HIERARCHY_CYCLE")
        bad2 = self._post("/api/product-types",
                          {"code": "M32-MP", "name": "x", "parent_id": 999999},
                          status=400)
        self.assertIn("parent_id", bad2["error"])

    def test_09_delete_blocked_when_referenced(self):
        t = self._type("M32-R1")
        self._product("M32-PR1", product_type=t["code"])
        bad = self._delete("/api/product-types/" + str(t["id"]), status=400)
        self.assertEqual(bad["code"], C.PT_DELETE_BLOCKED_CODE)

    def test_10_rename_blocked_when_referenced(self):
        t = self._type("M32-R2")
        self._product("M32-PR2", product_type=t["code"])
        bad = self._put("/api/product-types/" + str(t["id"]),
                        {"code": "M32-R2B"}, status=400)
        self.assertEqual(bad["code"], "PT_IN_USE")

    def test_11_delete_with_children_blocked(self):
        p = self._type("M32-DP")
        self._type("M32-DC", parent_id=p["id"])
        bad = self._delete("/api/product-types/" + str(p["id"]), status=400)
        self.assertEqual(bad["code"], "PT_HAS_CHILDREN")

    def test_12_delete_ok_when_unused(self):
        t = self._type("M32-D3")
        self.assertTrue(self._delete("/api/product-types/" + str(t["id"]))["ok"])
        gone = self._get("/api/product-types/" + str(t["id"]), status=400)
        self.assertEqual(gone["code"], "PT_NOT_FOUND")

    def test_13_product_references_dict_code(self):
        t = self._type("M32-REF", name="引用类型")
        p = self._product("M32-PREF", product_type=t["code"])
        self.assertEqual(p["product_type"], "M32-REF")
        self.assertEqual(p["product_type_name"], "引用类型")
        self.assertTrue(p["product_type_known"])
        # 引用数回读
        got = self._get("/api/product-types/" + str(t["id"]))["product_type"]
        self.assertEqual(got["ref_count"], 1)

    def test_14_product_unknown_type_auto_created(self):
        p = self._product("M32-PAUTO", product_type="M32-自动类型")
        self.assertEqual(p["product_type"], "M32-自动类型")
        self.assertEqual(p["product_type_name"], "M32-自动类型")
        self.assertTrue(p["product_type_known"])
        d = self._get("/api/product-types?keyword=M32-自动类型")
        self.assertEqual(d["total"], 1)

    def test_15_product_update_type_switch(self):
        a = self._type("M32-A")
        b = self._type("M32-B")
        p = self._product("M32-SW", product_type=a["code"])
        r = self._put("/api/products/" + str(p["id"]), {"product_type": b["code"]})
        self.assertEqual(r["product"]["product_type"], "M32-B")
        self.assertEqual(r["product"]["product_type_name"], "类型 M32-B")

    def test_16_backfill_keeps_legacy_value(self):
        """老数据回填：老文本原样当 code，货品取值一个字符都不改。"""
        with closing(_conn()) as c:
            c.execute("DELETE FROM product_type WHERE code = '老文本类型'")
            c.execute("INSERT INTO product (sku, name, product_type)"
                      " VALUES ('M32-LEG', '老货品', '老文本类型')")
            c.commit()
        with closing(_conn()) as c:
            made = PTS.backfill_from_products(c)
            c.commit()
        with closing(_conn()) as c:
            row = c.execute("SELECT product_type FROM product WHERE sku='M32-LEG'").fetchone()
            self.assertEqual(row["product_type"], "老文本类型")
            hit = c.execute("SELECT name FROM product_type WHERE code='老文本类型'").fetchone()
            self.assertIsNotNone(hit)
            self.assertEqual(hit["name"], "老文本类型")
        self.assertEqual(made, 1)

    def test_17_resolve_name_fallback(self):
        name, known = PTS.resolve_name(_conn(), "M32-不存在的类型")
        self.assertEqual(name, "M32-不存在的类型")
        self.assertFalse(known)
        name2, known2 = PTS.resolve_name(_conn(), "")
        self.assertEqual(name2, "")
        self.assertFalse(known2)

    def test_18_list_filters_and_pagination(self):
        self._type("M32-L1", name="列表甲")
        self._type("M32-L2", name="列表乙")
        d = self._get("/api/product-types?keyword=M32-L1")
        self.assertEqual(d["total"], 1)
        p = self._get("/api/product-types?page=1&page_size=1")
        self.assertEqual(len(p["product_types"]), 1)
        self.assertGreaterEqual(p["total"], 2)
        bad = self._get("/api/product-types?page=abc", status=400)
        self.assertEqual(bad["code"], "PT_BAD_PARAM")

    def test_19_owner_isolation_shared_visible(self):
        a = self._type("M32-OA", owner_id=self.owner)
        b = self._type("M32-OB", owner_id=self.other)
        c = self._type("M32-OG")  # 全局
        with main.app.test_request_context():
            g.scope = {"owner_all": False, "owner_ids": [self.owner]}
            d = PTS.list_records(main.get_db(), {})
            codes = {t["code"] for t in d["product_types"]}
        self.assertIn("M32-OA", codes)     # 本货主可见
        self.assertIn("M32-OG", codes)     # 全局可见
        self.assertNotIn("M32-OB", codes)  # 他货主不可见
        self.assertTrue(a and b and c)

    def test_20_scope_missing_denied(self):
        with main.app.test_request_context():
            g.scope = None
            with self.assertRaises(S.ScopeError):
                PTS.list_records(main.get_db(), {})

    def test_21_restricted_write_denied(self):
        with main.app.test_request_context():
            g.scope = {"owner_all": False, "owner_ids": [self.owner]}
            with self.assertRaises(S.ScopeError):
                PTS.create(main.get_db(), {
                    "code": "M32-X", "name": "越权", "owner_id": self.other})

    def test_22_refs_endpoint(self):
        t = self._type("M32-RF")
        self._product("M32-PRF", product_type=t["code"])
        refs = self._get("/api/product-types/refs")["refs"]
        self.assertEqual(refs.get("M32-RF"), 1)

    def test_22b_restricted_detail_update_delete_denied(self):
        """受限账号不能读/改/删别人货主的类型（按 ID 访问也要守门）。"""
        t = self._type("M32-RESTRICT", owner_id=self.other)
        with main.app.test_request_context():
            g.scope = {"owner_all": False, "owner_ids": [self.owner]}
            with self.assertRaises(S.ScopeError):
                PTS.get(main.get_db(), t["id"])
            with self.assertRaises(S.ScopeError):
                PTS.update(main.get_db(), t["id"], {"name": "越权改"})
            with self.assertRaises(S.ScopeError):
                PTS.delete(main.get_db(), t["id"])

    def test_22c_restricted_can_touch_global_and_own(self):
        """受限账号可读/改自己的类型与全局类型。"""
        own = self._type("M32-MINE", owner_id=self.owner)
        glob = self._type("M32-GLOB2")
        with main.app.test_request_context():
            g.scope = {"owner_all": False, "owner_ids": [self.owner]}
            self.assertEqual(PTS.get(main.get_db(), own["id"])["code"], "M32-MINE")
            self.assertEqual(PTS.get(main.get_db(), glob["id"])["code"], "M32-GLOB2")
            PTS.update(main.get_db(), own["id"], {"name": "本货主可改"})

    def test_23_no_stock_side_effect(self):
        before = self._snapshot()
        t = self._type("M32-SS")
        self._product("M32-PSS", product_type=t["code"])
        self._put("/api/product-types/" + str(t["id"]), {"name": "改名"})
        after = self._snapshot()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()


class M32RuleEngineLinkTest(unittest.TestCase):
    """M32 产品类型接入上架规则引擎：条件组新增第 14 维 ``product_type``。

    验证三件事：
    1. 条件组维度白名单已含 ``product_type``（不再被拒为「未知维度」）；
    2. 推荐上下文带出货品的产品类型编码，规则行可按它命中；
    3. **既有规则行不受影响** —— 条件组里没有该键即视为通配（不限）。
    """

    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        with closing(_conn()) as c:
            c.execute("PRAGMA foreign_keys=OFF")
            c.execute("DELETE FROM putaway_rule WHERE name LIKE 'M32-链接%'")
            c.execute("DELETE FROM product WHERE sku LIKE 'M32-LNK%'")
            c.execute("DELETE FROM asn_line WHERE id IN"
                      " (SELECT id FROM asn WHERE code LIKE 'M32-LNK%')")
            c.execute("DELETE FROM asn WHERE code LIKE 'M32-LNK%'")
            c.execute("DELETE FROM owner WHERE code = 'M32-LNK-OW'")
            c.commit()
        self.owner = self.client.post("/api/owners", json={
            "code": "M32-LNK-OW", "name": "M32 联动货主"}).get_json()["owner"]["id"]

    def _post(self, path, payload, status=201):
        r = self.client.post(path, json=payload)
        self.assertEqual(r.status_code, status, (path, r.get_data(as_text=True)[:200]))
        return r.get_json() or {}

    def _rule_set(self):
        sets = self.client.get("/api/putaway-rule-sets").get_json()["rule_sets"]
        return sets[0]["id"]

    def test_01_product_type_is_known_dim(self):
        self.assertIn("product_type", C.PUTAWAY_CONDITION_DIMS)
        self.assertEqual(C.PUTAWAY_CONDITION_LABELS["product_type"], "产品类型")
        r = self.client.post("/api/putaway-rules", json={
            "rule_set_id": self._rule_set(), "name": "M32-链接-维度",
            "condition": {"product_type": "PT-FG"},
            "steps": [{"type": "all_active"}], "enabled": 0})
        self.assertEqual(r.status_code, 201, r.get_json())

    def test_02_condition_dims_count_is_14(self):
        d = self.client.get("/api/putaway-rule-types").get_json()
        dims = {x["value"] for x in d["condition_dims"]}
        self.assertIn("product_type", dims)
        self.assertEqual(len(d["condition_dims"]), 14)

    def test_03_trial_matches_by_product_type(self):
        r = self.client.post("/api/putaway-rules/trial", json={
            "condition": {"product_type": "PT-FG"},
            "ctx": {"product_type": "PT-FG"}})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertTrue(r.get_json()["matched"])
        r2 = self.client.post("/api/putaway-rules/trial", json={
            "condition": {"product_type": "PT-FG"},
            "ctx": {"product_type": "PT-RM"}})
        self.assertFalse(r2.get_json()["matched"])
        # 没有该键 → 通配
        r3 = self.client.post("/api/putaway-rules/trial", json={
            "condition": {}, "ctx": {"product_type": "PT-FG"}})
        self.assertTrue(r3.get_json()["matched"])

    def test_04_existing_rules_unaffected(self):
        """既有规则行（条件组里没有 product_type 键）仍按通配命中。"""
        r = self.client.post("/api/putaway-rules/trial", json={
            "condition": {"doc_type": "采购"},
            "ctx": {"doc_type": "采购", "product_type": "PT-FG"}})
        self.assertTrue(r.get_json()["matched"])

    def test_05_context_carries_product_type(self):
        """推荐上下文取货品的产品类型编码（供规则行按类型分流）。"""
        import putaway_rules as PR
        prod = self._post("/api/products", {
            "sku": "M32-LNK-P1", "name": "链接测试物料", "product_type": "PT-RM"})
        pid = prod["product"]["id"]
        with closing(_conn()) as c:
            c.execute("INSERT INTO asn (code, owner_id, doc_type, status)"
                      " VALUES ('M32-LNK-ASN', ?, '采购', 'open')", (self.owner,))
            asn_id = c.execute("SELECT id FROM asn WHERE code='M32-LNK-ASN'").fetchone()[0]
            c.execute("INSERT INTO asn_line (asn_id, product_id, expected_qty)"
                      " VALUES (?, ?, 10)", (asn_id, pid))
            line_id = c.execute(
                "SELECT id FROM asn_line WHERE asn_id = ?", (asn_id,)).fetchone()[0]
            c.commit()
        with main.app.app_context():
            ctx = PR.build_context(main.get_db(), asn_id, line_id, 10)
        self.assertEqual(ctx["ctx"]["product_type"], "PT-RM")


if __name__ == "__main__":
    unittest.main()
