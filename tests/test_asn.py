"""收货单（ASN）与上架接口测试。"""
import os
import sys
import tempfile
import unittest

_TMP = os.path.join(tempfile.gettempdir(), "miniwms_unittest")
os.makedirs(_TMP, exist_ok=True)
os.environ.setdefault("MINIWMS_DB", os.path.join(_TMP, "test.db"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402  (须在环境变量设置之后)


class AsnApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        import sqlite3
        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        try:
            # 先子后主
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

    def _mk_location(self, code, loc_type, status="active"):
        return self.client.post(
            "/api/locations",
            json={"code": code, "name": code, "loc_type": loc_type, "status": status},
        ).get_json()["location"]["id"]

    def _mk_asn(self, owner_id, product_id, expected=10, code="ASN-001"):
        resp = self.client.post("/api/asns", json={
            "code": code, "owner_id": owner_id,
            "lines": [{"product_id": product_id, "expected_qty": expected}],
        })
        self.assertEqual(resp.status_code, 201, resp.get_json())
        return resp.get_json()

    def _stock_row(self, owner_id, product_id, location_id):
        import sqlite3
        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(
                "SELECT * FROM stock WHERE owner_id=? AND product_id=? AND location_id=?",
                (owner_id, product_id, location_id),
            ).fetchone()
        finally:
            conn.close()

    # ---------- 创建 ----------
    def test_create_ok_and_defaults(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        body = self._mk_asn(oid, pid)
        self.assertEqual(body["asn"]["status"], "open")
        self.assertEqual(body["lines"][0]["remaining_receive"], 10)

    def test_create_duplicate_code(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        self._mk_asn(oid, pid, code="ASN-X")
        resp = self.client.post("/api/asns", json={
            "code": "ASN-X", "owner_id": oid,
            "lines": [{"product_id": pid, "expected_qty": 1}]})
        self.assertEqual(resp.status_code, 409)

    def test_create_owner_mismatch(self):
        oid = self._mk_owner("OW-A")
        oid2 = self._mk_owner("OW-B")
        pid = self._mk_product(oid2)  # 物料属于 OW-B
        resp = self.client.post("/api/asns", json={
            "code": "ASN-M", "owner_id": oid,
            "lines": [{"product_id": pid, "expected_qty": 5}]})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("不一致", resp.get_json()["errors"][0])

    def test_create_bad_qty(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        resp = self.client.post("/api/asns", json={
            "code": "ASN-Q", "owner_id": oid,
            "lines": [{"product_id": pid, "expected_qty": 0}]})
        self.assertEqual(resp.status_code, 400)

    # ---------- 收货 ----------
    def test_receive_flow_and_status(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        recv_loc = self._mk_location("RCV-1", "receiving")
        body = self._mk_asn(oid, pid, expected=10)
        asn_id, line_id = body["asn"]["id"], body["lines"][0]["id"]

        # 部分收货 → receiving
        resp = self.client.post(f"/api/asns/{asn_id}/receive", json={
            "line_id": line_id, "location_id": recv_loc, "qty": 6})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(resp.get_json()["status"], "receiving")
        row = self._stock_row(oid, pid, recv_loc)
        self.assertEqual(row["qty"], 6)

        # 收满 → putaway（已收满未上架）
        resp = self.client.post(f"/api/asns/{asn_id}/receive", json={
            "line_id": line_id, "location_id": recv_loc, "qty": 4})
        self.assertEqual(resp.get_json()["status"], "putaway")

    def test_receive_wrong_location_type(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        storage_loc = self._mk_location("ST-1", "storage")
        body = self._mk_asn(oid, pid)
        resp = self.client.post(f"/api/asns/{body['asn']['id']}/receive", json={
            "line_id": body["lines"][0]["id"], "location_id": storage_loc, "qty": 5})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("类型不正确", resp.get_json()["error"])

    def test_receive_over_remaining(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        recv_loc = self._mk_location("RCV-1", "receiving")
        body = self._mk_asn(oid, pid, expected=5)
        resp = self.client.post(f"/api/asns/{body['asn']['id']}/receive", json={
            "line_id": body["lines"][0]["id"], "location_id": recv_loc, "qty": 6})
        self.assertEqual(resp.status_code, 400)

    # ---------- 上架 ----------
    def test_putaway_flow_and_close(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        recv_loc = self._mk_location("RCV-1", "receiving")
        store_loc = self._mk_location("ST-1", "storage")
        body = self._mk_asn(oid, pid, expected=10)
        asn_id, line_id = body["asn"]["id"], body["lines"][0]["id"]

        self.client.post(f"/api/asns/{asn_id}/receive", json={
            "line_id": line_id, "location_id": recv_loc, "qty": 10})
        resp = self.client.post(f"/api/asns/{asn_id}/putaway", json={
            "line_id": line_id, "from_location_id": recv_loc,
            "to_location_id": store_loc, "qty": 10})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(resp.get_json()["status"], "closed")

        # 暂存位清零但保留行；目标位 10
        src = self._stock_row(oid, pid, recv_loc)
        dst = self._stock_row(oid, pid, store_loc)
        self.assertEqual(src["qty"], 0)
        self.assertIsNotNone(src)  # 零行保留
        self.assertEqual(dst["qty"], 10)

        # 流水 3 条：收货 + 上架出 + 上架入
        import sqlite3
        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        try:
            n, types = conn.execute(
                "SELECT COUNT(*), GROUP_CONCAT(ref_type) FROM stock_movement"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(n, 3)
        self.assertIn("asn_receive", types)
        self.assertIn("asn_putaway_out", types)
        self.assertIn("asn_putaway_in", types)

    def test_putaway_to_receiving_loc_forbidden(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        recv_loc = self._mk_location("RCV-1", "receiving")
        other_recv = self._mk_location("RCV-2", "receiving")
        body = self._mk_asn(oid, pid)
        asn_id, line_id = body["asn"]["id"], body["lines"][0]["id"]
        self.client.post(f"/api/asns/{asn_id}/receive", json={
            "line_id": line_id, "location_id": recv_loc, "qty": 5})
        resp = self.client.post(f"/api/asns/{asn_id}/putaway", json={
            "line_id": line_id, "from_location_id": recv_loc,
            "to_location_id": other_recv, "qty": 5})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("类型不正确", resp.get_json()["error"])

    def test_putaway_over_remaining(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        recv_loc = self._mk_location("RCV-1", "receiving")
        store_loc = self._mk_location("ST-1", "storage")
        body = self._mk_asn(oid, pid, expected=5)
        asn_id, line_id = body["asn"]["id"], body["lines"][0]["id"]
        self.client.post(f"/api/asns/{asn_id}/receive", json={
            "line_id": line_id, "location_id": recv_loc, "qty": 5})
        resp = self.client.post(f"/api/asns/{asn_id}/putaway", json={
            "line_id": line_id, "from_location_id": recv_loc,
            "to_location_id": store_loc, "qty": 3})
        self.assertEqual(resp.status_code, 200)
        # 再上 3 超过剩余 2
        resp = self.client.post(f"/api/asns/{asn_id}/putaway", json={
            "line_id": line_id, "from_location_id": recv_loc,
            "to_location_id": store_loc, "qty": 3})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("待上架", resp.get_json()["error"])

    # ---------- 取消 ----------
    def test_cancel_open_ok_and_received_forbidden(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        recv_loc = self._mk_location("RCV-1", "receiving")
        body = self._mk_asn(oid, pid, code="ASN-C")
        resp = self.client.post(f"/api/asns/{body['asn']['id']}/cancel")
        self.assertEqual(resp.status_code, 200)

        # 已收货的不可取消
        body2 = self._mk_asn(oid, pid, code="ASN-D")
        asn_id, line_id = body2["asn"]["id"], body2["lines"][0]["id"]
        self.client.post(f"/api/asns/{asn_id}/receive", json={
            "line_id": line_id, "location_id": recv_loc, "qty": 1})
        resp = self.client.post(f"/api/asns/{asn_id}/cancel")
        self.assertEqual(resp.status_code, 409)


if __name__ == "__main__":
    unittest.main()
