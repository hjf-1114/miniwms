"""M40 工作量统计 API（服务层见 ``workload_service.py``）。

纯只读，所有端点经 ``scope.scoped_where()`` 注入货主条件：
- GET /api/workload/dict       字典（节点 / 时间段 / 数据源 / 口径）
- GET /api/workload/overview   概览卡 + Top N + 近 14 天趋势
- GET /api/workload/operators  操作人下拉（当前可见事件去重）
- GET /api/workload/by-operator 按人统计
- GET /api/workload/by-node    按节点统计
- GET /api/workload/by-period  按日 / 周 / 月统计
- GET /api/workload/events     事件明细（分页，用于下钻核对）
"""
from flask import Blueprint, jsonify, request

import workload_service as WL
from main import get_db

workload_bp = Blueprint("workload", __name__)


def _run(action, *args):
    try:
        return jsonify(action(get_db(), *args))
    except WL.WorkloadError as e:
        return jsonify(ok=False, error=str(e), code=e.code), 400


@workload_bp.get("/api/workload/dict")
def workload_dict():
    return jsonify(WL.dictionary())


@workload_bp.get("/api/workload/overview")
def workload_overview():
    return _run(WL.overview, request.args)


@workload_bp.get("/api/workload/operators")
def workload_operators():
    return _run(WL.operators, request.args)


@workload_bp.get("/api/workload/by-operator")
def workload_by_operator():
    return _run(WL.by_operator, request.args)


@workload_bp.get("/api/workload/by-node")
def workload_by_node():
    return _run(WL.by_node, request.args)


@workload_bp.get("/api/workload/by-period")
def workload_by_period():
    return _run(WL.by_period, request.args)


@workload_bp.get("/api/workload/events")
def workload_events():
    return _run(WL.events_report, request.args)
