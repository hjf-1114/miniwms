"""库存账查询/流水查询/余额核对接口测试。"""
import os
import sqlite3
import sys
import tempfile
import unittest

_TMP = os.path.join(tempfile.gettempdir(), "miniwms_unittest")
os.makedirs(_TMP, exist_ok=True)
os.environ.setdefault("MINIWMS_DB", os.path.join(_TMP, "test.db"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402  (须在环境变量设置之后)


class StockApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()
        cls._asn_seq = 0
        cls._dn_seq = 0

    def setUp(self):
        self._asn_seq = 0
        self._dn_seq = 0
        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        try:
            # 先子后主
            conn.execute("DELETE FROM dn_pick")
            conn.execute("DELETE FROM dn_line")
            conn.execute("DELETE FROM dn")
            conn.execute("DELETE FROM customer")
            conn.execute("DELETE FROM batch_location")
            conn.execute("DELETE FROM stock_batch")
            conn.execute("DELETE FROM stock_movement")
            conn.execute("DELETE FROM stock")
            conn.execute("DELETE FROM asn_line")
            conn.execute("DELETE FROM asn")
            conn.execute("DELETE FROM product")
            conn.execute("DELETE FROM owner")
            conn.execute("DELETE FROM location")
            conn.commit()
        finally:
            conn.close()

    # ---------- 夹具 ----------
    def _mk_owner(self, code="OW1"):
        return self.client.post(
            "/api/owners", json={"code": code, "name": f"货主{code}"}
        ).get_json()["owner"]["id"]

    def _mk_product(self, owner_id, sku="P1"):
        return self.client.post(
            "/api/products", json={"sku": sku, "name": f"物料{sku}", "owner_id": owner_id}
        ).get_json()["product"]["id"]

    def _mk_location(self, code, loc_type):
        return self.client.post(
            "/api/locations",
            json={"code": code, "name": code, "loc_type": loc_type},
        ).get_json()["location"]["id"]

    def _inbound(self, owner_id, product_id, to_loc_id, qty):
        """走完整 ASN 链路入库：收货位收货 → 上架到 to_loc_id。"""
        self._asn_seq += 1
        recv_loc = self._mk_location(f"RCV-I{self._asn_seq}", "receiving")
        body = self.client.post("/api/asns", json={
            "code": f"ASN-I{self._asn_seq}", "owner_id": owner_id,
            "lines": [{"product_id": product_id, "expected_qty": qty}],
        }).get_json()
        asn_id, line_id = body["asn"]["id"], body["lines"][0]["id"]
        self.client.post(f"/api/asns/{asn_id}/receive", json={
            "line_id": line_id, "location_id": recv_loc, "qty": qty})
        self.client.post(f"/api/asns/{asn_id}/putaway", json={
            "line_id": line_id, "from_location_id": recv_loc,
            "to_location_id": to_loc_id, "qty": qty})

    def _mk_customer(self, owner_id, code="C1"):
        return self.client.post("/api/customers", json={
            "code": code, "name": code, "owner_id": owner_id,
        }).get_json()["customer"]["id"]

    def _ship_partial(self, owner_id, product_id, from_loc_id, qty):
        """出库 qty 件：DN 拣满并发货，库存减少。"""
        self._dn_seq += 1
        cid = self._mk_customer(owner_id, code=f"C-DN-S{self._dn_seq}")
        body = self.client.post("/api/dns", json={
            "code": f"DN-S{self._dn_seq}", "owner_id": owner_id,
            "customer_id": cid,
            "lines": [{"product_id": product_id, "ordered_qty": qty}],
        }).get_json()
        dn_id, line_id = body["dn"]["id"], body["lines"][0]["id"]
        self.client.post(f"/api/dns/{dn_id}/pick", json={
            "line_id": line_id, "location_id": from_loc_id, "qty": qty})
        self.client.post(f"/api/dns/{dn_id}/ship")

    def _direct_db(self):
        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        conn.row_factory = sqlite3.Row
        return conn

    # ---------- 库存余额查询 ----------
    def test_stock_list_after_inbound(self):
        """入库后暂存位零行保留 + 存储位余额，两行均带联表编码。"""
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        store = self._mk_location("ST-1", "storage")
        self._inbound(oid, pid, store, 10)
        data = self.client.get("/api/stock").get_json()
        self.assertEqual(data["total"], 2)  # 暂存位零行 + 存储位余额
        store_rows = [r for r in data["stocks"] if r["location_code"] == "ST-1"]
        self.assertEqual(len(store_rows), 1)
        row = store_rows[0]
        self.assertEqual(row["qty"], 10)
        self.assertEqual(row["allocated_qty"], 0)
        self.assertEqual(row["available_qty"], 10)
        self.assertEqual(row["owner_code"], "OW1")
        self.assertEqual(row["sku"], "P1")
        # 暂存位零行保留：qty=0 但行仍在（区分「用完」与「从未入库」）
        recv_rows = [r for r in data["stocks"] if r["location_code"] == "RCV-I1"]
        self.assertEqual(len(recv_rows), 1)
        self.assertEqual(recv_rows[0]["qty"], 0)

    def test_stock_has_stock_filter(self):
        """has_stock=1 过滤零行，只剩真实有货的行。"""
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        store = self._mk_location("ST-1", "storage")
        self._inbound(oid, pid, store, 10)
        data = self.client.get("/api/stock?has_stock=1").get_json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["stocks"][0]["location_code"], "ST-1")
        self.assertEqual(data["stocks"][0]["qty"], 10)

    def test_stock_available_reduced_by_allocation(self):
        """已分配减少可用量：可用 = 在手 - 已分配。"""
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        store = self._mk_location("ST-1", "storage")
        self._inbound(oid, pid, store, 10)
        self._dn_seq += 1
        cid = self._mk_customer(oid, code=f"C-DN-S{self._dn_seq}")
        body = self.client.post("/api/dns", json={
            "code": f"DN-S{self._dn_seq}", "owner_id": oid, "customer_id": cid,
            "lines": [{"product_id": pid, "ordered_qty": 4}],
        }).get_json()
        self.client.post(f"/api/dns/{body['dn']['id']}/pick", json={
            "line_id": body["lines"][0]["id"], "location_id": store, "qty": 4})
        row = self.client.get(f"/api/stock?location_id={store}").get_json()["stocks"][0]
        self.assertEqual(row["qty"], 10)
        self.assertEqual(row["allocated_qty"], 4)
        self.assertEqual(row["available_qty"], 6)

    def test_stock_filters(self):
        oid1 = self._mk_owner("OW-A")
        oid2 = self._mk_owner("OW-B")
        pid1 = self._mk_product(oid1, "P-A")
        pid2 = self._mk_product(oid2, "P-B")
        s1 = self._mk_location("ST-1", "storage")
        s2 = self._mk_location("ST-2", "storage")
        self._inbound(oid1, pid1, s1, 5)
        self._inbound(oid2, pid2, s2, 7)

        data = self.client.get(f"/api/stock?owner_id={oid1}&has_stock=1").get_json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["stocks"][0]["sku"], "P-A")

        data = self.client.get(f"/api/stock?product_id={pid2}&has_stock=1").get_json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["stocks"][0]["location_code"], "ST-2")

        data = self.client.get(f"/api/stock?location_id={s1}").get_json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["stocks"][0]["sku"], "P-A")

        resp = self.client.get("/api/stock?owner_id=abc")
        self.assertEqual(resp.status_code, 400)

    def test_stock_pagination(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        for i in range(1, 4):
            self._inbound(oid, pid, self._mk_location(f"ST-{i}", "storage"), 1)
        # 含 3 个暂存位零行共 6 行；has_stock=1 后为 3 行
        data = self.client.get("/api/stock?page=1&page_size=2").get_json()
        self.assertEqual(data["total"], 6)
        self.assertEqual(len(data["stocks"]), 2)
        data = self.client.get("/api/stock?has_stock=1&page=1&page_size=2").get_json()
        self.assertEqual(data["total"], 3)
        self.assertEqual(len(data["stocks"]), 2)
        data = self.client.get("/api/stock?has_stock=1&page=2&page_size=2").get_json()
        self.assertEqual(len(data["stocks"]), 1)

    # ---------- 流水查询 ----------
    def test_movements_list_and_join_fields(self):
        """入库链路产生 receive/putaway_out/putaway_in 三条流水，带联表编码。"""
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        store = self._mk_location("ST-1", "storage")
        self._inbound(oid, pid, store, 10)
        data = self.client.get("/api/movements").get_json()
        self.assertEqual(data["total"], 3)
        types = sorted(m["ref_type"] for m in data["movements"])
        self.assertEqual(types, ["asn_putaway_in", "asn_putaway_out", "asn_receive"])
        recv = next(m for m in data["movements"] if m["ref_type"] == "asn_receive")
        self.assertEqual(recv["qty_delta"], 10)
        self.assertEqual(recv["owner_code"], "OW1")
        self.assertEqual(recv["sku"], "P1")
        self.assertIn("RCV-I1", recv["location_code"])
        self.assertIsNotNone(recv["ref_id"])

    def test_movements_ref_type_filter(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        store = self._mk_location("ST-1", "storage")
        self._inbound(oid, pid, store, 10)
        self._ship_partial(oid, pid, store, 4)
        data = self.client.get("/api/movements?ref_type=dn_ship").get_json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["movements"][0]["qty_delta"], -4)

    def test_movements_location_filter(self):
        """按库位过滤流水：暂存位上有 receive(+10) 与 putaway_out(-10) 两条。"""
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        store = self._mk_location("ST-1", "storage")
        self._inbound(oid, pid, store, 10)
        recv_row = self.client.get("/api/movements?ref_type=asn_receive").get_json()
        recv_loc = recv_row["movements"][0]["location_id"]
        data = self.client.get(f"/api/movements?location_id={recv_loc}").get_json()
        self.assertEqual(data["total"], 2)
        types = sorted(m["ref_type"] for m in data["movements"])
        self.assertEqual(types, ["asn_putaway_out", "asn_receive"])

    def test_movements_pagination_and_bad_args(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        store = self._mk_location("ST-1", "storage")
        self._inbound(oid, pid, store, 10)
        data = self.client.get("/api/movements?page=1&page_size=2").get_json()
        self.assertEqual(data["total"], 3)
        self.assertEqual(len(data["movements"]), 2)
        resp = self.client.get("/api/movements?page=x")
        self.assertEqual(resp.status_code, 400)

    # ---------- 余额核对 ----------
    def test_reconcile_empty_db_consistent(self):
        data = self.client.get("/api/stock/reconcile").get_json()
        self.assertTrue(data["consistent"])
        self.assertEqual(data["checked"], 0)
        self.assertEqual(data["mismatches"], [])

    def test_reconcile_consistent_after_full_flow(self):
        """收货→上架→拣货→发货全程后，余额 = 流水累加。"""
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        store = self._mk_location("ST-1", "storage")
        self._inbound(oid, pid, store, 10)
        self._ship_partial(oid, pid, store, 4)
        data = self.client.get("/api/stock/reconcile").get_json()
        self.assertTrue(data["consistent"], data["mismatches"])
        self.assertEqual(data["checked"], 2)  # 暂存位零行 + 存储位余额
        # 余 6 件，流水净额 +6
        data = self.client.get("/api/stock?location_id=%d" % store).get_json()
        self.assertEqual(data["stocks"][0]["qty"], 6)

    def test_reconcile_detects_tampered_qty(self):
        """人为篡改 stock.qty 后核对报不一致，并给出两侧数值。"""
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        store = self._mk_location("ST-1", "storage")
        self._inbound(oid, pid, store, 10)
        conn = self._direct_db()
        try:
            conn.execute("UPDATE stock SET qty = 99 WHERE location_id = ?", (store,))
            conn.commit()
        finally:
            conn.close()
        data = self.client.get("/api/stock/reconcile").get_json()
        self.assertFalse(data["consistent"])
        self.assertEqual(len(data["mismatches"]), 1)
        m = data["mismatches"][0]
        self.assertEqual(m["book_qty"], 99)
        self.assertEqual(m["movement_sum"], 10)
        self.assertEqual(m["sku"], "P1")

    def test_reconcile_detects_orphan_movement(self):
        """有流水但无库存行且累加非零 → 不一致（账面凭空少了库存）。"""
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        store = self._mk_location("ST-1", "storage")
        self._inbound(oid, pid, store, 10)
        conn = self._direct_db()
        try:
            conn.execute("DELETE FROM stock")
            conn.commit()
        finally:
            conn.close()
        data = self.client.get("/api/stock/reconcile").get_json()
        self.assertFalse(data["consistent"])
        self.assertEqual(data["mismatches"][0]["issue"], "有流水但无库存行")
        self.assertEqual(data["mismatches"][0]["movement_sum"], 10)

    def test_reconcile_zero_row_without_movement_ok(self):
        """零行 + 无流水（如流水被清理）不算异常：qty=0 与累加 0 一致。"""
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        store = self._mk_location("ST-1", "storage")
        self._inbound(oid, pid, store, 10)
        conn = self._direct_db()
        try:
            conn.execute("DELETE FROM stock_movement")
            conn.execute("UPDATE stock SET qty = 0, allocated_qty = 0")
            conn.commit()
        finally:
            conn.close()
        data = self.client.get("/api/stock/reconcile").get_json()
        self.assertTrue(data["consistent"], data["mismatches"])


if __name__ == "__main__":
    unittest.main()
