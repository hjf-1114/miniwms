"""M25 上架推荐规则引擎：配置化硬约束 / 条件组 / fallback / 可解释输出 / 拦截。

对照 ``docs/PUTAWAY_RECOMMENDATION.md`` §14 阶段 E 验收标准与
用户 2026-11-10 的 1A / 2A / 3A 口径。
"""
import json
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


class M25PutawayRulesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_db()
        cls.client = main.app.test_client()

    def setUp(self):
        c = _conn()
        c.execute("PRAGMA foreign_keys=OFF")
        for t in ("putaway_recommendation", "putaway_rule", "putaway_rule_set",
                  "product_location", "batch_location", "stock_movement",
                  "stock_batch", "stock", "batch_receipt", "batch",
                  "asn_line", "asn", "product", "location", "zone", "owner",
                  "warehouse", "customer"):
            try:
                c.execute(f"DELETE FROM {t}")
            except sqlite3.OperationalError:
                pass
        c.commit()
        main._seed_zones(c)      # setUp 清掉了 zone，补回种子储区供 zone_by_code 使用
        PR.seed_rules(c)
        c.commit()
        c.close()

        self.owner = self._post("/api/owners", {"code": "M25-OW", "name": "M25 货主"})["owner"]["id"]
        self.wh = self._post("/api/warehouses", {"code": "M25-WH", "name": "M25 仓"})["warehouse"]["id"]
        self.product = self._post("/api/products", {
            "sku": "M25-SKU", "name": "M25 物料", "owner_id": self.owner,
        })["product"]["id"]
        body = self._post("/api/asns", {
            "code": "M25-ASN", "owner_id": self.owner, "source_warehouse": self.wh,
            "lines": [{"product_id": self.product, "expected_qty": 10, "batch_no": "LOT-A"}],
        })
        self.asn_id = body["asn"]["id"]
        self.line_id = body["lines"][0]["id"]
        self.loc = self._loc("M25-A")

    def _post(self, path, payload):
        r = self.client.post(path, json=payload)
        self.assertEqual(r.status_code, 201, (path, r.get_json()))
        return r.get_json()

    def _loc(self, code, **kw):
        payload = {"code": code, "name": code, "loc_type": "storage"}
        payload.update(kw)
        return self._post("/api/locations", payload)["location"]["id"]

    def _rules(self, where="", params=()):
        c = _conn()
        try:
            return [dict(r) for r in c.execute(
                "SELECT * FROM putaway_rule " + where + " ORDER BY priority", params)]
        finally:
            c.close()

    def _set_flags(self, **flags):
        c = _conn()
        cols = ", ".join(f"{k} = ?" for k in flags)
        c.execute(f"UPDATE asn SET {cols} WHERE id = ?",
                  list(flags.values()) + [self.asn_id])
        c.commit()
        c.close()

    def _rec(self, qty=1):
        r = self.client.get(
            f"/api/putaway/recommend?asn_id={self.asn_id}&line_id={self.line_id}&qty={qty}")
        self.assertEqual(r.status_code, 200, r.get_json())
        return r.get_json()

    # ---------- DDL / 常量 / 种子 ----------
    def test_01_three_tables_and_indexes(self):
        c = _conn()
        try:
            names = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"putaway_rule_set", "putaway_rule",
                             "putaway_recommendation"} <= names)
            idx = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='index'")}
            self.assertTrue({"idx_prs_status", "idx_pr_set", "idx_pr_type",
                             "idx_prec_asn", "idx_prec_action"} <= idx)
            cols = {r[1] for r in c.execute("PRAGMA table_info(putaway_rule)")}
            self.assertTrue({"rule_type", "condition", "steps", "limits",
                             "score", "priority", "enabled"} <= cols)
        finally:
            c.close()

    def test_02_constant_dimensions(self):
        self.assertEqual(len(C.PUTAWAY_CONDITION_DIMS), 13)
        self.assertEqual(len(C.PUTAWAY_LIMIT_KEYS), 8)
        self.assertEqual(len(C.PUTAWAY_SCORE_RULES), 9)
        # 8 种预设寻址 + 1 种 M20 兼容型
        self.assertEqual(len(C.PUTAWAY_STEP_TYPES), 9)
        self.assertIn("all_active", C.PUTAWAY_STEP_TYPES)
        self.assertEqual(C.RULE_TYPES, ("putaway", "outbound"))

    def test_03_seed_11_rows_7_enabled(self):
        rows = self._rules()
        self.assertEqual(len(rows), 11)
        self.assertEqual(sum(r["enabled"] for r in rows), 7)
        self.assertEqual(sorted(r["priority"] for r in rows if r["enabled"]),
                         [5, 8, 10, 20, 25, 50, 999])
        self.assertTrue(all(r["rule_type"] == C.RULE_TYPE_PUTAWAY for r in rows))

    def test_04_seed_is_idempotent_and_does_not_resurrect_deleted_row(self):
        c = _conn()
        c.execute("DELETE FROM putaway_rule WHERE priority = 5")
        c.commit()
        c.close()
        with closing(_conn()) as c:
            self.assertEqual(PR.seed_rules(c), self._rules(
                "WHERE priority = 999")[0]["rule_set_id"])
        self.assertEqual(len(self._rules()), 10)
        self.assertFalse(any(r["priority"] == 5 for r in self._rules()))

    def test_05_normal_rule_limits_are_config_not_code(self):
        r = [x for x in self._rules() if x["priority"] == 50][0]
        lim = json.loads(r["limits"])
        self.assertEqual(set(lim), set(C.PUTAWAY_LIMIT_KEYS))
        enabled = {k for k, v in lim.items() if v.get("enabled")}
        # 种子保守默认：M20 的三类混放只排除推荐候选、从不在过账时拦截，
        # 若上线即全开会让老演示数据大面积 400。能力保留在配置，M26 编辑器按仓开启。
        self.assertEqual(enabled, {"loc_status", "storage_class", "capacity"})
        self.assertFalse(lim["mix_product"]["enabled"])
        self.assertFalse(lim["mix_owner"]["enabled"])
        # M20 口径：混放只排除推荐候选，不在过账时拦截；M26 打开 enabled 即升级为硬拦截
        self.assertTrue(lim["mix_product"]["recommend_only"])
        self.assertTrue(lim["mix_owner"]["recommend_only"])
        # M21 明细层允许同库位多批次，批号混放连推荐阶段也不排除
        self.assertFalse(lim["mix_batch"]["enabled"])
        self.assertNotIn("recommend_only", lim["mix_batch"])
        self.assertTrue(lim["storage_class"]["temp_class_from_product"])

    def test_06_seed_steps_are_fallback_chain_with_compat_head(self):
        r = [x for x in self._rules() if x["priority"] == 50][0]
        types = [s["type"] for s in json.loads(r["steps"])]
        self.assertEqual(types[0], "all_active")
        self.assertEqual(types[1:], ["zone_by_product", "same_product",
                                     "same_category", "empty_in_zone"])
        cold = [x for x in self._rules() if x["priority"] == 5][0]
        self.assertEqual([s["type"] for s in json.loads(cold["steps"])],
                         ["zone_by_code", "empty_in_zone"])

    # ---------- 条件组 ----------
    def test_07_condition_null_is_wildcard(self):
        self.assertTrue(PR.matches({}, {"doc_type": "采购", "goods_property": "合格"}))
        self.assertTrue(PR.matches({"is_cold_chain": None}, {"is_cold_chain": 1}))
        self.assertTrue(PR.matches({"external_doc_type": ""}, {"external_doc_type": "X"}))

    def test_08_condition_dimensions_are_and(self):
        cond = {"is_cold_chain": 1, "goods_property": "合格", "doc_type": "采购"}
        self.assertTrue(PR.matches(cond, {"is_cold_chain": 1,
                                          "goods_property": "合格", "doc_type": "采购"}))
        self.assertFalse(PR.matches(cond, {"is_cold_chain": 0,
                                           "goods_property": "合格", "doc_type": "采购"}))

    def test_09_condition_list_value_is_multiselect(self):
        self.assertTrue(PR.matches({"goods_property": ["待检", "冻结"]},
                                   {"goods_property": "冻结"}))
        self.assertFalse(PR.matches({"goods_property": ["待检", "冻结"]},
                                    {"goods_property": "合格"}))

    def test_10_priority_first_hit_wins(self):
        self._set_flags(is_cold_chain=1, is_hazard=1)
        body = self._rec()
        self.assertEqual(body["hit_priority"], 5)
        self.assertEqual(body["rule_set"], C.RULE_SET_DEFAULT_CODE)
        self.assertIn("is_cold_chain", body["hit_condition"])

    def test_11_normal_asn_hits_rule_50(self):
        body = self._rec()
        self.assertEqual(body["hit_priority"], 50)
        self.assertEqual(body["engine"], "rules")

    def test_12_rule_set_resolution_precedence(self):
        c = _conn()
        sid = c.execute("SELECT id FROM putaway_rule_set WHERE code=?",
                        (C.RULE_SET_DEFAULT_CODE,)).fetchone()[0]
        c.execute("INSERT INTO putaway_rule_set (code, name, warehouse_id, owner_id, status) "
                  "VALUES ('M25-SPEC', '专属包', ?, ?, 'active')",
                  (self.wh, self.owner))
        c.commit()
        c.close()
        with closing(_conn()) as c:
            got = PR.resolve_rule_set(c, {"asn": {"source_warehouse": self.wh},
                                          "owner_id": self.owner})
        self.assertEqual(got["code"], "M25-SPEC")
        c = _conn()
        c.execute("DELETE FROM putaway_rule_set WHERE code='M25-SPEC'")
        c.commit()
        c.close()
        self.assertEqual(sid, sid)

    # ---------- 推荐 / 可解释输出 / 日志 ----------
    def test_13_recommend_is_explainable(self):
        body = self._rec()
        for key in ("rule_set", "hit_rule", "hit_priority", "hit_condition",
                    "hit_step", "step_name", "fallback", "top_n",
                    "recommendations", "rejected", "candidate_count", "engine"):
            self.assertIn(key, body)
        self.assertEqual(body["step_name"], "all_active")

    def test_14_recommend_keeps_m20_compat_fields(self):
        body = self._rec()
        self.assertIn("bound_location_count", body)
        first = body["recommendations"][0]
        for key in ("location_id", "code", "score", "reasons", "reason_labels",
                    "breakdown", "used_qty", "capacity", "effective_mix"):
            self.assertIn(key, first)
        self.assertIn("empty_location", first["reasons"])
        self.assertEqual(first["reason_labels"][first["reasons"].index("empty_location")],
                         C.PUTAWAY_REASON_LABELS[C.PUTAWAY_R_EMPTY])

    def test_15_recommendation_log_written(self):
        self._rec()
        c = _conn()
        try:
            n = c.execute("SELECT COUNT(*) FROM putaway_recommendation").fetchone()[0]
            row = c.execute("SELECT * FROM putaway_recommendation ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            c.close()
        self.assertGreaterEqual(n, 1)
        self.assertEqual(row["action"], "preview")
        self.assertEqual(row["asn_id"], self.asn_id)
        self.assertIsInstance(json.loads(row["top_json"]), list)

    def test_16_same_score_sorted_by_code(self):
        self._loc("M25-B")
        body = self._rec()
        codes = [r["code"] for r in body["recommendations"]]
        self.assertEqual(codes, sorted(codes))

    def test_17_score_extension_is_opt_in(self):
        c = _conn()
        c.execute("UPDATE putaway_rule SET score=? WHERE priority=50",
                  (json.dumps({"R09": 7}),))
        c.commit()
        c.close()
        c = _conn()
        c.execute("INSERT INTO stock (owner_id, product_id, location_id, qty, allocated_qty) "
                  "VALUES (?, ?, ?, 2, 0)", (self.owner, self.product, self.loc))
        c.commit()
        c.close()
        body = self._rec()
        self.assertIn("R09", body["recommendations"][0]["breakdown"])
        self.assertEqual(body["recommendations"][0]["breakdown"]["R09"], 7)

    # ---------- 配置端点 ----------
    def test_18_rule_types_dictionary(self):
        r = self.client.get("/api/putaway-rule-types")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual(len(d["condition_dims"]), 13)
        self.assertEqual(len(d["step_types"]), 9)
        self.assertEqual(len(d["limit_keys"]), 8)
        self.assertEqual(len(d["score_rules"]), 9)
        self.assertEqual(len(d["rule_types"]), 2)

    def test_19_create_set_and_rule_crud(self):
        sid = self.client.post("/api/putaway-rule-sets", json={
            "code": "M25-SET2", "name": "测试策略包"}).get_json()["id"]
        rid = self.client.post("/api/putaway-rules", json={
            "rule_set_id": sid, "rule_type": C.RULE_TYPE_OUTBOUND, "priority": 7,
            "name": "出库规则占位", "condition": {"doc_type": "销退"},
            "steps": [{"type": "empty_in_zone"}],
            "limits": {"capacity": {"enabled": True}},
        }).get_json()["id"]
        rows = self.client.get(f"/api/putaway-rules?set_id={sid}").get_json()["rules"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rule_type"], "outbound")
        up = self.client.put(f"/api/putaway-rules/{rid}", json={
            "id": rid, "rule_set_id": sid, "rule_type": C.RULE_TYPE_OUTBOUND,
            "priority": 9, "name": "出库规则占位", "enabled": 0})
        self.assertEqual(up.status_code, 200, up.get_json())
        self.assertEqual(self.client.delete(f"/api/putaway-rules/{rid}").status_code, 200)

    def test_20_outbound_rule_type_reuses_same_table(self):
        sid = self.client.get("/api/putaway-rule-sets").get_json()["rule_sets"][0]["id"]
        r = self.client.post("/api/putaway-rules", json={
            "rule_set_id": sid, "rule_type": "outbound", "priority": 3,
            "name": "M35 占位", "condition": {}, "steps": [{"type": "all_active"}],
            "limits": {}, "enabled": 0})
        self.assertEqual(r.status_code, 201, r.get_json())
        self.assertFalse(any(x["name"] == "M35 占位" and x["enabled"]
                             for x in self._rules()))

    def test_21_invalid_condition_dim_rejected(self):
        sid = self.client.get("/api/putaway-rule-sets").get_json()["rule_sets"][0]["id"]
        r = self.client.post("/api/putaway-rules", json={
            "rule_set_id": sid, "name": "坏规则", "condition": {"not_a_dim": 1}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("未知维度", r.get_json()["error"])

    def test_22_invalid_step_type_rejected(self):
        sid = self.client.get("/api/putaway-rule-sets").get_json()["rule_sets"][0]["id"]
        r = self.client.post("/api/putaway-rules", json={
            "rule_set_id": sid, "name": "坏步骤", "steps": [{"type": "teleport"}]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("未知寻址类型", r.get_json()["error"])

    def test_23_invalid_rule_type_rejected(self):
        sid = self.client.get("/api/putaway-rule-sets").get_json()["rule_sets"][0]["id"]
        r = self.client.post("/api/putaway-rules", json={
            "rule_set_id": sid, "name": "坏类型", "rule_type": "unknown"})
        self.assertEqual(r.status_code, 400)

    def test_24_trial_endpoint(self):
        r = self.client.post("/api/putaway-rules/trial", json={
            "condition": {"is_cold_chain": 1}, "ctx": {"is_cold_chain": 1}})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["matched"])
        r = self.client.post("/api/putaway-rules/trial", json={
            "condition": {"is_cold_chain": 1}, "ctx": {"is_cold_chain": 0}})
        self.assertFalse(r.get_json()["matched"])

    def test_25_recommendations_endpoint_and_stats(self):
        self._rec()
        r = self.client.get("/api/putaway-recommendations")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertGreaterEqual(d["total"], 1)
        self.assertIn("preview", d["stats"])

    # ---------- 拦截（2A 核心） ----------
    def test_26_cold_chain_putaway_blocked(self):
        self._set_flags(is_cold_chain=1)
        recv = self._loc("M25-RECV", loc_type="receiving")
        r = self.client.post(f"/api/asns/{self.asn_id}/receive", json={
            "line_id": self.line_id, "location_id": recv, "qty": 3})
        self.assertEqual(r.status_code, 200, r.get_json())
        r = self.client.post(f"/api/asns/{self.asn_id}/putaway", json={
            "line_id": self.line_id, "from_location_id": recv,
            "to_location_id": self.loc, "qty": 3})
        self.assertEqual(r.status_code, 400)
        self.assertIn("STORAGE_CLASS_MISMATCH", str(r.get_json()))

    def test_27_no_rule_no_block(self):
        self._set_flags(is_cold_chain=1)
        recv = self._loc("M25-RECV", loc_type="receiving")
        self.client.post(f"/api/asns/{self.asn_id}/receive", json={
            "line_id": self.line_id, "location_id": recv, "qty": 3})
        c = _conn()
        c.execute("DELETE FROM putaway_rule")
        c.commit()
        c.close()
        with closing(_conn()) as c:
            ok, code, msg = PR.validate_location(c, self.asn_id,
                                                 self.line_id, self.loc, 3)
        self.assertTrue(ok)
        self.assertEqual((code, msg), ("", ""))
        r = self.client.post(f"/api/asns/{self.asn_id}/putaway", json={
            "line_id": self.line_id, "from_location_id": recv,
            "to_location_id": self.loc, "qty": 3})
        self.assertEqual(r.status_code, 200, r.get_json())

    def test_28_constraints_are_not_bought_by_score(self):
        """命中规则行的 limits 是过滤，不是加权：高分库位照样被拒。"""
        self._set_flags(is_cold_chain=1)
        c = _conn()
        c.execute("INSERT INTO product_location (product_id, location_id, priority) "
                  "VALUES (?, ?, 999)", (self.product, self.loc))
        c.commit()
        c.close()
        with closing(_conn()) as c:
            ok, code, _ = PR.validate_location(c, self.asn_id,
                                               self.line_id, self.loc, 1)
        self.assertFalse(ok)
        self.assertEqual(code, "STORAGE_CLASS_MISMATCH")

    def test_29_enabling_mix_batch_limit_takes_effect(self):
        # 用 with closing 包住写事务：断言失败时连接仍会关闭并回滚，
        # 否则共享物理库的写锁会把后续所有测试模块的 init_db() 全部锁死。
        with closing(_conn()) as c:
            c.execute("INSERT INTO stock (owner_id, product_id, location_id, qty, allocated_qty) "
                      "VALUES (?, ?, ?, 2, 0)", (self.owner, self.product, self.loc))
            # 库位已有另一批号：走 M21 stock_batch 明细层，比直接塞 batch_receipt 更贴近真实账
            c.execute("INSERT INTO stock_batch (owner_id, product_id, location_id, "
                      "batch_no, qty) VALUES (?, ?, ?, 'LOT-OTHER', 2)",
                      (self.owner, self.product, self.loc))
            row = c.execute("SELECT limits FROM putaway_rule WHERE priority=50").fetchone()
            lim = json.loads(row[0])
            lim["mix_batch"]["enabled"] = True
            c.execute("UPDATE putaway_rule SET limits=? WHERE priority=50",
                      (json.dumps(lim, ensure_ascii=False),))
            c.commit()
        body = self._rec()
        self.assertNotIn("M25-A", [x["code"] for x in body["recommendations"]])
        self.assertTrue(any(x.get("reason_code") == "MIX_BATCH_FORBIDDEN"
                            for x in body["rejected"]))

    def test_30_legacy_engine_fallback_when_all_rules_deleted(self):
        with closing(_conn()) as c:
            c.execute("DELETE FROM putaway_rule")
            c.commit()
        try:
            body = self._rec()
            # 零启用规则 → 服务层分发到 M20 简版；两个分支都用 engine 标识来源，
            # 前端可解释，且不再有规则引擎的命中字段。
            self.assertEqual(body["engine"], "legacy")
            self.assertNotIn("hit_rule", body)
            self.assertIn("recommendations", body)
        finally:
            # 本类共享物理库；删光规则后必须恢复种子基线，否则后续测试模块的
            # init_db() 会因策略包仍在而不复活规则行，出现「应拦截却放行」的假失败。
            with closing(_conn()) as c:
                c.execute("DELETE FROM putaway_rule")
                c.execute("DELETE FROM putaway_rule_set")
                c.commit()
                PR.seed_rules(c)
                c.commit()

    def test_31_mix_product_limit_is_opt_in(self):
        """种子默认不拦混放（老链路零回归）；在 limits 里启用后立即拦截。

        同时验证 2A「拦截内容全部来自配置、代码零 hardcoded 红线」：
        同一个库位、同一张单据，只改一行配置的 enabled 就从不放行变为拒绝。
        """
        other = self._post("/api/products", {
            "sku": "M25-SKU2", "name": "另一物料", "owner_id": self.owner,
        })["product"]["id"]
        with closing(_conn()) as c:
            c.execute("INSERT INTO stock (owner_id, product_id, location_id, qty, allocated_qty) "
                      "VALUES (?, ?, ?, 1, 0)", (self.owner, other, self.loc))
            c.commit()
        try:
            with closing(_conn()) as c:
                ok, code, _ = PR.validate_location(c, self.asn_id, self.line_id,
                                                   self.loc, 1)
            self.assertTrue(ok, code)
            with closing(_conn()) as c:
                lim = json.loads(c.execute(
                    "SELECT limits FROM putaway_rule WHERE priority=50").fetchone()[0])
                lim["mix_product"]["enabled"] = True
                c.execute("UPDATE putaway_rule SET limits=? WHERE priority=50",
                          (json.dumps(lim, ensure_ascii=False),))
                c.commit()
            with closing(_conn()) as c:
                ok, code, _ = PR.validate_location(c, self.asn_id, self.line_id,
                                                   self.loc, 1)
            self.assertFalse(ok)
            self.assertEqual(code, "MIX_PRODUCT_FORBIDDEN")
        finally:
            with closing(_conn()) as c:
                c.execute("DELETE FROM putaway_rule")
                c.execute("DELETE FROM putaway_rule_set")
                c.commit()
                PR.seed_rules(c)
                c.commit()

    def test_32_recommend_only_excludes_candidate_but_allows_post(self):
        """三态语义：种子混放项排除推荐候选，但不过账拦截（M20 行为不回归）。"""
        other = self._post("/api/products", {
            "sku": "M25-SKU3", "name": "第三物料", "owner_id": self.owner,
        })["product"]["id"]
        with closing(_conn()) as c:
            c.execute("INSERT INTO stock (owner_id, product_id, location_id, qty, allocated_qty) "
                      "VALUES (?, ?, ?, 1, 0)", (self.owner, other, self.loc))
            c.commit()
        body = self._rec()
        self.assertNotIn("M25-A", [x["code"] for x in body["recommendations"]])
        self.assertTrue(any(x.get("reason_code") == "MIX_PRODUCT_FORBIDDEN"
                            for x in body["rejected"]))
        with closing(_conn()) as c:
            ok, code, _ = PR.validate_location(c, self.asn_id, self.line_id,
                                               self.loc, 1)
        self.assertTrue(ok, code)


if __name__ == "__main__":
    unittest.main()
