"""Install editable outbound defaults in the selected demonstration database."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import constants as C
import putaway_rules as PR


def seed(db):
    catalog = [
        ("调拨 · 有效期优先", {"doc_type": "调拨"}, C.OUTBOUND_STRATEGY_STRICT_FEFO,
         "先使用到期日最早的可用批次，相同效期优先拣选位。"),
        ("销售 · 先专属后通用", {"doc_type": "销售"}, C.OUTBOUND_STRATEGY_OWN_FIRST,
         "先使用本客户专属库存，再使用通用库存。每组按效期顺序分配。"),
        ("通用 · 有效期优先", {}, C.OUTBOUND_STRATEGY_STRICT_FEFO,
         "其他出库类型按最早失效日期分配，无效期的批次排最后。"),
    ]
    count = 0
    for strategy in db.execute("SELECT id FROM putaway_rule_set WHERE status = 'active'").fetchall():
        set_id = strategy[0]
        previous = PR.list_rule_rows(db, set_id, C.RULE_TYPE_OUTBOUND)
        names = {rule["name"] for rule in previous}
        priority = max((rule["priority"] for rule in previous), default=0)
        for name, condition, method, note in catalog:
            if name in names:
                continue
            priority += 10
            PR.save_rule(db, {
                "rule_set_id": set_id, "rule_type": C.RULE_TYPE_OUTBOUND, "name": name, "priority": priority,
                "condition": condition, "steps": [{"type": "all_active"}],
                "limits": {"quality_state": {"reject_expired": True}},
                "score": {"strategy": method, "prefer_picking": True}, "note": note,
            })
            count += 1
    return count


if __name__ == "__main__":
    from contextlib import closing
    import main

    main.init_db()
    with closing(main._connect(main.DB_PATH)) as db, db:
        print(f"Added {seed(db)} outbound allocation rules.")
