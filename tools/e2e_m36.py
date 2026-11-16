"""M36 可复跑端到端回归（独立端口 + 临时库，绝不碰 5100 demo）。

用法（Playwright 只装在 C:/Python311/python.exe，venv 里没有）：
    C:/Python311/python.exe tools/e2e_m36.py
    C:/Python311/python.exe tools/e2e_m36.py --port 5122 --keep

覆盖：作业页首屏 → 生成复核单（批次快照）→ 逐行通过 / 登记差异 → 差异只留痕
（库存不变、无调整单、DN 状态不变）→ 完成复核 → 差异留痕查询 → 取消复核单 →
无 JS 错误与业务 4xx。

造数阶段直连临时库停用 putaway 规则行，只为让演示批次能走上架过账，不改业务表。
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
PORT = 5126
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
            http("GET", "/api/dn-reviews/dict")
            return True
        except Exception:
            time.sleep(0.4)
    raise RuntimeError("服务启动超时")


def seed_business_data(db):
    """造库存 + 2 张已分配 DN（带批次维度的 dn_pick）。"""
    with closing(sqlite3.connect(db)) as c:
        c.execute("UPDATE putaway_rule SET enabled = 0 WHERE rule_type = 'putaway'")
        c.commit()
    owner = http("POST", "/api/owners", {"code": "E36-OW", "name": "E36 货主"})["owner"]["id"]
    wh = http("POST", "/api/warehouses", {"code": "E36-WH", "name": "E36 仓"})["warehouse"]["id"]
    product = http("POST", "/api/products", {
        "sku": "E36-SKU", "name": "E36 物料", "owner_id": owner})["product"]["id"]
    cust = http("POST", "/api/customers", {
        "code": "E36-C", "name": "E36 客户", "owner_id": owner})["customer"]["id"]
    recv = http("POST", "/api/locations", {
        "code": "E36-RECV", "name": "E36 暂存位", "loc_type": "receiving"})["location"]["id"]
    loc = http("POST", "/api/locations", {
        "code": "E36-A", "name": "E36 存储位", "loc_type": "storage"})["location"]["id"]
    for tag in ("A", "B"):
        body = http("POST", "/api/asns", {
            "code": f"E36-ASN-{tag}", "owner_id": owner, "source_warehouse": wh,
            "doc_type": "采购",
            "lines": [{"product_id": product, "expected_qty": 10,
                       "batch_no": f"LOT-{tag}", "expiry_date": "2026-12-01",
                       "goods_property": "合格", "external_batch_no": "/"}]})
        aid, lid = body["asn"]["id"], body["lines"][0]["id"]
        http("POST", f"/api/asns/{aid}/receive",
             {"line_id": lid, "location_id": recv, "qty": 10, "stock_status": ""})
        http("POST", f"/api/asns/{aid}/putaway",
             {"line_id": lid, "from_location_id": recv, "to_location_id": loc, "qty": 10})
    dn1 = http("POST", "/api/dns", {
        "code": "E36-DN-1", "owner_id": owner, "customer_id": cust,
        "source_warehouse": wh,
        "lines": [{"product_id": product, "ordered_qty": 10}]})["dn"]["id"]
    dn2 = http("POST", "/api/dns", {
        "code": "E36-DN-2", "owner_id": owner, "customer_id": cust,
        "source_warehouse": wh,
        "lines": [{"product_id": product, "ordered_qty": 10}]})["dn"]["id"]
    for dn in (dn1, dn2):
        http("POST", "/api/outbound/allocate", {"dn_ids": [dn]})
    return dn1, dn2, loc, product


def stock_and_logs(db):
    with closing(sqlite3.connect(db)) as c:
        c.row_factory = sqlite3.Row
        rows = [dict(r) for r in c.execute(
            "SELECT location_id, qty, allocated_qty FROM stock ORDER BY id")]
        adj = c.execute("SELECT COUNT(*) FROM stock_adjustment").fetchone()[0]
        movs = c.execute(
            "SELECT COUNT(*) FROM stock_movement WHERE ref_type='dn_review'").fetchone()[0]
    return rows, adj, movs


def run(port, keep):
    global BASE, PORT
    PORT = port
    BASE = f"http://127.0.0.1:{PORT}"
    tmp = tempfile.mkdtemp(prefix="miniwms_e2e_m36_")
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
        dn1, dn2, loc, product = seed_business_data(db)
        before_rows, before_adj, before_mov = stock_and_logs(db)

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
            page.goto(BASE + "/dn_review.html", wait_until="networkidle")
            check("复核页标题", "出库复核" in page.title())
            check("状态下拉已加载", page.locator("#statusFilter option").count() >= 4,
                  page.locator("#statusFilter option").count())
            check("原因码下拉已加载", page.locator("#logReason option").count() == 7,
                  page.locator("#logReason option").count())
            check("待复核 DN 下拉有两张",
                  page.locator("#dnSel option").count() == 2,
                  page.locator("#dnSel option").count())

            # ── 生成复核单 ──────────────────────────────────────────
            page.locator("#dnSel").select_option(label="E36-DN-1 · E36-OW · 10")
            page.locator("#rvNote").fill("E2E 复核演示")
            page.locator("button:has-text('生成复核单')").click()
            page.wait_for_timeout(600)
            check("复核单列表有数据", page.locator("#rvBody tr").count() == 1)
            check("复核作业区可见", page.locator("#detailCard").is_visible())
            detail = http("GET", "/api/dn-reviews/1")
            rv_id = detail["id"]
            check("复核行带批次快照",
                  bool(detail["lines"][0]["batch_no"]) and bool(detail["lines"][0]["expiry_date"]),
                  detail["lines"][0]["batch_no"])
            check("复核行应拣为 10", detail["lines"][0]["expected_qty"] == 10)
            check("复核行带波次", detail["lines"][0]["wave_no"].startswith("WV-"))

            # ── 登记差异（前端自动判定：实拣 8 < 应拣 10 → diff）────
            tr = page.locator("tr.review-line").first
            tr.locator(".actual").fill("8")
            tr.locator(".reason").select_option("SHORT")
            tr.locator(".lnote").fill("现场少 2 件")
            tr.locator("button:has-text('提交')").click()
            page.wait_for_timeout(600)
            d = http("GET", f"/api/dn-reviews/{rv_id}")
            check("差异判定已落库", d["lines"][0]["result"] == "diff"
                  and d["lines"][0]["diff_qty"] == -2, d["lines"][0]["result"])
            check("差异留痕已写", d["diff_lines"] == 1 and len(d["diff_logs"]) == 1)
            check("原因码落库", d["diff_logs"][0]["reason_code"] == "SHORT")

            # ── 差异只留痕：库存/调整单/DN 状态都不变 ───────────────
            after_rows, after_adj, after_mov = stock_and_logs(db)
            check("库存未被复核改动", after_rows == before_rows)
            check("未生成调整单", after_adj == before_adj)
            check("未写库存流水", after_mov == before_mov)
            dn_status = http("GET", f"/api/dns/{dn1}")["dn"]["status"]
            check("DN 状态未被复核改动", dn_status == "picking", dn_status)
            check("I1 主账一致", http("GET", "/api/stock/reconcile")["consistent"])
            check("I2/I3 批次一致", http("GET", "/api/stock/batch-reconcile")["consistent"])

            # ── 差异留痕查询 ────────────────────────────────────────
            check("留痕查询表有数据", page.locator("#logBody tr").count() >= 1)
            logs = http("GET", f"/api/dn-reviews/logs?review_id={rv_id}")
            check("日志 action 正确", logs["stats"].get("diff_registered", 0) == 1, logs["stats"])
            check("原因统计正确", logs["reasons"].get("SHORT") == 1, logs["reasons"])

            # ── 完成复核 ────────────────────────────────────────────
            page.locator("button:has-text('完成复核')").click()
            page.wait_for_timeout(500)
            check("复核完成状态", http("GET", f"/api/dn-reviews/{rv_id}")["status"] == "completed")

            # ── 取消另一张复核单 ────────────────────────────────────
            page.goto(BASE + "/dn_review.html", wait_until="networkidle")
            page.locator("#dnSel").select_option(label="E36-DN-2 · E36-OW · 10")
            page.locator("button:has-text('生成复核单')").click()
            page.wait_for_timeout(500)
            page.locator("button:has-text('取消复核单')").click()
            page.wait_for_timeout(500)
            cancel_id = http("GET", "/api/dn-reviews?keyword=E36-DN-2")["reviews"][0]["id"]
            check("复核单已取消",
                  http("GET", f"/api/dn-reviews/{cancel_id}")["status"] == "cancelled")

            # ── 出库页入口与运行时健康 ──────────────────────────────
            page.goto(BASE + "/dns.html", wait_until="networkidle")
            check("出库页有复核入口",
                  page.locator("button:has-text('出库复核')").count() == 1)
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
