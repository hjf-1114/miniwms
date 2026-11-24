"""上架推荐 API（M20 简版）：只读预览，不写库存。

GET /api/putaway/recommend?asn_id=&line_id=&qty=&top_n=
前端「一键采纳」只是把返回的 location_id 填进现有
POST /api/asns/<id>/putaway 的 to_location_id，不新增过账路径。
"""
import math

from flask import Blueprint, g, jsonify, request

from batch_service import get_asn_receipt
from inventory_service import EPS
from main import get_db, current_scope
import putaway_service
import scope as S

putaway_bp = Blueprint("putaway", __name__, url_prefix="/api/putaway")


@putaway_bp.route("/recommend", methods=["GET"])
def recommend():
    args = request.args
    try:
        asn_id = int(args.get("asn_id"))
        line_id = int(args.get("line_id"))
        top_n = min(20, max(1, int(args.get("top_n", 5))))
        qty = float(args.get("qty") or 0) or None
    except (TypeError, ValueError):
        return jsonify({"ok": False, "errors": [
            "asn_id/line_id/top_n 必填且为整数，qty 为数字"
        ]}), 400

    db = get_db()
    try:
        asn = db.execute("SELECT owner_id, source_warehouse FROM asn WHERE id = ?", (asn_id,)).fetchone()
        if asn is not None:
            S.assert_owner_allowed(current_scope(), asn["owner_id"])
            if g.warehouse_id and asn["source_warehouse"] not in (None, g.warehouse_id):
                raise S.ScopeError("该入库单不属于当前仓库")
        barcode = (args.get("receipt_barcode") or "").strip()
        receipt = get_asn_receipt(db, asn_id, line_id, barcode) if barcode else None
        if qty is not None and not math.isfinite(qty):
            raise ValueError("上架数量无效")
        if receipt is not None:
            qty = qty if qty is not None else receipt["remaining_putaway"]
            if qty <= EPS or qty > receipt["remaining_putaway"] + EPS:
                raise ValueError("推荐数量必须为正且不超过该批次号的待上架数量")
        data = putaway_service.recommend(db, asn_id, line_id, qty, top_n, receipt=receipt)
        db.commit()          # M25 推荐日志落库；不改库存
    except S.ScopeError:
        raise
    except ValueError as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, **data})
