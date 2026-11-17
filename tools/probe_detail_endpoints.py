"""全量详情端点探测：把每个 `/api/<资源>/<id>` 的 GET 端点都打一遍，查 5xx。

为什么需要：像 `routes_location.get_location()` 那样「函数里用了未定义的 `db`」
的缺陷，表现是**运行时 NameError → 500**，且只在那条路径被真正调用时才炸。
单测通常只覆盖列表 / 创建 / 更新 / 删除，**详情端点容易被漏测**
（库位详情就漏了 6 天、编辑功能一直不可用）。

本工具从 Flask 的 url_map 自动取出所有「带整数 id 的 GET 端点」，
用库里真实存在的 id 逐个探测，把 5xx 与「返回体非 JSON」全部列出来。

用法：
    python tools/probe_detail_endpoints.py
    python tools/probe_detail_endpoints.py --port 5152
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import closing

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
INT_RE = re.compile(r"<int:(\w+)>")


def http(method, path, expect_json=True):
    req = urllib.request.Request(f"{BASE}{path}", method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with OPENER.open(req, timeout=15) as r:
            body = r.read().decode()
            return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def seed():
    """造一批真实数据，供详情端点探测用（走真实 API）。"""
    def post(path, payload):
        req = urllib.request.Request(
            f"{BASE}{path}", data=json.dumps(payload, ensure_ascii=False).encode(),
            method="POST", headers={"Content-Type": "application/json"})
        try:
            with OPENER.open(req, timeout=15) as r:
                return json.loads(r.read().decode() or "{}")
        except Exception:
            return {}

    ow = post("/api/owners", {"code": "PROBE-OW", "name": "探针货主"})
    oid = ow.get("owner", {}).get("id")
    wh = post("/api/warehouses", {"code": "PROBE-WH", "name": "探针仓库"})
    wid = wh.get("warehouse", {}).get("id")
    zn = post("/api/zones", {"code": "PROBE-ZN", "name": "探针储区", "warehouse_id": wid})
    zid = zn.get("zone", {}).get("id")
    pr = post("/api/products", {"sku": "PROBE-P1", "name": "探针物料", "owner_id": oid})
    pid = pr.get("product", {}).get("id")
    post("/api/locations", {"code": "PROBE-L1", "name": "探针库位",
                            "warehouse_id": wid, "zone_id": zid})
    cu = post("/api/customers", {"code": "PROBE-CU", "name": "探针客户", "owner_id": oid})
    post("/api/suppliers", {"code": "PROBE-SU", "name": "探针供应商", "owner_id": oid})
    post("/api/carriers", {"code": "PROBE-CA", "name": "探针承运商"})
    post("/api/product-types", {"code": "PROBE-PT", "name": "探针类型"})
    post("/api/location-groups", {"code": "PROBE-LG", "name": "探针货位组"})
    post("/api/stocktake-plans", {"code": "PROBE-PL", "name": "探针方案",
                                  "scope_type": "full", "blind_mode": "none"})
    asn = post("/api/asns", {"code": "PROBE-ASN", "owner_id": oid,
                             "source_warehouse": wid, "doc_type": "采购",
                             "lines": [{"product_id": pid, "expected_qty": 5}]})
    print(f"  探针数据就绪：owner={oid} product={pid} asn={asn.get('asn', {}).get('id')}")


def main():
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5152)
    args = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="miniwms_probe_")
    db = os.path.join(tmp, "probe.db")
    env = os.environ.copy()
    env.update({
        "MINIWMS_DB": db, "MINIWMS_PORT": str(args.port),
        "MINIWMS_ALLOW_ANONYMOUS": "1",
        "NO_PROXY": "localhost,127.0.0.1", "no_proxy": "localhost,127.0.0.1",
        "HTTP_PROXY": "", "HTTPS_PROXY": "", "http_proxy": "", "https_proxy": "",
    })
    BASE = f"http://127.0.0.1:{args.port}"
    print(f"临时库：{db}\n服务端口：{args.port}\n")

    proc = subprocess.Popen([VENV_PY, "main.py"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        # 等服务就绪
        deadline = time.time() + 30
        ready = False
        while time.time() < deadline:
            if proc.poll() is not None:
                raise RuntimeError("Flask 进程提前退出")
            st, body = http("GET", "/api/owners?page_size=1")
            if st == 200 and '"ok": true' in body.replace('"ok":true', '"ok": true'):
                ready = True
                break
            time.sleep(0.4)
        if not ready:
            raise RuntimeError("服务启动超时")
        seed()

        # 取出所有「带整数 id 的 GET 端点」
        sys.path.insert(0, ROOT)
        os.environ["MINIWMS_DB"] = db
        import main as MAIN
        MAIN.init_db(db)
        targets = []
        for rule in MAIN.app.url_map.iter_rules():
            s = str(rule)
            if not s.startswith("/api/") or "GET" not in rule.methods:
                continue
            if not INT_RE.search(s):
                continue
            targets.append(s)
        targets.sort()

        print(f"\n探测 {len(targets)} 个详情端点 ……")
        bad = []
        for tpl in targets:
            # 用 1..3 三个 id 试，命中真实数据概率更高
            for probe_id in (1, 2, 3):
                path = re.sub(r"<int:\w+>", str(probe_id), tpl)
                st, body = http("GET", path)
                if st >= 500:
                    bad.append((path, st, body[:160]))
                    break
                if st == 200:
                    try:
                        json.loads(body)
                    except Exception:
                        bad.append((path, st, "响应不是 JSON: " + body[:120]))
                    break
                # 404 说明该 id 不存在，换下一个 id
        for path, st, detail in bad:
            print(f"  [异常] {path} → HTTP {st} | {detail}")
        if not bad:
            print("全部详情端点正常")
        else:
            print(f"\n共 {len(bad)} 个详情端点异常")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
