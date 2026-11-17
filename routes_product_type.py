"""M32 产品类型字典：/api/product-types 的增删改查。

端点
----
- ``GET    /api/product-types``          列表（keyword/status/owner_id/parent_id + 分页）
- ``POST   /api/product-types``          新建（201）
- ``GET    /api/product-types/dict``     字典（状态枚举 / 层级上限 / 种子类型）
- ``GET    /api/product-types/refs``     全量引用计数
- ``GET    /api/product-types/<id>``     详情
- ``PUT    /api/product-types/<id>``     编辑（可部分）
- ``DELETE /api/product-types/<id>``     删除（仍被货品引用则拒绝）

``ProductTypeError → 400``；货主越权由 scope 层抛 ``ScopeError → 403``。
"""
from flask import Blueprint, jsonify, request

import product_type_service as PTS
from main import get_db, request_payload

product_type_bp = Blueprint("product_type", __name__, url_prefix="/api/product-types")


def _error(e):
    return jsonify(ok=False, error=str(e), code=e.code), 400


@product_type_bp.get("/dict")
def dict_data():
    return jsonify({"ok": True, **PTS.dictionary()})


@product_type_bp.get("/refs")
def ref_counts():
    db = get_db()
    return jsonify({"ok": True, "refs": PTS.ref_counts(db)})


@product_type_bp.route("", methods=["GET"])
def list_types():
    db = get_db()
    try:
        data = PTS.list_records(db, request.args)
    except PTS.ProductTypeError as e:
        return _error(e)
    return jsonify({"ok": True, **data})


@product_type_bp.route("", methods=["POST"])
def create_type():
    db = get_db()
    try:
        item = PTS.create(db, request_payload())
    except PTS.ProductTypeError as e:
        return _error(e)
    return jsonify({"ok": True, "product_type": item}), 201


@product_type_bp.route("/<int:rid>", methods=["GET"])
def get_type(rid):
    db = get_db()
    try:
        item = PTS.get(db, rid)
    except PTS.ProductTypeError as e:
        return _error(e)
    return jsonify({"ok": True, "product_type": item})


@product_type_bp.route("/<int:rid>", methods=["PUT"])
def update_type(rid):
    db = get_db()
    try:
        item = PTS.update(db, rid, request_payload())
    except PTS.ProductTypeError as e:
        return _error(e)
    return jsonify({"ok": True, "product_type": item})


@product_type_bp.route("/<int:rid>", methods=["DELETE"])
def delete_type(rid):
    db = get_db()
    try:
        PTS.delete(db, rid)
    except PTS.ProductTypeError as e:
        return _error(e)
    return jsonify({"ok": True})
