"""M26 可复跑端到端回归（独立端口 + 临时库，绝不碰 5100 demo）。

用法（Playwright 只装在 C:/Python311/python.exe，venv 里没有）：
    C:/Python311/python.exe tools/e2e_m26.py
    C:/Python311/python.exe tools/e2e_m26.py --port 5119 --keep   # 保留临时库排查

覆盖：规则编辑器渲染 → 14 维条件组 / fallback / limits / score → 试算 →
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
    """创建已点收、已验收的单据，生成可用于上架的验收条码和推荐日志。"""
    owner = http("POST", "/api/owners", {"code": "E2E-OW", "name": "E2E 货主"})["owner"]["id"]
    wh = http("POST", "/api/warehouses", {"code": "E2E-WH", "name": "E2E 仓"})["warehouse"]["id"]
    product = http("POST", "/api/products", {
        "sku": "E2E-SKU", "name": "E2E 物料", "owner_id": owner})["product"]["id"]
    zones = http("GET", "/api/zones?page_size=100").get("zones", [])
    shared = next((z["id"] for z in zones if z.get("code") == "ZONE-SHARED"), None)
    http("POST", "/api/locations", {
        "code": "E2E-LOC", "name": "E2E 存储位", "loc_type": "storage",
        "zone_id": shared})
    receiving = http("POST", "/api/locations", {
        "code": "E2E-RCV", "name": "E2E 收货暂存位", "loc_type": "receiving",
    })["location"]["id"]
    asn = http("POST", "/api/asns", {
        "code": "E2E-ASN", "owner_id": owner, "source_warehouse": wh,
        "lines": [{"product_id": product, "expected_qty": 5, "batch_no": "E2E-LOT"}]})
    aid, lid = asn["asn"]["id"], asn["lines"][0]["id"]
    receiving_payload = {"line_id": lid, "location_id": receiving, "qty": 5}
    count = http("POST", f"/api/asns/{aid}/count", receiving_payload)
    accepted = http("POST", f"/api/asns/{aid}/receive", {
        **receiving_payload, "receiving_count_id": count["count_id"], "batch_no": "E2E-LOT",
    })
    barcode = accepted["batch"]["batch_no"]
    rec = http("GET", f"/api/putaway/recommend?asn_id={aid}&line_id={lid}&qty=1&receipt_barcode={barcode}")
    return aid, barcode, rec.get("recommendation_id")


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
            editor = page.get_by_role("dialog")
            check("规则在弹框内编辑", editor.is_visible())
            check("14 维条件组控件", page.locator("#condGrid .fld").count() == 14,
                  page.locator("#condGrid .fld").count())
            check("fallback 步骤可见", page.locator("#stepsBox .step-row").count() >= 1)
            check("8 项 limits 三态矩阵", page.locator("#limitsBody tr").count() == 8)
            check("9 条扩展评分输入", page.locator("#scoreBody input").count() == 9)

            # 试算：冷链条件 vs 冷链上下文
            editor.locator("#cond_is_cold_chain").select_option("1")
            editor.get_by_text("试算当前条件", exact=True).click()
            editor.locator("#editorTrialCtx").fill('{"is_cold_chain": 1}')
            editor.get_by_role("button", name="试算匹配").click()
            page.wait_for_function("document.getElementById('editorTrialResult').textContent.startsWith('✅ 命中')")
            check("弹框条件试算命中", True)
            editor.locator("#cond_is_cold_chain").select_option("")
            editor.locator("#step_type_0").select_option("zone_by_code")
            editor.locator("[data-param=zone_code]").fill("ZONE-NG")
            editor.locator("#lim_storage_class").select_option("enforce")
            editor.locator("#limitZones").fill("ZONE-NG")

            # 新增规则行 → 12 行；再删除 → 11 行
            page.locator("#ruleName").fill("E2E 临时规则")
            page.locator("#rulePriority").fill("777")
            page.locator("button:has-text('保存规则行')").click()
            page.wait_for_function(
                "n => document.querySelectorAll('#ruleBody tr.rule-row').length === n", arg=12)
            check("新增规则行写库", page.locator("#ruleBody tr.rule-row").count() == 12,
                  page.locator("#ruleBody tr.rule-row").count())
            rules = http("GET", "/api/putaway-rules")["rules"]
            new_rule = next((r for r in rules if r["name"] == "E2E 临时规则"), None)
            check("新规则带 14 维空条件组", new_rule is not None and
                  json.loads(new_rule["condition"]) == {}, new_rule and new_rule["id"])
            check("中文储区字段写入规则配置",
                  json.loads(new_rule["steps"]) == [{"type": "zone_by_code", "zone_code": "ZONE-NG"}]
                  and json.loads(new_rule["limits"])["storage_class"]["allowed_zone_codes"] == ["ZONE-NG"])
            page.get_by_role("row").filter(has_text="E2E 临时规则").get_by_role("button", name="编辑").click()
            check("回显已保存的储区设置",
                  editor.locator("[data-param=zone_code]").input_value() == "ZONE-NG"
                  and editor.locator("#lim_storage_class").input_value() == "enforce")
            editor.get_by_role("button", name="取消").click()
            http("DELETE", f"/api/putaway-rules/{new_rule['id']}")
            page.locator("button:has-text('刷新')").first.click()
            page.wait_for_function(
                "n => document.querySelectorAll('#ruleBody tr.rule-row').length === n", arg=11)
            check("删除规则行后恢复 11 行",
                  page.locator("#ruleBody tr.rule-row").count() == 11)

            # 上移：reorder 端点把优先级改成 10/20/…，验证第一行 id 变化
            before = page.locator("#ruleBody tr.rule-row").first.get_attribute("data-idx")
            page.locator("#ruleBody tr.rule-row").nth(1).locator("button[title='上移']").click()
            page.wait_for_function("document.querySelector('#ruleBody .rule-row td:nth-child(2)').textContent.trim() === '10'")
            first_priority = page.locator("#ruleBody tr.rule-row").first.locator("td").nth(1).inner_text()
            check("拖拽优先级保存生效", first_priority.strip() == "10", first_priority.strip())
            check("排序前有行索引", before is not None)

            # 字典：6 条种子 → 新增 → 7 条
            check("字典种子 6 条", page.locator("#docTypeBody tr").count() >= 6,
                  page.locator("#docTypeBody tr").count())
            page.locator("#dtCode").fill("E2E")
            page.locator("#dtName").fill("E2E 外部单据")
            page.get_by_role("button", name="+ 新增", exact=True).click()
            page.locator("#docTypeBody").get_by_text("E2E 外部单据", exact=True).wait_for()
            check("字典新增可查", page.locator("#docTypeBody tr").count() >= 7,
                  page.locator("#docTypeBody tr").count())

            # ── 业务数据 + 上架作业页 ────────────────────────────────
            aid, barcode, rec_id = seed_business_data()
            check("推荐接口产生日志 id", bool(rec_id), rec_id)
            page.goto(BASE + f"/asn_putaway.html?asn_id={aid}",
                      wait_until="networkidle")
            page.locator("#receiptScan").fill(barcode)
            page.locator("#receiptScan").press("Enter")
            page.locator("#recBody button").first.wait_for()
            hint = page.locator("#engineHint").inner_text()
            check("上架页展示中文规则与寻址步骤",
                  "日志 #" in hint and "命中规则 常规上架" in hint and "全部可用货位" in hint, hint)
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
