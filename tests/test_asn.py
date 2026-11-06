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
            conn.execute("DELETE FROM batch_location")
            conn.execute("DELETE FROM stock_batch")
            conn.execute("DELETE FROM stock_movement")
            conn.execute("DELETE FROM stock")
            conn.execute("DELETE FROM asn_line")
            conn.execute("DELETE FROM asn")
            conn.execute("DELETE FROM customer")
            conn.execute("DELETE FROM supplier")
            conn.execute("DELETE FROM warehouse")
            conn.execute("DELETE FROM product")
            conn.execute("DELETE FROM owner")
            conn.execute("DELETE FROM location")
            conn.commit()
        finally:
            conn.close()
        # 来源仓库为必填项：每个用例预置一个默认仓库
        self.wh_id = self._mk_warehouse("WH-DEFAULT")

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

    def _mk_warehouse(self, code="WH1"):
        return self.client.post(
            "/api/warehouses", json={"code": code, "name": f"仓{code}"}
        ).get_json()["warehouse"]["id"]

    def _mk_supplier(self, owner_id, code="SUP1", name="供货商1"):
        return self.client.post(
            "/api/suppliers",
            json={"code": code, "name": name, "owner_id": owner_id,
                  "contact": "陈先生", "phone": "13800000000", "address": "深圳"},
        ).get_json()["supplier"]["id"]

    def _mk_customer(self, owner_id, code="C1", name="客户1"):
        return self.client.post(
            "/api/customers",
            json={"code": code, "name": name, "owner_id": owner_id,
                  "contact": "林女士", "phone": "13900000000", "address": "广州"},
        ).get_json()["customer"]["id"]

    def _mk_asn(self, owner_id, product_id, expected=10, code="ASN-001", **extra):
        if "source_warehouse" not in extra:
            extra["source_warehouse"] = self.wh_id
        resp = self.client.post("/api/asns", json={
            "code": code, "owner_id": owner_id, **extra,
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
            "code": "ASN-X", "owner_id": oid, "source_warehouse": self.wh_id,
            "lines": [{"product_id": pid, "expected_qty": 1}]})
        self.assertEqual(resp.status_code, 409)

    def test_create_owner_mismatch(self):
        oid = self._mk_owner("OW-A")
        oid2 = self._mk_owner("OW-B")
        pid = self._mk_product(oid2)  # 物料属于 OW-B
        resp = self.client.post("/api/asns", json={
            "code": "ASN-M", "owner_id": oid, "source_warehouse": self.wh_id,
            "lines": [{"product_id": pid, "expected_qty": 5}]})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("不一致", resp.get_json()["errors"][0])

    def test_create_bad_qty(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        resp = self.client.post("/api/asns", json={
            "code": "ASN-Q", "owner_id": oid, "source_warehouse": self.wh_id,
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

    # ---------- M16 单据字段 / 自动单号 / 供应商快照 ----------
    def test_create_auto_code(self):
        """单号留空 → 自动生成 ASN-YYYYMMDD-001。"""
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        resp = self.client.post("/api/asns", json={
            "owner_id": oid, "source_warehouse": self.wh_id,
            "lines": [{"product_id": pid, "expected_qty": 1}]})
        self.assertEqual(resp.status_code, 201)
        self.assertRegex(resp.get_json()["asn"]["code"], r"^ASN-\d{8}-001$")

    def test_create_invalid_doc_type(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        resp = self.client.post("/api/asns", json={
            "code": "ASN-T", "owner_id": oid, "doc_type": "外星单", "source_warehouse": self.wh_id,
            "lines": [{"product_id": pid, "expected_qty": 1}]})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("doc_type", str(resp.get_json()["errors"]))

    def test_create_totals_and_category(self):
        """订单总数量 = Σ预期；品类数 = 不同物料数。"""
        oid = self._mk_owner()
        p1 = self._mk_product(oid, sku="P1")
        p2 = self._mk_product(oid, sku="P2")
        resp = self.client.post("/api/asns", json={
            "code": "ASN-MULTI", "owner_id": oid, "source_warehouse": self.wh_id,
            "lines": [
                {"product_id": p1, "expected_qty": 3},
                {"product_id": p2, "expected_qty": 5},
            ],
        })
        self.assertEqual(resp.status_code, 201, resp.get_json())
        a = resp.get_json()["asn"]
        self.assertEqual(a["total_qty"], 8)    # 3+5
        self.assertEqual(a["category_count"], 2)  # 2 种物料

    def test_create_duplicate_product_rejected(self):
        """明细同一物料出现多次应被拒（合并数量后再提交）。"""
        oid = self._mk_owner()
        p1 = self._mk_product(oid, sku="P1")
        resp = self.client.post("/api/asns", json={
            "code": "ASN-DUP", "owner_id": oid, "source_warehouse": self.wh_id,
            "lines": [
                {"product_id": p1, "expected_qty": 3},
                {"product_id": p1, "expected_qty": 2},
            ],
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("合并", resp.get_json()["errors"][0])

    def test_create_with_supplier_snapshot(self):
        """关联供应商时落库名称/联系人/电话/地址快照，且多货主隔离。"""
        oid = self._mk_owner()
        oid2 = self._mk_owner("OW-X")
        pid = self._mk_product(oid)
        sup_id = self._mk_supplier(oid, code="SUP-A", name="代工厂")
        body = self._mk_asn(oid, pid, code="ASN-SUP", supplier_id=sup_id)
        a = body["asn"]
        self.assertEqual(a["supplier_name"], "代工厂")
        self.assertEqual(a["supplier_contact"], "陈先生")
        self.assertEqual(a["supplier_phone"], "13800000000")
        self.assertEqual(a["supplier_address"], "深圳")
        # 跨货主供应商被拒：建属 oid2 的供应商，再用 oid 的 ASN 引用
        sup2 = self._mk_supplier(oid2, code="SUP-B", name="别家")
        resp = self.client.post("/api/asns", json={
            "code": "ASN-SUP2", "owner_id": oid, "supplier_id": sup2,
            "source_warehouse": self.wh_id,
            "lines": [{"product_id": pid, "expected_qty": 1}]})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("供应商", resp.get_json()["errors"][0])

    def test_create_source_warehouse_validation(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        # 不存在的仓库
        resp = self.client.post("/api/asns", json={
            "code": "ASN-WH", "owner_id": oid, "source_warehouse": 99999,
            "lines": [{"product_id": pid, "expected_qty": 1}]})
        self.assertEqual(resp.status_code, 400)
        # 停用仓库
        wh = self._mk_warehouse("WH-X")
        self.client.put(f"/api/warehouses/{wh}", json={"status": "inactive"})
        resp = self.client.post("/api/asns", json={
            "code": "ASN-WH2", "owner_id": oid, "source_warehouse": wh,
            "lines": [{"product_id": pid, "expected_qty": 1}]})
        self.assertEqual(resp.status_code, 400)
        # 正常仓库
        resp = self.client.post("/api/asns", json={
            "code": "ASN-WH3", "owner_id": oid, "source_warehouse": self._mk_warehouse("WH-Y"),
            "lines": [{"product_id": pid, "expected_qty": 1}]})
        self.assertEqual(resp.status_code, 201, resp.get_json())
        self.assertEqual(resp.get_json()["asn"]["source_warehouse_code"], "WH-Y")

    def test_supplier_referenced_cannot_delete(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        sup_id = self._mk_supplier(oid)
        self._mk_asn(oid, pid, code="ASN-REF", supplier_id=sup_id)
        resp = self.client.delete(f"/api/suppliers/{sup_id}")
        self.assertEqual(resp.status_code, 409)

    # ---------- 本轮新增：仓库必填 / 客户联动 / 联系方式覆盖 / 明细快照 ----------
    def test_create_without_source_warehouse_allowed(self):
        """表单层必填；API 层允许省略（兼容内部作业链路与外部对接）。"""
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        resp = self.client.post("/api/asns", json={
            "code": "ASN-NOWH", "owner_id": oid,
            "lines": [{"product_id": pid, "expected_qty": 1}]})
        self.assertEqual(resp.status_code, 201, resp.get_json())
        self.assertIsNone(resp.get_json()["asn"]["source_warehouse_code"])

    def test_default_doc_type_is_purchase(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        body = self._mk_asn(oid, pid, code="ASN-DOCDEF")
        self.assertEqual(body["asn"]["doc_type"], "采购")

    def test_customer_owner_mismatch_reports_error(self):
        """选了别家货主的客户应明确报错，而不是前端被过滤掉看不到。"""
        oid = self._mk_owner()
        oid2 = self._mk_owner("OW-B")
        pid = self._mk_product(oid)
        cid2 = self._mk_customer(oid2, code="C-B", name="别家客户")
        resp = self.client.post("/api/asns", json={
            "code": "ASN-CUST", "owner_id": oid, "source_warehouse": self.wh_id,
            "customer_id": cid2,
            "lines": [{"product_id": pid, "expected_qty": 1}]})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("客户", resp.get_json()["errors"][0])

    def test_contact_overrides_and_customer_snapshot(self):
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        cid = self._mk_customer(oid)
        sid = self._mk_supplier(oid)
        resp = self.client.post("/api/asns", json={
            "code": "ASN-OV", "owner_id": oid, "source_warehouse": self.wh_id,
            "customer_id": cid, "supplier_id": sid,
            "supplier_contact": "王经理", "supplier_phone": "13711112222",
            "customer_contact": "李经理", "customer_phone": "13633334444",
            "customer_address": "东莞",
            "lines": [{"product_id": pid, "expected_qty": 2}]})
        self.assertEqual(resp.status_code, 201, resp.get_json())
        a = resp.get_json()["asn"]
        self.assertEqual(a["customer_id"], cid)
        self.assertEqual(a["supplier_contact"], "王经理")
        self.assertEqual(a["supplier_phone"], "13711112222")
        self.assertEqual(a["customer_contact"], "李经理")
        self.assertEqual(a["customer_phone"], "13633334444")
        self.assertEqual(a["customer_address"], "东莞")
        detail = self.client.get(f"/api/asns/{a['id']}").get_json()["asn"]
        self.assertEqual(detail["supplier_contact"], "王经理")
        self.assertEqual(detail["customer_code"], "C1")

    def test_line_product_snapshot_fields(self):
        oid = self._mk_owner()
        pid = self.client.post("/api/products", json={
            "sku": "MED-1", "name": "医药物料", "owner_id": oid,
            "spec": "10mg×24片", "article_no": "HN-7788", "unit": "盒",
            "reg_cert_no": "国械注准20260001", "registrant": "某某药业",
            "mfg_enterprise": "深圳制药厂", "mfg_license": "粤20260001",
        }).get_json()["product"]["id"]
        line = {
            "product_id": pid, "expected_qty": 5,
            "product_name": "医药物料", "spec": "10mg×24片", "article_no": "HN-7788",
            "unit": "盒", "brand": "测试牌", "mfg_enterprise": "深圳制药厂",
            "reg_cert_no": "国械注准20260001", "registrant": "某某药业",
            "mfg_license": "粤20260001", "shelf_life_days": 730,
            "batch_no": "B20261104", "production_date": "2026-11-01",
            "expiry_date": "2028-11-01",
        }
        resp = self.client.post("/api/asns", json={
            "code": "ASN-SNAP", "owner_id": oid, "source_warehouse": self.wh_id,
            "lines": [line]})
        self.assertEqual(resp.status_code, 201, resp.get_json())
        created = resp.get_json()["lines"][0]
        self.assertEqual(created["spec"], "10mg×24片")
        self.assertEqual(created["batch_no"], "B20261104")
        self.assertEqual(created["registrant"], "某某药业")
        detail = self.client.get(
            f"/api/asns/{resp.get_json()['asn']['id']}").get_json()["lines"][0]
        self.assertEqual(detail["article_no"], "HN-7788")
        self.assertEqual(detail["production_date"], "2026-11-01")
        self.assertEqual(detail["expiry_date"], "2028-11-01")
        self.assertEqual(detail["mfg_license"], "粤20260001")
        # 未传快照的老式明细，回退到物料主数据当前档案
        plain = self._mk_asn(oid, pid, code="ASN-FALLBACK")
        self.assertEqual(plain["lines"][0]["spec"], "10mg×24片")

    # ---------- M19：明细字段可人工编辑 + 新增 7 个医疗器械字段 + 验收入库批次 ----------
    def test_line_snapshot_is_human_editable(self):
        """自动带出只是默认值：人工改过的档案/批次字段必须原样留档，不被物料主数据覆盖。"""
        oid = self._mk_owner()
        pid = self.client.post("/api/products", json={
            "sku": "MED-2", "name": "档案原名", "owner_id": oid,
            "spec": "档案规格", "mfg_enterprise": "档案厂家", "origin": "档案产地",
        }).get_json()["product"]["id"]
        edited = {
            "product_id": pid, "expected_qty": 3,
            "product_name": "人工改过的名称", "spec": "人工改过的规格",
            "article_no": "ART-EDIT", "unit": "箱", "brand": "人工品牌",
            "mfg_enterprise": "人工厂家", "production_enterprise": "人工生产企业",
            "origin": "人工产地", "udi": "UDI-EDIT-001",
            "reg_cert_no": "人工注册证", "registrant": "人工注册人",
            "mfg_license": "人工许可证", "shelf_life_days": 365,
            "batch_no": "LOT-EDIT", "sterilization_batch_no": "ST-EDIT",
            "production_date": "2026-02-01", "expiry_date": "2028-02-01",
            "internal_batch_no": "IB-EDIT", "goods_property": "不合格",
            "patient_name": "李四",
        }
        resp = self.client.post("/api/asns", json={
            "code": "ASN-EDIT", "owner_id": oid, "source_warehouse": self.wh_id,
            "lines": [edited]})
        self.assertEqual(resp.status_code, 201, resp.get_json())
        detail = self.client.get(
            f"/api/asns/{resp.get_json()['asn']['id']}").get_json()["lines"][0]
        for key in ("product_name", "spec", "article_no", "unit", "brand",
                    "mfg_enterprise", "production_enterprise", "origin", "udi",
                    "reg_cert_no", "registrant", "mfg_license", "shelf_life_days",
                    "batch_no", "sterilization_batch_no", "production_date",
                    "expiry_date", "internal_batch_no", "goods_property",
                    "patient_name"):
            self.assertEqual(str(detail[key]), str(edited[key]),
                             f"字段 {key} 被覆盖，人工编辑未留档")

    def test_receive_returns_unique_batch_no(self):
        """每次收货都返回唯一批次号；首次入库内部批次号与批次号一致。"""
        oid = self._mk_owner()
        pid = self._mk_product(oid)
        loc = self._mk_location("RCV-BAT", "receiving")
        body = self._mk_asn(oid, pid, expected=8)
        r1 = self.client.post(f"/api/asns/{body['asn']['id']}/receive", json={
            "line_id": body["lines"][0]["id"], "location_id": loc, "qty": 3})
        self.assertEqual(r1.status_code, 200, r1.get_json())
        b1 = r1.get_json()["batch"]
        self.assertTrue(b1["batch_no"].startswith("BAT-"))
        self.assertEqual(b1["internal_batch_no"], b1["batch_no"])
        r2 = self.client.post(f"/api/asns/{body['asn']['id']}/receive", json={
            "line_id": body["lines"][0]["id"], "location_id": loc, "qty": 3})
        b2 = r2.get_json()["batch"]
        self.assertNotEqual(b1["batch_no"], b2["batch_no"])
        self.assertEqual(b1["batch_id"], b2["batch_id"])


if __name__ == "__main__":
    unittest.main()
