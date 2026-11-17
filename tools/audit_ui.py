"""前端巡检：真实浏览器遍历所有页面，点遍弹窗按钮，捕获运行时错误。

用途：找出「页面能打开但弹窗打不开 / 下拉为空 / 脚本报错」这类**只有交互才暴露**的前端缺陷。
M32–M34 与 PRD 自审中已发现 3 处同类缺陷（供应商弹窗、客户地区联动、库存物料下拉），
本脚本把这类检查固化为可复跑工具。

用法（Playwright 只装在 C:/Python311/python.exe）：
    C:/Python311/python.exe tools/audit_ui.py
    C:/Python311/python.exe tools/audit_ui.py --port 5145 --keep

不修改任何业务数据：只打开弹窗、填必填项并关闭，**不提交**。
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
PORT = 5145
BASE = ""
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
ISSUES = []


def note(kind, page, detail):
    ISSUES.append((kind, page, detail))
    print(f"  [{kind}] {page} · {detail}")


def http(method, path, payload=None, expect=(200, 201)):
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with OPENER.open(req, timeout=15) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode() or "{}")
        if e.code not in expect:
            raise RuntimeError(f"{method} {path} → HTTP {e.code}: {str(body)[:200]}")
        return body
    except Exception:
        return {}


def wait_ready(proc, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("Flask 进程提前退出")
        try:
            http("GET", "/api/owners?page_size=1")
            return True
        except Exception:
            time.sleep(0.4)
    raise RuntimeError("服务启动超时")


# 这些页面用「页内卡片型表单」而不是 modal 弹窗（openForm 调用数为 0），
# 巡检时改为检查页内是否出现可填写的表单区
INLINE_FORM_PAGES = {"stocktake_plans.html", "stock_adjustments.html",
                     "inventory_transfers.html", "putaway_rules.html",
                     "print_templates.html", "care.html"}

# (页面, 打开弹窗的按钮文本) —— 覆盖所有「新建 / + 新建 XX」入口
PAGE_BUTTONS = [
    ("owners.html", "+ 新建货主"),
    ("products.html", "+ 新建物料"),
    ("customers.html", "+ 新建客户"),
    ("suppliers.html", "+ 新建供应商"),
    ("carriers.html", "+ 新建承运商"),
    ("warehouses.html", "+ 新建仓库"),
    ("zones.html", "+ 新建储区"),
    ("locations.html", "+ 新建库位"),
    ("location_groups.html", "+ 新建货位组"),
    ("product_types.html", "+ 新建产品类型"),
    ("drivers.html", "+ 新建司机"),
    ("vehicles.html", "+ 新建车辆"),
    ("asns.html", "+ 新建收货单"),
    ("dns.html", "+ 新建出库单"),
    ("transfers.html", "+ 新建移库单"),
    ("stocktakes.html", "+ 新建盘点单"),
    ("stocktake_plans.html", "+ 新建方案"),
    ("stock_adjustments.html", "+ 手工制单"),
    ("inventory_transfers.html", "+ 制单"),
    ("serials.html", "+ 登记序列号"),
    ("roles.html", "+ 新建角色"),
    ("users.html", "+ 新建用户"),
    ("putaway_rules.html", "+ 新建策略包"),
    ("print_templates.html", "+ 新建"),
    ("outbound_allocation.html", None),
    ("dn_review.html", None),
    ("dn_order.html", None),
    ("care.html", "创建计划"),
    ("gsp.html", None),
    ("workload.html", None),
    ("operation_report.html", None),
    ("stock.html", None),
    ("stock_batch.html", None),
    ("batches.html", "+ 预配置批次"),
]

LOGIN_PAGES = {"roles.html", "users.html", "owner_warehouses.html"}
_TOKEN = {}


def login_token():
    """用默认管理员账号换一个 token（仅用于巡检需要登录的页面）。"""
    if "t" in _TOKEN:
        return _TOKEN["t"]
    try:
        d = http("POST", "/api/auth/login",
                 {"username": "admin", "password": "admin123"})
        _TOKEN["t"] = d.get("token") or ""
    except Exception:
        _TOKEN["t"] = ""
    return _TOKEN["t"]


# 页面路径 → 需要先造的数据（通过真实 API）
def seed():
    """造足够的演示数据，让「下拉为空」不再是因为缺数据造成的噪声。"""
    ow = http("POST", "/api/owners", {"code": "AUDIT-OW", "name": "巡检货主"})
    oid = ow.get("owner", {}).get("id")
    http("POST", "/api/products", {"sku": "AUDIT-P1", "name": "巡检物料", "owner_id": oid})
    wh = http("POST", "/api/warehouses", {"code": "AUDIT-WH", "name": "巡检仓库"})
    wid = wh.get("warehouse", {}).get("id")
    zn = http("POST", "/api/zones", {"code": "AUDIT-ZN", "name": "巡检储区",
                                    "warehouse_id": wid})
    zid = zn.get("zone", {}).get("id")
    for i in (1, 2, 3):
        http("POST", "/api/locations", {"code": f"AUDIT-L{i}", "name": f"巡检库位{i}",
                                        "warehouse_id": wid, "zone_id": zid,
                                        "loc_type": "storage" if i > 1 else "receiving"})
    http("POST", "/api/customers", {"code": "AUDIT-CU", "name": "巡检客户", "owner_id": oid})
    http("POST", "/api/suppliers", {"code": "AUDIT-SU", "name": "巡检供应商", "owner_id": oid})
    http("POST", "/api/carriers", {"code": "AUDIT-CA", "name": "巡检承运商"})
    http("POST", "/api/stocktake-plans", {"code": "AUDIT-PL", "name": "巡检方案",
                                          "scope_type": "full", "blind_mode": "none"})
    return oid


def audit(page, browser):
    """打开页面 → 点新建按钮 → 收集 JS 错误 → 关闭弹窗。"""
    from playwright.sync_api import sync_playwright
    errors = []
    pg = browser.new_page()
    pg.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
    pg.on("console", lambda m: errors.append("console.error: " + m.text)
          if m.type == "error" and "favicon" not in m.text
          and "Failed to load resource" not in m.text else None)
    if page in LOGIN_PAGES:
        tok = login_token()
        if tok:
            pg.goto(BASE + "/login.html", wait_until="domcontentloaded")
            pg.evaluate("t => { localStorage.setItem('miniwms_token', t); }", tok)
    try:
        pg.goto(BASE + "/" + page, wait_until="networkidle")
    except Exception as e:
        note("加载失败", page, str(e)[:120])
        pg.close()
        return
    pg.wait_for_timeout(600)

    # 1) 页面加载后的错误
    for e in list(errors):
        note("JS 错误", page, e[:200])
    errors.clear()

    # 2) 下拉选项非空检查：所有 select 至少有一个非空 option
    empty_selects = pg.evaluate("""() => {
      const out = [];
      document.querySelectorAll('select').forEach(s => {
        if (s.id) return;                       // 工具栏筛选下拉可合法为空
        const opts = Array.from(s.options).filter(o => o.value !== '' && o.textContent.trim());
        if (!opts.length) out.push(s.name || s.className || '(未命名)');
      });
      return out;
    }""")
    for s in empty_selects:
        note("下拉为空", page, f"select[{s}] 没有可用选项")

    # 3) 点「新建」按钮 → 弹窗必须能打开且不报错
    btn_text = dict(PAGE_BUTTONS).get(page)
    if btn_text:
        btn = pg.get_by_role("button", name=btn_text, exact=True)
        if btn.count() == 0:
            btn = pg.get_by_role("button", name=btn_text, exact=False)
        if btn.count() == 0:
            note("按钮缺失", page, f"未找到按钮「{btn_text}」")
        else:
            try:
                btn.first.click(timeout=5000)
                pg.wait_for_timeout(700)
            except Exception as e:
                note("点击失败", page, f"{btn_text} → {str(e)[:120]}")
            else:
                if page in INLINE_FORM_PAGES:
                    # 页内卡片型表单：确认页内出现可填写的表单区
                    if pg.locator("form, .card input, .card select, .card textarea").count() == 0:
                        note("页内表单未出现", page, f"点击「{btn_text}」后页内没有可填写表单区")
                    for e in list(errors):
                        note("表单 JS 错误", page, e[:200])
                    errors.clear()
                elif pg.locator(".modal-overlay .modal").count() == 0:
                    note("弹窗未打开", page, f"点击「{btn_text}」后没有弹窗（页面脚本报错导致）")
                else:
                    # 弹窗内所有 select 必须有非空 option（除显式 placeholder）
                    bad = pg.evaluate("""() => {
                      const out = [];
                      document.querySelectorAll('.modal select').forEach(s => {
                        const hasPh = Array.from(s.options).some(o => o.value === '');
                        const real = Array.from(s.options).filter(o => o.value !== '' && o.textContent.trim());
                        const nm = s.name || '(未命名)';
                        if (nm === 'city') return;   // 省市联动项：未选省时为空属正常
                        if (!real.length && !hasPh) out.push(nm);
                        else if (!real.length) out.push('EMPTY:' + nm);
                      });
                      return out;
                    }""")
                    for b in bad:
                        note("弹窗下拉为空", page, b)
                    # 关闭弹窗
                    try:
                        pg.keyboard.press("Escape")
                        pg.wait_for_timeout(200)
                        if pg.locator(".modal-overlay .modal").count():
                            pg.locator(".modal-overlay .modal button").last.click(timeout=3000)
                            pg.wait_for_timeout(200)
                    except Exception:
                        pass
                for e in list(errors):
                    note("弹窗 JS 错误", page, e[:200])
                errors.clear()
    pg.close()


def run(port, keep):
    global BASE, PORT
    PORT = port
    BASE = f"http://127.0.0.1:{PORT}"
    tmp = tempfile.mkdtemp(prefix="miniwms_audit_")
    db = os.path.join(tmp, "audit.db")
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
        seed()
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True, channel="chrome",
                                            args=["--no-proxy-server"])
            except Exception as e:
                print(f"  [启动回落] channel=chrome 失败：{str(e)[:160]}")
                browser = p.chromium.launch(headless=True, args=["--no-proxy-server"])
            for page, _btn in PAGE_BUTTONS:
                audit(page, browser)
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

    print(f"\n=== 巡检完成：发现 {len(ISSUES)} 个问题 ===")
    if not ISSUES:
        print("全部页面加载正常、弹窗可开、下拉非空")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    sys.exit(run(a.port, a.keep))
