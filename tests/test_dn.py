"""出库单（DN）拣货/发货/超卖拦截接口测试。"""
import os
import sys
import tempfile
import unittest

_TMP = os.path.join(tempfile.gettempdir(), "miniwms_unittest")
os.makedirs(_TMP, exist_ok=True)
os.environ.setdefault("MINIWMS_DB", os.path.join(_TMP, "test.db"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402  (须在环境变量设置之后)


class DnApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()
        cls._asn_seq = 0

    def setUp(self):
        import sqlite3
        self._asn_seq = 0
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
            conn.execute("DELETE FROM supplier")
            conn.execute("DELETE FROM warehouse")
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

    def _inbound(self, owner_id, product_id, to_loc_id, qty):
        """走完整 ASN 链路入库：收货位收货 → 上架到 to_loc_id（storage/picking）。"""
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

    def _mk_warehouse(self, code="WH1"):
        return self.client.post(
            "/api/warehouses", json={"code": code, "name": f"仓{code}"}
        ).get_json()["warehouse"]["id"]

    def _mk_dn(self, owner_id, product_id, ordered=5, code="DN-001", customer_id=None, **extra):
        if customer_id is None:
            customer_id = self._mk_customer(owner_id, code=f"C-{code}")
        resp = self.client.post("/api/dns", json={
            "code": code, "owner_id": owner_id, "customer_id": customer_id, **extra,
            "lines": [{"product_id": product_id, "ordered_qty": ordered}],
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
        body = self._mk_dn(oid, pid)
        self.assertEqual(body["dn"]["status"], "open")
        self.assertEqual(body["lines"][0]["remaining_pick"], 5)

    def test_create_duplicate_code(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        cid = self._mk_customer(oid, code="C-DUP")
        self._mk_dn(oid, pid, code="DN-X", customer_id=cid)
        resp = self.client.post("/api/dns", json={
            "code": "DN-X", "owner_id": oid, "customer_id": cid,
            "lines": [{"product_id": pid, "ordered_qty": 1}]})
        self.assertEqual(resp.status_code, 409)

    def test_create_owner_mismatch(self):
        oid = self._mk_owner("OW-A")
        oid2 = self._mk_owner("OW-B")
        pid = self._mk_product(oid2)  # 物料属于 OW-B
        cid = self._mk_customer(oid, code="C-MM")  # 客户属于 OW-A
        resp = self.client.post("/api/dns", json={
            "code": "DN-M", "owner_id": oid, "customer_id": cid,
            "lines": [{"product_id": pid, "ordered_qty": 5}]})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("不一致", resp.get_json()["errors"][0])

    def test_create_bad_qty(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        resp = self.client.post("/api/dns", json={
            "code": "DN-Q", "owner_id": oid,
            "lines": [{"product_id": pid, "ordered_qty": 0}]})
        self.assertEqual(resp.status_code, 400)

    def test_create_requires_customer(self):
        """出库必须指定客户（此前缺失的维度）。"""
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        resp = self.client.post("/api/dns", json={
            "code": "DN-NC", "owner_id": oid,
            "lines": [{"product_id": pid, "ordered_qty": 1}]})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("customer_id", resp.get_json()["errors"][0])

    def test_create_customer_owner_mismatch(self):
        """客户必须属于该货主（多货主隔离），选错货主客户应被拒。"""
        oid_a = self._mk_owner("OW-A")
        oid_b = self._mk_owner("OW-B")
        pid = self._mk_product(oid_a)  # 物料属于 OW-A
        cid_b = self._mk_customer(oid_b, code="C-B")  # 客户属于 OW-B
        resp = self.client.post("/api/dns", json={
            "code": "DN-MM", "owner_id": oid_a, "customer_id": cid_b,
            "lines": [{"product_id": pid, "ordered_qty": 1}]})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("客户", resp.get_json()["errors"][0])

    def test_detail_returns_customer_info(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        cid = self._mk_customer(oid, code="C-INFO")
        body = self._mk_dn(oid, pid, ordered=2, code="DN-INFO", customer_id=cid)
        body = self.client.get(f"/api/dns/{body['dn']['id']}").get_json()
        self.assertEqual(body["dn"]["customer_code"], "C-INFO")
        self.assertEqual(body["dn"]["customer_name"], "C-INFO")

    # ---------- M16 单据字段 / 自动单号 / 多仓库 ----------
    def test_create_auto_code(self):
        """单号留空 → 自动生成 DN-YYYYMMDD-001。"""
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        cid = self._mk_customer(oid)
        resp = self.client.post("/api/dns", json={
            "owner_id": oid, "customer_id": cid,
            "lines": [{"product_id": pid, "ordered_qty": 1}]})
        self.assertEqual(resp.status_code, 201)
        self.assertRegex(resp.get_json()["dn"]["code"], r"^DN-\d{8}-001$")

    def test_create_invalid_doc_type(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        cid = self._mk_customer(oid)
        resp = self.client.post("/api/dns", json={
            "code": "DN-T", "owner_id": oid, "customer_id": cid,
            "doc_type": "外星单",
            "lines": [{"product_id": pid, "ordered_qty": 1}]})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("doc_type", str(resp.get_json()["errors"]))

    def test_create_totals_and_category(self):
        oid = self._mk_owner()
        p1 = self._mk_product(oid, sku="P1")
        p2 = self._mk_product(oid, sku="P2")
        cid = self._mk_customer(oid, code="C-M")
        resp = self.client.post("/api/dns", json={
            "code": "DN-MULTI", "owner_id": oid, "customer_id": cid,
            "lines": [
                {"product_id": p1, "ordered_qty": 3},
                {"product_id": p2, "ordered_qty": 5},
            ],
        })
        self.assertEqual(resp.status_code, 201, resp.get_json())
        d = resp.get_json()["dn"]
        self.assertEqual(d["total_qty"], 8)
        self.assertEqual(d["category_count"], 2)

    def test_create_duplicate_product_rejected(self):
        oid = self._mk_owner()
        p1 = self._mk_product(oid, sku="P1")
        cid = self._mk_customer(oid, code="C-DUP")
        resp = self.client.post("/api/dns", json={
            "code": "DN-DUP", "owner_id": oid, "customer_id": cid,
            "lines": [
                {"product_id": p1, "ordered_qty": 3},
                {"product_id": p1, "ordered_qty": 2},
            ],
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("合并", resp.get_json()["errors"][0])

    def test_create_with_source_warehouse(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        cid = self._mk_customer(oid)
        wh = self._mk_warehouse("WH-DN")
        body = self._mk_dn(oid, pid, code="DN-WH", customer_id=cid, source_warehouse=wh)
        self.assertEqual(body["dn"]["source_warehouse_code"], "WH-DN")
        # 详情也返回仓库信息
        body = self.client.get(f"/api/dns/{body['dn']['id']}").get_json()
        self.assertEqual(body["dn"]["source_warehouse_name"], "仓WH-DN")
        # 不存在的仓库被拒
        resp = self.client.post("/api/dns", json={
            "code": "DN-WHX", "owner_id": oid, "customer_id": cid,
            "source_warehouse": 99999,
            "lines": [{"product_id": pid, "ordered_qty": 1}]})
        self.assertEqual(resp.status_code, 400)
        # 停用仓库被拒
        self.client.put(f"/api/warehouses/{wh}", json={"status": "inactive"})
        resp = self.client.post("/api/dns", json={
            "code": "DN-WHY", "owner_id": oid, "customer_id": cid,
            "source_warehouse": wh,
            "lines": [{"product_id": pid, "ordered_qty": 1}]})
        self.assertEqual(resp.status_code, 400)

    # ---------- 拣货 ----------
    def test_pick_flow_only_allocates(self):
        """拣货只做分配：qty 不变、allocated_qty 增加，写 qty_delta=0 的流水。"""
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        store = self._mk_location("ST-1", "storage")
        self._inbound(oid, pid, store, 10)
        body = self._mk_dn(oid, pid, ordered=6)
        dn_id, line_id = body["dn"]["id"], body["lines"][0]["id"]

        resp = self.client.post(f"/api/dns/{dn_id}/pick", json={
            "line_id": line_id, "location_id": store, "qty": 4})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(resp.get_json()["status"], "picking")

        row = self._stock_row(oid, pid, store)
        self.assertEqual(row["qty"], 10)          # 在手不变
        self.assertEqual(row["allocated_qty"], 4)  # 仅分配
        self.assertEqual(resp.get_json()["lines"][0]["remaining_pick"], 2)

        import sqlite3
        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        conn.row_factory = sqlite3.Row
        try:
            mv = conn.execute(
                "SELECT qty_delta FROM stock_movement WHERE ref_type='dn_pick'"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(len(mv), 1)
        self.assertEqual(mv[0]["qty_delta"], 0)

    def test_pick_oversell_intercepted(self):
        """超卖拦截：可用量不足的拣货被拒绝，库存与分配均不变。"""
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        store = self._mk_location("ST-1", "storage")
        self._inbound(oid, pid, store, 5)

        # 同一张单拣超可用量
        body = self._mk_dn(oid, pid, ordered=6, code="DN-A")
        resp = self.client.post(f"/api/dns/{body['dn']['id']}/pick", json={
            "line_id": body["lines"][0]["id"], "location_id": store, "qty": 6})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("可用库存不足", resp.get_json()["error"])

        # 两张单抢同一份库存：第一张拣走 5 后第二张可用为 0
        body2 = self._mk_dn(oid, pid, ordered=3, code="DN-B")
        self.client.post(f"/api/dns/{body['dn']['id']}/pick", json={
            "line_id": body["lines"][0]["id"], "location_id": store, "qty": 5})
        resp = self.client.post(f"/api/dns/{body2['dn']['id']}/pick", json={
            "line_id": body2["lines"][0]["id"], "location_id": store, "qty": 3})
        self.assertEqual(resp.status_code, 400)
        row = self._stock_row(oid, pid, store)
        self.assertEqual(row["qty"], 5)
        self.assertEqual(row["allocated_qty"], 5)

    def test_pick_over_ordered(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        store = self._mk_location("ST-1", "storage")
        self._inbound(oid, pid, store, 10)
        body = self._mk_dn(oid, pid, ordered=3)
        resp = self.client.post(f"/api/dns/{body['dn']['id']}/pick", json={
            "line_id": body["lines"][0]["id"], "location_id": store, "qty": 4})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("剩余待拣", resp.get_json()["error"])

    def test_pick_no_stock_row(self):
        """库位从未入库（无库存行）：拣货拒绝，不产生非法库存行。"""
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        empty = self._mk_location("ST-E", "storage")
        body = self._mk_dn(oid, pid, ordered=1)
        resp = self.client.post(f"/api/dns/{body['dn']['id']}/pick", json={
            "line_id": body["lines"][0]["id"], "location_id": empty, "qty": 1})
        self.assertEqual(resp.status_code, 400)
        self.assertIsNone(self._stock_row(oid, pid, empty))

    def test_pick_wrong_location_type(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        recv_loc = self._mk_location("RCV-1", "receiving")
        store = self._mk_location("ST-1", "storage")
        self._inbound(oid, pid, store, 10)
        body = self._mk_dn(oid, pid, ordered=3)
        resp = self.client.post(f"/api/dns/{body['dn']['id']}/pick", json={
            "line_id": body["lines"][0]["id"], "location_id": recv_loc, "qty": 3})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("类型不正确", resp.get_json()["error"])

    def test_pick_disabled_location(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        store = self._mk_location("ST-1", "storage")
        self._inbound(oid, pid, store, 10)
        dead = self._mk_location("ST-D", "storage", status="inactive")
        body = self._mk_dn(oid, pid, ordered=3)
        resp = self.client.post(f"/api/dns/{body['dn']['id']}/pick", json={
            "line_id": body["lines"][0]["id"], "location_id": dead, "qty": 3})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("停用", resp.get_json()["error"])

    # ---------- 发货 ----------
    def test_ship_flow_deducts_and_closes(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        store = self._mk_location("ST-1", "storage")
        self._inbound(oid, pid, store, 10)
        body = self._mk_dn(oid, pid, ordered=6)
        dn_id, line_id = body["dn"]["id"], body["lines"][0]["id"]

        self.client.post(f"/api/dns/{dn_id}/pick", json={
            "line_id": line_id, "location_id": store, "qty": 6})
        resp = self.client.post(f"/api/dns/{dn_id}/ship")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(resp.get_json()["status"], "shipped")

        row = self._stock_row(oid, pid, store)
        self.assertEqual(row["qty"], 4)   # 在手扣减
        self.assertEqual(row["allocated_qty"], 0)  # 分配清零

        # 未拣满 4 件也留在库位；再发货被拒
        resp = self.client.post(f"/api/dns/{dn_id}/ship")
        self.assertEqual(resp.status_code, 409)

        import sqlite3
        conn = sqlite3.connect(os.environ["MINIWMS_DB"])
        conn.row_factory = sqlite3.Row
        try:
            ships = conn.execute(
                "SELECT qty_delta FROM stock_movement WHERE ref_type='dn_ship'"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(len(ships), 1)
        self.assertEqual(ships[0]["qty_delta"], -6)

    def test_ship_before_fully_picked(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        store = self._mk_location("ST-1", "storage")
        self._inbound(oid, pid, store, 10)
        body = self._mk_dn(oid, pid, ordered=5)
        resp = self.client.post(f"/api/dns/{body['dn']['id']}/ship")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("未拣满", resp.get_json()["error"])

    def test_ship_multi_location_picks(self):
        """一行拆多个库位拣货，发货按明细逐位扣减。"""
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        store1 = self._mk_location("ST-1", "storage")
        store2 = self._mk_location("ST-2", "storage")
        self._inbound(oid, pid, store1, 4)
        self._inbound(oid, pid, store2, 4)
        body = self._mk_dn(oid, pid, ordered=6)
        dn_id, line_id = body["dn"]["id"], body["lines"][0]["id"]

        self.client.post(f"/api/dns/{dn_id}/pick", json={
            "line_id": line_id, "location_id": store1, "qty": 4})
        self.client.post(f"/api/dns/{dn_id}/pick", json={
            "line_id": line_id, "location_id": store2, "qty": 2})
        resp = self.client.post(f"/api/dns/{dn_id}/ship")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(self._stock_row(oid, pid, store1)["qty"], 0)
        self.assertEqual(self._stock_row(oid, pid, store2)["qty"], 2)

    # ---------- 取消 ----------
    def test_cancel_ok_and_after_pick_forbidden(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        store = self._mk_location("ST-1", "storage")
        self._inbound(oid, pid, store, 10)

        body = self._mk_dn(oid, pid, ordered=3, code="DN-C")
        resp = self.client.post(f"/api/dns/{body['dn']['id']}/cancel")
        self.assertEqual(resp.status_code, 200)

        # 已拣货的不可取消
        body2 = self._mk_dn(oid, pid, ordered=3, code="DN-D")
        self.client.post(f"/api/dns/{body2['dn']['id']}/pick", json={
            "line_id": body2["lines"][0]["id"], "location_id": store, "qty": 1})
        resp = self.client.post(f"/api/dns/{body2['dn']['id']}/cancel")
        self.assertEqual(resp.status_code, 409)

    def test_shipped_not_cancellable(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        store = self._mk_location("ST-1", "storage")
        self._inbound(oid, pid, store, 10)
        body = self._mk_dn(oid, pid, ordered=2, code="DN-E")
        dn_id, line_id = body["dn"]["id"], body["lines"][0]["id"]
        self.client.post(f"/api/dns/{dn_id}/pick", json={
            "line_id": line_id, "location_id": store, "qty": 2})
        self.client.post(f"/api/dns/{dn_id}/ship")
        resp = self.client.post(f"/api/dns/{dn_id}/cancel")
        self.assertEqual(resp.status_code, 409)


if __name__ == "__main__":
    unittest.main()
