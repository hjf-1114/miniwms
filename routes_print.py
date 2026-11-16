"""M41 打印模板配置 + PDF 预览 API（服务层见 ``print_service.py``）。

模板配置是系统配置表，单据取值经 scope 注入货主条件：
- GET  /api/print/dict                     字典（节点 / 单据来源 / 区块 / 字段）
- GET  /api/print/templates                模板列表（?node=）
- POST /api/print/templates                新建模板
- PUT  /api/print/templates/<id>           编辑模板
- DELETE /api/print/templates/<id>         删除模板
- POST /api/print/templates/<id>/activate  设为节点唯一生效模板
- GET  /api/print/preview                  预览取数 + 渲染 HTML（不写库）
- GET  /api/print/pdf                      系统 Chrome headless 生成 PDF（不写库）

预览页的 overrides 只作用于本次打印件，不回写任何业务单据。
"""
from flask import Blueprint, jsonify, request, Response

import print_service as PS
from main import get_db

print_bp = Blueprint("print", __name__)


def _error(e):
    return jsonify(ok=False, error=str(e), code=e.code), 400


def _json(action, *args):
    try:
        return jsonify(action(get_db(), *args))
    except PS.PrintError as e:
        return _error(e)


def _template(action, *args, status=200):
    try:
        return jsonify(ok=True, template=action(get_db(), *args)), status
    except PS.PrintError as e:
        return _error(e)


@print_bp.get("/api/print/dict")
def print_dict():
    return jsonify(PS.dictionary())


@print_bp.get("/api/print/templates")
def print_templates():
    return _json(PS.list_templates, request.args.get("node", ""))


@print_bp.post("/api/print/templates")
def print_template_create():
    return _template(PS.create_template, request.get_json(silent=True) or {}, status=201)


@print_bp.put("/api/print/templates/<int:tid>")
def print_template_update(tid):
    return _template(PS.update_template, tid, request.get_json(silent=True) or {})


@print_bp.delete("/api/print/templates/<int:tid>")
def print_template_delete(tid):
    return _json(PS.delete_template, tid)


@print_bp.post("/api/print/templates/<int:tid>/activate")
def print_template_activate(tid):
    return _template(PS.activate_template, tid)


@print_bp.get("/api/print/preview")
def print_preview():
    return _json(PS.preview, request.args)


@print_bp.get("/api/print/pdf")
def print_pdf():
    try:
        data, filename = PS.pdf_args(get_db(), request.args)
        return Response(data, mimetype="application/pdf", headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "no-store",
        })
    except PS.PrintError as e:
        return _error(e)
