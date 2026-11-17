"""M31 司机与车辆主数据：CRUD / 唯一性 / 效期分级 / 货主隔离 / 只读边界。

共享物理库约定同 M37–M41：closing 包裸连接；setUp 只清 M31 前缀的主数据与
本模块自建的 owner/carrier，不动别的里程碑数据；setUpClass 显式 init_db。
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
import fleet_service as FS  # noqa: E402
import main  # noqa: E402
import scope as S  # noqa: E402
from flask import g  # noqa: E402

DB = os.environ["MINIWMS_DB"]


def _conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


class M31FleetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        with closing(_conn()) as c:
            c.execute("PRAGMA foreign_keys=OFF")
            for t in ("driver", "vehicle"):
                c.execute(f"DELETE FROM {t}")
            c.execute("DELETE FROM carrier WHERE code LIKE 'M31-%'")
            c.execute("DELETE FROM owner WHERE code LIKE 'M31-%'")
            c.commit()
        self.owner = self._post("/api/owners", {
            "code": "M31-OW", "name": "M31 货主"})["owner"]["id"]
        self.other = self._post("/api/owners", {
            "code": "M31-OW9", "name": "M31 其他货主"})["owner"]["id"]
        self.carrier = self._post("/api/carriers", {
            "code": "M31-CAR", "name": "M31 承运商"})["carrier"]["id"]

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

    def _driver(self, code="M31-D1", **kw):
        body = {"code": code, "name": "司机" + code, "owner_id": self.owner,
                "carrier_id": self.carrier}
        body.update(kw)
        return self._post("/api/drivers", body)["driver"]

    def _vehicle(self, code="M31-V1", **kw):
        body = {"code": code, "plate_no": "沪A" + code[-2:] + "31",
                "owner_id": self.owner, "carrier_id": self.carrier}
        body.update(kw)
        return self._post("/api/vehicles", body)["vehicle"]

    def _snapshot(self):
        with closing(_conn()) as c:
            return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in ("stock", "stock_movement", "stock_adjustment", "stock_batch")}

    # ---------- 测试 ----------
    def test_01_dictionary(self):
        d = self._get("/api/fleet/dict")
        self.assertEqual(len(d["statuses"]), 2)
        self.assertEqual(len(d["license_types"]), 8)
        self.assertEqual(len(d["vehicle_types"]), 6)
        self.assertEqual(len(d["expiry_levels"]), 4)
        self.assertEqual(len(d["expiry_kinds"]), 3)
        self.assertTrue(d["owner_null_means_shared"])
        self.assertFalse(d["dn_binding"])
        self.assertEqual(d["soon_days_default"], C.FLEET_EXPIRY_SOON_DAYS_DEFAULT)

    def test_02_create_driver_defaults(self):
        d = self._driver(license_no="M31-LIC-1", license_expiry="")
        self.assertEqual(d["license_type"], "C1")
        self.assertEqual(d["status"], C.FLEET_STATUS_ACTIVE)
        self.assertEqual(d["owner_id"], self.owner)
        self.assertEqual(d["carrier_id"], self.carrier)
        self.assertEqual(d["license_expiry"], "")

    def test_03_create_vehicle_numbers(self):
        v = self._vehicle(max_load_kg="1200.5", length_cm="420", width_cm="180",
                          height_cm="190", vehicle_type="冷藏车")
        self.assertEqual(v["max_load_kg"], 1200.5)
        self.assertEqual(v["length_cm"], 420.0)
        self.assertEqual(v["vehicle_type"], "冷藏车")
        self.assertEqual(v["status"], C.FLEET_STATUS_ACTIVE)

    def test_04_validation(self):
        bad = self._post("/api/drivers", {"code": "", "name": ""}, status=400)
        self.assertIn("编码", bad["error"])
        self._post("/api/drivers", {"code": "M31-D9", "name": "x",
                                    "license_expiry": "2026/01/01"}, status=400)
        self._post("/api/vehicles", {"code": "M31-V9", "plate_no": "沪Z99999",
                                     "max_load_kg": "abc"}, status=400)
        self._post("/api/drivers", {"code": "M31-D8", "name": "x",
                                    "license_type": "Z9"}, status=400)

    def test_05_unique_code_and_value(self):
        self._driver(license_no="M31-LIC-2")
        dup = self._post("/api/drivers", {"code": "M31-D1", "name": "重复"}, status=400)
        self.assertEqual(dup["code"], "FLEET_DUPLICATE_CODE")
        dup2 = self._post("/api/drivers", {"code": "M31-D2", "name": "重复",
                                           "license_no": "M31-LIC-2"}, status=400)
        self.assertEqual(dup2["code"], "FLEET_DUPLICATE_VALUE")
        # 空驾驶证号允许多个
        self._driver("M31-D3", license_no="")
        self._driver("M31-D4", license_no="")
        v = self._vehicle()
        dup3 = self._post("/api/vehicles", {"code": "M31-V2", "plate_no": v["plate_no"]},
                          status=400)
        self.assertEqual(dup3["code"], "FLEET_DUPLICATE_VALUE")

    def test_06_update_partial_and_unique_exclusion(self):
        d = self._driver(license_no="M31-LIC-3")
        r = self._put("/api/drivers/" + str(d["id"]), {"phone": "13800000001"})
        self.assertEqual(r["driver"]["phone"], "13800000001")
        self.assertEqual(r["driver"]["name"], d["name"])
        r = self._put("/api/drivers/" + str(d["id"]),
                      {"status": C.FLEET_STATUS_INACTIVE})
        self.assertEqual(r["driver"]["status"], C.FLEET_STATUS_INACTIVE)
        # 更新成自己已有车牌/证号不算重复
        self._put("/api/drivers/" + str(d["id"]), {"license_no": "M31-LIC-3"})

    def test_07_delete(self):
        d = self._driver()
        self._delete("/api/drivers/" + str(d["id"]))
        self._get("/api/drivers/" + str(d["id"]), status=400)
        v = self._vehicle()
        self._delete("/api/vehicles/" + str(v["id"]))
        self._get("/api/vehicles/" + str(v["id"]), status=400)

    def test_08_expiry_level_pure(self):
        today = datetime.date.today()
        self.assertEqual(FS._expiry_level("", today, 30), C.FLEET_EXPIRY_NONE)
        self.assertEqual(FS._expiry_level("bad", today, 30), C.FLEET_EXPIRY_NONE)
        self.assertEqual(FS._expiry_level(
            (today - datetime.timedelta(days=1)).isoformat(), today, 30),
            C.FLEET_EXPIRY_EXPIRED)
        self.assertEqual(FS._expiry_level(
            (today + datetime.timedelta(days=10)).isoformat(), today, 30),
            C.FLEET_EXPIRY_SOON)
        self.assertEqual(FS._expiry_level(
            (today + datetime.timedelta(days=100)).isoformat(), today, 30),
            C.FLEET_EXPIRY_NORMAL)

    def test_09_expiry_report_levels_and_filters(self):
        today = datetime.date.today()
        self._driver("M31-DE", license_no="LE",
                     license_expiry=(today - datetime.timedelta(days=3)).isoformat())
        self._driver("M31-DS", license_no="LS",
                     license_expiry=(today + datetime.timedelta(days=7)).isoformat())
        self._driver("M31-DN", license_no="LN")
        self._vehicle("M31-VS", plate_no="沪B11131",
                      insurance_expiry=(today + datetime.timedelta(days=9)).isoformat(),
                      transport_expiry=(today - datetime.timedelta(days=2)).isoformat())
        self._vehicle("M31-VN", plate_no="沪B22231")
        rep = self._get("/api/fleet/expiry")
        stats = {s["level"]: s["count"] for s in rep["stats"]}
        self.assertEqual(stats[C.FLEET_EXPIRY_EXPIRED], 2)
        self.assertEqual(stats[C.FLEET_EXPIRY_SOON], 2)
        self.assertEqual(stats[C.FLEET_EXPIRY_NONE], 3)
        self.assertEqual(rep["total"], 7)  # 3 人 × 驾驶证 + 2 车 × 保险/运输证
        lvl = self._get("/api/fleet/expiry?level=expired&kind=driver_license")
        self.assertEqual(lvl["total"], 1)
        self.assertEqual(lvl["items"][0]["code"], "M31-DE")
        narrow = self._get("/api/fleet/expiry?days=5")
        narrow_stats = {s["level"]: s["count"] for s in narrow["stats"]}
        self.assertEqual(narrow_stats[C.FLEET_EXPIRY_SOON], 0)

    def test_10_list_keyword_status_pagination(self):
        self._driver("M31-DA", license_no="LA")
        self._driver("M31-DB", license_no="LB")
        self._driver("M31-DC", license_no="LC",
                     status=C.FLEET_STATUS_INACTIVE)
        all_rows = self._get("/api/drivers?page=1&page_size=2")
        self.assertEqual(all_rows["total"], 3)
        self.assertEqual(len(all_rows["drivers"]), 2)
        page2 = self._get("/api/drivers?page=2&page_size=2")
        self.assertEqual(len(page2["drivers"]), 1)
        kw = self._get("/api/drivers?keyword=M31-DB")
        self.assertEqual(kw["total"], 1)
        st = self._get("/api/drivers?status=inactive")
        self.assertEqual(st["total"], 1)

    def test_11_allow_null_shared_and_owner_filter(self):
        self._driver("M31-SH", owner_id=None, name="共享司机", license_no="SH")
        self._driver("M31-OW", owner_id=self.owner, name="本货主", license_no="OW")
        self._driver("M31-OT", owner_id=self.other, name="别家", license_no="OT")
        with main.app.test_request_context():
            g.scope = {"owner_all": False, "owner_ids": [self.owner]}
            rows = FS.list_records(main.get_db(), "driver", {})
        codes = [x["code"] for x in rows["drivers"]]
        self.assertIn("M31-SH", codes)
        self.assertIn("M31-OW", codes)
        self.assertNotIn("M31-OT", codes)

    def test_12_scope_missing_denied(self):
        d = self._driver()
        with main.app.test_request_context():
            g.scope = None
            with self.assertRaises(S.ScopeError):
                FS.list_records(main.get_db(), "driver", {})
            with self.assertRaises(S.ScopeError):
                FS.get(main.get_db(), "driver", d["id"])

    def test_13_restricted_detail_denied(self):
        d = self._driver(owner_id=self.other)
        with main.app.test_request_context():
            g.scope = {"owner_all": False, "owner_ids": [self.owner]}
            with self.assertRaises(S.ScopeError):
                FS.get(main.get_db(), "driver", d["id"])

    def test_14_restricted_write_denied(self):
        with main.app.test_request_context():
            g.scope = {"owner_all": False, "owner_ids": [self.owner]}
            with self.assertRaises(S.ScopeError):
                FS.create(main.get_db(), "driver", {
                    "code": "M31-X", "name": "越权", "owner_id": self.other})

    def test_15_fk_validation(self):
        bad1 = self._post("/api/drivers", {"code": "M31-FK1", "name": "x",
                                           "carrier_id": 999999}, status=400)
        self.assertIn("carrier_id", bad1["error"])
        bad2 = self._post("/api/vehicles", {"code": "M31-FK2", "plate_no": "沪C22231",
                                            "owner_id": 999999}, status=400)
        self.assertIn("owner_id", bad2["error"])

    def test_16_api_smoke_all_endpoints(self):
        d = self._driver()
        v = self._vehicle()
        self.assertEqual(self._get("/api/drivers")["total"], 1)
        self.assertEqual(self._get("/api/vehicles")["total"], 1)
        self.assertEqual(self._get("/api/drivers/" + str(d["id"]))["id"], d["id"])
        self.assertEqual(self._get("/api/vehicles/" + str(v["id"]))["id"], v["id"])
        self.assertEqual(self._put("/api/vehicles/" + str(v["id"]),
                                   {"note": "已年检"})["vehicle"]["note"], "已年检")
        self.assertTrue(self._delete("/api/drivers/" + str(d["id"]))["ok"])
        self.assertTrue(self._delete("/api/vehicles/" + str(v["id"]))["ok"])

    def test_17_expiry_pagination_and_bad_param(self):
        self._driver("M31-DP", license_no="LP",
                     license_expiry=datetime.date.today().isoformat())
        rep = self._get("/api/fleet/expiry?page=1&page_size=1")
        self.assertEqual(len(rep["items"]), 1)
        self.assertGreaterEqual(rep["total"], 1)
        bad = self._get("/api/fleet/expiry?days=999", status=400)
        self.assertEqual(bad["code"], "FLEET_BAD_PARAM")
        bad2 = self._get("/api/fleet/expiry?kind=unknown", status=400)
        self.assertEqual(bad2["code"], "FLEET_BAD_PARAM")

    def test_18_no_stock_side_effect(self):
        before = self._snapshot()
        d = self._driver()
        v = self._vehicle(max_load_kg=800)
        self._put("/api/drivers/" + str(d["id"]), {"phone": "13900000000"})
        self._put("/api/vehicles/" + str(v["id"]), {"max_load_kg": 900})
        after = self._snapshot()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
