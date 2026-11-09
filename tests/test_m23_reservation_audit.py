"""M23 补充：专属库存风险放行（事前提示 + 日志，不拦截）测试。

用户 2026-11-09 二次确认：专属库存发给其他客户时**提示并让用户决定是否继续**，
可以选择继续、也可以不继续；继续则写入风险放行日志。系统不强制拒绝、不实现分配。

验收要点：
- 只读预检能准确报出将消耗的其他客户专属库存（与发货同一套 FEFO 分摊）
- 发货**不被拦截**，状态照常 → shipped，I1 / I2 / I3 仍全绿
- 每次放行逐条落 ``customer_reservation_log``，可查、可按单过滤、可区分是否界面确认
- 发货失败 / 重复发货 / 用户取消时，不产生脏日志
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
import reservation_audit as RA  # noqa: E402

_CLEAN_ORDER = (
    "customer_reservation_log", "batch_location", "stock_batch", "stock_movement", "stock",
    "stock_adjustment_line", "stock_adjustment",
    "dn_pick", "dn_line", "dn", "stocktake_line", "stocktake", "stocktake_plan",
    "transfer_line", "transfer", "asn_line", "asn",
    "batch_receipt", "batch", "user_owner_grant", "user_warehouse_grant",
    "owner_warehouse", "customer", "supplier", "product", "owner",
    "location", "warehouse",
)


class M23ReservationAuditTest(unittest.TestCase):
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
        self.oid = self._owner("R23-OW")
        self.pid = self._product("R23-P1", self.oid)
        self.wh = self._warehouse("R23-WH")
        self.rcv = self._location("R23-RCV", "receiving")
        self.sto = self._location("R23-STO", "storage")
        self.cus1 = self._customer("R23-CUS1", self.oid)
        self.cus2 = self._customer("R23-CUS2", self.oid)

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

    def _stock(self, qty=6, designated=None, code="R23-ASN"):
        """收货 + 上架一批（可指定专属客户）到 storage 位。"""
        line = {"product_id": self.pid, "expected_qty": qty}
        if designated:
            line["designated_customer_id"] = designated
        r = self.client.post("/api/asns", json={
            "code": code, "owner_id": self.oid, "source_warehouse": self.wh,
            "lines": [line],
        })
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        aid, lid = r.get_json()["asn"]["id"], r.get_json()["lines"][0]["id"]
        rv = self.client.post(f"/api/asns/{aid}/receive", json={
            "line_id": lid, "location_id": self.rcv, "qty": qty})
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        pu = self.client.post(f"/api/asns/{aid}/putaway", json={
            "line_id": lid, "from_location_id": self.rcv,
            "to_location_id": self.sto, "qty": qty})
        self.assertEqual(pu.status_code, 200, pu.get_data(as_text=True))
        return rv.get_json()["batch"]["batch_no"]

    def _dn(self, customer_id, qty=3, code="R23-DN"):
        r = self.client.post("/api/dns", json={
            "code": code, "owner_id": self.oid, "customer_id": customer_id,
            "lines": [{"product_id": self.pid, "ordered_qty": qty}],
        })
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        b = r.get_json()
        return b["dn"]["id"], b["lines"][0]["id"]

    def _pick(self, did, dlid, qty=3):
        r = self.client.post(f"/api/dns/{did}/pick", json={
            "line_id": dlid, "location_id": self.sto, "qty": qty})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()

    def _check(self, did):
        r = self.client.get(f"/api/dns/{did}/reservation-check")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()

    def _logs(self, **qs):
        q = "&".join(f"{k}={v}" for k, v in qs.items())
        r = self.client.get("/api/reservation-logs" + ("?" + q if q else ""))
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
        self.assertTrue(i1["consistent"], "I1 不成立")
        both = self.client.get("/api/stock/batch-reconcile").get_json()
        self.assertTrue(both["consistent"], both["mismatches"])

    # ── A. 表结构（1）──
    def test_01_log_table_and_indexes(self):
        conn = self._db()
        try:
            cols = {r[1] for r in conn.execute(
                "PRAGMA table_info(customer_reservation_log)")}
            idx = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name LIKE 'idx_resv_log%'")}
        finally:
            conn.close()
        self.assertTrue({"dn_id", "designated_customer_id", "batch_no",
                         "code", "action", "acknowledged", "operator"} <= cols)
        self.assertEqual(len(idx), 2)

    # ── B. 只读预检（4）──
    def test_02_common_stock_has_no_risk(self):
        self._stock()
        did, dlid = self._dn(self.cus2)
        self._pick(did, dlid)
        chk = self._check(did)
        self.assertEqual(chk["risk_count"], 0)
        self.assertEqual(chk["risks"], [])

    def test_03_same_customer_has_no_risk(self):
        self._stock(designated=self.cus1)
        did, dlid = self._dn(self.cus1)
        self._pick(did, dlid)
        self.assertEqual(self._check(did)["risk_count"], 0)

    def test_04_other_customer_reports_risk_with_full_context(self):
        bno = self._stock(designated=self.cus1)
        did, dlid = self._dn(self.cus2)
        self._pick(did, dlid)
        chk = self._check(did)
        self.assertEqual(chk["risk_count"], 1)
        risk = chk["risks"][0]
        self.assertEqual(risk["batch_no"], bno)
        self.assertEqual(risk["designated_customer_id"], self.cus1)
        self.assertEqual(risk["dn_customer_id"], self.cus2)
        self.assertEqual(risk["code"], C.CUSTOMER_RESERVED_FORBIDDEN)
        self.assertEqual(risk["qty"], 3.0)
        self.assertIn("不能发给客户", risk["reason"])
        self.assertEqual(self._logs()["total"], 0, "预检只读，不得写日志")

    def test_05_precheck_missing_dn_returns_404(self):
        r = self.client.get("/api/dns/999999/reservation-check")
        self.assertEqual(r.status_code, 404)

    # ── C. 放行与留痕（5）──
    def test_06_ship_not_blocked_and_logs_risk(self):
        self._stock(designated=self.cus1)
        did, dlid = self._dn(self.cus2)
        self._pick(did, dlid)
        r = self.client.post(f"/api/dns/{did}/ship",
                             json={"risk_acknowledged": True})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["status"], C.DN_SHIPPED)
        self.assertEqual(r.get_json()["risk_count"], 1)
        self.assertEqual(r.get_json()["risk_logged"], 1)
        logs = self._rows("SELECT * FROM customer_reservation_log")
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["action"], C.RESERVATION_RISK_ALLOWED)
        self.assertEqual(logs[0]["acknowledged"], 1)
        self.assertEqual(logs[0]["dn_customer_id"], self.cus2)
        self._all_ok()

    def test_07_api_direct_ship_marked_unacknowledged(self):
        """API 直调不带确认标记也放行，但审计动作标为未确认。"""
        self._stock(designated=self.cus1)
        did, dlid = self._dn(self.cus2)
        self._pick(did, dlid)
        r = self.client.post(f"/api/dns/{did}/ship", json={})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        logs = self._rows("SELECT action, acknowledged FROM customer_reservation_log")
        self.assertEqual(logs[0]["action"], C.RESERVATION_RISK_ALLOWED_UNACK)
        self.assertEqual(logs[0]["acknowledged"], 0)

    def test_08_no_risk_no_log(self):
        self._stock()
        did, dlid = self._dn(self.cus1)
        self._pick(did, dlid)
        r = self.client.post(f"/api/dns/{did}/ship", json={})
        self.assertEqual(r.get_json()["risk_logged"], 0)
        self.assertEqual(self._logs()["total"], 0)

    def test_09_user_declines_no_stock_change_and_no_log(self):
        """用户选择「不继续」＝不发 ship 请求：库存不动、日志为空。"""
        self._stock(designated=self.cus1)
        did, dlid = self._dn(self.cus2)
        self._pick(did, dlid)
        self.assertEqual(self._check(did)["risk_count"], 1)
        # 前端在此 return，后端不接收任何写请求
        stock = self._rows("SELECT qty FROM stock WHERE location_id = ?", self.sto)
        self.assertEqual(stock[0]["qty"], 6.0)
        self.assertEqual(self._logs()["total"], 0)
        self.assertEqual(self.client.get(f"/api/dns/{did}").get_json()["dn"]
                         ["status"], C.DN_PICKING)

    def test_10_repeat_ship_does_not_duplicate_log(self):
        self._stock(designated=self.cus1)
        did, dlid = self._dn(self.cus2)
        self._pick(did, dlid)
        self.client.post(f"/api/dns/{did}/ship", json={"risk_acknowledged": True})
        again = self.client.post(f"/api/dns/{did}/ship", json={})
        self.assertEqual(again.status_code, 409)
        self.assertEqual(self._logs()["total"], 1)

    # ── D. 日志查询与事务（3）──
    def test_11_logs_api_filter_and_join_fields(self):
        self._stock(designated=self.cus1)
        did, dlid = self._dn(self.cus2)
        self._pick(did, dlid)
        self.client.post(f"/api/dns/{did}/ship", json={"risk_acknowledged": True})
        data = self._logs(dn_id=did)
        self.assertEqual(data["total"], 1)
        row = data["logs"][0]
        self.assertEqual(row["designated_customer_code"], "R23-CUS1")
        self.assertEqual(row["dn_customer_code"], "R23-CUS2")
        self.assertEqual(row["location_code"], "R23-STO")
        self.assertEqual(row["sku"], "R23-P1")
        self.assertEqual(self._logs(dn_id=999999)["total"], 0)
        self.assertEqual(self._logs(keyword="NOT-EXIST")["total"], 0)
        bad = self.client.get("/api/reservation-logs?dn_id=abc")
        self.assertEqual(bad.status_code, 400)

    def test_12_ship_failure_rolls_back_log(self):
        """未拣满时发货被既有状态机拒绝；日志不应先落库造成脏记录。"""
        self._stock(designated=self.cus1)
        did, _ = self._dn(self.cus2, qty=3)
        r = self.client.post(f"/api/dns/{did}/ship", json={})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self._logs()["total"], 0)

    def test_13_inspect_dn_unknown_returns_none(self):
        conn = self._db()
        try:
            self.assertIsNone(RA.inspect_dn(conn, 999999))
        finally:
            conn.close()

    # ── E. 同一货位分次拣货：预检/日志与发货实扣严格一致（2）──
    def _split_pick(self, did, dlid, each=2):
        """同一行分两次拣同一货位 → 两条指向同一 (product, location) 的 dn_pick。

        ship() 是逐条扣减的；预检若不做顺序模拟，第二条仍看到未扣减的账，
        会把风险重复记到第一批（或漏报第二批）。
        """
        self._pick(did, dlid, each)
        self._pick(did, dlid, each)

    def test_14_multi_pick_does_not_duplicate_first_batch(self):
        b1 = self._stock(qty=2, designated=self.cus1, code="R23-A1")
        self._stock(qty=3, designated=self.cus2, code="R23-A2")
        did, dlid = self._dn(self.cus2, qty=4, code="R23-DN-A")
        self._split_pick(did, dlid)

        chk = self._check(did)
        self.assertEqual(chk["risk_count"], 1, "实扣只消耗第一批一次，不得重复报风险")
        self.assertEqual(chk["risks"][0]["batch_no"], b1)
        self.assertEqual(chk["risks"][0]["designated_customer_id"], self.cus1)

        r = self.client.post(f"/api/dns/{did}/ship", json={"risk_acknowledged": True})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["risk_count"], 1)
        self.assertEqual(r.get_json()["risk_logged"], 1)
        logs = self._logs(dn_id=did)["logs"]
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["batch_no"], b1)
        self._all_ok()

    def test_15_multi_pick_detects_risk_on_second_batch(self):
        """第一批与客户一致、第二批专属他人：预检不得漏报第二批。"""
        self._stock(qty=2, designated=self.cus2, code="R23-B1")
        b2 = self._stock(qty=3, designated=self.cus1, code="R23-B2")
        did, dlid = self._dn(self.cus2, qty=4, code="R23-DN-B")
        self._split_pick(did, dlid)

        chk = self._check(did)
        self.assertEqual(chk["risk_count"], 1, "第二条 dn_pick 实际消耗第二批，不得漏报")
        self.assertEqual(chk["risks"][0]["batch_no"], b2)
        self.assertEqual(chk["risks"][0]["designated_customer_id"], self.cus1)

        self.client.post(f"/api/dns/{did}/ship", json={"risk_acknowledged": True})
        logs = self._logs(dn_id=did)["logs"]
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["batch_no"], b2)
        self._all_ok()


if __name__ == "__main__":
    unittest.main()
