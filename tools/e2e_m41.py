"""M41 可复跑端到端回归（独立端口 + 临时库，绝不碰 5100 demo）。

用法（Playwright 只装在 C:/Python311/python.exe，venv 里没有）：
    C:/Python311/python.exe tools/e2e_m41.py
    C:/Python311/python.exe tools/e2e_m41.py --port 5156 --keep

覆盖：模板 CRUD / 激活唯一 / L1 占位符 / L2 拖拽 / 预览取数 / 临时编辑不回写 /
Chrome PDF 字节流 / 单据页打印入口 / 导航 / I1/I2/I3 / 无 JS 错误。
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
import urllib.parse
import urllib.request
from contextlib import closing

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
PORT = 5156
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
        with OPENER.open(req, timeout=30) as r:
            body = r.read()
            code = r.status
            ctype = r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        body, code, ctype = e.read(), e.code, ""
    if code not in expect:
        raise RuntimeError(f"{method} {path} → HTTP {code}: {body[:200]!r}")
    if "application/pdf" in ctype:
        return body
    return json.loads(body.decode() or "{}")


def wait_ready(proc, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("Flask 进程提前退出，请看上方日志")
        try:
            http("GET", "/api/print/dict")
            return True
        except Exception:
            time.sleep(0.4)
    raise RuntimeError("服务启动超时")


def snapshot(db):
    with closing(sqlite3.connect(db)) as c:
        return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("stock", "stock_movement", "stock_adjustment", "stock_batch",
                          "asn", "dn", "asn_line", "dn_line")}


def seed_data():
    ow = http("POST", "/api/owners", {"code": "E41-OW", "name": "E41 货主"})["owner"]["id"]
    pid = http("POST", "/api/products", {
        "sku": "E41-P", "name": "E41 物料", "owner_id": ow})["product"]["id"]
    lid = http("POST", "/api/locations", {
        "code": "E41-L", "name": "E41 库位", "owner_id": ow})["location"]["id"]
    cid = http("POST", "/api/customers", {
        "code": "E41-C", "name": "E41 客户", "owner_id": ow})["customer"]["id"]
    asn_id = http("POST", "/api/asns", {
        "code": "E41-ASN", "owner_id": ow,
        "lines": [{"product_id": pid, "expected_qty": 12}]})["asn"]["id"]
    dn_id = http("POST", "/api/dns", {
        "code": "E41-DN", "owner_id": ow, "customer_id": cid,
        "lines": [{"product_id": pid, "ordered_qty": 6}]})["dn"]["id"]
    return ow, pid, lid, cid, asn_id, dn_id


def run(port, keep):
    global BASE, PORT
    PORT = port
    BASE = f"http://127.0.0.1:{PORT}"
    tmp = tempfile.mkdtemp(prefix="miniwms_e2e_m41_")
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
        ow, pid, lid, cid, asn_id, dn_id = seed_data()
        before = snapshot(db)

        # ── 后端 ────────────────────────────────────────────────────
        d = http("GET", "/api/print/dict")
        check("字典 6 节点 / 3 来源 / 5 区块",
              len(d["nodes"]) == 6 and len(d["sources"]) == 3 and len(d["blocks"]) == 5)
        ts = http("GET", "/api/print/templates")
        check("默认模板 6 张且各节点生效", ts["total"] == 6 and
              sum(1 for t in ts["templates"] if t["is_active"]) == 6)
        made = http("POST", "/api/print/templates", {
            "node": "receive", "code": "E41-CUSTOM", "name": "E41 自定义", "is_active": 0,
            "layout_json": {"blocks": [], "content_template": "{{code}}｜{{lines_table}}"},
        })["template"]
        check("新建模板 201", made["code"] == "E41-CUSTOM")
        check("重复编码 400",
              http("POST", "/api/print/templates",
                   {"node": "receive", "code": "E41-CUSTOM", "name": "dup"},
                   expect=(400,)).get("code") == "PRINT_DUPLICATE_CODE")
        http("POST", f"/api/print/templates/{made['id']}/activate", {})
        rows = http("GET", "/api/print/templates?node=receive")["templates"]
        check("激活唯一", sum(1 for t in rows if t["is_active"]) == 1)

        prev = http("GET", f"/api/print/preview?node=receive&source=asn&doc_id={asn_id}")
        check("预览取数", prev["document"]["code"] == "E41-ASN" and
              len(prev["document"]["lines"]) == 1)
        check("L1 占位符渲染", "{{code}}" not in prev["html"] and "E41-ASN" in prev["html"])
        ov = urllib.parse.quote(json.dumps({"fields": {"receiver": "王五"},
                                            "lines": [{"qty": 99}]}, ensure_ascii=False))
        prev2 = http("GET", f"/api/print/preview?node=receive&source=asn&doc_id={asn_id}&overrides={ov}")
        check("临时编辑进入渲染", prev2["document"]["lines"][0]["qty"] == 99 and
              prev2["document"]["fields"]["receiver"] == "王五")
        with closing(sqlite3.connect(db)) as c:
            note = c.execute("SELECT note FROM asn WHERE id=?", (asn_id,)).fetchone()[0]
        check("临时编辑未回写业务单据", note == "")
        pdf = http("GET", f"/api/print/pdf?node=receive&source=asn&doc_id={asn_id}&overrides={ov}")
        check("Chrome 生成 PDF", pdf[:5] == b"%PDF-" and len(pdf) > 5000, len(pdf))

        # ── 前端 ────────────────────────────────────────────────────
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True, channel="chrome",
                                            args=["--no-proxy-server"])
            except Exception:
                browser = p.chromium.launch(headless=True, args=["--no-proxy-server"])
            page = browser.new_page()
            js_errors, bad = [], []
            page.on("pageerror", lambda e: js_errors.append(str(e)))
            page.on("response", lambda r: bad.append((r.status, r.url))
                    if r.status >= 400 and "/api/" in r.url and "favicon" not in r.url else None)

            page.goto(BASE + "/print_templates.html", wait_until="networkidle")
            check("模板页标题", "打印模板配置" in page.title())
            check("导航含模板配置", page.locator("a[data-href='/print_templates.html']").count() == 1)
            page.locator("#nodeFilter").select_option("receive")
            page.wait_for_timeout(200)
            check("列表有自定义模板", page.locator("#tplList .block").count() >= 2,
                  page.locator("#tplList .block").count())
            page.locator("#tplList button:has-text('编辑')").first.click()
            check("编辑器载入编码", page.locator("#tplCode").input_value() != "")
            check("L1 字段 chip 可点", page.locator("#fieldChips .chip").count() >= 15,
                  page.locator("#fieldChips .chip").count())
            page.locator("#tabL2").click()
            page.locator("#btnAddBlock").click()
            page.locator("#btnAddBlock").click()
            check("L2 区块渲染", page.locator("#blockList .block").count() >= 2,
                  page.locator("#blockList .block").count())
            page.locator("#blockList [data-block-title]").nth(1).fill("第二区块")
            first_title = page.locator("#blockList [data-block-title]").first.input_value()
            page.evaluate("""() => {
              const blocks = [...document.querySelectorAll('#blockList .block')];
              const dt = new DataTransfer();
              blocks[0].dispatchEvent(new DragEvent('dragstart', {bubbles:true, dataTransfer:dt}));
              blocks[1].dispatchEvent(new DragEvent('dragover', {bubbles:true, cancelable:true, dataTransfer:dt}));
              blocks[1].dispatchEvent(new DragEvent('drop', {bubbles:true, cancelable:true, dataTransfer:dt}));
            }""")
            page.wait_for_timeout(200)
            after_title = page.locator("#blockList [data-block-title]").first.input_value()
            check("区块拖拽改顺序", first_title != after_title, (first_title, after_title))
            page.locator("#btnAddBlock").click()
            check("添加区块", page.locator("#blockList .block").count() >= 3)

            page.goto(BASE + f"/print_preview.html?node=receive&source=asn&doc_id={asn_id}",
                      wait_until="networkidle")
            page.wait_for_timeout(500)
            check("预览页标题", "打印预览" in page.title())
            check("导航含打印预览", page.locator("a[data-href='/print_preview.html']").count() == 1)
            check("表头可编辑字段", page.locator("#editFields input").count() >= 1,
                  page.locator("#editFields input").count())
            check("明细可编辑行", page.locator("#editLines .lineedit").count() == 1)
            src1 = page.locator("#pdfFrame").get_attribute("src") or ""
            check("iframe 指向 PDF 端点", "/api/print/pdf?" in src1, src1[:80])
            page.locator("#editFields input").first.fill("预览临时收货人")
            page.wait_for_timeout(800)
            src2 = page.locator("#pdfFrame").get_attribute("src") or ""
            check("临时编辑刷新 PDF", src1 != src2 and "preview" not in src2)
            r = page.request.get(BASE + src2)
            check("PDF 响应可打印", r.status == 200 and r.body()[:5] == b"%PDF-",
                  (r.status, r.body()[:5]))
            page.goto(BASE + "/asns.html", wait_until="networkidle")
            check("收货单列表有打印入口", page.locator("tbody button:has-text('打印')").count() >= 1,
                  page.locator("tbody button:has-text('打印')").count())

            after = snapshot(db)
            check("打印不改业务数据", before == after, (before, after))
            check("I1 主账一致", http("GET", "/api/stock/reconcile")["consistent"])
            check("I2/I3 批次一致", http("GET", "/api/stock/batch-reconcile")["consistent"])
            check("无 JS 运行时错误", not js_errors, "; ".join(js_errors[:2]))
            check("无业务 4xx/5xx", all(s == 400 for s, _ in bad), str(bad[:3]))
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
