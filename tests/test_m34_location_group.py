"""M34 货位组主数据：CRUD / 层级无关 / 拣货顺序 / 引用保护 / 老数据回填 / 盘点维度。

共享物理库约定同 M31–M33：closing 包裸连接；setUp 只清 M34 前缀的数据与
本模块自建的库位，不动别的里程碑数据；setUpClass 显式 init_db。
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
import location_group_service as LGS  # noqa: E402
import main  # noqa: E402
from flask import g  # noqa: E402

DB = os.environ["MINIWMS_DB"]


def _conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


class M34LocationGroupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        with closing(_conn()) as c:
            c.execute("PRAGMA foreign_keys=OFF")
            c.execute("DELETE FROM location WHERE code LIKE 'M34-%'")
            c.execute("DELETE FROM location_group WHERE code LIKE 'M34-%'")
            c.commit()

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

    def _group(self, code="M34-G1", **kw):
        body = {"code": code, "name": "组 " + code}
        body.update(kw)
        return self._post("/api/location-groups", body)["location_group"]

    def _location(self, code="M34-L1", **kw):
        body = {"code": code, "name": "库位 " + code}
        body.update(kw)
        return self._post("/api/locations", body)["location"]

    def _snapshot(self):
        with closing(_conn()) as c:
            return tuple(c.execute(
                "SELECT (SELECT COUNT(*) FROM stock), (SELECT COUNT(*) FROM stock_movement),"
                " (SELECT COUNT(*) FROM stock_adjustment), (SELECT COUNT(*) FROM stock_batch)"
            ).fetchone())

    # ---------- 用例 ----------
    def test_01_dict_endpoint(self):
        d = self._get("/api/location-groups/dict")
        self.assertEqual(len(d["statuses"]), 2)
        self.assertEqual(len(d["mix_fields"]), 3)
        self.assertEqual(d["capacity_unlimited"], 0)
        self.assertFalse(d["owner_isolated"])

    def test_02_create_and_get(self):
        g = self._group("M34-G1", pick_seq=10, capacity_volume=1000,
                        capacity_weight=50, allow_mix_product=1)
        self.assertEqual(g["code"], "M34-G1")
        self.assertEqual(g["pick_seq"], 10)
        self.assertEqual(g["capacity_volume"], 1000)
        self.assertEqual(g["capacity_weight"], 50)
        self.assertEqual(g["allow_mix_product"], 1)
        self.assertFalse(g["volume_unlimited"])
        self.assertTrue(g["weight_unlimited"] is False)
        got = self._get("/api/location-groups/" + str(g["id"]))["location_group"]
        self.assertEqual(got["id"], g["id"])

    def test_03_duplicate_code_rejected(self):
        self._group("M34-DUP")
        bad = self._post("/api/location-groups", {"code": "M34-DUP", "name": "x"},
                         status=400)
        self.assertEqual(bad["code"], "LOC_GROUP_DUPLICATE_CODE")

    def test_04_required_fields(self):
        bad = self._post("/api/location-groups", {"name": "无编码"}, status=400)
        self.assertIn("code", bad["error"])
        bad2 = self._post("/api/location-groups", {"code": "M34-NONAME"}, status=400)
        self.assertIn("name", bad2["error"])

    def test_05_update_partial(self):
        g = self._group("M34-U1")
        r = self._put("/api/location-groups/" + str(g["id"]), {"pick_seq": 99})
        self.assertEqual(r["location_group"]["pick_seq"], 99)
        self.assertEqual(r["location_group"]["code"], "M34-U1")

    def test_06_delete_blocked_when_referenced(self):
        g = self._group("M34-R1")
        self._location("M34-L-R1", location_group=g["code"])
        bad = self._delete("/api/location-groups/" + str(g["id"]), status=400)
        self.assertEqual(bad["code"], "LOC_GROUP_IN_USE")

    def test_07_rename_blocked_when_referenced(self):
        g = self._group("M34-R2")
        self._location("M34-L-R2", location_group=g["code"])
        bad = self._put("/api/location-groups/" + str(g["id"]),
                        {"code": "M34-R2B"}, status=400)
        self.assertEqual(bad["code"], "LOC_GROUP_IN_USE")

    def test_08_delete_ok_when_unused(self):
        g = self._group("M34-D3")
        self.assertTrue(self._delete("/api/location-groups/" + str(g["id"]))["ok"])
        gone = self._get("/api/location-groups/" + str(g["id"]), status=400)
        self.assertEqual(gone["code"], "LOC_GROUP_NOT_FOUND")

    def test_09_location_auto_creates_group(self):
        loc = self._location("M34-L-AUTO", location_group="M34-自动组")
        self.assertEqual(loc["location_group"], "M34-自动组")
        self.assertEqual(loc["location_group_name"], "M34-自动组")
        self.assertTrue(loc["location_group_known"])
        d = self._get("/api/location-groups?keyword=M34-自动组")
        self.assertEqual(d["total"], 1)

    def test_10_location_code_prefix_autocreate(self):
        """库位留空分组时按编码前缀自动生成，并同步建组行。"""
        loc = self._location("M34-PRE-1")
        self.assertTrue(loc["location_group"])
        d = self._get("/api/location-groups?keyword=" + loc["location_group"])
        self.assertEqual(d["total"], 1)

    def test_11_backfill_keeps_legacy_value(self):
        with closing(_conn()) as c:
            c.execute("DELETE FROM location_group WHERE code = '老分组文本'")
            c.execute("INSERT INTO location (code, name, location_group)"
                      " VALUES ('M34-LEG', '老库位', '老分组文本')")
            c.commit()
        with closing(_conn()) as c:
            made = LGS.backfill_from_locations(c)
            c.commit()
        with closing(_conn()) as c:
            row = c.execute("SELECT location_group FROM location WHERE code='M34-LEG'").fetchone()
            self.assertEqual(row["location_group"], "老分组文本")
            hit = c.execute("SELECT name FROM location_group WHERE code='老分组文本'").fetchone()
            self.assertIsNotNone(hit)
            self.assertEqual(hit["name"], "老分组文本")
        self.assertEqual(made, 1)

    def test_12_resolve_name_fallback(self):
        name, known = LGS.resolve_name(_conn(), "M34-不存在")
        self.assertEqual(name, "M34-不存在")
        self.assertFalse(known)
        name2, known2 = LGS.resolve_name(_conn(), "")
        self.assertEqual(name2, "")
        self.assertFalse(known2)

    def test_13_pick_order_view(self):
        self._group("M34-P2", pick_seq=20)
        self._group("M34-P1", pick_seq=10)
        self._location("M34-L-P1", location_group="M34-P1")
        po = self._get("/api/location-groups/pick-order")
        codes = [g["code"] for g in po["groups"] if g["code"].startswith("M34-P")]
        self.assertEqual(codes, ["M34-P1", "M34-P2"])
        g1 = [g for g in po["groups"] if g["code"] == "M34-P1"][0]
        self.assertEqual(g1["location_count"], 1)
        self.assertEqual(g1["locations"][0]["code"], "M34-L-P1")

    def test_14_list_filters(self):
        self._group("M34-L1", name="甲组")
        self._group("M34-L2", name="乙组", status="inactive")
        d = self._get("/api/location-groups?keyword=M34-L1")
        self.assertEqual(d["total"], 1)
        d2 = self._get("/api/location-groups?status=inactive")
        self.assertEqual(d2["total"], 1)
        d3 = self._get("/api/location-groups?page=1&page_size=1")
        self.assertEqual(len(d3["location_groups"]), 1)
        bad = self._get("/api/location-groups?page=abc", status=400)
        self.assertEqual(bad["code"], "LOC_GROUP_BAD_PARAM")
        bad2 = self._get("/api/location-groups?status=nope", status=400)
        self.assertEqual(bad2["code"], "LOC_GROUP_BAD_PARAM")

    def test_15_fk_validation(self):
        bad = self._post("/api/location-groups", {"code": "M34-FK", "name": "x",
                                                  "warehouse_id": 999999},
                         status=400)
        self.assertIn("warehouse_id", bad["error"])
        bad2 = self._post("/api/location-groups", {"code": "M34-FK2", "name": "x",
                                                   "zone_id": 999999}, status=400)
        self.assertIn("zone_id", bad2["error"])

    def test_16_location_groups_endpoint_includes_legacy(self):
        self._location("M34-LG1", location_group="M34-组一")
        groups = self._get("/api/locations/groups")["groups"]
        self.assertIn("M34-组一", groups)

    def test_17_stocktake_dimension_prefers_group(self):
        """盘点维度：优先真实货位组，为空回落 zone 文本（老方案不丢匹配）。"""
        from stocktake_service import SCOPE_COLUMNS
        self.assertIn("COALESCE(NULLIF(l.location_group, '')",
                      SCOPE_COLUMNS["location_group"])
        self.assertIn("l.zone", SCOPE_COLUMNS["location_group"])

    def test_18_no_stock_side_effect(self):
        before = self._snapshot()
        g = self._group("M34-SIDE")
        self._location("M34-L-SIDE", location_group=g["code"])
        self._put("/api/location-groups/" + str(g["id"]), {"pick_seq": 3})
        after = self._snapshot()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
