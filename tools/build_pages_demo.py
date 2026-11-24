"""Package the MiniWMS frontend, Flask business code and synthetic SQLite seed."""

import argparse
import hashlib
import os
from pathlib import Path
import re
import subprocess
from zipfile import ZIP_DEFLATED, ZipFile
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
def seed_browser_database():
    from datetime import date, timedelta
    from seed_demo import seed as seed_basic
    from seed_putaway_demo import seed as seed_putaway
    from seed_module_demo import get, post
    from seed_outbound_rules import seed as seed_outbound
    from contextlib import closing
    import main

    seed_basic(reset=False)
    seed_putaway()
    with closing(main._connect(main.DB_PATH)) as db, db:
        seed_outbound(db)
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
    level = post(f"/api/products/{basic_product['id']}/packages", {
        "pkg_level": "2级", "qty_per_package": 2,
    })["package"]
    post(f"/api/products/{basic_product['id']}/barcodes", {"barcode": "DEMO-PACK-2", "package_id": level["id"]})
    post("/api/products", {"sku": "DEMO-BOX-MATERIAL", "name": "示例纸箱", "category": "包装耗材",
                           "barcode": "DEMO-CARTON", "owner_id": basic_owner["id"]})
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
    receiving = next(row for row in get("/api/locations?page_size=100")["locations"] if row["code"] == "RECV-01")
    for suffix, days, quantity in (("EARLY", 45, 3), ("LATE", 180, 8)):
        target = post("/api/locations", {
            "code": f"DEMO-ALLOC-{suffix}", "name": f"DEMO-ALLOC-{suffix}",
            "loc_type": "picking", "warehouse_id": source["id"],
            "location_group": "DEMO-ACTIVITY" if suffix == "EARLY" else "DEMO-FULL",
        })["location"]
        order = post("/api/asns", {
            "code": f"ASN-DEMO-{suffix}", "owner_id": basic_owner["id"], "source_warehouse": source["id"],
            "lines": [{"product_id": basic_product["id"], "expected_qty": quantity}],
        })
        path = f"/api/asns/{order['asn']['id']}"
        line = order["lines"][0]["id"]
        count = post(path + "/count", {"line_id": line, "qty": quantity})
        receipt = post(path + "/receive", {
            "line_id": line, "receiving_count_id": count["count_id"], "qty": quantity,
            "location_id": receiving["id"], "batch_no": f"DEMO-{suffix}",
            "expiry_date": (date.today() + timedelta(days=days)).isoformat(),
        })
        post(path + "/putaway", {
            "line_id": line, "receipt_barcode": receipt["batch"]["batch_no"], "qty": quantity,
            "from_location_id": receiving["id"], "to_location_id": target["id"],
        })

    customer = next(row for row in get("/api/customers?page_size=100")["customers"] if row["owner_id"] == basic_owner["id"])
    post("/api/dns", {
        "code": "DN-DEMO-WORK", "workflow_required": True, "owner_id": basic_owner["id"],
        "customer_id": customer["id"], "source_warehouse": source["id"],
        "lines": [{"product_id": basic_product["id"], "ordered_qty": 6}],
    })


def build(output, prefix):
    output.mkdir(parents=True, exist_ok=True)
    (output / "js").mkdir(exist_ok=True)
    (output / "css").mkdir(exist_ok=True)
    (output / ".nojekyll").touch()
    with TemporaryDirectory(prefix="miniwms-pages-") as temporary:
        seed_path = Path(temporary) / "preview.db"
        os.environ["MINIWMS_DB"] = str(seed_path)
        os.environ["MINIWMS_ALLOW_ANONYMOUS"] = "1"
        seed_browser_database()
        (output / "js" / "sample-db.bin").write_bytes(seed_path.read_bytes())

    tracked = subprocess.check_output(["git", "ls-files", "-z", "--", "*.py"], cwd=ROOT).decode().split("\0")
    with ZipFile(output / "js" / "server.zip", "w", ZIP_DEFLATED) as archive:
        for filename in tracked:
            if filename and "/" not in filename:
                archive.write(ROOT / filename, filename)
    (output / "js" / "preview-api.js").write_bytes((ROOT / "preview" / "api.js").read_bytes())
    (output / "js" / "demo-worker.js").write_bytes((ROOT / "preview" / "worker.js").read_bytes())
    page_path = re.compile(r"(?<![\w./])/(?=(?:[a-z][\w-]*\.html\b|css/|js/))")
    frontend = ROOT / "web_app"
    release = hashlib.sha256((output / "js" / "server.zip").read_bytes()).hexdigest()[:12]
    files = [*frontend.glob("*.html"), *(frontend / "js").glob("*.js"), *(frontend / "css").glob("*.css")]
    for source in files:
        template = source.read_text(encoding="utf-8")
        page = page_path.sub(lambda _: prefix + "/", template)
        if source.suffix == ".html":
            api_tag = f'<script src="{prefix}/js/api.js"></script>'
            page = page.replace(api_tag, f'<script src="{prefix}/js/preview-api.js?v={release}"></script>\n' + api_tag)
        (output / source.relative_to(frontend)).write_text(page, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--prefix", default="/miniwms")
    arguments = parser.parse_args()
    build(arguments.output, arguments.prefix.rstrip("/"))
