"""ABC 分类：/api/abc 的分布概览、计算预览与重算。

端点
----
- ``GET  /api/abc/dict``       字典（分类枚举 / 阈值 / 窗口天数）
- ``GET  /api/abc/overview``   当前分布（按货主的 A/B/C 数量 + 未分类数）
- ``GET  /api/abc/compute``    计算预览（**只读**，不写库）
- ``POST /api/abc/recalculate`` 重算并写回 ``product.abc_class``

写操作**只改货品的一列分类标记**，不改库存、不改单据。
"""
from flask import Blueprint, jsonify, request

import abc_service as ABC
import constants as C
from main import get_db, request_payload

abc_bp = Blueprint("abc", __name__, url_prefix="/api/abc")


def _error(e):
    return jsonify(ok=False, error=str(e), code=e.code), 400


@abc_bp.get("/dict")
def dict_data():
    return jsonify(ok=True, classes=list(C.ABC_CLASSES),
                   thresholds={"a": C.ABC_A_CUM, "b": C.ABC_B_CUM},
                   window_days=C.ABC_WINDOW_DAYS,
                   basis="近 N 天出库金额累计占比（按货主分别计算）")


@abc_bp.get("/overview")
def overview():
    db = get_db()
    try:
        return jsonify(ok=True, **ABC.overview(db))
    except ABC.AbcError as e:
        return _error(e)


@abc_bp.get("/compute")
def compute():
    """计算预览：只读，不写库。"""
    db = get_db()
    days = request.args.get("days") or C.ABC_WINDOW_DAYS
    try:
        days = int(days)
    except (TypeError, ValueError):
        return _error(ABC.AbcError("days 必须是整数"))
    try:
        items = ABC.compute(db, days)
    except ABC.AbcError as e:
        return _error(e)
    return jsonify(ok=True, days=days, items=items, total=len(items))


@abc_bp.post("/recalculate")
def recalculate():
    """重算并写回货品的 ABC 分类。"""
    db = get_db()
    payload = request_payload() or {}
    days = payload.get("days") or C.ABC_WINDOW_DAYS
    try:
        days = int(days)
    except (TypeError, ValueError):
        return _error(ABC.AbcError("days 必须是整数"))
    owner_id = payload.get("owner_id")
    try:
        data = ABC.apply(db, days, owner_id)
    except ABC.AbcError as e:
        return _error(e)
    return jsonify(ok=True, **data)
