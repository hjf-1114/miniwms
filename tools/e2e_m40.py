"""M40 可复跑端到端回归（独立端口 + 临时库，绝不碰 5100 demo）。

用法（Playwright 只装在 C:/Python311/python.exe，venv 里没有）：
    C:/Python311/python.exe tools/e2e_m40.py
    C:/Python311/python.exe tools/e2e_m40.py --port 5136 --keep

覆盖：七端点 API → 页面首屏 → 四个 tab → 按人/节点/日周月聚合 → 筛选下钻 →
事件分页 → 只读无副作用 → 导航入口 → I1/I2/I3 → 无 JS 错误。
"""
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import closing

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
PORT = 5136
BASE = ""
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok), str(detail)[:160]))
    print(("  [PASS] " if ok else "  [FAIL] ") + name + (" · " + str(detail)[:120] if detail else ""))


def http(method, path, payload=None, expect=(200, 201)):
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with OPENER.open(req, timeout=15) as r:
            body = json.loads(r.read().decode() or "{}")
            code = r.status
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode() or "{}")
        code = e.code
    if code not in expect:
        raise RuntimeError(f"{method} {path} → HTTP {code}: {str(body)[:200]}")
    return body


def wait_ready(proc, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("Flask 进程提前退出，请看上方日志")
        try:
            http("GET", "/api/workload/dict")
            return True
        except Exception:
            time.sleep(0.4)
    raise RuntimeError("服务启动超时")


def inventory_snapshot(db):
    with closing(sqlite3.connect(db)) as c:
        return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("stock", "stock_movement", "stock_adjustment", "stock_batch")}


def seed_data(db):
    """造工作量演示事件：8 类数据源 / 2 位操作人 / 2 天。"""
    ow = http("POST", "/api/owners", {"code": "E40-OW", "name": "E40 货主"})["owner"]["id"]
    pid = http("POST", "/api/products", {
        "sku": "E40-P", "name": "E40 物料", "owner_id": ow})["product"]["id"]
    lid = http("POST", "/api/locations", {
        "code": "E40-L", "name": "E40 库位", "owner_id": ow})["location"]["id"]
    d1, d2 = "2026-11-10 09:15:00", "2026-11-11 14:30:00"
    with closing(sqlite3.connect(db)) as c:
        c.execute("INSERT INTO asn (code, owner_id, doc_type, status) "
                  "VALUES ('E40-ASN',?,'采购','open')", (ow,))
        asn_id = c.execute("SELECT id FROM asn WHERE code='E40-ASN'").fetchone()[0]
        c.execute("INSERT INTO asn_line (asn_id, product_id, expected_qty) VALUES (?,?,10)",
                  (asn_id, pid))
        asn_line_id = c.execute(
            "SELECT id FROM asn_line WHERE asn_id=?", (asn_id,)).fetchone()[0]
        c.execute("INSERT INTO dn (code, owner_id, status) VALUES ('E40-DN',?,'open')", (ow,))
        dn_id = c.execute("SELECT id FROM dn WHERE code='E40-DN'").fetchone()[0]
        c.execute("INSERT INTO dn_line (dn_id, product_id, ordered_qty) VALUES (?,?,8)",
                  (dn_id, pid))
        line_id = c.execute(
            "SELECT id FROM dn_line WHERE dn_id=?", (dn_id,)).fetchone()[0]
        c.execute("INSERT INTO dn_pick (dn_id, line_id, location_id, qty) VALUES (?,?,?,8)",
                  (dn_id, line_id, lid))
        pick_id = c.execute(
            "SELECT id FROM dn_pick WHERE dn_id=?", (dn_id,)).fetchone()[0]
        c.execute("INSERT INTO dn_review (code, dn_id, dn_code, owner_id, status) "
                  "VALUES ('E40-RV',?,'E40-DN',?,'done')", (dn_id, ow))
        rv_id = c.execute("SELECT id FROM dn_review WHERE code='E40-RV'").fetchone()[0]
        c.execute("INSERT INTO care_plan (code, owner_id, project, next_due_date) "
                  "VALUES ('E40-CP',?,'TEMP_HUMIDITY','2026-11-10')", (ow,))
        plan_id = c.execute("SELECT id FROM care_plan WHERE code='E40-CP'").fetchone()[0]
        c.execute("INSERT INTO care_task (code, plan_id, plan_code, owner_id, project, due_date) "
                  "VALUES ('E40-CT',?,'E40-CP',?,'TEMP_HUMIDITY','2026-11-10')",
                  (plan_id, ow))
        task_id = c.execute("SELECT id FROM care_task WHERE code='E40-CT'").fetchone()[0]
        c.execute("INSERT INTO inventory_transfer (code, transfer_type, status) "
                  "VALUES ('E40-IT','batch','approved')")
        it_id = c.execute(
            "SELECT id FROM inventory_transfer WHERE code='E40-IT'").fetchone()[0]
        c.execute("INSERT INTO inventory_transfer_line (transfer_id, owner_id, product_id, "
                  "location_id, qty) VALUES (?,?,?,?,3)", (it_id, ow, pid, lid))
        it_line = c.execute(
            "SELECT id FROM inventory_transfer_line WHERE transfer_id=?", (it_id,)).fetchone()[0]

        # 库存流水四类（收货 / 拣货 / 发货 / 移库）
        for ref, qty, delta, who, when in (
            ("asn_receive", 10, 0, "alice", d1),
            ("dn_pick", 0, 6, "alice", d1),
            ("dn_ship", -4, -4, "bob", d2),
            ("move_out", -2, 0, "bob", d2),
        ):
            c.execute("INSERT INTO stock_movement (owner_id, product_id, location_id, qty_delta, "
                      "allocated_delta, ref_type, ref_id, operator, created_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?)",
                      (ow, pid, lid, qty, delta, ref, 1, who, when))
        c.execute("INSERT INTO care_log (task_id, task_code, plan_id, plan_code, owner_id, "
                  "project, due_date, action, result, operator, created_at) "
                  "VALUES (?,?,?,?,?,'TEMP_HUMIDITY','2026-11-10','abnormal','abnormal',?,?)",
                  (task_id, "E40-CT", plan_id, "E40-CP", ow, "alice", d2))
        c.execute("INSERT INTO dn_review_line (review_id, dn_id, pick_id, product_id, "
                  "location_id, expected_qty, actual_qty, result, operator, reviewed_at) "
                  "VALUES (?,?,?,?,?,8,8,'ok',?,?)", (rv_id, dn_id, pick_id, pid, lid, "carol", d1))
        c.execute("INSERT INTO dn_review_diff_log (review_id, review_code, dn_id, dn_code, "
                  "owner_id, diff_qty, reason_code, operator, created_at) "
                  "VALUES (?,?,?,?,?,-2,'SHORT',?,?)",
                  (rv_id, "E40-RV", dn_id, "E40-DN", ow, "carol", d2))
        c.execute("INSERT INTO dn_order_log (action, owner_id, source_dn_ids, operator, created_at) "
                  "VALUES ('split',?,?,?,?)", (ow, str(dn_id), "dave", d1))
        c.execute("INSERT INTO putaway_recommendation (asn_id, asn_line_id, action, operator, "
                  "created_at) VALUES (?,?,'adopt',?,?)", (asn_id, asn_line_id, "alice", d2))
        c.execute("INSERT INTO outbound_allocation_log (dn_id, qty, action, operator, created_at) "
                  "VALUES (?,5,'allocated',?,?)", (dn_id, "carol", d1))
        c.execute("INSERT INTO inventory_change_log (transfer_id, line_id, field_name, operator, "
                  "system_at) VALUES (?,?,'batch_no',?,?)", (it_id, it_line, "dave", d2))
        # 做平 I1/I2/I3：流水 qty 合计 4（10+0-4-2），allocated 合计 2（+6-4）
        c.execute("INSERT INTO stock_batch (owner_id, product_id, location_id, batch_no, "
                  "batch_key, qty, allocated_qty) VALUES (?,?,?,'','',4,2)", (ow, pid, lid))
        c.execute("INSERT INTO stock (owner_id, product_id, location_id, qty, allocated_qty) "
                  "VALUES (?,?,?,4,2)", (ow, pid, lid))
        c.commit()
    return ow, pid, lid


def run(port, keep):
    global BASE, PORT
    PORT = port
    BASE = f"http://127.0.0.1:{PORT}"
    tmp = tempfile.mkdtemp(prefix="miniwms_e2e_m40_")
    db = os.path.join(tmp, "e2e.db")
    env = os.environ.copy()
    env.update({
        "MINIWMS_DB": db, "MINIWMS_PORT": str(port),
        "MINIWMS_ALLOW_ANONYMOUS": "1",
        "NO_PROXY": "localhost,127.0.0.1", "no_proxy": "localhost,127.0.0.1",
        "HTTP_PROXY": "", "HTTPS_PROXY": "", "http_proxy": "", "https_proxy": "",
    })
    print(f"临时库：{db}\n服务端口：{port}\n")
    proc = subprocess.Popen([VENV_PY, "main.py"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    browser = None
    try:
        wait_ready(proc)
        ow, pid, lid = seed_data(db)
        before = inventory_snapshot(db)

        # ── 后端断言 ────────────────────────────────────────────────
        d = http("GET", "/api/workload/dict")
        check("字典节点齐全", len(d["nodes"]) == 16, len(d["nodes"]))
        check("字典八类数据源", len(d["sources"]) == 8, len(d["sources"]))
        ov = http("GET", "/api/workload/overview")
        check("概览事件总数", ov["cards"][0]["value"] == 11, ov["cards"][0])
        check("概览操作人数", ov["cards"][1]["value"] == 4, ov["cards"][1])
        check("概览 Top 节点", len(ov["top_nodes"]) == 5, len(ov["top_nodes"]))

        ops = http("GET", "/api/workload/by-operator")
        counts = {o["operator"]: o["count"] for o in ops["operators"]}
        check("按人聚合正确",
              counts == {"alice": 4, "bob": 2, "carol": 3, "dave": 2}, counts)
        nodes = http("GET", "/api/workload/by-node")
        check("按节点覆盖 11 类", len(nodes["nodes"]) == 11, len(nodes["nodes"]))
        check("按节点事件合计", sum(n["count"] for n in nodes["nodes"]) == 11)

        day = http("GET", "/api/workload/by-period?period=day")
        check("按日两段", len(day["series"]) == 2, [s["key"] for s in day["series"]])
        week = http("GET", "/api/workload/by-period?period=week")
        check("按周一桶", len(week["series"]) == 1 and week["series"][0]["count"] == 11)
        month = http("GET", "/api/workload/by-period?period=month")
        check("按月一桶", month["series"][0]["key"] == "2026-11")

        ev = http("GET", "/api/workload/events?page=1&page_size=5")
        check("事件明细分页", ev["total"] == 11 and len(ev["events"]) == 5, ev["total"])
        check("操作人过滤", http("GET", "/api/workload/events?operator=alice")["total"] == 4)
        check("节点过滤", http("GET", "/api/workload/events?node=review")["total"] == 1)
        check("日期过滤", http("GET", "/api/workload/events?date_from=2026-11-11")["total"] == 6)
        check("参数校验 400",
              http("GET", "/api/workload/by-period?period=year", expect=(400,)).get("code")
              == "WORKLOAD_BAD_PARAM")

        # ── 前端断言 ────────────────────────────────────────────────
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True, channel="chrome",
                                            args=["--no-proxy-server"])
            except Exception:
                browser = p.chromium.launch(headless=True, args=["--no-proxy-server"])
            page = browser.new_page()
            js_errors, bad_responses = [], []
            page.on("pageerror", lambda e: js_errors.append(str(e)))
            page.on("response", lambda r: bad_responses.append((r.status, r.url))
                    if r.status >= 400 and "/api/" in r.url and "favicon" not in r.url else None)
            page.goto(BASE + "/workload.html", wait_until="networkidle")

            check("工作量页标题", "工作量统计" in page.title())
            check("四个 tab", page.locator("#wlTabs .tab").count() == 4,
                  page.locator("#wlTabs .tab").count())
            check("概览卡渲染", page.locator("#wlOverview .card").count() == 1)
            check("导航含工作量统计", page.locator("a[data-href='/workload.html']").count() == 1)

            check("按人表四行", page.locator("#opBody tr").count() == 4,
                  page.locator("#opBody tr").count())
            page.locator("button[data-tab='node']").click()
            check("节点面板激活", page.locator("#panel-node").is_visible())
            check("按节点表 11 行", page.locator("#nodeBody tr").count() == 11,
                  page.locator("#nodeBody tr").count())

            page.locator("button[data-tab='period']").click()
            check("时间段面板激活", page.locator("#panel-period").is_visible())
            check("按日两行", page.locator("#periodBody tr").count() == 2,
                  page.locator("#periodBody tr").count())
            page.locator("#wlPeriod").select_option("month")
            page.locator("#btnPeriod").click()
            page.wait_for_timeout(300)
            check("切按月一行", page.locator("#periodBody tr").count() == 1,
                  page.locator("#periodBody tr").count())

            page.locator("button[data-tab='events']").click()
            check("事件面板激活", page.locator("#panel-events").is_visible())
            check("事件表二十行以内", 0 < page.locator("#eventBody tr").count() <= 20,
                  page.locator("#eventBody tr").count())
            page.locator("#btnNext").click()
            page.wait_for_timeout(300)
            check("翻页生效", "第 2 页" in page.locator("#eventPageInfo").inner_text(),
                  page.locator("#eventPageInfo").inner_text())

            page.locator("#wlOperator").select_option("alice")
            page.locator("#btnApply").click()
            page.wait_for_timeout(500)
            check("前端筛选生效",
                  "共 1 人" in page.locator("#opStat").inner_text()
                  and "4" in page.locator("#opBody tr").first.inner_text(),
                  page.locator("#opStat").inner_text())
            page.locator("#btnReset").click()
            page.wait_for_timeout(400)
            check("重置恢复四人", "4 人" in page.locator("#opStat").inner_text(),
                  page.locator("#opStat").inner_text())

            after = inventory_snapshot(db)
            check("报表未改库存流水", before == after, (before, after))
            check("I1 主账一致", http("GET", "/api/stock/reconcile")["consistent"])
            check("I2/I3 批次一致", http("GET", "/api/stock/batch-reconcile")["consistent"])
            check("无 JS 运行时错误", not js_errors, "; ".join(js_errors[:2]))
            check("无业务 4xx/5xx（预期 400 除外）",
                  all(s == 400 for s, _ in bad_responses), str(bad_responses[:3]))
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        if keep:
            print(f"\n临时库保留：{db}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    print(f"\nE2E {passed}/{len(CHECKS)} 通过")
    for name, ok, detail in CHECKS:
        if not ok:
            print(f"  FAIL · {name} · {detail}")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--keep", action="store_true", help="保留临时库便于排查")
    a = ap.parse_args()
    sys.exit(run(a.port, a.keep))
