"""M23 规则层：指定客户出库校验 + 质量状态转换矩阵（纯函数，不接业务路由）。

用户 2026-11-09 确认的两条口径：

* **出库校验「只落规则」**：本模块不读 DB、不写 DB、不注册蓝图、不在前端提示。
  ``check_customer_reservation()`` 只定义判定结果，供 M35 出库分配实现时接入。
  设计依据 ``docs/PUTAWAY_RECOMMENDATION.md`` §6.3。
* **质量状态按 B 口径**：``goods_property``（批次维度，合格/不合格）与
  ``stock_status``（库位维度，非限制/质检/冻结）是两个独立字段，不改原有枚举。
  本模块只定义转换矩阵与允许校验，**不实现状态变更**；状态变更属 M24 库存转移单。

所有函数无副作用，可脱离 Flask / 数据库单独测试。
"""
import constants as C


def _as_id(value):
    """把库存明细 / 单据上的客户 ID 规整为正整数或 None（空 = 通用库存）。"""
    if value in (None, "", 0, "0", False):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def check_customer_reservation(stock_customer_id, dn_customer_id):
    """校验一条库存明细能否用于某张出库单。

    ============ ==================== ==========================================
    库存归属      出库单客户           结果
    ============ ==================== ==========================================
    空（通用）     任意                允许
    有值           相同                允许
    有值           不同 / 未指定       拒绝 CUSTOMER_RESERVED_FORBIDDEN
    ============ ==================== ==========================================

    返回 ``{"allowed": bool, "code": str, "reason": str}``。
    """
    reserved = _as_id(stock_customer_id)
    if reserved is None:
        return {"allowed": True, "code": C.CUSTOMER_RESERVED_OK,
                "reason": "通用库存，任意客户均可出库"}
    target = _as_id(dn_customer_id)
    if target is None:
        return {"allowed": False, "code": C.CUSTOMER_RESERVED_FORBIDDEN,
                "reason": f"该库存专属客户 {reserved}，但出库单未指定客户"}
    if target != reserved:
        return {"allowed": False, "code": C.CUSTOMER_RESERVED_FORBIDDEN,
                "reason": f"该库存专属客户 {reserved}，不能发给客户 {target}"}
    return {"allowed": True, "code": C.CUSTOMER_RESERVED_OK,
            "reason": f"专属客户 {reserved} 与出库单一致"}


def reservation_rule_matrix():
    """§6.3 规则表的可机读版本（供单测 / 文档 / 后续 M35 复用）。"""
    return (
        {"stock": None, "dn": "任意", "allowed": True},
        {"stock": 1, "dn": 1, "allowed": True},
        {"stock": 1, "dn": 2, "allowed": False},
        {"stock": 1, "dn": None, "allowed": False},
    )


def preferred_consumption_order(customer_id):
    """§6.3 消耗顺序建议：优先专属库存，再用通用库存。

    **仅为规则定义**，M23 不实现分配；FEFO 与之的优先级冲突列为第 16 章待确认项。
    """
    return {
        "strategy": C.RESERVATION_CONSUME_OWN_FIRST,
        "customer_id": _as_id(customer_id),
        "order": ("designated", "common"),
        "implemented": False,
    }


# goods_property 转换矩阵：不合格为单向，除特采审批外不可直接改回合格。
QUALITY_TRANSITIONS = (
    ("合格", "合格", True),
    ("合格", "不合格", True),
    ("不合格", "不合格", True),
    ("不合格", "合格", False),
)


def can_change_goods_property(current, target, special_approval=False):
    """质量状态（批次维度）能否从 current 变到 target。

    不合格 → 合格 默认禁止；带 ``special_approval=True``（特采已审批）才允许。
    本函数只判定，**不写库**：实际变更须走 M24 库存转移单并留痕。
    """
    cur = str(current or "").strip() or "合格"
    tgt = str(target or "").strip() or "合格"
    if tgt not in C.GOODS_PROPERTIES:
        return {"allowed": False, "reason":
                f"目标货品属性只能是 {'/'.join(C.GOODS_PROPERTIES)}"}
    if cur not in C.GOODS_PROPERTIES:
        return {"allowed": False, "reason": "当前货品属性非法"}
    if cur == "不合格" and tgt == "合格":
        if special_approval:
            return {"allowed": True, "reason": "不合格回合格需特采审批，已提供审批标记"}
        return {"allowed": False, "reason": "不合格为单向状态，回合格须走特采审批"}
    return {"allowed": True, "reason": "允许转换"}


def stock_status_allowed(value):
    """库位维度库存状态取值校验；'' 保留给 M21 老数据（未设置）。"""
    return str(value or "").strip() in ((C.STOCK_STATUS_UNSET,) + C.STOCK_STATUSES)


def quality_transition_matrix():
    """转换矩阵的可机读版本，供文档与单测取证。"""
    return QUALITY_TRANSITIONS
