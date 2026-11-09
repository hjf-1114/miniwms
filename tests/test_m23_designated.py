"""M23 指定客户专属库存 + 销退口径测试。

用户 2026-11-09 确认口径：
1. 质量状态按 B：``goods_property``（合格/不合格）与 ``stock_status``
   （非限制/质检/冻结）两个独立字段，不改枚举，状态变更留 M24。
2. 出库校验「只落规则」：``reservation_rules`` 是纯函数，不接 ``routes_dn.py``。
3. 销退外部批次号必填，缺则 400；确实没有须显式填 ``/``（合法占位 + 告警）。

验收标准：专属与通用库存不合并；验收时可填/可留空；I1 / I2 / I3 仍全绿。
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

import main  # noqa: E402
import constants as C  # noqa: E402
import reservation_rules as RR  # noqa: E402
from batch_service import validate_external_batch_no  # noqa: E402

_CLEAN_ORDER = (
    "batch_location", "stock_batch", "stock_movement", "stock",
    "stock_adjustment_line", "stock_adjustment",
    "dn_pick", "dn_line", "dn", "stocktake_line", "stocktake", "stocktake_plan",
    "transfer_line", "transfer", "asn_line", "asn",
    "batch_receipt", "batch", "user_owner_grant", "user_warehouse_grant",
    "owner_warehouse", "customer", "supplier", "product", "owner",
    "location", "warehouse",
)


class M23DesignatedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()
        cls.db_path = os.environ["MINIWMS_DB"]

    def _db(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _clean(self):
        conn = self._db()
        try:
            for t in _CLEAN_ORDER:
                conn.execute(f"DELETE FROM {t}")
            conn.commit()
        finally:
            conn.close()

    def setUp(self):
        self._clean()
        os.environ["MINIWMS_ALLOW_ANONYMOUS"] = "1"
        self.oid = self._owner("M23-OW")
        self.oid2 = self._owner("M23-OW2")
        self.pid = self._product("M23-P1", self.oid)
        self.wh = self._warehouse("M23-WH")
        self.rcv = self._location("M23-RCV", "receiving")
        self.sto = self._location("M23-STO", "storage")
        self.cus = self._customer("M23-CUS", self.oid)
        self.cus2 = self._customer("M23-CUS2", self.oid)
        self.foreign_cus = self._customer("M23-CUS-F", self.oid2)

    def tearDown(self):
        self._clean()
        os.environ["MINIWMS_ALLOW_ANONYMOUS"] = "1"

    # ── 夹具 ──
    def _owner(self, code):
        r = self.client.post("/api/owners", json={"code": code, "name": code})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        return r.get_json()["owner"]["id"]

    def _product(self, sku, oid):
        r = self.client.post("/api/products",
                             json={"sku": sku, "name": sku, "owner_id": oid})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        return r.get_json()["product"]["id"]

    def _location(self, code, loc_type):
        r = self.client.post("/api/locations", json={
            "code": code, "name": code, "loc_type": loc_type})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        return r.get_json()["location"]["id"]

    def _warehouse(self, code):
        r = self.client.post("/api/warehouses", json={"code": code, "name": code})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        return r.get_json()["warehouse"]["id"]

    def _customer(self, code, oid):
        r = self.client.post("/api/customers", json={
            "code": code, "name": code, "owner_id": oid})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        return r.get_json()["customer"]["id"]

    def _asn(self, qty=10, code="M23-ASN", doc_type="采购", **extra):
        body = {
            "code": code, "owner_id": self.oid, "source_warehouse": self.wh,
            "doc_type": doc_type,
            "lines": [{"product_id": self.pid, "expected_qty": qty, **extra}],
        }
        return self.client.post("/api/asns", json=body)

    def _asn_ok(self, **kw):
        r = self._asn(**kw)
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        b = r.get_json()
        return b["asn"]["id"], b["lines"][0]["id"]

    def _receive(self, asn_id, line_id, qty=10, **extra):
        return self.client.post(f"/api/asns/{asn_id}/receive", json={
            "line_id": line_id, "location_id": self.rcv, "qty": qty, **extra})

    def _receive_ok(self, asn_id, line_id, qty=10, **extra):
        r = self._receive(asn_id, line_id, qty, **extra)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()

    def _putaway(self, asn_id, line_id, qty=10):
        r = self.client.post(f"/api/asns/{asn_id}/putaway", json={
            "line_id": line_id, "from_location_id": self.rcv,
            "to_location_id": self.sto, "qty": qty})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()

    def _rows(self, sql, *args):
        conn = self._db()
        try:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()

    def _all_ok(self):
        i1 = self.client.get("/api/stock/reconcile").get_json()
        self.assertTrue(i1["consistent"], "I1 主账=流水不成立")
        both = self.client.get("/api/stock/batch-reconcile").get_json()
        self.assertTrue(both["consistent"], both["mismatches"])
        return both

    # ── A. DDL / 迁移（2）──
    def test_01_m23_columns_exist(self):
        conn = self._db()
        try:
            asn_cols = {r[1] for r in conn.execute("PRAGMA table_info(asn)")}
            rc_cols = {r[1] for r in conn.execute("PRAGMA table_info(batch_receipt)")}
        finally:
            conn.close()
        self.assertIn("external_batch_no", asn_cols)
        self.assertTrue({"designated_customer_id", "external_batch_no", "warning"}
                        <= rc_cols)

    def test_02_init_db_idempotent(self):
        main.init_db()
        main.init_db()
        conn = self._db()
        try:
            n = len([r for r in conn.execute("PRAGMA table_info(batch_receipt)")
                     if r[1] == "warning"])
        finally:
            conn.close()
        self.assertEqual(n, 1, "重复迁移不得重复加列")

    # ── B. 销退外部批次号（6）──
    def test_03_sales_return_without_external_no_rejected_on_create(self):
        r = self._asn(doc_type="销退", code="M23-RET-NO")
        self.assertEqual(r.status_code, 400)
        self.assertIn("确实没有请填 /", r.get_json()["errors"][0])

    def test_04_sales_return_slash_allowed_traces_and_warns(self):
        aid, lid = self._asn_ok(doc_type="销退", code="M23-RET-SLASH",
                                external_batch_no=C.EXTERNAL_BATCH_NO_ABSENT)
        r = self._receive_ok(aid, lid, 4)
        self.assertEqual(r["warning"], C.WARN_MISSING_EXTERNAL_BATCH_NO)
        self.assertEqual(r["batch"]["warning"], C.WARN_MISSING_EXTERNAL_BATCH_NO)
        self.assertEqual(r["batch"]["external_batch_no"],
                         C.EXTERNAL_BATCH_NO_ABSENT)
        self.assertEqual(r["batch"]["internal_source"], C.INTERNAL_BATCH_FIRST_IN)
        rows = self._rows("SELECT external_batch_no, warning FROM batch_receipt")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["external_batch_no"], "/")
        self.assertEqual(rows[0]["warning"], C.WARN_MISSING_EXTERNAL_BATCH_NO)

    def test_05_sales_return_real_external_no_used(self):
        aid, lid = self._asn_ok(doc_type="销退", code="M23-RET-REAL",
                                external_batch_no="IB-EXT-7788")
        r = self._receive_ok(aid, lid, 4)
        self.assertEqual(r["batch"]["internal_batch_no"], "IB-EXT-7788")
        self.assertEqual(r["batch"]["internal_source"], C.INTERNAL_BATCH_EXTERNAL)
        self.assertEqual(r["warning"], "")
        self.assertEqual(r["batch"]["warning"], "")

    def test_06_non_return_doc_type_allows_empty_external_no(self):
        aid, lid = self._asn_ok(code="M23-PURCHASE")
        r = self._receive_ok(aid, lid, 4)
        self.assertEqual(r["warning"], "")
        self.assertEqual(r["batch"]["external_batch_no"], "")

    def test_07_receive_explicit_empty_external_no_rejected(self):
        """创建时行快照已有值，API 直调验收显式清空仍须 400（防御旁路）。"""
        aid, lid = self._asn_ok(doc_type="销退", code="M23-RET-CLEAR",
                                external_batch_no="IB-EXT-1")
        r = self._receive(aid, lid, 4, external_batch_no="")
        self.assertEqual(r.status_code, 400)
        self.assertIn("销退单必须填写", r.get_json()["error"])

    def test_08_external_no_too_long_and_head_default(self):
        too_long = "X" * (C.EXTERNAL_BATCH_NO_MAXLEN + 1)
        r = self._asn(doc_type="销退", code="M23-RET-LONG",
                      external_batch_no=too_long)
        self.assertEqual(r.status_code, 400)
        # 单据头填一次作为全单默认值：明细行不填也能通过（好产品的批量录入体验）
        body = {
            "code": "M23-RET-HEAD", "owner_id": self.oid,
            "source_warehouse": self.wh, "doc_type": "销退",
            "external_batch_no": "IB-HEAD-9",
            "lines": [{"product_id": self.pid, "expected_qty": 5}],
        }
        cr = self.client.post("/api/asns", json=body)
        self.assertEqual(cr.status_code, 201, cr.get_data(as_text=True))
        b = cr.get_json()
        self.assertEqual(b["asn"]["external_batch_no"], "IB-HEAD-9")
        self.assertEqual(b["lines"][0]["external_batch_no"], "IB-HEAD-9")
        rv = self._receive_ok(b["asn"]["id"], b["lines"][0]["id"], 5)
        self.assertEqual(rv["batch"]["internal_batch_no"], "IB-HEAD-9")

    # ── C. 指定客户链路（6）──
    def test_09_designated_customer_written_to_all_three_layers(self):
        aid, lid = self._asn_ok(code="M23-C1",
                                designated_customer_id=self.cus)
        r = self._receive_ok(aid, lid, 6)
        self.assertEqual(r["batch"]["designated_customer_id"], self.cus)
        rc = self._rows("SELECT designated_customer_id FROM batch_receipt")
        sb = self._rows("SELECT designated_customer_id FROM stock_batch")
        mv = self._rows("SELECT designated_customer_id FROM stock_movement")
        self.assertEqual(rc[0]["designated_customer_id"], self.cus)
        self.assertEqual(sb[0]["designated_customer_id"], self.cus)
        self.assertEqual(mv[0]["designated_customer_id"], self.cus)
        self._all_ok()

    def test_10_blank_designated_is_common_stock(self):
        aid, lid = self._asn_ok(code="M23-COMMON")
        r = self._receive_ok(aid, lid, 6)
        self.assertIsNone(r["batch"]["designated_customer_id"])
        sb = self._rows("SELECT designated_customer_id FROM stock_batch")
        self.assertIsNone(sb[0]["designated_customer_id"])
        self._all_ok()

    def test_11_foreign_owner_customer_rejected(self):
        r = self._asn(code="M23-FOREIGN",
                      designated_customer_id=self.foreign_cus)
        self.assertEqual(r.status_code, 400)
        self.assertIn("不属于该货主", r.get_json()["errors"][0])

    def test_12_exclusive_and_common_never_merged(self):
        a1, l1 = self._asn_ok(code="M23-EXCL",
                              designated_customer_id=self.cus)
        self._receive_ok(a1, l1, 4)
        a2, l2 = self._asn_ok(code="M23-GENERAL")
        self._receive_ok(a2, l2, 6)
        rows = self._rows(
            "SELECT designated_customer_id, qty FROM stock_batch "
            "WHERE location_id = ? ORDER BY qty", self.rcv)
        self.assertEqual(len(rows), 2, "专属与通用必须分成两条明细行")
        self.assertEqual({r["qty"] for r in rows}, {4.0, 6.0})
        self.assertEqual({r["designated_customer_id"] for r in rows},
                         {self.cus, None})
        self.assertEqual(sum(r["qty"] for r in rows), 10.0)
        # 「不合并」由唯一索引保证：指定客户是明细行维度之一。
        conn = self._db()
        try:
            idx = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' "
                "AND name='uq_stock_batch'").fetchone()[0]
        finally:
            conn.close()
        self.assertIn("designated_customer_id", idx)
        self._all_ok()

    def test_13_receive_value_overrides_creation_draft(self):
        """创建时填的只是草稿默认值，验收时可改，三层账只认验收值。"""
        aid, lid = self._asn_ok(code="M23-OVERRIDE",
                                designated_customer_id=self.cus)
        r = self._receive_ok(aid, lid, 4, designated_customer_id=self.cus2)
        self.assertEqual(r["batch"]["designated_customer_id"], self.cus2)
        sb = self._rows("SELECT designated_customer_id FROM stock_batch")
        self.assertEqual(sb[0]["designated_customer_id"], self.cus2)
        self._all_ok()

    def test_14_designated_and_stock_status_survive_putaway(self):
        aid, lid = self._asn_ok(code="M23-PUT",
                                designated_customer_id=self.cus)
        self._receive_ok(aid, lid, 5, stock_status="质检")
        self._putaway(aid, lid, 5)
        rows = self._rows(
            "SELECT designated_customer_id, stock_status, qty FROM stock_batch "
            "WHERE location_id = ?", self.sto)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["designated_customer_id"], self.cus)
        self.assertEqual(rows[0]["stock_status"], "质检")
        self.assertEqual(rows[0]["qty"], 5.0)
        self._all_ok()

    # ── D. 规则层与质量状态（4）──
    def test_15_reservation_rule_matrix(self):
        cases = RR.reservation_rule_matrix()
        self.assertEqual(len(cases), 4)
        for case in cases:
            out = RR.check_customer_reservation(case["stock"], case["dn"])
            self.assertEqual(out["allowed"], case["allowed"], case)
        self.assertEqual(
            RR.check_customer_reservation(1, 2)["code"],
            C.CUSTOMER_RESERVED_FORBIDDEN)
        self.assertEqual(
            RR.preferred_consumption_order(1)["order"], ("designated", "common"))
        self.assertFalse(RR.preferred_consumption_order(1)["implemented"],
                         "M23 只落规则，不实现分配")

    def test_16_reservation_rule_not_wired_to_dn(self):
        """边界固化：专属库存发给别的客户，本轮出库路由仍不拦截（留 M35）。"""
        aid, lid = self._asn_ok(code="M23-NO-GUARD",
                                designated_customer_id=self.cus)
        self._receive_ok(aid, lid, 4)
        self._putaway(aid, lid, 4)
        dn = self.client.post("/api/dns", json={
            "code": "M23-DN-OTHER", "owner_id": self.oid,
            "customer_id": self.cus2,
            "lines": [{"product_id": self.pid, "ordered_qty": 2}],
        })
        self.assertEqual(dn.status_code, 201, dn.get_data(as_text=True))
        did, dlid = dn.get_json()["dn"]["id"], dn.get_json()["lines"][0]["id"]
        pick = self.client.post(f"/api/dns/{did}/pick", json={
            "line_id": dlid, "location_id": self.sto, "qty": 2})
        self.assertEqual(pick.status_code, 200, pick.get_data(as_text=True))
        ship = self.client.post(f"/api/dns/{did}/ship", json={})
        self.assertEqual(ship.status_code, 200, ship.get_data(as_text=True))
        self._all_ok()

    def test_17_quality_transition_matrix_and_independent_enums(self):
        self.assertEqual(C.GOODS_PROPERTIES, ("合格", "不合格"),
                         "用户选 B：批次维度枚举不改")
        self.assertEqual(C.STOCK_STATUSES, ("非限制", "质检", "冻结"))
        self.assertTrue(RR.can_change_goods_property("合格", "不合格")["allowed"])
        bad = RR.can_change_goods_property("不合格", "合格")
        self.assertFalse(bad["allowed"])
        self.assertTrue(RR.can_change_goods_property(
            "不合格", "合格", special_approval=True)["allowed"])
        self.assertTrue(RR.stock_status_allowed(""))
        self.assertTrue(RR.stock_status_allowed("质检"))
        self.assertFalse(RR.stock_status_allowed("待检"))
        self.assertEqual(len(RR.quality_transition_matrix()), 4)

    def test_18_invalid_stock_status_rejected(self):
        aid, lid = self._asn_ok(code="M23-BAD-STATUS")
        r = self._receive(aid, lid, 3, stock_status="待检")
        self.assertEqual(r.status_code, 400)
        self.assertIn("stock_status", r.get_json()["error"])

    # ── E. 销退纯函数（1）──
    def test_19_validate_external_batch_no_matrix(self):
        self.assertEqual(validate_external_batch_no("采购", ""), ("", "", ""))
        self.assertEqual(validate_external_batch_no("采购", "/"), ("", "", ""))
        self.assertEqual(validate_external_batch_no("采购", "IB-1"),
                         ("IB-1", "", ""))
        self.assertTrue(validate_external_batch_no("销退", "")[2])
        self.assertEqual(validate_external_batch_no("销退", "/"),
                         ("/", C.WARN_MISSING_EXTERNAL_BATCH_NO, ""))
        self.assertEqual(validate_external_batch_no("销退", "IB-2"),
                         ("IB-2", "", ""))
        self.assertTrue(validate_external_batch_no(
            "销退", "X" * (C.EXTERNAL_BATCH_NO_MAXLEN + 1))[2])


if __name__ == "__main__":
    unittest.main()
