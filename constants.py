"""全局状态枚举与常量（参考 Sentry WMS 的 constants.py 设计：状态机显式集中）。

单据状态少而明确，所有模块只从这里取状态值，禁止散落字符串。
"""

# ASN（到货通知/收货单）状态机: open → receiving → putaway → closed
ASN_OPEN = "open"              # 已创建，未收货
ASN_RECEIVING = "receiving"    # 部分或全部已收货，未（完全）上架
ASN_PUTAWAY = "putaway"        # 已收满，上架进行中
ASN_CLOSED = "closed"          # 已收满且上架完成
ASN_CANCELLED = "cancelled"    # 已取消（未收货部分作废）
ASN_STATUSES = (ASN_OPEN, ASN_RECEIVING, ASN_PUTAWAY, ASN_CLOSED, ASN_CANCELLED)

# DN（出库单/发货单）状态机: open → picking → shipped
DN_OPEN = "open"            # 已创建，未拣货
DN_PICKING = "picking"      # 拣货进行中（部分或全部已分配）
DN_SHIPPED = "shipped"      # 已发货（库存已扣减）
DN_CANCELLED = "cancelled"  # 已取消（仅未拣货可取消）
DN_STATUSES = (DN_OPEN, DN_PICKING, DN_SHIPPED, DN_CANCELLED)

# ASN（收货单）单据类型
ASN_DOC_TYPES = ("调拨", "销退", "采购", "期初", "其他")

# DN（出库单）单据类型
DN_DOC_TYPES = ("销售", "调拨", "退货", "其他")

# 库存动作流水类型
MOVEMENT_ASN_RECEIVE = "asn_receive"    # 收货入暂存位（+）
MOVEMENT_ASN_PUTAWAY_OUT = "asn_putaway_out"  # 上架出暂存位（-）
MOVEMENT_ASN_PUTAWAY_IN = "asn_putaway_in"    # 上架入目标位（+）
MOVEMENT_DN_PICK = "dn_pick"            # 拣货分配（M5）
MOVEMENT_DN_SHIP = "dn_ship"            # 发货（M5）
MOVEMENT_MOVE_OUT = "move_out"          # 移库出（M16）
MOVEMENT_MOVE_IN = "move_in"            # 移库入（M16）
MOVEMENT_ADJUSTMENT = "adjustment"      # 盘点调整（M17）

# 移库单（Stock Transfer）状态机: open → done（含 cancelled）
TRANSFER_OPEN = "open"              # 已创建，未执行
TRANSFER_DONE = "done"              # 已执行（库存已转移）
TRANSFER_CANCELLED = "cancelled"    # 已取消（仅未执行可取消）
TRANSFER_STATUSES = (TRANSFER_OPEN, TRANSFER_DONE, TRANSFER_CANCELLED)

# 盘点单（Stocktake）状态机: open → frozen → adjusted → closed
STOCKTAKE_OPEN = "open"          # 已创建，未冻结
STOCKTAKE_FROZEN = "frozen"      # 已冻结并快照账存（盲盘阶段）
STOCKTAKE_ADJUSTED = "adjusted"  # 已按盘点差异生成调整流水
STOCKTAKE_CLOSED = "closed"      # 已解冻结案
STOCKTAKE_STATUSES = (STOCKTAKE_OPEN, STOCKTAKE_FROZEN, STOCKTAKE_ADJUSTED, STOCKTAKE_CLOSED)

# ── M22 库存调整单 ──
ADJUSTMENT_PREFIX = "ADJ"            # ADJ-YYYYMMDD-NNN
ADJUSTMENT_DRAFT = "draft"           # 已制单，未过账
ADJUSTMENT_APPROVED = "approved"     # 已审核过账（终态）
ADJUSTMENT_CANCELLED = "cancelled"   # 已取消（仅 draft 可取消，盘点单可重新生成）
ADJUSTMENT_STATUSES = (ADJUSTMENT_DRAFT, ADJUSTMENT_APPROVED, ADJUSTMENT_CANCELLED)
ADJUSTMENT_MANUAL = "manual"             # 手工报损 / 报溢 / 数量修正
ADJUSTMENT_STOCKTAKE_DIFF = "stocktake_diff"  # 盘点差异人工生成
ADJUSTMENT_REVERSAL = "reversal"         # 冲销（反向单，仍需人工审核）
ADJUSTMENT_SOURCE_TYPES = (ADJUSTMENT_MANUAL, ADJUSTMENT_STOCKTAKE_DIFF,
                           ADJUSTMENT_REVERSAL)
REASON_CODES = ("报损", "报溢", "盘点差异", "错账更正", "冲销")

# ── M22 盘点方案 ──
SCOPE_FULL = "full"          # 全盘
SCOPE_PARTIAL = "partial"    # 部分盘点（范围条件组）
SCOPE_DYNAMIC = "dynamic"    # 动碰盘点
SCOPE_TYPES = (SCOPE_FULL, SCOPE_PARTIAL, SCOPE_DYNAMIC)
BLIND_NONE = "none"              # 明盘
BLIND_HIDE_BOOK = "hide_book"    # 录入时隐藏账存，复核可见差异
BLIND_HIDE_DIFF = "hide_diff"    # 录入时隐藏差异
BLIND_MODES = (BLIND_NONE, BLIND_HIDE_BOOK, BLIND_HIDE_DIFF)
BASELINE_LAST_CLOSED = "last_closed"    # 上次结案时间（默认）
BASELINE_SINCE_DAYS = "since_days"      # 近 N 天
BASELINE_EXPLICIT = "explicit"          # 创建盘点单时手工指定
BASELINE_MANUAL_WINDOW = "manual_window"  # 方案里的手工时间窗
BASELINE_TYPES = (BASELINE_LAST_CLOSED, BASELINE_SINCE_DAYS, BASELINE_EXPLICIT,
                  BASELINE_MANUAL_WINDOW)
# 部分盘点范围条件组允许的维度（维度间 AND；null / 空数组 = 不限）
SCOPE_DIMENSIONS = (
    "owner_id", "warehouse_id", "zone_id", "location_group", "location_ids",
    "loc_type", "product_ids", "category_l1", "category_l2", "main_category",
    "product_type", "batch_no", "batch_key", "goods_property", "has_stock",
)
# 动碰交易类型白名单可选项（来自 MOVEMENT_*，空数组 = 不限）
DYNAMIC_MOVEMENT_TYPES = (
    MOVEMENT_ASN_RECEIVE, MOVEMENT_ASN_PUTAWAY_OUT, MOVEMENT_ASN_PUTAWAY_IN,
    MOVEMENT_DN_PICK, MOVEMENT_DN_SHIP, MOVEMENT_MOVE_OUT, MOVEMENT_MOVE_IN,
    MOVEMENT_ADJUSTMENT,
)

# 批次（M19，医疗器械 / GSP 口径）：每次验收入库生成唯一批次号
BATCH_RECEIPT_PREFIX = "BAT"          # 入库批次流水号：BAT-YYYYMMDD-NNN
BATCH_PROFILE_PREFIX = "BP"           # 批次档案号（关键批次信息主档）
GOODS_PROPERTIES = ("合格", "不合格")  # 收货明细 / 批次流水「货品属性」枚举

# 内部批次号来源（用户口径：首次入库=批次号；销退=出库内部批次号；一般为数据传入）
INTERNAL_BATCH_FIRST_IN = "first_in"
INTERNAL_BATCH_RETURN = "return_trace"
INTERNAL_BATCH_EXTERNAL = "external"
INTERNAL_BATCH_SOURCES = (
    INTERNAL_BATCH_FIRST_IN, INTERNAL_BATCH_RETURN, INTERNAL_BATCH_EXTERNAL,
)

# ── M20 储区 / 混放 / 上架推荐 ──
TEMP_CLASSES = ("常温", "阴凉", "冷藏", "冷冻")
MIX_FIELDS = ("allow_mix_product", "allow_mix_batch", "allow_mix_owner")
MIX_LABELS = {
    "allow_mix_product": "允许产品混放",
    "allow_mix_batch": "允许批号混放",
    "allow_mix_owner": "允许货主混放",
}
# 推荐理由码：前端与日志共用，避免散落中文字符串
PUTAWAY_R_BIND = "bound_location"        # 货品绑定货位
PUTAWAY_R_ZONE = "default_zone"          # 货品推荐储区
PUTAWAY_R_SAME_PRODUCT = "same_product"  # 同产品聚拢
PUTAWAY_R_SAME_BATCH = "same_batch"      # 同批号聚拢
PUTAWAY_R_EMPTY = "empty_location"       # 空库位
PUTAWAY_R_CAPACITY = "capacity_fit"      # 剩余容量充足
PUTAWAY_REASON_LABELS = {
    PUTAWAY_R_BIND: "货品绑定货位",
    PUTAWAY_R_ZONE: "货品推荐储区",
    PUTAWAY_R_SAME_PRODUCT: "同产品聚拢",
    PUTAWAY_R_SAME_BATCH: "同批号聚拢",
    PUTAWAY_R_EMPTY: "空库位优先",
    PUTAWAY_R_CAPACITY: "剩余容量充足",
}

# ── M20 剩余：ASN/DN 单据标识位与外部单据类别 ──
# v3.3 口径：这里只落字段与解析，**不做任何混放 / 冷链 / 危险业务校验**；
# 是否拦截、拦到哪些货位，全部由 M25/M26 的上架规则行配置决定。
DOC_FLAG_FIELDS = ("is_virtual", "is_direct", "is_cold_chain", "is_hazard")
DOC_FLAG_LABELS = {
    "is_virtual": "虚拟",
    "is_direct": "直调",
    "is_cold_chain": "冷链",
    "is_hazard": "危险",
}
EXTERNAL_DOC_TYPE_MAXLEN = 32


def parse_doc_flags(payload):
    """解析 ASN/DN 的 4 个标识位与 external_doc_type。

    返回 ``(flags, external_doc_type, errors)``。兼容 0/1 整数、布尔、``'0'``/``'1'``
    字符串、``None`` 与空串；缺省按 0 / 空串处理。非法值只报字段格式错误，
    **不附加任何业务约束**（v3.3）。
    """
    flags, errors = {}, []
    for key in DOC_FLAG_FIELDS:
        raw = payload.get(key)
        if raw is None or raw == "":
            flags[key] = 0
        elif raw is True or raw == 1 or raw == "1":
            flags[key] = 1
        elif raw is False or raw == 0 or raw == "0":
            flags[key] = 0
        else:
            errors.append(f"{key} 只能是 0/1")
    ext = str(payload.get("external_doc_type") or "").strip()
    if len(ext) > EXTERNAL_DOC_TYPE_MAXLEN:
        errors.append(f"external_doc_type 最长 {EXTERNAL_DOC_TYPE_MAXLEN} 字符")
    return flags, ext, errors


# ── M23 指定客户专属库存 / 销退口径（用户 2026-11-09 确认）──
# designated_customer_id 是「出库对象锁定」，与 owner_id（物权归属）正交。
# 本轮只落规则与链路，出库分配校验不接入 routes_dn.py（用户选 A）。
CUSTOMER_RESERVED_OK = "OK"
CUSTOMER_RESERVED_FORBIDDEN = "CUSTOMER_RESERVED_FORBIDDEN"
RESERVATION_CONSUME_OWN_FIRST = "own_first"  # 建议先专属后通用，仅为规则定义
# 用户 2026-11-09 二次确认：专属库存发给其他客户「要提示 + 要留痕，但允许继续」。
# 规则层仍不拦截、不实现分配；下面两个值只用于风险放行日志的 action。
RESERVATION_RISK_ALLOWED = "risk_allowed"          # 前端二次确认后继续
RESERVATION_RISK_ALLOWED_UNACK = "risk_allowed_unack"  # API 直调，未经界面确认

# 销退单外部批次号（＝外部传入的原内部批次号）：必填；确实没有时显式填 '/'
EXTERNAL_BATCH_NO_ABSENT = "/"
EXTERNAL_BATCH_NO_MAXLEN = 64
WARN_MISSING_EXTERNAL_BATCH_NO = "MISSING_EXTERNAL_BATCH_NO"

# 质量状态按用户选 B：两个独立字段，GOODS_PROPERTIES 保持两值不变。
#   goods_property（批次维度）：合格 / 不合格
#   stock_status  （库位维度）：非限制 / 质检 / 冻结；'' = 老数据未设置
STOCK_STATUSES = ("非限制", "质检", "冻结")
STOCK_STATUS_UNSET = ""
QUALITY_SPECIAL_APPROVAL = "special_approval"  # 不合格 → 合格 的特采审批标记

# ── M24 库存转移单（用户 2026-11-09 四条 A 口径）──
# A1 只做同库位内的批次维度变更，跨库位一律走 M17 移库单；主账 stock.qty 零 UPDATE。
# A2 七类转移类型一次做全，但底层统一为「源维度组合 → 目标维度组合」的净零搬迁，
#    类型只作展示与校验标签，不写独立分支。
# A3 只做 IT 单自身的 unallocate，不做 DN 拣货分配取消。
# A4 权限码只落常量/注释（不挂装饰器）；特采只接布尔标记 + reason_code=特采。
INVENTORY_TRANSFER_PREFIX = "IT"        # IT-YYYYMMDD-NNN
IT_DRAFT = "draft"                       # 已制单，未分配
IT_ALLOCATED = "allocated"               # 已分配库存（真实占用可用库存）
IT_APPROVED = "approved"                 # 审核生效（终态，不可改不可删）
IT_CLOSED = "closed"                     # 空单关闭（无任何库存动作）
IT_CANCELLED = "cancelled"               # 空单删除标记
IT_STATUSES = (IT_DRAFT, IT_ALLOCATED, IT_APPROVED, IT_CLOSED, IT_CANCELLED)

IT_TYPE_BATCH_CORRECT = "batch_correct"      # 批号 / 效期 / 产地等错录补正
IT_TYPE_QUALITY_CHANGE = "quality_change"    # goods_property / stock_status 变更
IT_TYPE_BATCH_SPLIT = "batch_split"          # 同库位内拆成不同维度组合
IT_TYPE_CUSTOMER_BIND = "customer_bind"      # 通用 → 专属
IT_TYPE_CUSTOMER_UNBIND = "customer_unbind"  # 专属 → 通用
IT_TYPE_CUSTOMER_REBIND = "customer_rebind"  # 专属 → 另一专属（M23 章追加）
IT_TYPE_LOCATION_MOVE = "location_move"      # 保留标签；M24 内库位恒等，跨库位走 M17
IT_TYPES = (IT_TYPE_BATCH_CORRECT, IT_TYPE_QUALITY_CHANGE, IT_TYPE_BATCH_SPLIT,
            IT_TYPE_CUSTOMER_BIND, IT_TYPE_CUSTOMER_UNBIND,
            IT_TYPE_CUSTOMER_REBIND, IT_TYPE_LOCATION_MOVE)

IT_REASON_CODES = ("错录补正", "质检判定", "客户要求", "盘点差异", "特采", "冲销", "其他")
IT_SPECIAL_APPROVAL_REASON = "特采"          # 不合格 → 合格 的唯一放行原因码
IT_ERROR_QTY_NOT_ZERO = "TRANSFER_QTY_MUST_BE_ZERO"
IT_QTY_FORBIDDEN_FIELDS = ("delta_qty", "qty_adjust", "to_qty", "qty_delta")

# 6 个动作权限码：按用户选 A4，只落常量与注释，不挂 require_permission 装饰器。
IT_PERM_CREATE = "inventory_transfer:create"
IT_PERM_ALLOCATE = "inventory_transfer:allocate"
IT_PERM_UNALLOCATE = "inventory_transfer:unallocate"
IT_PERM_APPROVE = "inventory_transfer:approve"
IT_PERM_CLOSE = "inventory_transfer:close"
IT_PERM_DELETE = "inventory_transfer:delete"
IT_PERMISSIONS = (IT_PERM_CREATE, IT_PERM_ALLOCATE, IT_PERM_UNALLOCATE,
                  IT_PERM_APPROVE, IT_PERM_CLOSE, IT_PERM_DELETE)

# M24 流水类型（qty_delta 恒为 0 的成对 / 分配流水）
MOVEMENT_IT_ALLOCATE = "it_allocate"        # 分配占用（allocated_delta > 0）
MOVEMENT_IT_UNALLOCATE = "it_unallocate"    # 取消分配（allocated_delta < 0）
MOVEMENT_IT_TRANSFER = "it_transfer"        # 审核生效：源 -Q / 目标 +Q
