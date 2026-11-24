"""Build the real MiniWMS frontend with read-only API data for GitHub Pages."""

import argparse
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
PAGES = (
    "app", "index", "asns", "asn_receive", "asn_putaway", "dns", "stock",
    "stock_batch", "putaway_rules",
)
LISTS = {
    "asns": "asns", "dns": "dns", "owners": "owners", "customers": "customers",
    "suppliers": "suppliers", "products": "products", "warehouses": "warehouses",
    "locations": "locations", "stock": "stocks", "stock/batch": "stock_batches",
    "movements": "movements", "putaway-rule-sets": "rule_sets",
    "putaway-rules": "rules", "external-doc-types": "doc_types",
    "putaway-recommendations": "recommendations",
}


def snapshot():
    from seed_demo import seed as seed_basic
    from seed_putaway_demo import seed as seed_putaway
    from seed_module_demo import get, post

    seed_basic(reset=False)
    seed_putaway()
    lists, responses, recommendations = {}, {}, {}
    warehouses = get("/api/warehouses?page_size=100")["warehouses"]
    preferred = next(row for row in warehouses if row["code"] == "DEMO-PA-WH")
    owner = next(row for row in get("/api/owners?page_size=100")["owners"] if row["code"] == "DEMO-PA-OWNER")
    product = next(row for row in get("/api/products?page_size=100")["products"] if row["sku"] == "DEMO-PA-STD")
    pending = post("/api/asns", {
        "code": "DEMO-WAIT-ACCEPT", "source_warehouse": preferred["id"], "owner_id": owner["id"],
        "lines": [{"product_id": product["id"], "expected_qty": 6}],
    })
    post(f"/api/asns/{pending['asn']['id']}/count", {"line_id": pending["lines"][0]["id"], "qty": 6})
    source = next(row for row in warehouses if row["code"] == "WH-SZ")
    basic_owner = next(row for row in get("/api/owners?page_size=100")["owners"] if row["code"] == "OW-TMALL")
    basic_product = next(row for row in get("/api/products?page_size=100")["products"] if row["sku"] == "SKU-001")
    storage = next(row for row in get("/api/locations?page_size=100")["locations"] if row["code"] == "A-01-01")
    transfer = post("/api/dns", {
        "code": "DEMO-TR-OUT", "doc_type": "调拨", "owner_id": basic_owner["id"],
        "source_warehouse": source["id"], "target_warehouse": preferred["id"],
        "lines": [{"product_id": basic_product["id"], "ordered_qty": 4}],
    })
    transfer_path = f"/api/dns/{transfer['dn']['id']}"
    post(transfer_path + "/pick", {
        "line_id": transfer["lines"][0]["id"], "location_id": storage["id"], "qty": 4,
    })
    post(transfer_path + "/ship", {})

    for resource, key in LISTS.items():
        path = f"/api/{resource}"
        data = get(path + "?page_size=100")
        rows = data.pop(key)
        if data.get("total", len(rows)) > len(rows):
            raise RuntimeError(f"Demo export exceeds the first page: {path}")
        lists[path] = {"key": key, "rows": rows, "extra": data}
    for resource in ("batch/fields", "putaway-rule-types", "stock/reconcile", "stock/batch-reconcile"):
        responses[f"/api/{resource}"] = get(f"/api/{resource}")

    for row in lists["/api/asns"]["rows"]:
        path = f"/api/asns/{row['id']}"
        body = get(path)
        responses[path] = body
        for line in body["lines"]:
            for receipt in line["batch_receipts"]:
                if receipt["remaining_putaway"] <= 1e-9:
                    continue
                code = receipt["batch_no"]
                args = (f"asn_id={row['id']}&line_id={line['id']}&receipt_barcode={code}"
                        f"&qty={receipt['remaining_putaway']}&top_n=8")
                recommendations[code] = get("/api/putaway/recommend?" + args)
    for row in lists["/api/dns"]["rows"]:
        path = f"/api/dns/{row['id']}"
        for suffix in ("", "/reservation-check"):
            responses[path + suffix] = get(path + suffix)
    for row in lists["/api/dns"]["rows"]:
        responses[f"/api/reservation-logs?dn_id={row['id']}"] = get(f"/api/reservation-logs?dn_id={row['id']}")
    return {"lists": lists, "responses": responses, "warehouses": warehouses,
            "preferredWarehouse": preferred["id"], "recommendations": recommendations}


def build(output, prefix):
    with TemporaryDirectory(prefix="miniwms-pages-") as temporary:
        os.environ["MINIWMS_DB"] = str(Path(temporary) / "preview.db")
        os.environ["MINIWMS_ALLOW_ANONYMOUS"] = "1"
        data = snapshot()

    output.mkdir(parents=True, exist_ok=True)
    (output / "js").mkdir(exist_ok=True)
    (output / "css").mkdir(exist_ok=True)
    (output / ".nojekyll").touch()
    page_path = re.compile(r"(?<![\w./])/(?=(?:[a-z][\w-]*\.html\b|css/|js/))")
    public_pages = [f"{prefix}/{page}.html" for page in PAGES]
    config = ("window.MWS_PREVIEW_PAGES = " + json.dumps(public_pages) + ";\n"
              "window.MWS_PREVIEW_DATA = " + json.dumps(data, ensure_ascii=False) + ";\n")
    (output / "js" / "preview-data.js").write_text(config, encoding="utf-8")
    (output / "js" / "preview-api.js").write_bytes((ROOT / "preview" / "api.js").read_bytes())
    frontend = ROOT / "web_app"
    files = [frontend / f"{page}.html" for page in PAGES]
    files.extend((frontend / "js").glob("*.js"))
    files.extend((frontend / "css").glob("*.css"))
    for source in files:
        template = source.read_text(encoding="utf-8")
        page = page_path.sub(lambda _: prefix + "/", template)
        if source.suffix == ".html":
            api_tag = f'<script src="{prefix}/js/api.js"></script>'
            page = page.replace(api_tag, f'<script src="{prefix}/js/preview-data.js"></script>\n'
                                f'<script src="{prefix}/js/preview-api.js"></script>\n' + api_tag)
        (output / source.relative_to(frontend)).write_text(page, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--prefix", default="/miniwms")
    arguments = parser.parse_args()
    build(arguments.output, arguments.prefix.rstrip("/"))
