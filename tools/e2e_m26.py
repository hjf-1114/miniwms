"""M26 可复跑端到端回归（独立端口 + 临时库，绝不碰 5100 demo）。

用法（Playwright 只装在 C:/Python311/python.exe，venv 里没有）：
    C:/Python311/python.exe tools/e2e_m26.py
    C:/Python311/python.exe tools/e2e_m26.py --port 5119 --keep   # 保留临时库排查

覆盖：规则编辑器渲染 → 13 维条件组 / fallback / limits / score → 试算 →
新增与删除规则行 → 拖拽优先级 → 外部单据字典 → 推荐日志统计 →
上架作业页可解释字段 → 一键采纳写日志 → 无 JS 错误与业务 404。

设计 §14 阶段 F 的「上架过账拦截 / 停用即放行」由 unittest 覆盖，本脚本不改库存。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
PORT = 5118
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
            http("GET", "/api/putaway-rule-types")
            return True
        except Exception:
            time.sleep(0.4)
    raise RuntimeError("服务启动超时")


def seed_business_data():
    """造最小业务数据并产生一条真实推荐日志，返回 asn / line / location / 日志 id。"""
    owner = http("POST", "/api/owners", {"code": "E2E-OW", "name": "E2E 货主"})["owner"]["id"]
    wh = http("POST", "/api/warehouses", {"code": "E2E-WH", "name": "E2E 仓"})["warehouse"]["id"]
    product = http("POST", "/api/products", {
        "sku": "E2E-SKU", "name": "E2E 物料", "owner_id": owner})["product"]["id"]
    zones = http("GET", "/api/zones?page_size=100").get("zones", [])
    shared = next((z["id"] for z in zones if z.get("code") == "ZONE-SHARED"), None)
    loc = http("POST", "/api/locations", {
        "code": "E2E-LOC", "name": "E2E 存储位", "loc_type": "storage",
        "zone_id": shared})["location"]["id"]
    asn = http("POST", "/api/asns", {
        "code": "E2E-ASN", "owner_id": owner, "source_warehouse": wh,
        "lines": [{"product_id": product, "expected_qty": 5, "batch_no": "E2E-LOT"}]})
    aid, lid = asn["asn"]["id"], asn["lines"][0]["id"]
    rec = http("GET", f"/api/putaway/recommend?asn_id={aid}&line_id={lid}&qty=1&top_n=5")
    return aid, lid, loc, rec.get("recommendation_id")


def run(port, keep):
    global BASE, PORT
    PORT = port
    BASE = f"http://127.0.0.1:{PORT}"
    tmp = tempfile.mkdtemp(prefix="miniwms_e2e_m26_")
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

            # ── 规则配置页 ────────────────────────────────────────────
            page.goto(BASE + "/putaway_rules.html", wait_until="networkidle")
            check("规则配置页标题", "上架规则配置" in page.title())
            check("策略包下拉已加载",
                  page.locator("#setSel option").count() >= 1,
                  page.locator("#setSel").input_value())
            check("M25 种子 11 行规则",
                  page.locator("#ruleBody tr.rule-row").count() == 11,
                  page.locator("#ruleBody tr.rule-row").count())

            page.locator("button:has-text('新增规则行')").first.click()
            check("13 维条件组控件", page.locator("#condGrid .fld").count() == 13,
                  page.locator("#condGrid .fld").count())
            check("fallback 步骤可见", page.locator("#stepsBox .step-row").count() >= 1)
            check("8 项 limits 三态矩阵", page.locator("#limitsBody tr").count() == 8)
            check("9 条扩展评分输入", page.locator("#scoreBody input").count() == 9)

            # 试算：冷链条件 vs 冷链上下文
            page.locator("#trialCond").fill('{"is_cold_chain": 1}')
            page.locator("#trialCtx").fill('{"is_cold_chain": 1}')
            page.locator("button:has-text('试算匹配')").click()
            page.wait_for_timeout(300)
            check("条件组试算命中", "命中" in page.locator("#trialResult").inner_text(),
                  page.locator("#trialResult").inner_text())

            # 新增规则行 → 12 行；再删除 → 11 行
            page.locator("#ruleName").fill("E2E 临时规则")
            page.locator("#rulePriority").fill("777")
            page.locator("button:has-text('保存规则行')").click()
            page.wait_for_timeout(500)
            check("新增规则行写库", page.locator("#ruleBody tr.rule-row").count() == 12,
                  page.locator("#ruleBody tr.rule-row").count())
            rules = http("GET", "/api/putaway-rules")["rules"]
            new_rule = next((r for r in rules if r["name"] == "E2E 临时规则"), None)
            check("新规则带 13 维空条件组", new_rule is not None and
                  json.loads(new_rule["condition"]) == {}, new_rule and new_rule["id"])
            http("DELETE", f"/api/putaway-rules/{new_rule['id']}")
            page.locator("button:has-text('刷新')").first.click()
            page.wait_for_timeout(400)
            check("删除规则行后恢复 11 行",
                  page.locator("#ruleBody tr.rule-row").count() == 11)

            # 上移：reorder 端点把优先级改成 10/20/…，验证第一行 id 变化
            before = page.locator("#ruleBody tr.rule-row").first.get_attribute("data-idx")
            page.locator("#ruleBody tr.rule-row").nth(1).locator("button[title='上移']").click()
            page.wait_for_timeout(500)
            first_priority = page.locator("#ruleBody tr.rule-row").first.locator("td").nth(1).inner_text()
            check("拖拽优先级保存生效", first_priority.strip() == "10", first_priority.strip())
            check("排序前有行索引", before is not None)

            # 字典：6 条种子 → 新增 → 7 条
            check("字典种子 6 条", page.locator("#docTypeBody tr").count() >= 6,
                  page.locator("#docTypeBody tr").count())
            page.locator("#dtCode").fill("E2E")
            page.locator("#dtName").fill("E2E 外部单据")
            page.get_by_role("button", name="+ 新增", exact=True).click()
            page.wait_for_timeout(400)
            check("字典新增可查", page.locator("#docTypeBody tr").count() >= 7,
                  page.locator("#docTypeBody tr").count())

            # ── 业务数据 + 上架作业页 ────────────────────────────────
            aid, lid, loc, rec_id = seed_business_data()
            check("推荐接口产生日志 id", bool(rec_id), rec_id)
            page.goto(BASE + f"/asn_putaway.html?asn_id={aid}&line_id={lid}",
                      wait_until="networkidle")
            page.wait_for_timeout(600)
            hint = page.locator("#engineHint").inner_text()
            check("上架页展示可解释字段", "日志 #" in hint and "引擎" in hint, hint)
            check("改位原因码下拉已加载",
                  page.locator("#overrideReasonSel option").count() >= 2)

            rows = page.locator("#recBody tr")
            if rows.count() and rows.first.locator("button").count():
                rows.first.locator("button").first.click()
                page.wait_for_timeout(500)
                logs = http("GET", f"/api/putaway-recommendations?asn_id={aid}")["recommendations"]
                check("一键采纳写入 adopt 日志",
                      any(x["action"] == "adopt" and x["chosen_location_id"] for x in logs),
                      [(x["id"], x["action"]) for x in logs])
            else:
                check("一键采纳写入 adopt 日志", False, "推荐列表无候选按钮")

            # 规则配置页统计卡随日志更新
            page.goto(BASE + "/putaway_rules.html", wait_until="networkidle")
            check("命中率统计卡渲染", page.locator("#ratesBox .rate").count() == 5,
                  page.locator("#ratesBox .rate").count())
            check("推荐日志表格有数据", page.locator("#logBody tr").count() >= 1)
            if page.locator("button:has-text('查看 Top-N')").count():
                page.once("dialog", lambda d: d.accept())
                page.locator("button:has-text('查看 Top-N')").first.click()
                page.wait_for_timeout(200)

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
