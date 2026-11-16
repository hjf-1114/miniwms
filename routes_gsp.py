"""M39 GSP 合规报表 API（服务层见 ``gsp_service.py``）。

纯只读，不改库存、不写任何流水：
- GET /api/gsp/dict                报表字典（类型 / 效期分级 / 追溯环节 / 原因码）
- GET /api/gsp/overview            合规概览（各报表总量 + 效期分级 + 异常计数）
- GET /api/gsp/trace               批次追溯（batch_no / batch_key / product_id）
- GET /api/gsp/expiry              效期预警（level / days / product_id 过滤）
- GET /api/gsp/care                养护留痕（项目 / 动作 / 结果 / 原因码 / 日期）
- GET /api/gsp/receipt             验收留痕（批号 / 单据类型 / 质量 / 日期）
- GET /api/gsp/review              复核差异留痕（原因码 / 动作 / DN / 批号 / 日期）

所有端点经 scope.scoped_where() 注入货主条件，上下文缺失由 403 处理器统一拒绝。
"""
from flask import Blueprint, jsonify, request

import gsp_service as GS
from main import get_db

gsp_bp = Blueprint("gsp", __name__)


def _run(action, *args):
    try:
        return jsonify(action(get_db(), *args))
    except GS.GspError as e:
        return jsonify(ok=False, error=str(e), code=e.code), 400


@gsp_bp.route("/api/gsp/dict", methods=["GET"])
def gsp_dict():
    return jsonify(ok=True, **GS.dictionary())


@gsp_bp.route("/api/gsp/overview", methods=["GET"])
def gsp_overview():
    return _run(GS.overview)


@gsp_bp.route("/api/gsp/trace", methods=["GET"])
def gsp_trace():
    return _run(GS.trace_report, request.args)


@gsp_bp.route("/api/gsp/expiry", methods=["GET"])
def gsp_expiry():
    return _run(GS.expiry_report, request.args)


@gsp_bp.route("/api/gsp/care", methods=["GET"])
def gsp_care():
    return _run(GS.care_report, request.args)


@gsp_bp.route("/api/gsp/receipt", methods=["GET"])
def gsp_receipt():
    return _run(GS.receipt_report, request.args)


@gsp_bp.route("/api/gsp/review", methods=["GET"])
def gsp_review():
    return _run(GS.review_report, request.args)
