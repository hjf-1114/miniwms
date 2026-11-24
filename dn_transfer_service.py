"""Create the destination ASN in the transaction that ships a transfer DN."""

import json
import os

import constants as C
import scope as S
from batch_service import inventory_batch_info
from main import CONTROL_DB_PATH, gen_doc_no, get_control_db, init_warehouse_db


def prepare_destination(db, dn):
    if not dn["source_warehouse"] or not dn["target_warehouse"] or dn["source_warehouse"] == dn["target_warehouse"]:
        raise ValueError("调拨单必须指定不同的调出、调入仓库")
    control = get_control_db()
    target = control.execute("SELECT * FROM warehouse WHERE id = ?", (dn["target_warehouse"],)).fetchone()
    if target is None or target["status"] != "active":
        raise ValueError("调入仓库不存在或已停用")
    owners = S.owner_warehouse_grid(control, target["id"])
    if owners and dn["owner_id"] not in owners:
        raise ValueError("该货主未配置到调入仓库")
    target_path = init_warehouse_db(target["db_path"]) or CONTROL_DB_PATH
    source_path = db.execute("PRAGMA database_list").fetchone()["file"]
    if os.path.normcase(os.path.abspath(source_path)) == os.path.normcase(os.path.abspath(target_path)):
        return "main"
    db.execute("ATTACH DATABASE ? AS transfer_target", (target_path,))
    # SQLite 跨文件事务仅在回滚日志模式下保证进程崩溃时的原子性。
    for schema in ("main", "transfer_target"):
        mode = db.execute(f"PRAGMA {schema}.journal_mode").fetchone()[0]
        if mode not in ("delete", "truncate", "persist"):
            raise ValueError("跨库调拨要求两端使用 SQLite 回滚日志模式")
    return "transfer_target"


def create_transfer_asn(db, schema, dn, lines, picks):
    owner = dn["owner_id"]
    manifest = {}
    customer_ids = {}

    def mapped_customer(source_id):
        if not source_id:
            return None
        if source_id not in customer_ids:
            row = db.execute(f"""
                SELECT dst.id FROM main.customer src JOIN {schema}.customer dst
                ON dst.code = src.code AND dst.owner_id = src.owner_id WHERE src.id = ?
            """, (source_id,)).fetchone()
            customer_ids[source_id] = row["id"] if row else None
        return customer_ids[source_id]

    for pick in picks:
        info = inventory_batch_info(db, owner, pick["product_id"], dict(pick))
        scoped_customer = mapped_customer(pick["designated_customer_id"])
        if pick["designated_customer_id"] and scoped_customer is None:
            raise ValueError("调入仓缺少该批库存的指定客户档案")
        manifest.setdefault(pick["line_id"], []).append({
            "source_batch_no": pick["batch_no"], "qty": pick["qty"],
            "designated_customer_id": scoped_customer, "batch_info": info,
        })

    target_lines = []
    for line in lines:
        product = db.execute(f"SELECT id FROM {schema}.product WHERE owner_id = ? AND sku = ?",
                             (owner, line["sku"])).fetchone()
        if not product:
            raise ValueError(f"调入仓缺少该货主的物料档案：{line['sku']}")
        snap = json.loads(line["extra"] or "{}")
        mapped = mapped_customer(snap.get("designated_customer_id"))
        if snap.get("designated_customer_id") and not mapped:
            raise ValueError("调入仓缺少调拨明细的指定客户档案")
        snap["designated_customer_id"] = str(mapped or "")
        batches = manifest.get(line["id"], [])
        if batches:
            for key in batches[0]["batch_info"]:
                values = {batch["batch_info"][key] for batch in batches}
                if len(values) == 1 and any(values):
                    snap[key] = values.pop()
            customers = {batch["designated_customer_id"] for batch in batches}
            snap["designated_customer_id"] = str(next(iter(customers)) or "") if len(customers) == 1 else ""
        snap["transfer_batches"] = batches
        target_lines.append((product["id"], line["ordered_qty"], json.dumps(snap, ensure_ascii=False)))

    code = gen_doc_no(db, f"{schema}.asn", "ASN-TR")
    # Count-based legacy numbering may encounter a gap after an administrator removes a draft.
    while db.execute(f"SELECT 1 FROM {schema}.asn WHERE code = ?", (code,)).fetchone():
        stem, seq = code.rsplit("-", 1)
        code = f"{stem}-{int(seq) + 1:03d}"
    row = db.execute(f"""
        INSERT INTO {schema}.asn (
            code, owner_id, doc_type, external_no, order_source, source_warehouse, customer_id,
            customer_name, customer_contact, customer_phone, customer_address, carrier_name,
            total_qty, category_count, extra1, extra2, extra3, note,
            is_virtual, is_direct, is_cold_chain, is_hazard, external_doc_type
        ) VALUES (?, ?, '调拨', ?, '仓库调拨', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'TR')
    """, (code, owner, dn["code"], dn["target_warehouse"], mapped_customer(dn["customer_id"]),
          dn["customer_name"], dn["customer_contact"], dn["customer_phone"], dn["customer_address"],
          dn["carrier_name"], round(sum(line["ordered_qty"] for line in lines), 6), len(lines),
          dn["extra1"], dn["extra2"], dn["extra3"], f"由调拨出库单 {dn['code']} 发货生成",
          *(dn[key] for key in C.DOC_FLAG_FIELDS)))
    asn_id = row.lastrowid
    db.executemany(f"INSERT INTO {schema}.asn_line (asn_id, product_id, expected_qty, extra) VALUES (?, ?, ?, ?)",
                   [(asn_id, *line) for line in target_lines])
    return asn_id, code
