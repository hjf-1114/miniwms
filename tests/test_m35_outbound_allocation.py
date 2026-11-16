"""M35 出库波次分配 + FEFO：批次级分配 / 跳批留痕 / 拆批 / 规则复用 / 发货一致性。

验收口径来自用户 2026-11-10 3A：轻波次、FEFO、先专属后通用、强管控批次维度、
允许拆批与部分分配、跳批留痕、不合格冻结不可分配；以及 3A 的「与 M25/M26 共用
putaway_rule 表（rule_type='outbound'）」。存疑项见 docs/OPEN_QUESTIONS.md Q01–Q09。

共享物理库：裸连接一律 closing 包住；setUp 先清子表再清主表，重建储区/规则种子，
并把 putaway 规则行停用（M35 单测造库存要走上架过账，避免 M25 limits 拦截）。
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
import putaway_rules as PR  # noqa: E402

DB = os.environ["MINIWMS_DB"]


def _conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


class M35OutboundAllocationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        with closing(_conn()) as c:
            c.execute("PRAGMA foreign_keys=OFF")
            for t in ("outbound_allocation_log", "customer_reservation_log",
                      "putaway_recommendation", "putaway_rule", "putaway_rule_set",
                      "product_location", "batch_location", "stock_movement",
                      "stock_batch", "stock", "batch_receipt", "batch",
                      "dn_pick", "dn_line", "dn", "asn_line", "asn",
                      "product", "location", "zone", "owner", "warehouse", "customer"):
                try:
                    c.execute(f"DELETE FROM {t}")
                except sqlite3.OperationalError:
                    pass
            c.commit()
            main._seed_zones(c)
            PR.seed_rules(c)
            # 上架过账不拦截（本类只测出库分配）；outbound 规则由测试按需创建
            c.execute("UPDATE putaway_rule SET enabled = 0 WHERE rule_type = 'putaway'")
            c.commit()

        self.owner = self._post("/api/owners", {
            "code": "M35-OW", "name": "M35 货主"})["owner"]["id"]
        self.wh = self._post("/api/warehouses", {
            "code": "M35-WH", "name": "M35 仓"})["warehouse"]["id"]
        self.product = self._post("/api/products", {
            "sku": "M35-SKU", "name": "M35 物料", "owner_id": self.owner})["product"]["id"]
        self.cust = self._post("/api/customers", {
            "code": "M35-C", "name": "M35 客户", "owner_id": self.owner})["customer"]["id"]
        self.other_cust = self._post("/api/customers", {
            "code": "M35-C2", "name": "M35 客户二", "owner_id": self.owner})["customer"]["id"]
        self.recv = self._loc("M35-RECV", loc_type="receiving")
        self.loc = self._loc("M35-A")

    # ---------- 辅助 ----------
    def _post(self, path, payload, status=201):
        r = self.client.post(path, json=payload)
        self.assertEqual(r.status_code, status, (path, r.get_json()))
        return r.get_json()

    def _get(self, path):
        r = self.client.get(path)
        self.assertEqual(r.status_code, 200, (path, r.get_json()))
        return r.get_json()

    def _loc(self, code, **kw):
        payload = {"code": code, "name": code, "loc_type": "storage"}
        payload.update(kw)
        return self._post("/api/locations", payload)["location"]["id"]

    def _stock(self, tag, qty, *, expiry="", goods="合格", status="", dcid=None, loc=None):
        """走真实 ASN 收货 → 上架，造一条带完整维度的 stock_batch 明细。"""
        body = self._post("/api/asns", {
            "code": f"M35-ASN-{tag}", "owner_id": self.owner,
            "source_warehouse": self.wh, "doc_type": "采购",
            "lines": [{"product_id": self.product, "expected_qty": qty,
                       "batch_no": f"LOT-{tag}", "expiry_date": expiry,
                       "goods_property": goods, "designated_customer_id": dcid,
                       "external_batch_no": "/"}],
        })
        asn_id, line_id = body["asn"]["id"], body["lines"][0]["id"]
        self._post(f"/api/asns/{asn_id}/receive", {
            "line_id": line_id, "location_id": self.recv, "qty": qty,
            "stock_status": status}, status=200)
        self._post(f"/api/asns/{asn_id}/putaway", {
            "line_id": line_id, "from_location_id": self.recv,
            "to_location_id": loc or self.loc, "qty": qty}, status=200)

    def _dn(self, qty, customer=None, code="M35-DN"):
        body = self._post("/api/dns", {
            "code": code, "owner_id": self.owner,
            "customer_id": self.cust if customer is None else customer,
            "source_warehouse": self.wh,
            "lines": [{"product_id": self.product, "ordered_qty": qty}],
        })
        return body["dn"]["id"], body["lines"][0]["id"]

    def _plan(self, dn_ids, **kw):
        payload = dict(dn_ids=dn_ids)
        payload.update(kw)
        return self._post("/api/outbound/plan", payload, status=200)

    def _allocate(self, dn_ids, **kw):
        payload = dict(dn_ids=dn_ids)
        payload.update(kw)
        return self._post("/api/outbound/allocate", payload)

    def _pick_rows(self, dn_id):
        with closing(_conn()) as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM dn_pick WHERE dn_id = ? ORDER BY id", (dn_id,))]

    def _invariants(self):
        self.assertTrue(self._get("/api/stock/reconcile")["consistent"], "I1 不一致")
        self.assertTrue(self._get("/api/stock/batch-reconcile")["consistent"], "I2/I3 不一致")

    # ---------- DDL / 字典 / 参数 ----------
    def test_01_ddl_and_dictionary(self):
        with closing(_conn()) as c:
            names = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("outbound_allocation_log", names)
            cols = {r["name"] for r in c.execute("PRAGMA table_info(dn_pick)")}
            self.assertTrue({"batch_no", "batch_key", "designated_customer_id",
                             "goods_property", "stock_status", "expiry_date",
                             "production_date", "wave_no", "rule_id"} <= cols)
            self.assertIn("wave_no", {r["name"] for r in c.execute("PRAGMA table_info(dn)")})
        d = self._get("/api/outbound/dict")
        self.assertEqual([x["value"] for x in d["strategies"]], list(C.OUTBOUND_STRATEGIES))
        self.assertEqual(set(C.OUTBOUND_SKIP_CODES),
                         {x["value"] for x in d["skip_reasons"]})
        self.assertIn("outbound_allocation:allocate", d["permissions"])

    def test_02_param_validation(self):
        r = self.client.post("/api/outbound/plan", json={"dn_ids": []})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/outbound/allocate", json={"dn_ids": [999999]})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/outbound/allocate",
                             json={"dn_ids": [1], "strategy": "bad"})
        self.assertEqual(r.status_code, 400)
        r = self.client.get("/api/outbound/logs?page=abc")
        self.assertEqual(r.status_code, 400)

    # ---------- 试算 ----------
    def test_03_plan_is_read_only(self):
        self._stock("A", 10, expiry="2026-01-01")
        dn_id, _ = self._dn(4)
        before = self._get("/api/outbound/logs")["total"]
        d = self._plan([dn_id])
        self.assertTrue(d["dry_run"])
        self.assertEqual(d["totals"]["allocated_qty"], 4)
        self.assertEqual(self._get("/api/outbound/logs")["total"], before)
        self.assertEqual(self._pick_rows(dn_id), [])
        with closing(_conn()) as c:
            self.assertEqual(c.execute(
                "SELECT SUM(allocated_qty) FROM stock_batch").fetchone()[0], 0)
        self._invariants()

    def test_04_fefo_order(self):
        self._stock("EARLY", 3, expiry="2026-01-01")
        self._stock("LATE", 3, expiry="2026-12-01")
        dn_id, _ = self._dn(4)
        d = self._plan([dn_id])
        # 批号由验收登记生成 BAT-YYYYMMDD-NNN，按效期取证更贴近业务
        self.assertEqual([a["expiry_date"] for a in d["allocations"]],
                         ["2026-01-01", "2026-12-01"], "应按效期升序（FEFO）")
        self.assertEqual([a["qty"] for a in d["allocations"]], [3, 1])

    # ---------- 质量 / 专属红线 ----------
    def test_05_quality_and_status_skipped(self):
        self._stock("OK", 8, expiry="2026-03-01")
        self._stock("NG", 5, expiry="2026-02-01", goods="不合格")
        self._stock("FROZEN", 5, expiry="2026-02-15", status="冻结")
        dn_id, _ = self._dn(6)
        d = self._plan([dn_id])
        self.assertEqual(len(d["allocations"]), 1)
        self.assertEqual(d["allocations"][0]["goods_property"], "合格")
        self.assertEqual(d["allocations"][0]["stock_status"], "")
        codes = {s["reason_code"] for s in d["skipped"]}
        self.assertIn(C.OUTBOUND_SKIP_QUALITY, codes)
        self.assertIn(C.OUTBOUND_SKIP_STATUS, codes)
        out = self._allocate([dn_id])
        logs = self._get(f"/api/outbound/logs?dn_id={dn_id}&action=skipped")
        self.assertEqual(logs["total"], 2, "跳批必须留痕")
        self._invariants()

    def test_06_reservation_own_first_and_skip(self):
        self._stock("COMMON", 5, expiry="2026-01-01")
        self._stock("OWN", 5, expiry="2026-06-01", dcid=self.cust)
        self._stock("OTHER", 5, expiry="2026-02-01", dcid=self.other_cust)
        dn_id, _ = self._dn(7)
        d = self._plan([dn_id])
        alloc = d["allocations"]
        self.assertEqual(alloc[0]["designated_customer_id"], self.cust, "专属优先")
        self.assertIsNone(alloc[1]["designated_customer_id"])
        self.assertTrue(any(s["reason_code"] == C.OUTBOUND_SKIP_RESERVED
                            for s in d["skipped"]))
        strict = self._plan([dn_id], strategy=C.OUTBOUND_STRATEGY_STRICT_FEFO)
        self.assertEqual([a["expiry_date"] for a in strict["allocations"]],
                         ["2026-01-01", "2026-06-01"], "严格 FEFO 忽略专属分组")

    # ---------- 拆批 / 部分分配 ----------
    def test_07_split_and_shortage(self):
        self._stock("A", 2, expiry="2026-01-01")
        self._stock("B", 2, expiry="2026-02-01")
        dn_id, _ = self._dn(10)
        d = self._allocate([dn_id])
        self.assertEqual(len(d["allocations"]), 2, "允许拆批")
        self.assertEqual(d["totals"]["allocated_qty"], 4)
        self.assertEqual(len(d["shortages"]), 1)
        self.assertEqual(d["shortages"][0]["short_qty"], 6)
        self.assertEqual(self._get(f"/api/outbound/logs?dn_id={dn_id}&action=short")["total"], 1)
        with closing(_conn()) as c:
            line = c.execute("SELECT picked_qty FROM dn_line WHERE dn_id = ?",
                             (dn_id,)).fetchone()
            self.assertEqual(line["picked_qty"], 4)
            self.assertEqual(c.execute("SELECT status FROM dn WHERE id = ?",
                                       (dn_id,)).fetchone()[0], C.DN_PICKING)
        self._invariants()

    # ---------- 实分配 / 波次 / 日志 ----------
    def test_08_allocate_writes_batch_dims_and_wave(self):
        self._stock("A", 5, expiry="2026-01-01")
        dn_id, _ = self._dn(3)
        out = self._allocate([dn_id])
        self.assertTrue(out["wave_no"].startswith(C.WAVE_PREFIX + "-"))
        picks = self._pick_rows(dn_id)
        self.assertEqual(len(picks), 1)
        self.assertTrue(picks[0]["batch_no"].startswith("BAT-"))
        self.assertEqual(picks[0]["expiry_date"], "2026-01-01")
        self.assertEqual(picks[0]["wave_no"], out["wave_no"])
        self.assertEqual(picks[0]["qty"], 3)
        with closing(_conn()) as c:
            # 上架后暂存位保留 qty=0 的零行明细，取证要按存储位过滤
            sb = c.execute("SELECT qty, allocated_qty FROM stock_batch "
                           "WHERE location_id = ? AND qty > 0", (self.loc,)).fetchone()
            self.assertEqual(sb["qty"], 5)
            self.assertEqual(sb["allocated_qty"], 3, "分配只占 allocated_qty，不改在手")
            self.assertEqual(c.execute("SELECT wave_no FROM dn WHERE id = ?",
                                       (dn_id,)).fetchone()[0], out["wave_no"])
        waves = self._get("/api/outbound/waves")["waves"]
        self.assertEqual(waves[0]["wave_no"], out["wave_no"])
        self.assertEqual(waves[0]["dn_count"], 1)
        logs = self._get(f"/api/outbound/logs?wave_no={out['wave_no']}")
        self.assertEqual(logs["stats"].get("allocated"), 1)
        self._invariants()

    # ---------- 规则复用 ----------
    def test_09_outbound_rule_hit_is_explained(self):
        self._stock("A", 5, expiry="2026-01-01")
        sets = self._get("/api/putaway-rule-sets")["rule_sets"]
        sid = sets[0]["id"]
        self._post("/api/putaway-rules", {
            "rule_set_id": sid, "rule_type": C.RULE_TYPE_OUTBOUND,
            "priority": 40, "name": "M35 出库默认", "condition": {"doc_type": "销售"},
            "steps": [{"type": "all_active"}], "limits": {"quality_state": {"enabled": 1}},
            "score": {"R11": 15, "strategy": C.OUTBOUND_STRATEGY_STRICT_FEFO},
            "enabled": 1,
        })
        dn_id, _ = self._dn(2)
        d = self._plan([dn_id])
        self.assertEqual(d["allocations"][0]["engine"], C.OUTBOUND_ENGINE_RULES)
        self.assertIsNotNone(d["allocations"][0]["rule_id"])
        out = self._allocate([dn_id])
        logs = self._get(f"/api/outbound/logs?dn_id={dn_id}&action=allocated")["logs"]
        self.assertEqual(logs[0]["engine"], C.OUTBOUND_ENGINE_RULES)
        self.assertIsNotNone(logs[0]["rule_priority"])
        self._invariants()

    # ---------- 发货按分配批次扣减 ----------
    def test_10_ship_uses_recorded_batch(self):
        self._stock("EARLY", 3, expiry="2026-01-01")
        self._stock("LATE", 3, expiry="2026-12-01")
        dn_id, _ = self._dn(4)
        self._allocate([dn_id])
        r = self.client.post(f"/api/dns/{dn_id}/ship", json={})
        self.assertEqual(r.status_code, 200, r.get_json())
        with closing(_conn()) as c:
            rows = [dict(x) for x in c.execute(
                "SELECT expiry_date, qty, batch_no FROM stock_batch "
                "WHERE location_id = ? ORDER BY expiry_date", (self.loc,))]
            self.assertEqual(rows[0]["expiry_date"], "2026-01-01")
            self.assertEqual(rows[0]["qty"], 0, "早效期批次应先扣完")
            self.assertEqual(rows[1]["qty"], 2)
            self.assertEqual(c.execute("SELECT SUM(allocated_qty) FROM stock_batch")
                             .fetchone()[0], 0, "发货同步释放分配")
        self._invariants()

    # ---------- 取消分配 ----------
    def test_11_unallocate_releases_symmetrically(self):
        self._stock("A", 5, expiry="2026-01-01")
        dn_id, _ = self._dn(3)
        self._allocate([dn_id])
        picks = self._pick_rows(dn_id)
        out = self._post("/api/outbound/unallocate",
                         {"dn_id": dn_id, "pick_ids": [picks[0]["id"]]}, status=200)
        self.assertEqual(out["released_qty"], 3)
        self.assertEqual(out["status"], C.DN_OPEN)
        self.assertEqual(self._pick_rows(dn_id), [])
        with closing(_conn()) as c:
            self.assertEqual(c.execute("SELECT SUM(allocated_qty) FROM stock_batch")
                             .fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT SUM(qty) FROM stock_batch")
                             .fetchone()[0], 5)
        self.assertEqual(self._get(f"/api/outbound/logs?dn_id={dn_id}&action=unallocated")["total"], 1)
        self._invariants()

    # ---------- 老链路回归 ----------
    def test_12_manual_pick_still_works(self):
        self._stock("A", 5, expiry="2026-01-01")
        dn_id, line_id = self._dn(2, code="M35-DN-OLD")
        r = self.client.post(f"/api/dns/{dn_id}/pick", json={
            "line_id": line_id, "location_id": self.loc, "qty": 2})
        self.assertEqual(r.status_code, 200, r.get_json())
        picks = self._pick_rows(dn_id)
        self.assertEqual(picks[0]["batch_no"], "", "人工拣货老行不带批次维度")
        r = self.client.post(f"/api/dns/{dn_id}/ship", json={})
        self.assertEqual(r.status_code, 200, r.get_json())
        self._invariants()

    def test_13_shipped_dn_cannot_allocate(self):
        self._stock("A", 5, expiry="2026-01-01")
        dn_id, line_id = self._dn(2)
        self.client.post(f"/api/dns/{dn_id}/pick", json={
            "line_id": line_id, "location_id": self.loc, "qty": 2})
        self.client.post(f"/api/dns/{dn_id}/ship", json={})
        r = self.client.post("/api/outbound/allocate", json={"dn_ids": [dn_id]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("不可分配", r.get_json()["error"])

    def test_14_legacy_db_migration(self):
        """老库 dn_pick 只有 5 列且已有数据：M35 迁移必须成功。

        SQLite 的 ALTER TABLE ADD COLUMN 不允许函数型默认值
        （DEFAULT (datetime(...))），新库空表可能侥幸通过，老库有数据会直接报错。
        """
        path = os.path.join(tempfile.gettempdir(), "miniwms_m35_legacy.db")
        if os.path.exists(path):
            os.remove(path)
        try:
            with closing(sqlite3.connect(path)) as c:
                c.row_factory = sqlite3.Row
                c.execute("CREATE TABLE dn (id INTEGER PRIMARY KEY)")
                c.execute("""CREATE TABLE dn_pick (
                    id INTEGER PRIMARY KEY, dn_id INTEGER, line_id INTEGER,
                    location_id INTEGER, qty REAL)""")
                c.execute("INSERT INTO dn VALUES (1)")
                c.execute("INSERT INTO dn_pick (dn_id, line_id, location_id, qty) "
                          "VALUES (1, 1, 1, 2)")
                c.commit()
                main._migrate_m35(c)
                c.commit()
                cols = {r["name"] for r in c.execute("PRAGMA table_info(dn_pick)")}
                self.assertTrue({"batch_no", "batch_key", "expiry_date", "wave_no",
                                 "rule_id"} <= cols)
                self.assertIn("wave_no", {r["name"] for r in
                                          c.execute("PRAGMA table_info(dn)")})
                self.assertEqual(c.execute("SELECT COUNT(*) FROM dn_pick")
                                 .fetchone()[0], 1, "历史拣货行不丢")
                self.assertEqual(dict(c.execute("SELECT * FROM dn_pick").fetchone())["qty"], 2)
                self.assertEqual(c.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                    "AND name='outbound_allocation_log'").fetchone()[0], 1)
        finally:
            if os.path.exists(path):
                os.remove(path)


if __name__ == "__main__":
    unittest.main()
