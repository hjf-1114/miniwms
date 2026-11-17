"""M28 经营报表：收发汇总（排除库位间转移）/ 周转率 / 库位利用率 / 只读边界。

共享物理库约定同 M31–M34：closing 包裸连接；setUp 只清本模块造的
M28- 前缀数据，不动别的里程碑数据；setUpClass 显式 init_db。
"""
import datetime
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
import scope as S  # noqa: E402
from flask import g  # noqa: E402

DB = os.environ["MINIWMS_DB"]
TODAY = datetime.date.today()
FROM = (TODAY - datetime.timedelta(days=30)).isoformat()
TO = TODAY.isoformat()


def _conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


class M28OperationReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        with closing(_conn()) as c:
            c.execute("PRAGMA foreign_keys=OFF")
            # stock 表没有 note 列，只能按 product_id / location_id 标记清理
            pids = [r["id"] for r in c.execute(
                "SELECT id FROM product WHERE sku LIKE 'M28-%'")]
            lids = [r["id"] for r in c.execute(
                "SELECT id FROM location WHERE code LIKE 'M28-%'")]
            if pids:
                ph = ",".join("?" for _ in pids)
                c.execute(f"DELETE FROM stock WHERE product_id IN ({ph})", pids)
                c.execute(f"DELETE FROM stock_movement WHERE product_id IN ({ph})", pids)
            if lids:
                lh = ",".join("?" for _ in lids)
                c.execute(f"DELETE FROM stock WHERE location_id IN ({lh})", lids)
            c.execute("DELETE FROM stock_movement WHERE note LIKE 'M28-%'")
            c.execute("DELETE FROM location WHERE code LIKE 'M28-%'")
            c.execute("DELETE FROM product WHERE sku LIKE 'M28-%'")
            c.execute("DELETE FROM owner WHERE code LIKE 'M28-%'")
            c.commit()
        self.owner = self._post("/api/owners", {
            "code": "M28-OW", "name": "M28 货主"})["owner"]["id"]
        self.other = self._post("/api/owners", {
            "code": "M28-OW9", "name": "M28 其他货主"})["owner"]["id"]
        self.prod = self._post("/api/products", {
            "sku": "M28-P1", "name": "M28 货品", "purchase_price": 10})["product"]["id"]
        self.prod2 = self._post("/api/products", {
            "sku": "M28-P2", "name": "M28 无价货品"})["product"]["id"]
        self.loc = self._post("/api/locations", {
            "code": "M28-L1", "name": "M28 库位"})["location"]["id"]
        self.loc2 = self._post("/api/locations", {
            "code": "M28-L2", "name": "M28 空库位"})["location"]["id"]

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

    def _mv(self, note, qty_delta, ref_type="asn_receive", product_id=None,
            owner_id=None, location_id=None, when=None):
        """直插一条库存流水（报表只读，故这里直接造数据）。"""
        with closing(_conn()) as c:
            c.execute(
                "INSERT INTO stock_movement (owner_id, product_id, location_id,"
                " qty_delta, ref_type, ref_id, note, created_at)"
                " VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
                (owner_id if owner_id is not None else self.owner,
                 product_id or self.prod, location_id or self.loc,
                 qty_delta, ref_type, note,
                 when or f"{TODAY.isoformat()} 10:00:00"),
            )
            c.commit()

    def _stock(self, note, qty, product_id=None, owner_id=None, location_id=None):
        """stock 表无 note 列，用 note 参数仅作可读标记（清理靠 product/location）。"""
        with closing(_conn()) as c:
            c.execute(
                "INSERT INTO stock (owner_id, product_id, location_id, qty,"
                " allocated_qty, updated_at) VALUES (?, ?, ?, ?, 0,"
                " datetime('now','localtime'))",
                (owner_id if owner_id is not None else self.owner,
                 product_id or self.prod, location_id or self.loc, qty),
            )
            c.commit()

    # ---------- 用例 ----------
    def test_01_dict_endpoint(self):
        d = self._get("/api/operation-report/dict")
        self.assertEqual(len(d["periods"]), 3)
        self.assertEqual(len(d["cost_fields"]), 2)
        self.assertEqual(len(d["util_dimensions"]), 4)
        self.assertIn("asn_putaway_in", d["transfer_ref_types"])
        self.assertTrue(d["read_only"])

    def test_02_io_summary_excludes_transfer(self):
        """库位间转移流水不计入收发，否则收货量翻倍。"""
        self._mv("M28-收货", 100)
        self._mv("M28-上架出", -100, "asn_putaway_out")
        self._mv("M28-上架入", 100, "asn_putaway_in")
        self._mv("M28-移库出", -100, "move_out")
        self._mv("M28-移库入", 100, "move_in")
        self._mv("M28-发货", -40, "dn_ship")
        d = self._get(f"/api/operation-report/io-summary?date_from={FROM}&date_to={TO}")
        self.assertEqual(d["total"]["in_qty"], 100)
        self.assertEqual(d["total"]["out_qty"], 40)
        self.assertEqual(d["total"]["net_qty"], 60)
        self.assertEqual(d["total"]["movements"], 2)

    def test_03_io_summary_by_owner(self):
        self._mv("M28-A", 10, owner_id=self.owner)
        self._mv("M28-B", 20, owner_id=self.other)
        d = self._get(f"/api/operation-report/io-summary?date_from={FROM}&date_to={TO}")
        self.assertEqual(d["total"]["in_qty"], 30)
        codes = {i["owner_code"]: i["in_qty"] for i in d["items"]}
        self.assertEqual(codes.get("M28-OW"), 10)
        self.assertEqual(codes.get("M28-OW9"), 20)

    def test_04_io_summary_owner_filter(self):
        self._mv("M28-A", 10, owner_id=self.owner)
        self._mv("M28-B", 20, owner_id=self.other)
        d = self._get(f"/api/operation-report/io-summary?date_from={FROM}&date_to={TO}"
                      f"&owner_id={self.owner}")
        self.assertEqual(d["total"]["in_qty"], 10)

    def test_05_io_summary_top_products(self):
        self._mv("M28-P", 100)
        self._mv("M28-P2", 5, product_id=self.prod2)
        d = self._get(f"/api/operation-report/io-summary?date_from={FROM}&date_to={TO}")
        self.assertEqual(d["top_products"][0]["sku"], "M28-P1")
        self.assertEqual(d["top_products"][0]["in_qty"], 100)

    def test_06_date_range_validation(self):
        bad = self._get("/api/operation-report/io-summary"
                        "?date_from=2026-13-01&date_to=2026-12-01", status=400)
        self.assertEqual(bad["code"], "OP_REPORT_BAD_PARAM")
        bad2 = self._get("/api/operation-report/io-summary"
                         "?date_from=2026-12-01&date_to=2026-11-01", status=400)
        self.assertEqual(bad2["code"], "OP_REPORT_BAD_PARAM")
        bad3 = self._get("/api/operation-report/io-summary?period=year", status=400)
        self.assertEqual(bad3["code"], "OP_REPORT_BAD_PARAM")

    def test_07_turnover_basic(self):
        # 期末库存 100 × 单价 10 = 1000；期间出库 40 × 10 = 400
        self._stock("M28-期末库存", 100)
        self._mv("M28-收货", 140)
        self._mv("M28-发货", -40, "dn_ship")
        d = self._get(f"/api/operation-report/turnover?date_from={FROM}&date_to={TO}")
        item = [i for i in d["items"] if i["owner_id"] == self.owner][0]
        self.assertEqual(item["end_amount"], 1000)
        self.assertEqual(item["in_amount"], 1400)
        self.assertEqual(item["out_amount"], 400)
        # 期初 = 期末 − 净变化 = 1000 − 1000 = 0；平均 = 500
        self.assertEqual(item["begin_amount"], 0)
        self.assertEqual(item["avg_amount"], 500)
        self.assertEqual(item["turn_count"], 0.8)
        self.assertAlmostEqual(item["turn_days"], round(31 / 0.8, 2), places=1)

    def test_08_turnover_zero_avg_returns_none(self):
        """进出一致、期末无库存 → 期初 0、平均 0 → 不算周转次数。"""
        self._mv("M28-进货", 10)
        self._mv("M28-出货", -10, "dn_ship")
        d = self._get(f"/api/operation-report/turnover?date_from={FROM}&date_to={TO}")
        item = [i for i in d["items"] if i["owner_id"] == self.owner][0]
        self.assertEqual(item["end_amount"], 0)
        self.assertEqual(item["begin_amount"], 0)
        self.assertEqual(item["avg_amount"], 0)
        self.assertIsNone(item["turn_count"])
        self.assertIsNone(item["turn_days"])

    def test_09_turnover_no_price_flagged(self):
        self._stock("M28-无价库存", 50, product_id=self.prod2)
        d = self._get(f"/api/operation-report/turnover?date_from={FROM}&date_to={TO}")
        self.assertGreaterEqual(d["no_price_items"], 1)
        self.assertEqual(d["cost_field"], "purchase_price")

    def test_10_turnover_cost_field_switch(self):
        self._stock("M28-有价库存", 10)
        d = self._get(f"/api/operation-report/turnover?date_from={FROM}&date_to={TO}"
                      "&cost_field=sale_price")
        self.assertEqual(d["cost_field"], "sale_price")
        bad = self._get("/api/operation-report/turnover?cost_field=nope", status=400)
        self.assertEqual(bad["code"], "OP_REPORT_BAD_PARAM")

    def test_11_location_utilization(self):
        self._stock("M28-占用库存", 10, location_id=self.loc)
        d = self._get("/api/operation-report/location-utilization?dimension=all")
        self.assertGreaterEqual(d["used_locations"], 1)
        self.assertGreaterEqual(d["total_locations"], 2)
        self.assertLessEqual(d["used_locations"], d["total_locations"])
        self.assertGreaterEqual(d["utilization"], 0)
        codes = {l["code"] for l in d["empty_locations"]}
        self.assertIn("M28-L2", codes)

    def test_12_utilization_dimensions(self):
        self._stock("M28-占用库存", 10, location_id=self.loc)
        for dim in ("warehouse", "zone", "location_group", "all"):
            d = self._get("/api/operation-report/location-utilization?dimension=" + dim)
            self.assertEqual(d["dimension"], dim)
        bad = self._get("/api/operation-report/location-utilization?dimension=xxx",
                        status=400)
        self.assertEqual(bad["code"], "OP_REPORT_BAD_PARAM")

    def test_13_overview(self):
        self._stock("M28-期末库存", 100)
        self._mv("M28-收货", 140)
        self._mv("M28-发货", -40, "dn_ship")
        d = self._get(f"/api/operation-report/overview?date_from={FROM}&date_to={TO}")
        self.assertEqual(d["in_qty"], 140)
        self.assertEqual(d["out_qty"], 40)
        self.assertEqual(d["end_amount"], 1000)
        self.assertGreaterEqual(d["location_total"], 2)
        self.assertGreaterEqual(d["stock_qty"], 100)

    def test_14_scope_missing_denied(self):
        with main.app.test_request_context():
            g.scope = None
            import operation_report_service as ORS
            with self.assertRaises(S.ScopeError):
                ORS.io_summary(main.get_db(), {})
            with self.assertRaises(S.ScopeError):
                ORS.turnover(main.get_db(), {})

    def test_15_restricted_sees_only_own(self):
        self._stock("M28-本货主库存", 10, owner_id=self.owner)
        self._stock("M28-他货主库存", 50, owner_id=self.other)
        with main.app.test_request_context():
            g.scope = {"owner_all": False, "owner_ids": [self.owner]}
            import operation_report_service as ORS
            d = ORS.turnover(main.get_db(), {})
            owner_ids = {i["owner_id"] for i in d["items"]}
            self.assertNotIn(self.other, owner_ids)

    def test_16_no_write_side_effect(self):
        before = self._snapshot()
        self._get(f"/api/operation-report/overview?date_from={FROM}&date_to={TO}")
        self._get(f"/api/operation-report/io-summary?date_from={FROM}&date_to={TO}")
        self._get(f"/api/operation-report/turnover?date_from={FROM}&date_to={TO}")
        self._get("/api/operation-report/location-utilization")
        after = self._snapshot()
        self.assertEqual(before, after)

    def _snapshot(self):
        with closing(_conn()) as c:
            return tuple(c.execute(
                "SELECT (SELECT COUNT(*) FROM stock), (SELECT COUNT(*) FROM stock_movement),"
                " (SELECT COUNT(*) FROM stock_adjustment), (SELECT COUNT(*) FROM stock_batch),"
                " (SELECT COUNT(*) FROM asn), (SELECT COUNT(*) FROM dn)"
            ).fetchone())


if __name__ == "__main__":
    unittest.main()
