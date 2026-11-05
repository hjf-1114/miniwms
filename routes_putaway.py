"""上架推荐 API（M20 简版）：只读预览，不写库存。

GET /api/putaway/recommend?asn_id=&line_id=&qty=&top_n=
前端「一键采纳」只是把返回的 location_id 填进现有
POST /api/asns/<id>/putaway 的 to_location_id，不新增过账路径。
"""
from flask import Blueprint, jsonify, request

from main import get_db
import putaway_service

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

    try:
        data = putaway_service.recommend(get_db(), asn_id, line_id, qty, top_n)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, **data})
