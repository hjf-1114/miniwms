"""权限码目录（单一真源）。

背景：MiniWMS 到 M41 已沉淀 41 个权限码常量（`constants.py` 里各模块自己定义），
但角色只有 3 个、权限清单只覆盖 6 个域，且角色页让人**手填 JSON 数组** ——
既不可用也容易写错。本模块把权限码收敛成一份**带分组、带中文标签**的目录，
供三处使用：

1. ``GET /api/permissions`` —— 角色页改成**勾选式**配置，不再手填 JSON；
2. ``tools/seed_initial_data.py`` —— 播种「角色 + 账号 + 基础数据」的初始集；
3. 单测 —— 断言目录与各模块常量**不漂移**。

设计口径（延续 Q08「权限码只落常量不挂装饰器」的既有纪律）：

- **目录只做"可选项清单"**，不改变任何路由的鉴权行为；
  真正挂 ``@require_permission`` 的目前只有 ``user:manage``（用户与授权管理）。
- 权限码形态统一为 ``资源:动作``，动作分三类：
  - ``read`` / ``write`` / ``config``：通用读写与配置；
  - 业务动作词（``receive`` / ``approve`` / ``allocate`` …）：按模块自定义；
  - ``*``：全部权限，仅管理员角色使用。
- 保留**通配**语义：``auth.has_permission()`` 支持 ``owner:*`` 覆盖
  ``owner:read`` / ``owner:write``。目录里的 ``actions`` 用 ``["*"]`` 表示整组通配。
"""
import constants as C

# ── 目录定义 ──────────────────────────────────────────────────────────────
# 每项：(分组键, 分组名, 资源前缀, 中文标签, 动作列表)
# 动作列表里的 "*" 表示该资源整组通配（等价于把 "资源:*" 放进权限清单）。
_CATALOG = [
    # ── 系统管理 ──
    ("system", "系统管理", "user", "用户与角色",
     ["read", "write", "manage"]),
    ("system", "系统管理", "scope", "数据授权与仓库切换",
     ["read", "manage"]),

    # ── 基础主数据 ──
    ("master", "基础主数据", "owner", "货主", ["read", "write"]),
    ("master", "基础主数据", "warehouse", "仓库", ["read", "write"]),
    ("master", "基础主数据", "zone", "储区", ["read", "write"]),
    ("master", "基础主数据", "location", "库位", ["read", "write"]),
    ("master", "基础主数据", "location_group", "货位组",
     [C.LOC_GROUP_PERM_VIEW, C.LOC_GROUP_PERM_CONFIG]),
    ("master", "基础主数据", "product", "货品", ["read", "write"]),
    ("master", "基础主数据", "product_type", "产品类型",
     [C.PT_PERM_VIEW, C.PT_PERM_CONFIG]),
    ("master", "基础主数据", "serial", "序列号台账",
     [C.SERIAL_PERM_VIEW, C.SERIAL_PERM_CONFIG]),
    ("master", "基础主数据", "customer", "客户", ["read", "write"]),
    ("master", "基础主数据", "supplier", "供应商", ["read", "write"]),
    ("master", "基础主数据", "carrier", "承运商", ["read", "write"]),
    ("master", "基础主数据", "fleet", "司机与车辆",
     [C.FLEET_PERM_VIEW, C.FLEET_PERM_CONFIG]),

    # ── 入库作业 ──
    ("inbound", "入库作业", "asn", "收货单", ["read", "write", "receive", "putaway"]),
    ("inbound", "入库作业", "batch", "批次与效期", ["read", "write"]),

    # ── 出库作业 ──
    ("outbound", "出库作业", "dn", "出库单",
     ["read", "write", "ship", C.SPLIT_PERM_EXECUTE, C.SPLIT_PERM_LOG]),
    ("outbound", "出库作业", "outbound_allocation", "出库分配与波次",
     [C.OUTBOUND_PERM_PLAN, C.OUTBOUND_PERM_ALLOCATE,
      C.OUTBOUND_PERM_UNALLOCATE, C.OUTBOUND_PERM_WAVE,
      C.OUTBOUND_PERM_LOG, C.OUTBOUND_PERM_CONFIG]),
    ("outbound", "出库作业", "dn_review", "出库复核",
     [C.REVIEW_PERM_CREATE, C.REVIEW_PERM_CHECK, C.REVIEW_PERM_COMPLETE,
      C.REVIEW_PERM_CANCEL, C.REVIEW_PERM_LOG, C.REVIEW_PERM_CONFIG]),

    # ── 库存作业 ──
    ("stock", "库存作业", "stock", "库存账与调整", ["read", "write"]),
    ("stock", "库存作业", "stocktake", "盘点", ["read", "write"]),
    ("stock", "库存作业", "inventory_transfer", "库存转移",
     [C.IT_PERM_CREATE, C.IT_PERM_ALLOCATE, C.IT_PERM_UNALLOCATE,
      C.IT_PERM_APPROVE, C.IT_PERM_CLOSE, C.IT_PERM_DELETE]),
    ("stock", "库存作业", "care", "养护作业",
     [C.CARE_PERM_PLAN, C.CARE_PERM_GENERATE, C.CARE_PERM_EXECUTE,
      C.CARE_PERM_HANDLE, C.CARE_PERM_LOG, C.CARE_PERM_CONFIG]),

    # ── 规则与报表 ──
    ("rule", "规则与报表", "putaway_rule", "上架规则", ["read", "write"]),
    ("rule", "规则与报表", "gsp", "GSP 合规报表",
     [C.GSP_PERM_VIEW, C.GSP_PERM_EXPORT]),
    ("rule", "规则与报表", "workload", "工作量统计", [C.WL_PERM_VIEW]),
    ("rule", "规则与报表", "operation_report", "经营报表",
     [C.OP_REPORT_PERM_VIEW]),

    # ── 输出 ──
    ("output", "输出", "print", "打印与模板",
     [C.PRINT_PERM_VIEW, C.PRINT_PERM_PDF, C.PRINT_PERM_CONFIG]),
]


def _build():
    groups = []
    for gkey, gname, resource, label, actions in _CATALOG:
        items = []
        for act in actions:
            if act == "*":
                items.append({"code": f"{resource}:*", "label": f"{label}·全部",
                              "wildcard": True})
            elif ":" in act:
                # 模块自定义动作（常量已是完整权限码）
                items.append({"code": act, "label": f"{label}·{_act_label(act)}",
                              "wildcard": False})
            else:
                items.append({"code": f"{resource}:{act}",
                              "label": f"{label}·{_ACT_LABELS.get(act, act)}",
                              "wildcard": False})
        groups.append({"key": gkey, "name": gname, "resource": resource,
                       "label": label, "items": items})
    return groups


_ACT_LABELS = {
    "read": "查看", "write": "编辑", "manage": "管理", "config": "配置",
    "receive": "收货验收", "putaway": "上架", "ship": "发货",
}


def _act_label(code):
    """从完整权限码里取出动作部分的可读标签。"""
    act = code.split(":", 1)[1] if ":" in code else code
    base = act.split("_", 1)[0]
    return _ACT_LABELS.get(base, act)


GROUPS = _build()

# 全部可选权限码（扁平）
ALL_CODES = [it["code"] for g in GROUPS for it in g["items"]]

# 资源前缀清单（用于「整组通配」）
RESOURCES = []
for _g in GROUPS:
    if _g["resource"] not in RESOURCES:
        RESOURCES.append(_g["resource"])


def dictionary():
    """返回完整目录（供 API 与前端渲染勾选面板）。"""
    return {
        "groups": GROUPS,
        "codes": ALL_CODES,
        "resources": RESOURCES,
        "wildcard": "*",
        "note": ("目录仅是可选项清单，不改变路由鉴权行为；"
                 "当前真正挂权限校验的只有 user:manage。"),
    }


# ── 岗位角色模板 ──────────────────────────────────────────────────────────
# 按 WMS 常见岗位划分；每个模板的权限清单都**只用目录里的码**，
# 保证「选出来的角色」与「勾选面板」永远一致。
ROLE_TEMPLATES = [
    {
        "name": "系统管理员",
        "permissions": ["*"],
        "desc": "全部权限（*），含用户与角色管理；数据范围通常设为「全部仓库 + 全部货主」。",
    },
    {
        "name": "仓储主管",
        "permissions": [
            "owner:read", "owner:write", "warehouse:read", "warehouse:write",
            "zone:read", "zone:write", "location:read", "location:write",
            "location_group:view", "location_group:config",
            "product:read", "product:write", "product_type:view", "product_type:config",
            "customer:read", "supplier:read", "carrier:read",
            "fleet:view", "fleet:config",
            "asn:read", "asn:write", "asn:receive", "asn:putaway",
            "batch:read", "batch:write",
            "dn:read", "dn:write", "dn:ship",
            "outbound_allocation:plan", "outbound_allocation:allocate", "outbound_allocation:unallocate", "outbound_allocation:wave",
            "dn_review:check", "dn_review:complete",
            "stock:read", "stock:write", "stocktake:read", "stocktake:write",
            "inventory_transfer:create", "inventory_transfer:allocate",
            "inventory_transfer:unallocate", "inventory_transfer:approve",
            "care:plan", "care:generate", "care:execute", "care:handle",
            "putaway_rule:read", "putaway_rule:write",
            "gsp:view", "workload:view", "operation_report:view",
            "print:view", "print:pdf",
        ],
        "desc": "管仓库、货位、库存与作业复核；不含用户管理。",
    },
    {
        "name": "收货员",
        "permissions": [
            "owner:read", "product:read", "product_type:view",
            "supplier:read", "location:read", "zone:read", "batch:read",
            "asn:read", "asn:write", "asn:receive", "asn:putaway",
            "stock:read", "print:view",
        ],
        "desc": "只做入库侧：收货验收与上架。",
    },
    {
        "name": "拣货发货员",
        "permissions": [
            "owner:read", "customer:read", "product:read", "location:read",
            "batch:read", "stock:read",
            "dn:read", "dn:write", "dn:ship",
            "outbound_allocation:plan", "outbound_allocation:allocate", "outbound_allocation:unallocate", "outbound_allocation:wave",
            "dn_review:check", "dn_review:complete",
            "print:view", "print:pdf",
        ],
        "desc": "只做出库侧：拣货分配、发货、复核。",
    },
    {
        "name": "库存管理员",
        "permissions": [
            "owner:read", "product:read", "location:read", "location_group:view",
            "batch:read", "stock:read", "stock:write",
            "stocktake:read", "stocktake:write",
            "inventory_transfer:create", "inventory_transfer:allocate",
            "inventory_transfer:unallocate", "inventory_transfer:approve",
            "care:plan", "care:generate", "care:execute", "care:handle",
            "print:view",
        ],
        "desc": "负责盘点、库存调整与转移；数量变更入口在库存侧。",
    },
    {
        "name": "报表与质量员",
        "permissions": [
            "owner:read", "product:read", "batch:read", "stock:read",
            "gsp:view", "gsp:export", "workload:view", "operation_report:view",
            "print:view", "print:pdf",
        ],
        "desc": "只读报表岗：GSP 追溯、经营报表、工作量统计。",
    },
    {
        "name": "只读审计",
        "permissions": [
            "owner:read", "warehouse:read", "zone:read", "location:read",
            "product:read", "customer:read", "supplier:read", "carrier:read",
            "batch:read", "asn:read", "dn:read", "stock:read",
            "gsp:view", "workload:view", "operation_report:view",
        ],
        "desc": "全业务域只读，用于审计与外部观察。",
    },
]


def template_names():
    return [t["name"] for t in ROLE_TEMPLATES]


def template(name):
    for t in ROLE_TEMPLATES:
        if t["name"] == name:
            return t
    return None
