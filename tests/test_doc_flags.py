"""M20 剩余：ASN/DN 单据标识位与 external_doc_type 测试。

按 docs/PUTAWAY_RECOMMENDATION.md v3.3 口径：本里程碑**只落字段**，
混放 / 冷链 / 危险是否拦截由 M25/M26 的上架规则配置决定，
因此 `test_flags_do_not_block_putaway` 明确验证过渡期 `putaway()` 不做任何校验。
"""
import os
import sqlite3
import sys
import tempfile
import unittest

_TMP = os.path.join(tempfile.gettempdir(), "miniwms_unittest")
os.makedirs(_TMP, exist_ok=True)
os.environ.setdefault("MINIWMS_DB", os.path.join(_TMP, "test.db"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import constants as C  # noqa: E402
import main  # noqa: E402

ALL_ON = {k: 1 for k in C.DOC_FLAG_FIELDS}
ALL_ON["external_doc_type"] = "OMS-RETURN"
ALL_OFF = {k: 0 for k in C.DOC_FLAG_FIELDS}


class ParseDocFlagsTest(unittest.TestCase):
    """纯函数层：取值兼容性与字段格式校验。"""

    def test_defaults_are_zero_and_empty(self):
        flags, ext, errs = C.parse_doc_flags({})
        self.assertEqual(errs, [])
        self.assertEqual(flags, ALL_OFF)
        self.assertEqual(ext, "")
        self.assertEqual(len(C.DOC_FLAG_FIELDS), 4)

    def test_accepts_int_bool_and_string(self):
        flags, ext, errs = C.parse_doc_flags({
            "is_virtual": 1, "is_direct": True,
            "is_cold_chain": "1", "is_hazard": 0,
            "external_doc_type": "  ERP-PO  ",
        })
        self.assertEqual(errs, [])
        self.assertEqual(flags["is_virtual"], 1)
        self.assertEqual(flags["is_direct"], 1)
        self.assertEqual(flags["is_cold_chain"], 1)
        self.assertEqual(flags["is_hazard"], 0)
        self.assertEqual(ext, "ERP-PO")

    def test_accepts_false_zero_and_empty_string(self):
        flags, _, errs = C.parse_doc_flags({
            "is_virtual": False, "is_direct": 0,
            "is_cold_chain": "0", "is_hazard": "",
        })
        self.assertEqual(errs, [])
        self.assertTrue(all(v == 0 for v in flags.values()))

    def test_rejects_illegal_flag_and_overlong_external_type(self):
        _, _, errs = C.parse_doc_flags({
            "is_virtual": 2, "is_hazard": "yes",
            "external_doc_type": "X" * (C.EXTERNAL_DOC_TYPE_MAXLEN + 1),
        })
        self.assertIn("is_virtual 只能是 0/1", errs)
        self.assertIn("is_hazard 只能是 0/1", errs)
        self.assertTrue(any("external_doc_type 最长" in e for e in errs))

    def test_external_type_at_limit_is_accepted(self):
        _, ext, errs = C.parse_doc_flags({
            "external_doc_type": "Y" * C.EXTERNAL_DOC_TYPE_MAXLEN,
        })
        self.assertEqual(errs, [])
        self.assertEqual(len(ext), C.EXTERNAL_DOC_TYPE_MAXLEN)


class DocFlagApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        try:
            for t in ("product_location", "dn_pick", "dn_line", "dn",
                      "asn_line", "asn", "customer", "supplier",
                      "stock_movement", "stock", "warehouse", "product",
                      "owner", "location"):
                conn.execute(f"DELETE FROM {t}")
            conn.commit()
        finally:
            conn.close()
        self.owner_id = self.client.post(
            "/api/owners", json={"code": "OW-FLAG", "name": "标识测试货主"}
        ).get_json()["owner"]["id"]
        self.product_id = self.client.post(
            "/api/products", json={"sku": "SKU-FLAG", "name": "标识测试物料",
                                   "owner_id": self.owner_id}
        ).get_json()["product"]["id"]
        self.customer_id = self.client.post(
            "/api/customers", json={"code": "C-FLAG", "name": "标识测试客户",
                                    "owner_id": self.owner_id}
        ).get_json()["customer"]["id"]

    # ---------- 夹具 ----------
    def _asn(self, **extra):
        payload = {"owner_id": self.owner_id,
                   "lines": [{"product_id": self.product_id, "expected_qty": 3}]}
        payload.update(extra)
        return self.client.post("/api/asns", json=payload)

    def _dn(self, **extra):
        payload = {"owner_id": self.owner_id, "customer_id": self.customer_id,
                   "lines": [{"product_id": self.product_id, "ordered_qty": 2}]}
        payload.update(extra)
        return self.client.post("/api/dns", json=payload)

    def _loc(self, code, loc_type):
        return self.client.post(
            "/api/locations",
            json={"code": code, "name": code, "loc_type": loc_type},
        ).get_json()["location"]["id"]

    def _assert_flags(self, doc, flags=None, ext=""):
        flags = ALL_OFF if flags is None else flags
        for key, val in flags.items():
            self.assertEqual(doc[key], val, key)
        self.assertEqual(doc["external_doc_type"], ext)

    # ---------- ASN ----------
    def test_asn_create_with_all_flags(self):
        resp = self._asn(**ALL_ON)
        self.assertEqual(resp.status_code, 201, resp.get_json())
        self._assert_flags(resp.get_json()["asn"],
                           {k: 1 for k in C.DOC_FLAG_FIELDS}, "OMS-RETURN")

    def test_asn_detail_and_list_expose_flags(self):
        body = self._asn(**ALL_ON).get_json()
        asn_id = body["asn"]["id"]
        detail = self.client.get(f"/api/asns/{asn_id}").get_json()["asn"]
        self._assert_flags(detail, {k: 1 for k in C.DOC_FLAG_FIELDS}, "OMS-RETURN")
        listed = self.client.get("/api/asns").get_json()["asns"]
        row = next(x for x in listed if x["id"] == asn_id)
        self._assert_flags(row, {k: 1 for k in C.DOC_FLAG_FIELDS}, "OMS-RETURN")

    def test_asn_defaults_when_omitted(self):
        resp = self._asn()
        self.assertEqual(resp.status_code, 201, resp.get_json())
        self._assert_flags(resp.get_json()["asn"])

    def test_asn_rejects_illegal_flag(self):
        resp = self._asn(is_virtual=2)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("is_virtual 只能是 0/1", resp.get_json()["errors"])
        self.assertEqual(self.client.get("/api/asns").get_json()["total"], 0)

    def test_asn_rejects_overlong_external_type(self):
        resp = self._asn(external_doc_type="Z" * (C.EXTERNAL_DOC_TYPE_MAXLEN + 1))
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(any("external_doc_type 最长" in e
                            for e in resp.get_json()["errors"]))

    def test_asn_accepts_string_flags_from_html_select(self):
        """前端 modal 的 select 提交的是字符串 "0"/"1"，后端必须兼容。"""
        resp = self._asn(is_virtual="1", is_cold_chain="0", external_doc_type="WEB")
        self.assertEqual(resp.status_code, 201, resp.get_json())
        asn = resp.get_json()["asn"]
        self.assertEqual(asn["is_virtual"], 1)
        self.assertEqual(asn["is_cold_chain"], 0)
        self.assertEqual(asn["external_doc_type"], "WEB")

    # ---------- DN ----------
    def test_dn_create_with_all_flags(self):
        resp = self._dn(**ALL_ON)
        self.assertEqual(resp.status_code, 201, resp.get_json())
        self._assert_flags(resp.get_json()["dn"],
                           {k: 1 for k in C.DOC_FLAG_FIELDS}, "OMS-RETURN")

    def test_dn_detail_and_list_expose_flags(self):
        body = self._dn(**ALL_ON).get_json()
        dn_id = body["dn"]["id"]
        detail = self.client.get(f"/api/dns/{dn_id}").get_json()["dn"]
        self._assert_flags(detail, {k: 1 for k in C.DOC_FLAG_FIELDS}, "OMS-RETURN")
        listed = self.client.get("/api/dns").get_json()["dns"]
        row = next(x for x in listed if x["id"] == dn_id)
        self._assert_flags(row, {k: 1 for k in C.DOC_FLAG_FIELDS}, "OMS-RETURN")

    def test_dn_defaults_when_omitted(self):
        resp = self._dn()
        self.assertEqual(resp.status_code, 201, resp.get_json())
        self._assert_flags(resp.get_json()["dn"])

    def test_dn_rejects_illegal_flag(self):
        resp = self._dn(is_hazard="yes")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("is_hazard 只能是 0/1", resp.get_json()["errors"])

    # ---------- 2B：过渡期不拦截 ----------
    def test_flags_do_not_block_putaway(self):
        """v3.3 2B：冷链/危险标识只记录，putaway 到常温 storage 位不被拦截。"""
        recv = self._loc("FLAG-RCV", "receiving")
        store = self._loc("FLAG-ST", "storage")
        body = self._asn(is_cold_chain=1, is_hazard=1,
                         external_doc_type="COLD-HAZ").get_json()
        asn_id, line_id = body["asn"]["id"], body["lines"][0]["id"]

        got = self.client.post(f"/api/asns/{asn_id}/receive", json={
            "line_id": line_id, "location_id": recv, "qty": 3})
        self.assertEqual(got.status_code, 200, got.get_json())

        up = self.client.post(f"/api/asns/{asn_id}/putaway", json={
            "line_id": line_id, "from_location_id": recv,
            "to_location_id": store, "qty": 3})
        self.assertEqual(up.status_code, 200, up.get_json())
        self.assertEqual(up.get_json()["lines"][0]["putaway_qty"], 3)

        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        try:
            qty = conn.execute(
                "SELECT qty FROM stock WHERE owner_id=? AND product_id=? AND location_id=?",
                (self.owner_id, self.product_id, store),
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(qty)
        self.assertEqual(qty[0], 3)


class LegacyMigrationTest(unittest.TestCase):
    """旧库 ALTER 迁移：老单据自动得到 0 / 空串默认值，且重复执行幂等。"""

    def test_migrate_adds_columns_and_defaults(self):
        path = os.path.join(_TMP, "legacy_doc_flags.db")
        if os.path.exists(path):
            os.remove(path)
        conn = sqlite3.connect(path)
        try:
            conn.executescript("""
                CREATE TABLE zone (id INTEGER PRIMARY KEY);
                CREATE TABLE warehouse (id INTEGER PRIMARY KEY);
                CREATE TABLE location (id INTEGER PRIMARY KEY);
                CREATE TABLE product (id INTEGER PRIMARY KEY);
                CREATE TABLE asn (id INTEGER PRIMARY KEY, code TEXT);
                CREATE TABLE dn (id INTEGER PRIMARY KEY, code TEXT);
            """)
            conn.execute("INSERT INTO asn (code) VALUES ('OLD-ASN')")
            conn.execute("INSERT INTO dn (code) VALUES ('OLD-DN')")
            main._migrate_m20_columns(conn)
            main._migrate_m20_columns(conn)  # 幂等
            conn.commit()
            for table, code in (("asn", "OLD-ASN"), ("dn", "OLD-DN")):
                cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
                for key in C.DOC_FLAG_FIELDS + ("external_doc_type",):
                    self.assertIn(key, cols, f"{table}.{key}")
                row = conn.execute(
                    f"SELECT is_virtual, is_direct, is_cold_chain, is_hazard, "
                    f"external_doc_type FROM {table} WHERE code=?", (code,)
                ).fetchone()
                self.assertEqual(row, (0, 0, 0, 0, ""))
        finally:
            conn.close()
        os.remove(path)


if __name__ == "__main__":
    unittest.main()
