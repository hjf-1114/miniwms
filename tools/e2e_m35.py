"""M35 可复跑端到端回归（独立端口 + 临时库，绝不碰 5100 demo）。

用法（Playwright 只装在 C:/Python311/python.exe，venv 里没有）：
    C:/Python311/python.exe tools/e2e_m35.py
    C:/Python311/python.exe tools/e2e_m35.py --port 5121 --keep

覆盖：作业页渲染 → 字典 / DN 候选 → 只读试算（FEFO + 跳批留痕且不写库）→
执行分配（波次号 / 占用 / 批次维度）→ 波次汇总 / 留痕查询 → 发货按记录批次扣减 →
取消分配对称释放 → I1/I2/I3 一致性 → 无 JS 错误与业务 4xx。

造数阶段直连临时库停用 putaway 规则行，只是为了让「不合格」演示批次能走上架过账
（M25 的 limits 是过账硬拦截），不改动任何业务表；生产库不受影响。
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
PORT = 5120
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
            http("GET", "/api/outbound/dict")
            return True
        except Exception:
            time.sleep(0.4)
    raise RuntimeError("服务启动超时")


def seed_business_data(db):
    """造 4 个批次（早效期通用 / 专属本客户 / 他客户专属 / 不合格）+ 2 张 DN。"""
    with closing(sqlite3.connect(db)) as c:
        c.execute("UPDATE putaway_rule SET enabled = 0 WHERE rule_type = 'putaway'")
        c.commit()

    owner = http("POST", "/api/owners", {"code": "E35-OW", "name": "E35 货主"})["owner"]["id"]
    wh = http("POST", "/api/warehouses", {"code": "E35-WH", "name": "E35 仓"})["warehouse"]["id"]
    product = http("POST", "/api/products", {
        "sku": "E35-SKU", "name": "E35 物料", "owner_id": owner})["product"]["id"]
    cust = http("POST", "/api/customers", {
        "code": "E35-C1", "name": "E35 客户", "owner_id": owner})["customer"]["id"]
    other = http("POST", "/api/customers", {
        "code": "E35-C2", "name": "E35 客户二", "owner_id": owner})["customer"]["id"]
    recv = http("POST", "/api/locations", {
        "code": "E35-RECV", "name": "E35 暂存位", "loc_type": "receiving"})["location"]["id"]
    loc = http("POST", "/api/locations", {
        "code": "E35-A", "name": "E35 存储位", "loc_type": "storage"})["location"]["id"]

    def stock(tag, qty, expiry, dcid=None, goods="合格"):
        body = http("POST", "/api/asns", {
            "code": f"E35-ASN-{tag}", "owner_id": owner, "source_warehouse": wh,
            "doc_type": "采购",
            "lines": [{"product_id": product, "expected_qty": qty,
                       "batch_no": f"LOT-{tag}", "expiry_date": expiry,
                       "goods_property": goods, "designated_customer_id": dcid,
                       "external_batch_no": "/"}]})
        aid, lid = body["asn"]["id"], body["lines"][0]["id"]
        http("POST", f"/api/asns/{aid}/receive",
             {"line_id": lid, "location_id": recv, "qty": qty, "stock_status": ""})
        http("POST", f"/api/asns/{aid}/putaway",
             {"line_id": lid, "from_location_id": recv, "to_location_id": loc, "qty": qty})

    stock("COMMON", 5, "2026-01-01")
    stock("OWN", 5, "2026-06-01", dcid=cust)
    stock("OTHER", 5, "2026-02-01", dcid=other)   # 专属不匹配 → 跳批
    stock("NG", 3, "2026-01-15", goods="不合格")  # 不合格 → 跳批

    dn1 = http("POST", "/api/dns", {
        "code": "E35-DN-1", "owner_id": owner, "customer_id": cust,
        "source_warehouse": wh,
        "lines": [{"product_id": product, "ordered_qty": 7}]})["dn"]["id"]
    dn2 = http("POST", "/api/dns", {
        "code": "E35-DN-2", "owner_id": owner, "customer_id": cust,
        "source_warehouse": wh,
        "lines": [{"product_id": product, "ordered_qty": 2}]})["dn"]["id"]
    return dn1, dn2, loc


def run(port, keep):
    global BASE, PORT
    PORT = port
    BASE = f"http://127.0.0.1:{PORT}"
    tmp = tempfile.mkdtemp(prefix="miniwms_e2e_m35_")
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
        dn1, dn2, loc = seed_business_data(db)

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

            # ── 作业页首屏 ──────────────────────────────────────────
            page.goto(BASE + "/outbound_allocation.html", wait_until="networkidle")
            check("波次分配页标题", "出库波次分配" in page.title())
            check("策略字典已加载", page.locator("#strategySel option").count() == 2,
                  page.locator("#strategySel option").count())
            check("待分配 DN 两行", page.locator("#dnBody tr").count() == 2,
                  page.locator("#dnBody tr").count())
            check("列出行带波次列与取消按钮",
                  page.locator("#dnBody button:has-text('取消分配')").count() == 2)

            # ── 只读试算 ────────────────────────────────────────────
            page.locator(".dnChk").nth(0).check()
            page.locator(".dnChk").nth(1).check()
            page.locator("button:has-text('只读试算')").click()
            page.wait_for_timeout(500)
            check("试算结果区可见", page.locator("#resultCard").is_visible())
            check("试算标记为只读", "只读试算" in page.locator("#resultFlag").inner_text())
            check("试算有分配明细", page.locator("#allocBody tr").count() >= 2,
                  page.locator("#allocBody tr").count())
            check("试算有跳批留痕", page.locator("#skipBody tr").count() >= 2,
                  page.locator("#skipBody tr").count())
            check("试算未写日志", http("GET", "/api/outbound/logs")["total"] == 0)

            # ── 执行分配 ────────────────────────────────────────────
            page.locator("button:has-text('执行分配')").click()
            page.wait_for_timeout(700)
            wave = page.locator("#resultWave").inner_text().replace("波次 ", "").strip()
            check("生成轻波次号", wave.startswith("WV-"), wave)
            check("写库标记", "已写库" in page.locator("#resultFlag").inner_text())
            check("波次汇总表有数据", page.locator("#waveBody tr").count() >= 1)
            check("留痕表有数据", page.locator("#logBody tr").count() >= 2,
                  page.locator("#logBody tr").count())

            logs = http("GET", f"/api/outbound/logs?wave_no={wave}")
            check("日志含 allocated", logs["stats"].get("allocated", 0) >= 1, logs["stats"])
            check("日志含 skipped", logs["stats"].get("skipped", 0) >= 1, logs["stats"])

            picks = http("GET", f"/api/dns/{dn1}")["picks"]
            check("拣货明细带批次维度",
                  bool(picks) and all(str(p["batch_no"]).startswith("BAT-") for p in picks),
                  [p["batch_no"] for p in picks])
            check("拣货明细带效期", all(p["expiry_date"] for p in picks))

            # ── 发货按记录批次扣减 + 一致性 ──────────────────────────
            def batch_qty():
                with closing(sqlite3.connect(db)) as c:
                    c.row_factory = sqlite3.Row
                    return {r["batch_no"]: r["qty"] for r in c.execute(
                        "SELECT batch_no, qty FROM stock_batch WHERE location_id = ?", (loc,))}

            before = batch_qty()
            http("POST", f"/api/dns/{dn1}/ship", {})
            after = batch_qty()
            deducted = {}
            for p in picks:
                deducted[p["batch_no"]] = deducted.get(p["batch_no"], 0) + p["qty"]
            ok = all(abs(after.get(b, 0) - (before.get(b, 0) - q)) < 1e-6
                     for b, q in deducted.items())
            check("发货严格按分配记录批次扣减", ok, deducted)
            check("I1 主账一致", http("GET", "/api/stock/reconcile")["consistent"])
            check("I2/I3 批次一致", http("GET", "/api/stock/batch-reconcile")["consistent"])

            # ── 取消分配（对另一张未发货 DN） ───────────────────────
            page.goto(BASE + "/outbound_allocation.html", wait_until="networkidle")
            row = page.locator("#dnBody tr").filter(has_text="E35-DN-2")
            check("未发货 DN 仍在候选", row.count() == 1)
            row.locator("button:has-text('取消分配')").click()
            page.wait_for_timeout(600)
            un = http("GET", f"/api/outbound/logs?dn_id={dn2}&action=unallocated")
            check("取消分配留痕", un["total"] >= 1, un["stats"])
            check("取消后 I1 仍一致", http("GET", "/api/stock/reconcile")["consistent"])
            check("取消后 I2/I3 仍一致",
                  http("GET", "/api/stock/batch-reconcile")["consistent"])

            # ── 出库页入口与运行时健康 ──────────────────────────────
            page.goto(BASE + "/dns.html", wait_until="networkidle")
            check("出库页有波次分配入口",
                  page.locator("button:has-text('波次分配作业')").count() == 1)
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
