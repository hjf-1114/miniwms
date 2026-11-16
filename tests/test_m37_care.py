"""M37 养护管理：计划 / 周期生成 / 温湿度阈值 / 异常只留痕 / 货主隔离。

共享物理库约定同 M36：裸连接 closing 包住；setUp 只清本模块表与必要的
owner / location / warehouse，不动别的里程碑数据；setUpClass 显式 init_db。
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


def _conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


class M37CareTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        with closing(_conn()) as c:
            c.execute("PRAGMA foreign_keys=OFF")
            for t in ("care_log", "care_task", "care_plan", "warehouse", "location", "owner"):
                try:
                    c.execute(f"DELETE FROM {t}")
                except sqlite3.OperationalError:
                    pass
            c.commit()
        self.owner = self._post("/api/owners", {
            "code": "M37-OW", "name": "M37 货主"})["owner"]["id"]
        self.past = (datetime.date.today() - datetime.timedelta(days=3)).isoformat()

    # ---------- 辅助 ----------
    def _post(self, path, payload, status=201):
        r = self.client.post(path, json=payload)
        self.assertEqual(r.status_code, status, (path, r.get_json()))
        return r.get_json()

    def _get(self, path):
        r = self.client.get(path)
        self.assertEqual(r.status_code, 200, (path, r.get_json()))
        return r.get_json()

    def _bad(self, path, payload, status=400):
        r = self.client.post(path, json=payload)
        self.assertEqual(r.status_code, status, (path, r.get_json()))
        return r.get_json()

    def _plan(self, **kw):
        payload = {"owner_id": self.owner, "project": C.CARE_PROJECT_TEMP,
                   "cycle": C.CARE_CYCLE_WEEKLY, "next_due_date": self.past,
                   "temp_min": 2, "temp_max": 8, "hum_min": 30, "hum_max": 70}
        payload.update(kw)
        return self._post("/api/care-plans", payload)

    def _generate(self):
        return self._post("/api/care-tasks/generate", {})

    def _tasks(self):
        return self._get("/api/care-tasks")["tasks"]

    def _execute(self, task_id, **kw):
        payload = {"result": C.CARE_RESULT_NORMAL, "temperature": 5, "humidity": 50}
        payload.update(kw)
        return self._post(f"/api/care-tasks/{task_id}/execute", payload, status=200)

    # ---------- 用例 ----------
    def test_01_dictionary(self):
        d = self._get("/api/care/dict")
        self.assertEqual(len(d["projects"]), 5)
        self.assertEqual(len(d["reasons"]), 8)
        self.assertEqual(d["cycle_days"]["weekly"], 7)
        self.assertEqual(len(d["permissions"]), 6)

    def test_02_plan_crud(self):
        plan = self._plan(note="GSP 冷链库")
        self.assertEqual(plan["status"], C.CARE_PLAN_ENABLED)
        self.assertEqual(plan["project_label"], "温湿度")
        self.assertTrue(plan["code"].startswith("CP-"))
        detail = self._post(f"/api/care-plans/{plan['id']}/update", {
            "cycle": C.CARE_CYCLE_CUSTOM, "cycle_days": 3,
            "temp_max": 10, "note": "改到 10 度"}, status=200)
        self.assertEqual(detail["cycle_days"], 3)
        self.assertEqual(detail["temp_max"], 10)
        self.assertEqual(detail["owner_id"], self.owner)
        off = self._post(f"/api/care-plans/{plan['id']}/toggle",
                         {"status": C.CARE_PLAN_DISABLED}, status=200)
        self.assertEqual(off["status"], C.CARE_PLAN_DISABLED)
        self.assertEqual(len(self._get(f"/api/care-plans/{plan['id']}")["tasks"]), 0)

    def test_03_plan_validation(self):
        self._bad("/api/care-plans", {"owner_id": self.owner, "project": "UNKNOWN"})
        self._bad("/api/care-plans", {"owner_id": self.owner,
                                      "project": C.CARE_PROJECT_TEMP, "cycle": "yearly"})
        self._bad("/api/care-plans", {"owner_id": self.owner,
                                      "project": C.CARE_PROJECT_TEMP,
                                      "next_due_date": "2026/11/16"})
        self._bad("/api/care-plans", {"owner_id": self.owner,
                                      "project": C.CARE_PROJECT_TEMP,
                                      "temp_min": 20, "temp_max": 5})
        self._bad("/api/care-plans", {"owner_id": self.owner,
                                      "project": C.CARE_PROJECT_TEMP,
                                      "cycle": C.CARE_CYCLE_CUSTOM})
        self._post("/api/care-plans", {"owner_id": 99999,
                                       "project": C.CARE_PROJECT_TEMP}, status=404)

    def test_04_generate_snapshot_and_idempotent(self):
        plan = self._plan()
        first = self._generate()
        self.assertEqual(first["generated"], 1)
        self.assertEqual(first["codes"][0][:3], "CT-")
        task = self._tasks()[0]
        self.assertEqual(task["plan_id"], plan["id"])
        self.assertEqual(task["temp_max"], 8)
        self.assertEqual(task["hum_min"], 30)
        self.assertEqual(task["status"], C.CARE_TASK_PENDING)
        self.assertEqual(task["due_date"], datetime.date.today().isoformat())
        second = self._generate()
        self.assertEqual(second["generated"], 0)
        self.assertEqual(len(self._tasks()), 1)
        new_due = self._get(f"/api/care-plans/{plan['id']}")["next_due_date"]
        self.assertGreater(new_due, datetime.date.today().isoformat())

    def test_05_generate_specific_plan_disabled(self):
        plan = self._plan(status=C.CARE_PLAN_DISABLED)
        self._bad("/api/care-tasks/generate", {"plan_id": plan["id"]})
        self._post(f"/api/care-plans/{plan['id']}/toggle",
                   {"status": C.CARE_PLAN_ENABLED}, status=200)
        gen = self._post("/api/care-tasks/generate", {"plan_id": plan["id"]})
        self.assertEqual(gen["generated"], 1)

    def test_06_location_snapshot(self):
        loc = self._post("/api/locations", {
            "code": "M37-LOC", "name": "M37 库位", "loc_type": "storage"})["location"]["id"]
        plan = self._plan(location_id=loc)
        self.assertEqual(plan["location_code"], "M37-LOC")
        self._generate()
        self.assertEqual(self._tasks()[0]["location_id"], loc)

    def test_07_execute_normal(self):
        self._plan()
        self._generate()
        task = self._tasks()[0]
        done = self._execute(task["id"])
        self.assertEqual(done["status"], C.CARE_TASK_DONE)
        self.assertEqual(done["result"], C.CARE_RESULT_NORMAL)
        self.assertEqual(done["logs"][-1]["action"], C.CARE_LOG_CHECKED)
        self.assertEqual(done["temperature"], 5)

    def test_08_threshold_force_abnormal(self):
        self._plan()
        self._generate()
        task_id = self._tasks()[0]["id"]
        err = self._post(f"/api/care-tasks/{task_id}/execute",
                         {"result": C.CARE_RESULT_NORMAL, "temperature": 20,
                          "humidity": 50}, status=400)
        self.assertEqual(err["code"], "THRESHOLD_EXCEEDED")
        self.assertEqual(self._get(f"/api/care-tasks/{task_id}")["status"],
                         C.CARE_TASK_PENDING)
        done = self._execute(task_id, result=C.CARE_RESULT_ABNORMAL, temperature=20,
                             reason_code=C.CARE_REASON_TEMP_HIGH, note="冷库温控异常")
        self.assertEqual(done["status"], C.CARE_TASK_ABNORMAL)
        self.assertEqual(done["logs"][-1]["reason_label"], "温度超限（偏高）")

    def test_09_abnormal_reason_required(self):
        self._plan()
        self._generate()
        task_id = self._tasks()[0]["id"]
        err = self._bad(f"/api/care-tasks/{task_id}/execute",
                        {"result": C.CARE_RESULT_ABNORMAL, "temperature": 20,
                         "humidity": 50})
        self.assertEqual(err["code"], "REASON_REQUIRED")
        plan2 = self._post("/api/care-plans", {
            "owner_id": self.owner, "project": C.CARE_PROJECT_EXPIRY,
            "cycle": C.CARE_CYCLE_DAILY, "next_due_date": self.past})
        self._post("/api/care-tasks/generate", {"plan_id": plan2["id"]})
        t = [x for x in self._tasks() if x["plan_id"] == plan2["id"]][0]
        err2 = self._bad(f"/api/care-tasks/{t['id']}/execute",
                         {"result": C.CARE_RESULT_ABNORMAL, "reason_code": "NOPE"})
        self.assertIn("非法", err2["error"])

    def test_10_temperature_required_for_temp_project(self):
        self._plan()
        self._generate()
        task_id = self._tasks()[0]["id"]
        err = self._post(f"/api/care-tasks/{task_id}/execute",
                         {"result": C.CARE_RESULT_NORMAL}, status=400)
        self.assertIn("必填", err["error"])

    def test_11_handle_abnormal(self):
        self._plan()
        self._generate()
        task_id = self._tasks()[0]["id"]
        self._execute(task_id, result=C.CARE_RESULT_ABNORMAL, temperature=20,
                      reason_code=C.CARE_REASON_TEMP_HIGH)
        handled = self._post(f"/api/care-tasks/{task_id}/handle",
                             {"handled_note": "已联系工程部调整温控"}, status=200)
        self.assertEqual(handled["status"], C.CARE_TASK_HANDLED)
        self.assertEqual(handled["handler"], "")
        self.assertEqual(handled["logs"][-1]["action"], C.CARE_LOG_HANDLED)

    def test_12_handle_guard(self):
        self._plan()
        self._generate()
        task_id = self._tasks()[0]["id"]
        self._bad(f"/api/care-tasks/{task_id}/handle", {"handled_note": "未异常"})
        self._execute(task_id)
        self._bad(f"/api/care-tasks/{task_id}/handle", {"handled_note": "已完成"})
        err = self._post(f"/api/care-tasks/{task_id}/execute",
                         {"result": C.CARE_RESULT_NORMAL}, status=400)
        self.assertEqual(err["code"], "TASK_CLOSED")

    def test_13_cancel_task(self):
        self._plan()
        self._generate()
        task_id = self._tasks()[0]["id"]
        cancelled = self._post(f"/api/care-tasks/{task_id}/cancel", {}, status=200)
        self.assertEqual(cancelled["status"], C.CARE_TASK_CANCELLED)
        self.assertEqual(cancelled["logs"][-1]["action"], C.CARE_LOG_CANCELLED)

    def test_14_abnormal_never_touches_inventory(self):
        self._plan()
        self._generate()
        with closing(_conn()) as c:
            before = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                      for t in ("stock", "stock_movement", "stock_adjustment", "dn")}
        task_id = self._tasks()[0]["id"]
        self._execute(task_id, result=C.CARE_RESULT_ABNORMAL, temperature=20,
                      reason_code=C.CARE_REASON_TEMP_HIGH)
        self._post(f"/api/care-tasks/{task_id}/handle",
                   {"handled_note": "现场纠正"}, status=200)
        with closing(_conn()) as c:
            after = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                     for t in before}
        self.assertEqual(before, after)

    def test_15_logs_and_filters(self):
        self._plan()
        self._generate()
        task_id = self._tasks()[0]["id"]
        self._execute(task_id, result=C.CARE_RESULT_ABNORMAL, temperature=20,
                      reason_code=C.CARE_REASON_TEMP_HIGH)
        self._post(f"/api/care-tasks/{task_id}/handle", {"handled_note": "已处置"}, status=200)
        logs = self._get("/api/care/logs")
        self.assertEqual(logs["total"], 3)
        self.assertEqual(logs["stats"][C.CARE_LOG_GENERATED], 1)
        self.assertEqual(len(self._get(f"/api/care/logs?action={C.CARE_LOG_HANDLED}")["logs"]), 1)
        ov = self._get("/api/care/overview")
        self.assertEqual(ov["tasks"][C.CARE_TASK_HANDLED], 1)
        self.assertEqual(ov["plans"][C.CARE_PLAN_ENABLED], 1)

    def test_16_owner_scope_isolation(self):
        plan = self._plan()
        self._generate()
        task = self._tasks()[0]
        other = self._post("/api/owners", {
            "code": "M37-OW2", "name": "M37 货主二"})["owner"]["id"]
        with main.app.test_request_context():
            g.scope = {"owner_all": False, "owner_ids": [other]}
            where, params = [], []
            S.scoped_where(where, params, table="p")
            with closing(_conn()) as c:
                sql = "SELECT COUNT(*) AS n FROM care_plan p WHERE " + " AND ".join(where)
                self.assertEqual(c.execute(sql, params).fetchone()["n"], 0)
                where2, params2 = [], []
                S.scoped_where(where2, params2, table="t")
                sql2 = "SELECT COUNT(*) AS n FROM care_task t WHERE " + " AND ".join(where2)
                self.assertEqual(c.execute(sql2, params2).fetchone()["n"], 0)
                where3, params3 = [], []
                S.scoped_where(where3, params3, table="l")
                sql3 = "SELECT COUNT(*) AS n FROM care_log l WHERE " + " AND ".join(where3)
                self.assertEqual(c.execute(sql3, params3).fetchone()["n"], 0)
            with self.assertRaises(S.ScopeError):
                S.assert_owner_allowed(g.scope, plan["owner_id"])
            with self.assertRaises(S.ScopeError):
                S.assert_owner_allowed(g.scope, task["owner_id"])

    def test_17_old_db_migration_idempotent(self):
        main.init_db()
        main.init_db()
        with closing(_conn()) as c:
            for t in ("care_plan", "care_task", "care_log"):
                cols = {r["name"] for r in c.execute(f"PRAGMA table_info({t})")}
                self.assertIn("owner_id", cols)
                self.assertTrue(c.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
                    (t,)).fetchone()[0])

    def test_18_plan_list_pagination(self):
        for i in range(3):
            self._plan(code=None, note=f"计划 {i}")
        result = self._get("/api/care-plans?page=1&page_size=2")
        self.assertEqual(result["total"], 3)
        self.assertEqual(len(result["plans"]), 2)
        self.assertEqual(result["plans"][0]["project_label"], "温湿度")
        self.assertEqual(len(self._get("/api/care-plans?status=disabled")["plans"]), 0)


if __name__ == "__main__":
    unittest.main()
