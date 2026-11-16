"""M37 可复跑端到端回归（独立端口 + 临时库，绝不碰 5100 demo）。

用法（Playwright 只装在 C:/Python311/python.exe，venv 里没有）：
    C:/Python311/python.exe tools/e2e_m37.py
    C:/Python311/python.exe tools/e2e_m37.py --port 5129 --keep

覆盖：页面首屏 → 计划字典与下拉 → 生成到期任务（幂等）→ 正常执行 →
异常执行 + 原因码 → 异常处置只留痕（库存 / 调整单 / 流水不变）→
留痕查询 → 导航入口 → 无 JS 错误与业务 4xx。
"""
import argparse
import datetime
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
PORT = 5128
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
            http("GET", "/api/care/dict")
            return True
        except Exception:
            time.sleep(0.4)
    raise RuntimeError("服务启动超时")


def inventory_snapshot(db):
    with closing(sqlite3.connect(db)) as c:
        return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("stock", "stock_movement", "stock_adjustment", "stock_batch")}


def seed_plans():
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    ow = http("POST", "/api/owners", {"code": "E37-OW", "name": "E37 货主"})["owner"]["id"]
    loc = http("POST", "/api/locations", {
        "code": "E37-COLD", "name": "E37 冷藏库位", "loc_type": "storage"})["location"]["id"]
    p1 = http("POST", "/api/care-plans", {
        "owner_id": ow, "location_id": loc, "project": "TEMP_HUMIDITY",
        "cycle": "weekly", "next_due_date": yesterday,
        "temp_min": 2, "temp_max": 8, "hum_min": 30, "hum_max": 70,
        "note": "E2E 冷藏温湿度养护"})
    p2 = http("POST", "/api/care-plans", {
        "owner_id": ow, "project": "EXPIRY", "cycle": "daily",
        "next_due_date": yesterday, "note": "E2E 效期检查"})
    return p1, p2


def run(port, keep):
    global BASE, PORT
    PORT = port
    BASE = f"http://127.0.0.1:{PORT}"
    tmp = tempfile.mkdtemp(prefix="miniwms_e2e_m37_")
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
        p1, p2 = seed_plans()
        before = inventory_snapshot(db)

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
            page.on("dialog", lambda d: d.accept())

            # ── 首屏 ────────────────────────────────────────────────
            page.goto(BASE + "/care.html", wait_until="networkidle")
            check("养护页标题", "养护管理" in page.title())
            check("养护项目下拉 5 项", page.locator("#planProject option").count() == 5,
                  page.locator("#planProject option").count())
            check("周期下拉 5 项", page.locator("#planCycle option").count() == 5,
                  page.locator("#planCycle option").count())
            check("原因码下拉 8 项 + 占位", page.locator("#execReason option").count() == 9,
                  page.locator("#execReason option").count())
            check("计划列表两行", page.locator("#planBody tr").count() == 2,
                  page.locator("#planBody tr").count())
            check("统计卡已渲染", "启用计划" in page.locator("#overview").inner_text())
            check("导航含养护管理", page.locator("a[data-href='/care.html']").count() == 1)

            # ── 生成任务 ────────────────────────────────────────────
            page.locator("button:has-text('生成到期任务')").click()
            page.wait_for_timeout(600)
            check("任务列表生成两条", page.locator("#taskBody tr").count() == 2,
                  page.locator("#taskBody tr").count())
            first = [t for t in http("GET", "/api/care-tasks")["tasks"]
                     if t["project"] == "TEMP_HUMIDITY"][0]
            check("任务快照阈值", first["temp_max"] == 8 and first["hum_min"] == 30,
                  (first["temp_max"], first["hum_min"]))
            check("任务编号规则", first["code"].startswith("CT-"), first["code"])
            gen2 = http("POST", "/api/care-tasks/generate", {})
            check("重复生成幂等", gen2["generated"] == 0, gen2)

            # ── 正常执行（温湿度）───────────────────────────────────
            page.locator("tr.task-row").filter(has_text=first["code"]).click()
            page.wait_for_timeout(300)
            check("执行面板可见", page.locator("#detailCard").is_visible())
            check("详情显示阈值快照", "8" in page.locator("#execInfo").inner_text())
            page.locator("#execTemp").fill("5")
            page.locator("#execHum").fill("50")
            page.locator("button:has-text('提交养护结果')").click()
            page.wait_for_timeout(600)
            d1 = http("GET", f"/api/care-tasks/{first['id']}")
            check("正常养护已落库", d1["status"] == "done" and d1["result"] == "normal", d1["status"])
            check("正常留痕已写", d1["logs"][-1]["action"] == "checked")

            # ── 越界必须登记异常（API 边界）─────────────────────────
            p3 = http("POST", "/api/care-plans", {
                "owner_id": p1["owner_id"], "project": "TEMP_HUMIDITY",
                "cycle": "daily", "next_due_date": (datetime.date.today()
                                                    - datetime.timedelta(days=1)).isoformat(),
                "temp_min": 2, "temp_max": 8})
            gen3 = http("POST", "/api/care-tasks/generate", {"plan_id": p3["id"]})
            check("第三任务已生成", gen3["generated"] == 1, gen3)
            t3 = http("GET", "/api/care-tasks")["tasks"]
            t3 = [t for t in t3 if t["plan_id"] == p3["id"]][0]
            bad = http("POST", f"/api/care-tasks/{t3['id']}/execute",
                       {"result": "normal", "temperature": 25, "humidity": 50},
                       expect=(400,))
            check("越界拒绝判正常", bad.get("code") == "THRESHOLD_EXCEEDED", bad.get("error"))

            # ── 异常执行 + 处置（效期任务）──────────────────────────
            expiry = [t for t in http("GET", "/api/care-tasks")["tasks"]
                      if t["project"] == "EXPIRY"][0]
            page.locator("tr.task-row").filter(has_text=expiry["code"]).click()
            page.wait_for_timeout(300)
            page.locator("#execResult").select_option("abnormal")
            page.locator("#execReason").select_option("EXPIRY_NEAR")
            page.locator("#execNote").fill("E2E 发现临期批次")
            page.locator("button:has-text('提交养护结果')").click()
            page.wait_for_timeout(1200)
            d2 = http("GET", f"/api/care-tasks/{expiry['id']}")
            check("异常状态已落库", d2["status"] == "abnormal", d2["status"])
            check("异常原因已留痕", d2["logs"][-1]["reason_code"] == "EXPIRY_NEAR")
            check("处置面板出现", page.locator("#handleCard").is_visible(),
                  page.locator("#detailStatus").inner_text() + " / "
                  + page.locator("#msg").inner_text())
            page.locator("#handleNote").fill("E2E 已贴临期标识并通知货主")
            page.locator("button:has-text('登记处置')").click()
            page.wait_for_timeout(600)
            d3 = http("GET", f"/api/care-tasks/{expiry['id']}")
            check("异常已处置", d3["status"] == "handled" and bool(d3["handled_note"]))
            check("处置留痕已写", d3["logs"][-1]["action"] == "handled")

            # ── 留痕与库存边界 ──────────────────────────────────────
            after = inventory_snapshot(db)
            check("养护未改库存与流水", before == after, (before, after))
            check("I1 主账一致", http("GET", "/api/stock/reconcile")["consistent"])
            check("I2/I3 批次一致", http("GET", "/api/stock/batch-reconcile")["consistent"])
            page.locator("#logAction").select_option("handled")
            page.locator("#btnLogs").click()
            page.wait_for_timeout(400)
            check("留痕过滤有数据", page.locator("#logBody tr").count() >= 1)
            logs = http("GET", "/api/care/logs?action=handled")
            check("处置日志可查询", logs["total"] == 1, logs["total"])
            check("日志动作统计完整",
                  all(k in logs["stats"] for k in ("generated", "checked", "abnormal", "handled")),
                  logs["stats"])
            check("无 JS 运行时错误", not js_errors, "; ".join(js_errors[:2]))
            check("无业务 4xx/5xx", not bad_responses, str(bad_responses[:2]))
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
