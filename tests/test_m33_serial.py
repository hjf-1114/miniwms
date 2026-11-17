"""M33 序列号台账：建档 / 状态机 / 流水 / 货主隔离 / 库存零副作用。

共享物理库约定同 M31–M32：closing 包裸连接；setUp 只清 M33 前缀的数据与
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
import scope as S  # noqa: E402
import serial_service as SS  # noqa: E402
from flask import g  # noqa: E402

DB = os.environ["MINIWMS_DB"]


def _conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


class M33SerialTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        with closing(_conn()) as c:
            c.execute("PRAGMA foreign_keys=OFF")
            c.execute("DELETE FROM serial_transaction WHERE serial_id IN"
                      " (SELECT id FROM serial_no WHERE serial_no LIKE 'M33-%')")
            c.execute("DELETE FROM serial_no WHERE serial_no LIKE 'M33-%'")
            c.execute("DELETE FROM product WHERE sku LIKE 'M33-%'")
            c.execute("DELETE FROM owner WHERE code LIKE 'M33-%'")
            c.commit()
        self.owner = self._post("/api/owners", {
            "code": "M33-OW", "name": "M33 货主"})["owner"]["id"]
        self.other = self._post("/api/owners", {
            "code": "M33-OW9", "name": "M33 其他货主"})["owner"]["id"]
        self.product = self._post("/api/products", {
            "sku": "M33-P1", "name": "M33 序列号货品", "is_serial": 1})["product"]["id"]
        self.plain = self._post("/api/products", {
            "sku": "M33-P2", "name": "M33 普通货品"})["product"]["id"]

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

    def _act(self, path, payload=None, status=200):
        """动作端点（出库 / 状态变更）返回 200，不是 201。"""
        return self._call("POST", path, payload, status)

    def _serial(self, no="M33-SN1", **kw):
        body = {"serial_no": no, "product_id": self.product, "owner_id": self.owner}
        body.update(kw)
        return self._post("/api/serials", body)["serial"]

    def _snapshot(self):
        with closing(_conn()) as c:
            return tuple(c.execute(
                "SELECT (SELECT COUNT(*) FROM stock), (SELECT COUNT(*) FROM stock_movement),"
                " (SELECT COUNT(*) FROM stock_adjustment), (SELECT COUNT(*) FROM stock_batch)"
            ).fetchone())

    # ---------- 用例 ----------
    def test_01_dict_endpoint(self):
        d = self._get("/api/serials/dict")
        self.assertEqual(len(d["statuses"]), len(C.SERIAL_STATUSES))
        self.assertEqual(len(d["actions"]), 3)
        self.assertEqual(len(d["ref_types"]), 3)
        self.assertTrue(d["owner_null_means_global"])
        self.assertFalse(d["affects_stock"])
        self.assertFalse(d["dn_binding"])

    def test_02_create_writes_in_transaction(self):
        s = self._serial("M33-SN1", batch_no="B-01")
        self.assertEqual(s["status"], C.SERIAL_STATUS_IN_STOCK)
        self.assertEqual(len(s["transactions"]), 1)
        self.assertEqual(s["transactions"][0]["action"], C.SERIAL_ACTION_IN)
        self.assertEqual(s["transactions"][0]["to_status"], C.SERIAL_STATUS_IN_STOCK)
        self.assertEqual(s["product_code"], "M33-P1")

    def test_03_duplicate_serial_rejected(self):
        self._serial("M33-DUP")
        bad = self._post("/api/serials", {"serial_no": "M33-DUP",
                                           "product_id": self.product}, status=400)
        self.assertEqual(bad["code"], "SERIAL_DUPLICATE")

    def test_04_required_fields(self):
        bad = self._post("/api/serials", {"product_id": self.product}, status=400)
        self.assertIn("serial_no", bad["error"])
        bad2 = self._post("/api/serials", {"serial_no": "M33-X"}, status=400)
        self.assertIn("product_id", bad2["error"])

    def test_05_product_must_exist(self):
        bad = self._post("/api/serials", {"serial_no": "M33-NP",
                                           "product_id": 999999}, status=400)
        self.assertIn("product_id", bad["error"])

    def test_06_outbound_transitions(self):
        s = self._serial("M33-OUT")
        r = self._act("/api/serials/" + str(s["id"]) + "/outbound",
                       {"operator": "tester", "note": "发货", "ref_type": "dn",
                        "ref_id": 42})
        out = r["serial"]
        self.assertEqual(out["status"], C.SERIAL_STATUS_OUT)
        self.assertTrue(out["out_date"])
        self.assertIsNone(out["location_id"])
        txn = out["transactions"][-1]
        self.assertEqual(txn["action"], C.SERIAL_ACTION_OUT)
        self.assertEqual(txn["ref_type"], "dn")
        self.assertEqual(txn["ref_id"], 42)
        self.assertEqual(txn["operator"], "tester")

    def test_07_outbound_twice_rejected(self):
        s = self._serial("M33-OUT2")
        self._act("/api/serials/" + str(s["id"]) + "/outbound", {})
        bad = self._act("/api/serials/" + str(s["id"]) + "/outbound", {},
                         status=400)
        self.assertEqual(bad["code"], "SERIAL_BAD_TRANSITION")

    def test_08_frozen_and_unfreeze(self):
        s = self._serial("M33-FRZ")
        r = self._act("/api/serials/" + str(s["id"]) + "/status",
                       {"status": C.SERIAL_STATUS_FROZEN})
        self.assertEqual(r["serial"]["status"], C.SERIAL_STATUS_FROZEN)
        # 冻结后不能出库
        bad = self._act("/api/serials/" + str(s["id"]) + "/outbound", {},
                         status=400)
        self.assertEqual(bad["code"], "SERIAL_BAD_TRANSITION")
        # 解冻
        r2 = self._act("/api/serials/" + str(s["id"]) + "/status",
                        {"status": C.SERIAL_STATUS_IN_STOCK})
        self.assertEqual(r2["serial"]["status"], C.SERIAL_STATUS_IN_STOCK)

    def test_09_scrapped_is_terminal(self):
        s = self._serial("M33-SCR")
        self._act("/api/serials/" + str(s["id"]) + "/status",
                   {"status": C.SERIAL_STATUS_SCRAPPED})
        for target in (C.SERIAL_STATUS_IN_STOCK, C.SERIAL_STATUS_OUT,
                       C.SERIAL_STATUS_FROZEN):
            bad = self._act("/api/serials/" + str(s["id"]) + "/status",
                             {"status": target}, status=400)
            self.assertEqual(bad["code"], "SERIAL_BAD_TRANSITION")
        bad = self._act("/api/serials/" + str(s["id"]) + "/outbound", {},
                         status=400)
        self.assertEqual(bad["code"], "SERIAL_BAD_TRANSITION")

    def test_10_same_status_rejected(self):
        s = self._serial("M33-SAME")
        bad = self._act("/api/serials/" + str(s["id"]) + "/status",
                         {"status": C.SERIAL_STATUS_IN_STOCK}, status=400)
        self.assertEqual(bad["code"], "SERIAL_BAD_TRANSITION")

    def test_11_unknown_status_rejected(self):
        s = self._serial("M33-UNK")
        bad = self._act("/api/serials/" + str(s["id"]) + "/status",
                         {"status": "unknown"}, status=400)
        self.assertEqual(bad["code"], "SERIAL_BAD_PARAM")

    def test_12_update_non_state_fields(self):
        s = self._serial("M33-UPD", batch_no="B-1")
        r = self._put("/api/serials/" + str(s["id"]), {"batch_no": "B-2"})
        self.assertEqual(r["serial"]["batch_no"], "B-2")
        # 状态没被顺带改掉
        self.assertEqual(r["serial"]["status"], C.SERIAL_STATUS_IN_STOCK)

    def test_13_list_filters(self):
        self._serial("M33-L1", batch_no="BX")
        self._serial("M33-L2")
        d = self._get("/api/serials?keyword=M33-L1")
        self.assertEqual(d["total"], 1)
        d2 = self._get("/api/serials?status=in_stock")
        self.assertEqual(d2["total"], 2)
        d3 = self._get("/api/serials?product_id=" + str(self.product))
        self.assertEqual(d3["total"], 2)
        d4 = self._get("/api/serials?page=1&page_size=1")
        self.assertEqual(len(d4["serials"]), 1)
        bad = self._get("/api/serials?page=abc", status=400)
        self.assertEqual(bad["code"], "SERIAL_BAD_PARAM")
        bad2 = self._get("/api/serials?status=nope", status=400)
        self.assertEqual(bad2["code"], "SERIAL_BAD_PARAM")

    def test_14_stats_and_gap(self):
        self._serial("M33-S1")
        self._serial("M33-S2")
        self._act("/api/serials/" + str(
            self._get("/api/serials?keyword=M33-S2")["serials"][0]["id"]) + "/outbound", {})
        st = self._get("/api/serials/stats")
        self.assertEqual(st["total"], 2)
        item = [i for i in st["items"] if i["product_id"] == self.product][0]
        self.assertEqual(item["total"], 2)
        self.assertEqual(item["in_stock"], 1)
        self.assertEqual(item["out"], 1)
        # 当前库存 0、在库台账 1 → 缺口为 -1（只读提示）
        self.assertEqual(item["stock_qty"], 0)
        self.assertEqual(item["gap"], -1)

    def test_15_product_summary(self):
        self._serial("M33-PS")
        with main.app.test_request_context():
            g.scope = {"owner_all": True, "owner_ids": []}
            s = SS.product_summary(main.get_db(), self.product)
            self.assertEqual(s["total"], 1)
            self.assertEqual(s["in_stock"], 1)
            s2 = SS.product_summary(main.get_db(), self.plain)
            self.assertEqual(s2["total"], 0)

    def test_16_detail_includes_transactions(self):
        s = self._serial("M33-DET")
        self._act("/api/serials/" + str(s["id"]) + "/status",
                   {"status": C.SERIAL_STATUS_FROZEN})
        got = self._get("/api/serials/" + str(s["id"]))["serial"]
        self.assertEqual(len(got["transactions"]), 2)
        self.assertEqual(got["transactions"][-1]["from_status"],
                         C.SERIAL_STATUS_IN_STOCK)
        self.assertEqual(got["transactions"][-1]["to_status"],
                         C.SERIAL_STATUS_FROZEN)

    def test_17_owner_isolation_shared_visible(self):
        a = self._serial("M33-OA", owner_id=self.owner)
        b = self._serial("M33-OB", owner_id=self.other)
        c = self._serial("M33-OG", owner_id=None)
        with main.app.test_request_context():
            g.scope = {"owner_all": False, "owner_ids": [self.owner]}
            d = SS.list_records(main.get_db(), {})
            codes = {x["serial_no"] for x in d["serials"]}
        self.assertIn("M33-OA", codes)
        self.assertIn("M33-OG", codes)
        self.assertNotIn("M33-OB", codes)
        self.assertTrue(a and b and c)

    def test_18_scope_missing_denied(self):
        with main.app.test_request_context():
            g.scope = None
            with self.assertRaises(S.ScopeError):
                SS.list_records(main.get_db(), {})

    def test_19_restricted_detail_and_write_denied(self):
        s = self._serial("M33-RESTRICT", owner_id=self.other)
        with main.app.test_request_context():
            g.scope = {"owner_all": False, "owner_ids": [self.owner]}
            with self.assertRaises(S.ScopeError):
                SS.get(main.get_db(), s["id"])
            with self.assertRaises(S.ScopeError):
                SS.update(main.get_db(), s["id"], {"note": "越权"})
            with self.assertRaises(S.ScopeError):
                SS.change_status(main.get_db(), s["id"],
                                 {"status": C.SERIAL_STATUS_FROZEN})
            with self.assertRaises(S.ScopeError):
                SS.outbound(main.get_db(), s["id"], {})
            with self.assertRaises(S.ScopeError):
                SS.create(main.get_db(), {"serial_no": "M33-X",
                                          "product_id": self.product,
                                          "owner_id": self.other})

    def test_20_bad_ref_type_rejected(self):
        s = self._serial("M33-REF")
        bad = self._act("/api/serials/" + str(s["id"]) + "/outbound",
                         {"ref_type": "xxx"}, status=400)
        self.assertEqual(bad["code"], "SERIAL_BAD_PARAM")

    def test_21_no_stock_side_effect(self):
        before = self._snapshot()
        s = self._serial("M33-SIDE")
        self._act("/api/serials/" + str(s["id"]) + "/status",
                   {"status": C.SERIAL_STATUS_FROZEN})
        self._act("/api/serials/" + str(s["id"]) + "/status",
                   {"status": C.SERIAL_STATUS_IN_STOCK})
        self._act("/api/serials/" + str(s["id"]) + "/outbound", {})
        after = self._snapshot()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
