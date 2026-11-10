# 批次级库存 · 库存转移 · 上架推荐 · 库位混放 统一设计提案 v3.3（含盘点增强 · 口径定稿）

> **状态：设计口径已定稿，产品实现按 M20 起分阶段推进**（2026-11-05 第四轮确认）。本文只做逻辑梳理与实施计划，
> 不代表 README 描述的已实现功能；**里程碑编号已按 14 章定稿并同步 `DEVELOPMENT.md` /
> `README.md` / 首页里程碑表**，产品代码从 M20 起按阶段实施。
> 配套调研：`docs/REFERENCES.md`（GreaterWMS / Sentry / Aardvarkland 及本轮 v2 调研）。
>
> **v3.3 定稿（2026-11-05 用户第四轮答复：硬约束全部配置化）**
>
> > 用户原话：「入库混放硬校验和冷链 / 危险两个标识位的硬约束清单，最好是上架规则里面进行配置，
> > 当然是不局限于这些，我可以设置多个上架规则，并有优先级，然后根据单号信息或者产品信息来进行推荐上架货位」。
>
> | # | 议题 | 用户答复 | 本版落点 |
> |---|---|---|---|
> | 1 | M20 剩余怎么拆 | **1A**：M20 只补 4 个标识位 + `external_doc_type`（字段 / 表单 / 测试）；混放 + 冷链 + 危险拦截**整体并入 M25/M26 规则引擎一次做完，不写死** | 9.4 / 10.1 / 14 章 |
> | 2 | M20→M25 过渡期是否拦截 | **2B**：**完全不拦截，只靠推荐页提示** | 9.4 过渡期口径 |
> | 3 | 硬约束「可配」的边界 | **3B**：**全部交给规则决定，不保留系统级 hardcoded 红线** | 9.4 / 10.1 / 10.3 |
> | 4 | 条件维度首批范围 | **4A**：按 v3.2 的 **13 维起步**，M26 编辑器预留扩展位，后续按需加 | 10.2 |
>
> **3B 的口径解释（重要，避免实现时误读）**：取消的是「绕不过配置的系统级硬编码红线」
> （例如代码里写死「不合格品必须进 QC-NG-01」「危险品必须独立货位」），
> **不是取消「硬过滤」这一执行机制**。规则行 `limits` 一旦命中仍走 pass/reject 过滤、
> **不参与 score 加权**，即 10.1「约束不能被打分买通」继续有效：
> **红线从代码搬进规则表，而不是取消红线**。未配置任何规则行时按「无约束」处理，
> 兜底行（priority 999）仍负责 `NO_CANDIDATE` 与待人工处理。
>
> **v2 相对 v1 的口径变化（2026-11-04 用户反馈）**
>
> | # | v1 | v2（本版） |
> |---|---|---|
> | 1 | 上架推荐为主干，批次只作为「同批号推荐」的输入 | **批次级库存为主干**；库存明细、统一流水、库存转移、客户专属库存先于推荐落地 |
> | 2 | 新增独立的批次旁路流水 | **不新增第二套流水**：给 `stock_movement` 加维度列，一次动作可写 N 条批次流水，主账 SUM 不变 |
> | 3 | 规则靠 `filter` 短路 + `score` 加权打分 | **条件组（多维 AND）分流 + 规则行内多级 fallback 链**；score 只用于同储区内候选排序 |
> | 4 | 筛选条件「单选」 | 每个维度单选、**维度之间 AND**（富勒式），避免组合爆炸同时支持「不合格 + 销退」这类分流 |
> | 5 | 混放校验放在硬约束里泛化描述 | 明确**只作用于入库/上架环节**；产品混放、批号混放默认均**禁止**，按储区放开 |
> | 6 | `location.zone` 升级待确认 | **确认升级储区主数据表**（温区 / 危化 / 货主专区 / 单据形态白名单） |
> | 7 | 销退沿用历史内部批次号为主 | **销退默认由外部传入内部批次号**，系统回溯仅作兜底并给告警 |
> | 8 | 客户专属库存未涉及 | 新增 `designated_customer_id`，**有值则该库存只能该客户出**，解绑必须走库存转移留痕 |
> | 9 | 出库分配纳入阶段 D 一并设计 | **本版只留数据地基与边界，规则待后续专项沟通** |
> | 10 | 「货位是否混发」有歧义 | **确认为库位混放、入库环节**：产品不许混放 / 批号不许混放 |
>
> **v3 修订（2026-11-05 用户确认 4 条口径 + 新增单据标识需求）**
>
> | # | v2 | v3（本版） |
> |---|---|---|
> | 1 | 「虚拟单据」设计为 `doc_kind` 枚举，含义待确认 | **取消 `doc_kind` 枚举**：`asn` / `dn` 各加 4 个标识位 `is_virtual` / `is_direct` / `is_cold_chain` / `is_hazard` + 1 个 `external_doc_type` 外部单据类别；**全部纳入上架条件组维度**（第 8.4 节） |
> | 2 | 库存转移 `draft→submitted→approved→posted` 四态 | **5 个动作位**：制单 / 分配库存 / 审核 / 关闭单据 / 删除单据；分配库存复用 `allocated_qty` 锁定，关闭单据才过账；权限码先留钩子、细化后置（第 7.2 节） |
> | 3 | 指定客户「头给默认值 + 明细行覆盖」 | **只放明细行**，默认空，属于批次维度，**验收时人工填写**（第 6 章） |
> | 4 | 老库回填灰度期返回 `LEGACY_NO_BATCH_DETAIL` 豁免 | 用户「老库存你看着办，目前还在编码阶段」→ **取消灰度降级，I2/I3 一律硬校验**，演示库可 `--reset` 重建（第 5.4 节） |
>
> **v3.1 修订（2026-11-05 用户第二轮反馈）**
>
> | # | v3 | v3.1（本版） |
> |---|---|---|
> | 1 | 库存转移「关闭单据」＝唯一过账点，可带数量迁移 | **库存转移只改批次信息、不能调整库存数量**（`Σ qty_delta = 0` 硬校验，主账零 UPDATE）；数量只走**库存调整单**或**盘点差异生成的调整单**（7.1 / 7.3 / 7.6 / 18.7） |
> | 2 | 5 步状态机 `draft→allocated→approved→closed`，关闭可过账 | **两条互斥分支**：有分配走「制单→分配→审核（生效终态）」；**未分配才可关闭或删除，关闭也要求未分配且无库存动作**（7.2） |
> | 3 | 盘点未纳入设计范围 | 新增**第 18 章**：可配置 `stocktake_plan`，支持全盘 / 部分盘点（库位·储区·分类·产品·批号）/ 真盲盘 / 动碰盘点；差异生成调整单 |
> | 4 | 退出登录按「已实现」处理 | 核对后发现**独立页面有 `logout()`、工作台 `app.html` 无任何账号/退出入口**；按「不懂先问再改」暂不动代码，列为待确认（现已在 16.1 确认并实现） |
>
> **v3.2 定稿（2026-11-05 用户逐条答复 7 项口径）**
>
> | # | 问题 | 用户答复 | 本版落点 |
> |---|---|---|---|
> | 1 | 9 退出登录范围 | **只补工作台顶栏「账号名 + 退出按钮」**（前端清 token 跳登录页），不做服务端吊销 | 13 章 / 16.1 |
> | 2 | 10 审核后是否还要关闭 | **审核即生效、`approved` 为终态；「关闭」只留给未分配空单** | 7.2 状态机固化为终版 |
> | 3 | 11 分配库存是否占用 `allocated_qty` | **推翻暂定：需要占用库存，可用库存减少** | 7.2 主账 + 明细双层占用、审核释放、`allocated_delta` 留痕、新增「取消分配」动作 |
> | 4 | 12 里程碑编号 | **好的**（第 14 章 M20–M26 / 原四项 M27–M30；盘点增强排 M21 之后） | 14 章编号定稿，`DEVELOPMENT.md` / `README.md` / 首页同步 |
> | 5 | 18-A/B 动碰口径 | 默认 `last_closed`，**支持手工指定时间和交易类型** | 18.3 方案表扩字段 / 18.5 算法 |
> | 6 | 18-C 盲盘 | **仅录入时隐藏账存，复核阶段可见差异** | 18.6 分级接口终版 |
> | 7 | 18-D 盘点差异 | **额外增加一个按钮**：生成独立库存调整单再审核过账 | 18.7 显式人工按钮，不自动过账 |
>
> v3.2 起第 16.2 节只剩非阻塞议题。产品代码仍按「不懂先问再改」执行，
> 唯一落地的代码改动是范围已完全明确的「工作台退出登录」。

---

## 1. 本轮确认的口径

| 用户原话要点 | 本版设计落点 |
|---|---|
| 「批次是一个大头，库存和批次息息相关，入库和出库分配库存也有关联」 | 第 5 章：批次从「只写不读的档案」升级为**库存明细维度**；入库、上架、移库、盘点、发货五个动作统一同步 |
| 「后续要有修改批次信息的功能，叫库存转移，保证数据留痕」 | 第 7 章：`inventory_transfer` 单据 + 5 步动作（制单/分配库存/审核/关闭/删除）+ 成对流水 + 冲销 + 字段级变更文档，closed 后不可改 |
| 「销退一般都是外部传入内部批次号的」 | 第 7.5 节：外部传入优先，系统回溯降级为兜底并告警 |
| 「入库和批次里额外带指定客户字段，有值则只能这个客户出」 | 第 6 章：`designated_customer_id` 贯穿单据 → 批次 → 库存明细 → 流水 → 出库校验 |
| 「参考优秀项目有没有可以优化的地方，我们沟通」 | 第 3 章借鉴清单 + 第 3.4 节发现的 6 个现有代码隐患（沟通素材） |
| 「推荐规则要有地方设置过滤条件：不合格走不合格货位、虚拟单据走虚拟货位、正常单据走默认储区/同产品/同分类」 | 第 10 章：条件组分流 + 预置规则行 10/20/30/40/50/60/999 |
| 「货位是否混发＝库位混放，是入库环节，产品不允许混放或批号不能混放」 | 第 9 章：只在 `receive` / `putaway` 校验；`allow_mix_product` / `allow_mix_batch` 默认关 |
| 「出库分配逻辑要额外处理，到时候沟通」 | 第 15 章：本版只定边界、数据地基与待沟通清单，不实现 |
| 「location.zone 升级储区主数据表可以这样设置」 | 第 8 章：`zone` 主数据表 + `location.zone_id`，旧文本列保留兼容 |

**2026-11-05 追加确认（用户逐条答复）**

| 用户原话要点 | 本版设计落点 |
|---|---|
| 「入库单据 出库单据需要增加一个虚拟标识，直调标识，冷链标识，危险标识，和外部单据类别字段；后续上架可以按这些字段来推荐上架货位」 | 第 8.4 节：`asn` / `dn` 各加 4 个标识位 + `external_doc_type`；第 10.2 节条件组新增 5 个维度；第 10.3 节预置规则行按标识分流 |
| 「库存转移后续需要权限控制，目前分几步：制单、分配库存、审核、关闭单据、删除单据，后续再考虑详细的权限分配」 | 第 7.2 节：5 个动作位；每动作预留 `require_permission` 权限码，权限粒度后置。**状态机细节已由 v3.1/v3.2 修正为两条互斥分支 + 分配真实占用，见 7.2 / 16.1** |
| 「指定客户放在明细行里面，默认是为空的，属于批次维度，验收时可以人工去填写」 | 第 6 章：删掉单据头字段，`asn_line.extra.designated_customer_id` 默认空；`receive()` 时人工填写；纳入 `stock_batch` 唯一键 |
| 「老库存你看着办，没事的，目前还在编码阶段」 | 第 5.4 节：不做兼容性灰度，I2/I3 直接硬校验；演示库 `seed_demo.py --reset` 重建 |

**2026-11-05 第二轮反馈（v3.1）**

| 用户原话要点 | 本版设计落点 |
|---|---|
| 「没退出登录的功能，需要完善」 | 代码核对：`web_app/js/api.js:16` 已有 `logout()`、`layout.js:57` 独立页面侧栏已有「退出登录」；**工作台 `app.html` 无账号区/退出入口**。按「不懂先问再改」暂不改代码，待确认具体场景（第 16.2 节） |
| 「盘点需要支持盲盘、动碰盘点、全盘、部分盘点（按批号/货位/产品分类），最好可配置」 | **第 18 章**：`stocktake_plan` 方案表 + 范围条件组 + 真盲盘 API 分级 + 动碰算法 + 状态机改造 |
| 「库存转移只能修改批次信息，不能调整库存数量；数量只能做库存调整或盘点单根据差异生成库存调整」 | 7.1 / 7.3 / **7.6 库存调整单** / 18.7 盘点差异联动 |
| 「制单→分配库存→审核是正常流程；没分配可以关闭和删除；关闭也需要没有分配才能关闭」 | 7.2 两条互斥分支；关闭/删除均要求 `COUNT(line)=0` |
| 「不懂的可以再问再改，不要盲目修改，这条写到项目记忆中」 | 已写入 `.workbuddy/memory/MEMORY.md` 工作偏好段；本轮不写产品代码，疑问统一列在第 16.2 节 |

**2026-11-05 第三轮确认（v3.2 定稿，用户逐条答复）**

| 用户原话要点 | 本版设计落点 |
|---|---|
| 「只补工作台顶栏『账号名 + 退出按钮』（前端清 token 跳登录页），暂定前者」 | 13 章：`app.html` 顶栏账号区 + 退出按钮，复用 `api.js` 的 `logout()`；**不新增 `/api/auth/logout`**、不引入黑名单 / 审计表，保持 `auth.py` 无状态 token |
| 「审核即生效、approved 为终态，『关闭』只留给未分配的空单，按这个方向走」 | 7.2：删除「审核后还需关闭」的备选，两条互斥分支固化为终版；`approved` 后只能冲销 |
| 「分配库存……需要占用库存的，可用库存减少」 | 7.2 / 7.3：`allocate` 同时占用主账 `stock.allocated_qty` 与明细 `stock_batch.allocated_qty`（M5 超卖拦截因此能感知），写 `qty_delta=0` + `allocated_delta>0` 流水；`approve` / `unallocate` 对称释放；新增「取消分配」动作解决已分配单无法关闭/删除的死锁 |
| 「里程碑编号……好的」 | 14 章编号定稿：新里程碑 M20–M26、原四项 M27–M30；「库存调整单 + 盘点增强」插在 M21 之后占 M22；同步三处文档（见 14 章） |
| 「手工可以指定时间 和交易类型」 | 18.3 `stocktake_plan` 增 `movement_from/to` + `movement_types`；18.5 动碰算法支持时间窗 + `ref_type` 过滤，默认仍 `last_closed` |
| 「18-C 暂定前者」 | 18.6：`hide_book` 仅作用录入视角，`diff-preview` / 复核视角可见账存与差异 |
| 「额外增加一个按钮是生成独立库存调整单再审核过账」 | 18.7 / 18.8：盘点明细页新增显式按钮「生成库存调整单」，**由人工点击触发、不自动生成**；替换原 `adjust()` 直接写流水的自动行为 |

---

## 2. 现状盘点（代码级，已逐行核对）

| 位置 | 现状 | 对批次设计的影响 |
|---|---|---|
| `main.py:163-173` `stock` | `UNIQUE(owner_id, product_id, location_id)`，列仅 `qty / allocated_qty / updated_at` | **主账粒度 = 货主×物料×库位**，无批次、无客户、无质量状态 |
| `main.py:278-290` `stock_movement` | `qty_delta / ref_type / ref_id / note`，无 UNIQUE、无批次列 | 流水也不带批次，「批次级库存」目前无账可依 |
| `main.py:688-723` `batch` / `batch_receipt` | M19 三层：配置 → 档案（`batch_key` 指纹）→ 每次收货唯一 `BAT-YYYYMMDD-NNN` | **只有 `routes_asn.receive()` 一处写入**，其余动作不读不写，是数据孤岛 |
| `routes_asn.py:387-445` `receive()` | `stock_up()` + `record_receipt()` 同 `BEGIN IMMEDIATE` 事务 | 已具备「库存 + 批次原子写入」的正确雏形，可扩展为批次明细写入 |
| `routes_asn.py:448-493` `putaway()` | 只 `stock_move()`，**完全不引用批次表** | `batch_receipt.location_id` 至今是收货位快照，上架后实物位置失真 |
| `routes_dn.py:259-316` `pick()` | 库位由 payload 指定；`SELECT ... WHERE owner_id/product_id/location_id`；`可用 = qty - allocated_qty` | 无 FEFO、无客户过滤、无批次；`dn_pick` 无批次列 |
| `routes_dn.py:319-355` `ship()` | 按 `dn_pick` 逐库位扣 `qty` 与 `allocated_qty` | 扣减依据是拣货明细聚合，不是批次明细 |
| `routes_stock.py:144-215` `reconcile()` | 不变式：`stock.qty == SUM(stock_movement.qty_delta)`，按 `(owner, product, location)`，容差 `1e-9` | **必须原样保持**；不核 `allocated_qty` |
| `routes_transfer.py` / `routes_stocktake.py` | 移库 `stock_move()`、盘点 `adjust_stock()`，均不触碰批次表 | 五个动作里有两个会破坏批次明细一致性，必须补统一钩子 |
| `routes_location.py:14-20` / `:48-49` | `loc_type ∈ (receiving, storage, picking, returns)`；`zone` 为**自由文本**，无枚举 | 无 `virtual` / `quarantine` / `defective` 类型；无法表达不合格/虚拟货位 |
| `constants.py:53` | `GOODS_PROPERTIES = ("合格", "不合格")` | 无「待检 / 冻结」，无法表达质检分流与库存状态 |
| `constants.py:22,25` | `ASN_DOC_TYPES = (调拨, 销退, 采购, 期初, 其他)`；`DN_DOC_TYPES = (销售, 调拨, 退货, 其他)` | **代码中无任何单据标识位、无 `external_doc_type`**；v3 不建 `doc_kind` 枚举，改为 4 个布尔标识（第 8.4 节） |
| `main.py:116-139` `product.stock_qty` | 冗余列，**与 `stock` 主账无任何联动代码** | 迟早漂移，见第 3.4 节 |
| 测试 | 240 例全绿；批次断言只在 `test_batch.py`(18) / `test_asn.py` / `test_fresh_db_schema.py` | `test_dn.py` / `test_stock.py` / `test_transfer.py` / `test_stocktake.py` 批次关键字零命中 |

**结论**：目前批次是一套「收货时写入、之后再无人读取」的档案系统。用户说的「库存和批次息息相关」，
正是要补上 `stock ↔ batch` 之间的明细层与流水维度。

---

## 3. 业界经验与借鉴清单（本轮调研）

来源覆盖：富勒 WMS 上架策略拆解（策略包头/明细行/筛选条件/限制条件/六种预设规则）、
SAP EWM（批次主数据、FEFO、批次状态、客户库存 stock usage B、销售订单库存 E 专属、
物料凭证 movement type 与「过账后不可修改」）、Dynamics 365（Prevent multiple owners、
batch disposition codes 按业务类型分别封锁、tracking dimension 的 Owner 维度）、
Odoo（owned stock / 寄售专属库位与 ownership 校验）、领星 WMS（批次库存颗粒度、
移库/调整可指定批次）、顶妙 WMS（批次库存界面直接「调整库存 / 修改仓位」并全量生成库存变动明细）、
通天晓（3PL 多货主按货主差异化批次规则、WMS/ERP 数据边界）、库管易（FEFO 排序兜底、
先锁定再拣货、跳批审批留痕）、账本模式（流水 append-only、余额是投影、纠错用冲销）。

### 3.1 建议采纳

| 做法 | 出处 | 本项目落点 |
|---|---|---|
| 库存两层模型：明细层（库位×SKU×批次×状态×货主）+ 汇总层，先明细后汇总并定期校验 | 领星 / 多租户 ERP 表设计 / 账本模式 | 第 5 章：`stock_batch` 明细 + `stock` 汇总 |
| 流水 append-only；余额可全量重建；纠错用冲销而非删除或直改 | SAP 物料凭证 / CSDN 账本模式 / 帆软 | 第 5.3、7.3 节 |
| 批次属性修正走「原批次 → 目标批次」成对流水，保留原档案 | SAP 309 物料互转 / D365 库存转移日记账 | 第 7.2 节库存转移单 |
| 批次状态变更被视为一次**过账**，且「库存状态」与「批次状态」是两个独立维度 | SAP 341/342 / D365 disposition code | 第 7.1 节三类变更分三套机制 |
| 客户专属用「特殊库存标识 + 伙伴编号」，本质是所有权 ≠ 存放位置 | SAP 销售订单库存 E / 项目库存 Q / Odoo ownership | 第 6 章：`designated_customer_id`（最小实现，不引全套特殊库存） |
| 上架策略「包头 + 明细行 + 筛选条件 + 限制条件」；命中即止、有兜底行、统计命中率 | 富勒 / 天工 WMS / NetSuite | 第 10 章 |
| 筛选条件每维单选、**维度间 AND**；优先级 10/20/30 留插入空间；规则行 ≤20 条 | 富勒 | 第 10.2 节 |
| 限制条件按「库位状态 → 空间 → 库存/混放 → 复杂业务」由便宜到昂贵短路 | 富勒 | 第 9.4 节 |
| 不合格 / 特采进入虚拟库存区实现逻辑隔离；质检结果以批次为最小单位 | 大数跨境 WMS 全景 | 第 10.3 节不合格 / 虚拟分流 |
| FEFO 排序要有稳定兜底：失效日期 → 生产日期/入库时间 → 批次号 | 库管易 / SAP EWM | 第 15 章（本版仅预留） |
| 变更留痕字段级 old→new、业务时间与系统时间双存、原因码、操作人/审批人 | SAP CDHDR/CDPOS / 帆软 | 第 7.4 节 |
| 批次库存界面直接发起「调整 / 移库」并全量写库存变动明细 | 顶妙 WMS | 第 13 章前端 |

### 3.2 建议暂不采纳（避免过度设计）

- **SAP 全套特殊库存**（供应商寄售 K / 客户寄售 W / 订单库存 E / 项目库存 Q / 委外 O / 可退回包装 V）：
  本项目无 ERP、无成本核算、无 MRP，只需要「指定客户出库锁定」一个语义。
- **完整双时态查询 / 3PL 计费引擎 / RF-PDA 扫码校验**：硬件与业务前提不存在，只保留两个时间列。
- **通用规则引擎（Drools / Grule）**：上架是「一次求值、选最优库位」的无状态决策，
  表驱动 + 显式优先级更可审计，也守住「仅 Flask、零依赖」硬约束。
- **WMS/ERP 双向库存同步**：本项目没有上游 ERP，不存在双写竞态。
- **LPN / 容器 / 序列号 / 波次**：超出当前交仓范围。

### 3.3 两条直接改变 v1 结论的发现

1. **不要建第二套批次流水表。** 业界强调「余额与流水必须能互相核对」，两套流水最容易漂移。
   正确做法是**给现有 `stock_movement` 加维度列**：老流水这些列为 NULL/''，
   按 `(owner, product, location)` 分组 SUM 的结果完全不变，M6 reconcile 原样成立；
   一次动作拆成 N 条批次流水时，N 条之和仍等于主账变动量。
2. **上架不是纯加权打分。** 用户描述的「默认储区 → 同产品位置 → 同产品分类」是**优先级递降的
   fallback 链**，不是并列打分。纯加权会让「不合格品」因为同品集中得分高而混进正常库位。
   必须先用条件组把单据分流到互斥路径，再在路径内部按序 fallback，最后才用 score 排同储区候选。

### 3.4 调研对照后发现的 6 个现有代码隐患（沟通素材）

| # | 隐患 | 位置 | 建议 |
|---|---|---|---|
| 1 | `batch_receipt.batch_no` 是系统生成的 `BAT-` 流水号，而 `asn_line.extra.batch_no` 是供应商业务批号，**同名不同义** | `main.py:703` / `routes_asn.py:27-33` | 文档先明确语义，未来择期把系统号改名 `receipt_no`（涉及 M19 测试，需单独提交） |
| 2 | `batch_receipt.location_id` 是收货位快照，上架 / 移库 / 发货均不更新 | `routes_asn.py:448-493` | 保留为「首次收货位」历史值，另建 `batch_location` 表达实物分布 |
| 3 | `stock_movement` 无批次维度，reconcile 只核主账；批次数据只写不读 | `main.py:278-290` | 第 5 章统一流水维度 |
| 4 | `product.stock_qty` 冗余列与 `stock` 主账无联动 | `main.py:116-139` | 明确废弃或改为只读派生视图，避免两个「总库存」打架 |
| 5 | `GOODS_PROPERTIES` 仅「合格/不合格」，无待检/冻结 | `constants.py:53` | 扩为四值 + 状态转换矩阵（不合格单向，不可直接回合格） |
| 6 | `dn_pick` 无 UNIQUE，同一行同一库位可重复插入；拣货流水 `qty_delta=0` 不记 `allocated_delta` | `main.py:342-349` / `inventory_service.py:83-87` | 加唯一约束 + 流水补 `allocated_delta` 列（仅审计，不进 reconcile） |

---

## 4. 概念、术语与数据边界

| 术语 | 定义 | 本项目映射 |
|---|---|---|
| 外部批号 | 供应商 / 生产批号，可能重复、可能跨料号复用 | `asn_line.extra.batch_no` → `batch_receipt.info['batch_no']` |
| 内部批次号 | 系统内跨库位、跨仓追踪的唯一批次标识；销退回流靠它 | `batch_receipt.internal_batch_no`（M19 已有） |
| 验收入库批次流水号 | **每次收货一条、全局唯一** | `batch_receipt.batch_no` = `BAT-YYYYMMDD-NNN`（建议改名 `receipt_no` 消歧） |
| 关键批次信息 | 决定「是否同一批」的字段组合，可配 | `batch_config.is_key` + `batch.batch_key`（SHA-256 指纹，M19） |
| 批次档案 | 关键批次信息一致即同一档案 | `batch`（`batch_key` UNIQUE） |
| 批次库存明细 | 一批货在某个库位、某个质量状态、专属给某客户还剩多少 | **新增 `stock_batch`** |
| 指定客户 | 出库对象锁定标记，**与货主(owner)正交** | 新增 `designated_customer_id` |
| 库存转移 | 修改批次属性 / 质量状态 / 专属归属 / 分布，且全程留痕 | 新增 `inventory_transfer` 单据族 |
| 储区 | 带温区、危化、货主专区、单据形态白名单属性的区域 | 新增 `zone` 主数据表 |
| 条件组 | 一条规则行上多个筛选维度，维度间 AND、每维单选 | `putaway_rule.condition` JSON |
| fallback 链 | 一条规则行内按序尝试的多个寻址步骤 | `putaway_rule.steps` JSON |

**数据边界（每个数据项只有一个可写主数据源）**

| 数据项 | 主数据源 | 说明 |
|---|---|---|
| 物料 / 客户 / 货主 / 供应商主数据 | `product` / `customer` / `owner` / `supplier` | 单一来源，禁止在批次里另存一份可改副本 |
| 批次档案与关键字段口径 | `batch_config` + `batch` | 改配置**不重算历史指纹**（M19 口径延续） |
| 主账余额 | `stock`（只允许由流水过账产生） | 禁止任何路由层直接 `UPDATE stock` |
| 批次级余额 | `stock_batch`（同上） | 与主账由同一批流水驱动 |
| 库存事实（数量、批次、库位、状态、客户） | `stock_movement` append-only | 纠错只能追加冲销事件 |
| 批次属性展示值 | `batch.info` / `batch_receipt.info` JSON | **只读历史快照**；要「改」必须走库存转移生成新档案 |

---

## 5. 批次与库存主账：两层模型 + 统一流水（本设计的地基）

### 5.1 为什么不能改 `stock` 主键

`stock` 唯一键是 `(owner_id, product_id, location_id)`。若直接加 `batch_no` 进主键：

- `/api/stock/reconcile` 按三元组分组 SUM，会瞬间把一条库存拆成多行、与流水对不上 → **M6 不变式崩塌**；
- `pick()` 的 `SELECT ... WHERE owner_id=? AND product_id=? AND location_id=?` 会返回多行，
  `可用 = qty - allocated_qty` 与 `adjust_stock` 三道拦截全部失效 → **M5 超卖拦截崩塌**；
- 240 例现有测试里大量断言依赖单行余额。

因此采用业界标准的**两层模型**：`stock` 保持汇总层不变，新增 `stock_batch` 作明细层。

### 5.2 两层模型

```
stock（汇总层，不动）              stock_batch（明细层，新增）
UNIQUE(owner,product,location)     UNIQUE(owner,product,location,batch_no,batch_key,
                                          designated_customer_id,goods_property)
qty / allocated_qty               qty / allocated_qty / expiry_date / stock_status
        ▲                                   ▲
        └──────── 同一批 stock_movement 流水驱动 ────────┘
```

`stock_batch.qty` 与主账同口径「零行保留」，扣到 0 不删行。
`allocated_qty` 也要下沉到明细层，否则「专属库存被别的客户预占」无法拦截。

### 5.3 统一流水：给 `stock_movement` 加维度列（关键决策）

```sql
ALTER TABLE stock_movement ADD COLUMN batch_no                TEXT NOT NULL DEFAULT '';
ALTER TABLE stock_movement ADD COLUMN batch_key               TEXT NOT NULL DEFAULT '';
ALTER TABLE stock_movement ADD COLUMN internal_batch_no       TEXT NOT NULL DEFAULT '';
ALTER TABLE stock_movement ADD COLUMN designated_customer_id  INTEGER;
ALTER TABLE stock_movement ADD COLUMN goods_property          TEXT NOT NULL DEFAULT '';
ALTER TABLE stock_movement ADD COLUMN stock_status            TEXT NOT NULL DEFAULT '';
ALTER TABLE stock_movement ADD COLUMN expiry_date             TEXT NOT NULL DEFAULT '';
ALTER TABLE stock_movement ADD COLUMN allocated_delta         REAL NOT NULL DEFAULT 0;
ALTER TABLE stock_movement ADD COLUMN operator                TEXT NOT NULL DEFAULT '';
```

**三条口径必须写死**：

1. 所有新列都有默认值，老流水自动填 `''` / `0` / `NULL`，**不参与批次分组但参与主账 SUM**；
2. 一次物理动作涉及 N 个批次时写 **N 条流水**，满足
   `Σ(N 条 qty_delta) == 该库位主账变动量`；主账只 `UPDATE` 一次；
3. `allocated_delta` 只做审计，不进 reconcile（reconcile 仍只核 `qty_delta`）。

`inventory_service.adjust_stock()` 扩展为可选 `batches` 参数，保持向后兼容：

```python
def adjust_stock(db, owner_id, product_id, location_id, qty_delta, ref_type, ref_id,
                 allocated_delta=0.0, note="", batches=None):
    # batches=None  → 老链路：写 1 条无维度流水（现有 240 例测试全部走这条）
    # batches=[{batch_no, batch_key, designated_customer_id, goods_property, qty, ...}]
    #          → 按批次同步 stock_batch 明细，逐批次写带维度流水；Σ qty 必须等于 qty_delta
    ...
```

### 5.4 三个不变式（验收硬标准）

| # | 不变式 | 接口 | 状态 |
|---|---|---|---|
| I1 | `stock.qty == SUM(stock_movement.qty_delta)`，按 `(owner, product, location)`，容差 `1e-9` | `/api/stock/reconcile` | **M6 已有，必须原样保持** |
| I2 | `stock_batch.qty == SUM(stock_movement.qty_delta)`，按 `(owner, product, location, batch_no, designated_customer_id, goods_property)` | `/api/stock/batch-reconcile`（新增） | 新增 |
| I3 | `stock.qty == SUM(stock_batch.qty)`，按 `(owner, product, location)` | 同上 | 新增 |

**回填口径（用户 2026-11-05 授权自行决定）**：「老库存你看着办，没事的，目前还在编码阶段」。
既然没有生产数据与存量包袱，**取消 v2 的灰度降级方案，I2/I3 一律硬校验**：

- `init_db()` 建表时若发现已有 `stock` 但无 `stock_batch`，按 `batch_receipt` + `asn_line.extra`
  + 现存 `stock` 直接生成明细行，让 I2/I3 立即通过；
- 无法追溯到批次的历史行统一生成 `LEGACY-UNKNOWN` 占位明细并打标记，**但这不算豁免**：
  `/api/stock/batch-reconcile` 对任何不一致直接判 `consistent=false`（不再返回
  `LEGACY_NO_BATCH_DETAIL` 放行）；
- 演示数据直接用 `tools/seed_demo.py --reset` 重建，从头保证主账与明细一致；
- 好处：没有「灰度期豁免 → 收紧」两套逻辑，代码更简单，测试可以从第一天就断言三个不变式。

### 5.5 五个库存动作的统一钩子

| 动作 | 现有入口 | 必须同步 `stock_batch` | 批次流水来源 |
|---|---|---|---|
| 验收入库 | `routes_asn.receive()` `:387` | 新增明细行 | `record_receipt()` 返回的 `batch_no / batch_key / info` |
| 上架 | `routes_asn.putaway()` `:448` | 收货位 → 目标位转移 | 按该 ASN 行的 `batch_receipt` 拆批；同时写 `batch_location` |
| 拣货分配 | `routes_dn.pick()` `:259` | `allocated_qty` 下沉到明细 | 详见第 15 章（本版预留） |
| 发货 | `routes_dn.ship()` `:319` | 按 `dn_pick` 的批次明细扣减 | 同上 |
| 移库 / 盘点 | `routes_transfer.move()` `:184` / `routes_stocktake.adjust()` `:205` | 同步明细 | 移库沿用批次、盘点差异需指定批次或按 FEFO 兜底 |

**禁止路由层直接写 `stock_batch`**，全部经 `inventory_service` 钩子，避免主账与明细漂移。

---

## 6. 指定客户专属库存（`designated_customer_id`）

### 6.1 语义：与货主正交

| 维度 | 含义 | 现有/新增 |
|---|---|---|
| `owner_id` | 物权归属（3PL 多货主隔离） | 已有 |
| `designated_customer_id` | **出库对象锁定**：有值则这批货只能发给该客户 | 新增 |

用户说「入库和批次里面需要额外带有指定客户字段，如果这个内容有值，保证这个库存是只能这个客户出」。
这是 SAP「销售订单库存 / 项目库存」式的最小实现：**从通用库存里切出一块虚拟专属子库存**，
不引 `owner_type / valuated_flag` 全套特殊库存。

### 6.2 字段贯穿链路（v3：只在明细行）

**用户口径（2026-11-05）**：「放在明细行里面，默认是为空的，属于批次维度，验收时可以人工去填写」。
因此**取消 v2 的单据头字段**，只在 `asn_line` 上保存，且不在创建单据时强制填写。

| 落点 | 说明 |
|---|---|
| `asn_line.extra.designated_customer_id` | **唯一录入点，默认空**；创建收货单可不填，**验收（receive）时人工填写/修改** |
| `batch_receipt.designated_customer_id` | 新列，随 `record_receipt()` 从验收时的明细值写入（批次维度） |
| `stock_batch.designated_customer_id` | 唯一键组成部分；专属与通用**不允许合并在同一明细行** |
| `stock_movement.designated_customer_id` | 流水留痕，支持按客户追溯 |
| `dn_pick.designated_customer_id` | 拣货时记录实际消耗的批次归属 |

**「属于批次维度」的精确解释（本版口径）**：它是 `stock_batch` 的**库存明细维度**，
写入 `batch_receipt` 并参与唯一键，但**不加入 M19 `batch_config` 的 `is_key` 关键字段**，
即不参与 `batch_key` 指纹。理由：同一批货若分给两个客户，不应被拆成两个批次档案（档案仍按
批号/效期/灭菌批号等 7 个关键字段归并），只是库存明细行按客户分开。
若后续希望「指定客户不同＝不同批次档案」，把该字段加进 `batch_config.is_key` 即可，无需改代码结构。

**前端落点**：`web_app/asns.html` 的收货弹窗里，每行明细增加「指定客户」输入（可留空），
`doReceive()` 随 `line_id / location_id / qty / internal_batch_no` 一起提交；
`batch_receipt` 与 `stock_batch` 只认验收时的值，不认创建单据时的草稿值。

### 6.3 出库校验规则（本版只定义规则，实现放第 15 章之后）

| 库存 `designated_customer_id` | `dn.customer_id` | 结果 |
|---|---|---|
| 有值 | 相同 | ✅ 允许 |
| 有值 | 不同 / 单据无客户 | ❌ `CUSTOMER_RESERVED_FORBIDDEN` |
| 空（通用） | 任意 | ✅ 允许 |

消耗顺序建议：**优先消耗该客户的专属库存，再用通用库存**（避免专属库存长期呆滞），
但这条会影响 FEFO 排序优先级，列为第 16 章待确认项。

### 6.4 绑定与解绑必须走库存转移

- 通用库存 → 专属：库存转移单类型 `customer_bind`，写字段级变更 `NULL → CUST-001`；
- 专属 → 通用 / 换客户：`customer_unbind` / `customer_rebind`，**需要审批**；
- 任何情况下都不允许在页面上直接改 `stock_batch.designated_customer_id`。

---

## 7. 库存转移：修改批次信息且全程留痕

用户明确把「修改批次信息的功能」命名为**库存转移**。注意与 M17 `transfer`（移库单）区分：

| 单据 | 英文 / 前缀 | 语义 |
|---|---|---|
| M17 移库单 | `transfer` / `TR-` | **物理位置变化**，批次属性不变 |
| 库存转移单 | `inventory_transfer` / `IT-` | **批次属性 / 质量状态 / 专属归属 / 分布变化**，可能伴随库位变化 |
| 库存调整单 | `stock_adjustment` / `ADJ-` | **唯一允许调整库存数量**的手工单据（v3.1 新增，见 7.6） |

> **v3.1 口径修正（2026-11-05 用户强调，本章最重要的约束）**
> 「库存转移是**只能修改批次信息，不能调整库存数量**；库存数量只能做库存调整，
> 或者盘点单根据差异生成库存调整。」
> 因此库存转移单的**净数量变化恒为 0**（`Σ qty_delta = 0`，容差 `1e-9`），
> 它只在 `stock_batch` 明细层把数量从「旧维度组合」搬到「新维度组合」；
> **主账 `stock.qty` 全程一次都不 UPDATE**，I1 reconcile 天然保持。
> 任何让总库存增减的转移必须在审核前回滚并报 `TRANSFER_QTY_MUST_BE_ZERO`。
> 这同时推翻了 v3 里「关闭单据＝过账」的设计，状态机见 7.2。

### 7.1 三类变更必须分三套机制（业界共识，最易糊在一起）

| 类型 | 例子 | 机制 | 是否产生库存事务 |
|---|---|---|---|
| **A 批次属性修正** | 批号录错、效期补正、产地/生产企业改错 | `IT` 单 `batch_correct`：旧档案 → 新档案（`build_batch_key` 重新算指纹），**原档案保留不覆盖**，成对正负流水 | 是（成对） |
| **B 质量状态变更** | 合格 → 待检 / 不合格 / 冻结；不合格品隔离 | `IT` 单 `quality_change`：状态变更本身即一次过账；**不合格为单向**，不可直接改回合格，特采需审批 | 是（成对） |
| **C 库位 / 客户归属 / 批次维度变更** | 拆批到多库位（**总量不变**）、专属绑定解绑 | `IT` 单 `batch_split` / `customer_bind` / `customer_unbind`；移库复用 M17 但要同步 `stock_batch` | 是（净零） |

> **盘点差异、报损报溢、数量修正一律不属于库存转移**，必须走 7.6 节库存调整单。
> 拆批到多库位只改变「分布」不改变「总量」，属于库存转移；若拆分过程中出现数量增减，
> 超出部分拆成一张独立的库存调整单，不允许在同一张转移单里夹带。

**关键**：`batch.info` / `batch_receipt.info` 是历史事实快照，**永远不 UPDATE**。
改关键字段 → 指纹变化 → 生成/复用新 `batch` 档案 → 数量从旧明细转到新明细。
这与 SAP 309 物料互转、D365 库存转移日记账「原批次保留 + 新批次在更高层级生成」一致。

**库存状态 vs 批次状态**：`stock_status`（非限制 / 质检 / 冻结，库位维度）与
`goods_property`（合格 / 不合格，批次维度）是两个独立概念，不能合成一个字段。

### 7.2 单据模型与状态机

```sql
CREATE TABLE inventory_transfer (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  code           TEXT NOT NULL UNIQUE,          -- IT-YYYYMMDD-NNN
  transfer_type  TEXT NOT NULL,                 -- batch_correct|quality_change|batch_split|
                                                -- customer_bind|customer_unbind|location_move
  reason_code    TEXT NOT NULL DEFAULT '',      -- 错录补正/质检判定/客户要求/盘点差异/特采
  status         TEXT NOT NULL DEFAULT 'draft',
                                                -- 有分配: draft→allocated→approved(生效终态)
                                                -- 未分配: draft→closed(空单关闭) / cancelled(删除)
  reversal_of_id INTEGER,                      -- 冲销单指向原单
  creator        TEXT NOT NULL DEFAULT '',     -- 制单人
  allocator      TEXT NOT NULL DEFAULT '',     -- 分配库存人
  approver       TEXT NOT NULL DEFAULT '',     -- 审核人
  closer         TEXT NOT NULL DEFAULT '',     -- 关闭人
  business_at    TEXT NOT NULL DEFAULT '',     -- 业务发生时间（双时态）
  created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  allocated_at   TEXT NOT NULL DEFAULT '',
  approved_at    TEXT NOT NULL DEFAULT '',
  closed_at      TEXT NOT NULL DEFAULT '',
  note           TEXT NOT NULL DEFAULT ''
);
CREATE TABLE inventory_transfer_line (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  transfer_id    INTEGER NOT NULL REFERENCES inventory_transfer(id) ON DELETE CASCADE,
  product_id     INTEGER NOT NULL,
  from_location_id INTEGER, to_location_id     INTEGER,
  from_batch_id  INTEGER,   to_batch_id        INTEGER,
  from_batch_no  TEXT NOT NULL DEFAULT '',  to_batch_no  TEXT NOT NULL DEFAULT '',
  from_internal_batch_no TEXT NOT NULL DEFAULT '', to_internal_batch_no TEXT NOT NULL DEFAULT '',
  from_customer_id INTEGER, to_customer_id     INTEGER,
  from_goods_property TEXT NOT NULL DEFAULT '', to_goods_property TEXT NOT NULL DEFAULT '',
  qty            REAL NOT NULL,               -- 被修改的批次数量快照，不是增减量
  qty_before     REAL NOT NULL DEFAULT 0,      -- 变更前该维度余额
  qty_after      REAL NOT NULL DEFAULT 0,      -- 变更后该维度余额；整单必须总量守恒
);
```

**状态机（v3.1，用户 2026-11-05 修正口径）**

用户原话：「制单 → 分配库存 → 审核 是正常流程，没分配的情况下可以关闭和删除，
关闭也是需要没有分配的情况下才能关闭」。据此把流程拆成**两条互斥分支**：

| 分支 | 动作序列 | 状态 | 库存影响 |
|---|---|---|---|
| **正常（有分配）** | 制单 → 分配库存 → **审核（生效，终态）** | `draft → allocated → approved` | 审核时写**净零**批次流水 + 字段级变更文档，只改批次维度、不改数量 |
| **空单据（未分配）** | 制单 → **关闭** 或 **删除** | `draft → closed` / `cancelled` | **无任何库存动作** |

| 动作 | 前置条件 | 说明 | 权限码（预留） |
|---|---|---|---|
| 制单 | — | 建空单，不含明细 | `inventory_transfer:create` |
| 分配库存 | `draft` 且尚无已分配行 | **选定待修改的 `stock_batch` 行并真实占用库存**：主账 `stock.allocated_qty += qty`、明细 `stock_batch.allocated_qty += qty`，**可用库存减少**（M5 超卖拦截能感知）；写 `qty_delta=0` + `allocated_delta>0` 流水留痕，不动 `stock.qty` | `inventory_transfer:allocate` |
| **取消分配**（v3.2 新增） | `allocated` 且未审核 | 释放本单全部占用：两层 `allocated_qty` 对称减回、写负 `allocated_delta` 流水，单据回 `draft`，之后才可关闭 / 删除；**已审核单禁止** | `inventory_transfer:unallocate` |
| 审核 | `allocated` 且至少一行 | **批次信息变更生效**：`Σ qty_delta = 0` 硬校验 → 净零成对流水 + 两层维度明细更新 + **释放分配占用** + `batch_location` + `inventory_change_log`；单据进入终态 `approved` | `inventory_transfer:approve` |
| 关闭单据 | **必须未分配**（`draft` 且无行） | 空单据作废/结束，**不产生任何流水**；已分配单据禁止关闭（须先「取消分配」） | `inventory_transfer:close` |
| 删除单据 | **必须未分配**（`draft` 或已 `closed` 的空单） | 标记 `cancelled`；已分配 / 已审核单禁止删除 | `inventory_transfer:delete` |

- **「不能调整库存数量」的硬校验**：审核过账前计算 `Σ qty_delta`，必须 `== 0`（容差 `1e-9`），
  否则整单回滚并返回 `TRANSFER_QTY_MUST_BE_ZERO`；主账 `stock.qty` 全程零 UPDATE。
  **注意区分「数量」与「可用量」**：本单不改变任何数量，但分配阶段会临时压低可用量。
- **关闭与删除都要求「无分配行」**，避免有人用关闭动作把已选定的批次变更悄悄作废；
  接口层显式判断 `COUNT(line)=0`。用户同时确认「分配要占用库存」，因此**已分配又未审核的单据
  必须先「取消分配」释放占用，才能关闭 / 删除**——否则两层 `allocated_qty` 会永久挂住。
  这是 v3.2 补出的动作位，属于口径推导，不新增用户未确认的业务分支。
- **分配占用双层落账（v3.2 用户确认）**：只锁 `stock_batch.allocated_qty` 不够——
  `routes_dn.pick()` 的超卖校验读的是主账 `stock.allocated_qty`，不锁主账则「可用库存减少」
  对出库完全不可见。故 `allocate` 同步更新两层，`approve` / `unallocate` 对称释放。
  `allocated_delta` 只作审计列，**不进 I1 reconcile**（M6 不变式继续只核 `qty_delta`，
  与 3.4 隐患 #6 的修法合并）。
- `approved` 后不可改、不可删，只能新建 `reversal_of_id` 冲销单（冲销同样必须净零）。
- **权限**：只留 5 个 `@require_permission(...)` 钩子（M9 已有该装饰器），角色矩阵后置。
- 校验：每行 `qty ≤ 源明细可用量`；目标维度必须通过混放校验（第 9 章）；
  质量状态转换按允许矩阵（不合格 → 合格 拒绝，除非 `reason_code=特采` 且已审核）。

> **已确认（v3.2，第 16.1 节）**：用户「审核即生效、`approved` 为终态，『关闭』只留给未分配的
> 空单，按这个方向走」。因此审核事务末尾**不再**把状态置 `closed`，关闭动作严格限定
> `draft` + `COUNT(line)=0`；原「审核通过后系统自动关闭」的备选方案移除。

### 7.3 成对流水与冲销

```
原事实：ASN 收货 100 件，批号 LOT-A（合格）→ 流水 +100
发现错录：应为 LOT-B
正确做法：inventory_transfer 审核生效（approved，不是关闭）
  ① 流水 -100  (batch_no=LOT-A, note="IT-... 批次信息转出")
  ② 流水 +100  (batch_no=LOT-B, note="IT-... 批次信息转入")
  → Σ qty_delta = 0（硬校验）；主账 stock.qty 一次都不 UPDATE
  → 批次明细从 LOT-A 迁到 LOT-B，原档案、原流水、字段级变更文档三者永久保留
错误做法 1：UPDATE batch_receipt SET info=...   ← 禁止，历史被抹掉
错误做法 2：直接 ±50 让总库存变多/变少        ← 禁止，数量只能走库存调整单 / 盘点差异
```

验收测试要覆盖三条：① 净零校验（构造 Σ≠0 的转移单必须 400 且整单回滚）；
② 故意制造一笔错录再冲销，检查「原始记录 / 反向记录 / 最终余额」三者齐全；
③ 未分配单据可关闭可删除、已分配单据**关闭与删除都必须被拒绝**；
④ 分配后可用库存确实减少（`stock.allocated_qty` 与 `stock_batch.allocated_qty` 双层），
`unallocate` / `approve` 后对称释放且余额复原。

### 7.4 字段级变更文档（库存事务管「量」，变更文档管「属性」）

```sql
CREATE TABLE inventory_change_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  transfer_id   INTEGER NOT NULL REFERENCES inventory_transfer(id) ON DELETE CASCADE,
  line_id       INTEGER,
  object_type   TEXT NOT NULL,        -- batch|batch_receipt|stock_batch
  object_id     INTEGER,
  field_name    TEXT NOT NULL,        -- batch_no / expiry_date / goods_property / designated_customer_id ...
  old_value     TEXT NOT NULL DEFAULT '',
  new_value     TEXT NOT NULL DEFAULT '',
  operator      TEXT NOT NULL DEFAULT '',
  approver      TEXT NOT NULL DEFAULT '',
  business_at   TEXT NOT NULL DEFAULT '',
  system_at     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  reason_code   TEXT NOT NULL DEFAULT ''
);
```

追溯页提供两个入口：**查看关联单据**、**发起业务动作**（按批次+库位冻结、生成复核/盘点任务、异常工单）。
只查不能办的追溯，现场仍会拿编号手工传递，最容易漏冻结、错批次。

### 7.5 销退内部批次号口径（用户确认）

当前 `_resolve_internal()` 优先级（`batch_service.py:48-62`）：
`payload 传入 > 明细快照 > 销退回溯历史 > 首次入库=批次号`。

用户口径「销退一般都是外部传入内部批次号的」，本版调整为：

| 场景 | 规则 | 来源标记 |
|---|---|---|
| 外部传了内部批次号（正常） | 直接采用，**并校验该号在本系统或外部来源登记中存在** | `external` |
| 销退但外部没传 | 沿用该档案最近一次 `internal_batch_no`，**同时写告警 `MISSING_EXTERNAL_BATCH_NO`** | `return_trace` |
| 首次入库（采购/期初） | 内部批次号 = 本次 `BAT-` 批次流水号 | `first_in` |

补充建议：销退单上增加 `original_dn_id` / `original_internal_batch_no` 两个字段，与传入值交叉校验；
不一致时不允许静默采用，生成异常记录交人工确认。这条直接影响追溯链完整性。

### 7.6 库存数量的唯一入口：库存调整单（v3.1 新增）

用户口径：「库存数量只能做库存调整，或者盘点单根据差异生成库存调整」。

| 允许改变 `stock.qty` 的动作 | 说明 |
|---|---|
| 正常出入库（ASN 收货/上架、DN 发货、移库） | 既有业务，单据驱动，写 `stock_movement` |
| **库存调整单** `stock_adjustment` | 手工报损 / 报溢 / 数量修正的**唯一入口** |
| **盘点差异生成的库存调整单** | 盘点不再直接 `adjust_stock()`，改为按差异生成调整单，审核后过账 |

```sql
CREATE TABLE stock_adjustment (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  code         TEXT NOT NULL UNIQUE,        -- ADJ-YYYYMMDD-NNN
  source_type  TEXT NOT NULL,               -- manual | stocktake_diff
  source_id    INTEGER,                    -- 盘点单 id（差异来源）
  reason_code  TEXT NOT NULL DEFAULT '',   -- 报损/报溢/盘点差异/错账更正
  status       TEXT NOT NULL DEFAULT 'draft', -- draft → approved（过账）/ cancelled
  creator      TEXT NOT NULL DEFAULT '',
  approver     TEXT NOT NULL DEFAULT '',
  created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  approved_at  TEXT NOT NULL DEFAULT '',
  note         TEXT NOT NULL DEFAULT ''
);
CREATE TABLE stock_adjustment_line (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  adjustment_id  INTEGER NOT NULL REFERENCES stock_adjustment(id) ON DELETE CASCADE,
  owner_id       INTEGER NOT NULL, product_id INTEGER NOT NULL,
  location_id    INTEGER NOT NULL,
  batch_no       TEXT NOT NULL DEFAULT '',
  delta_qty      REAL NOT NULL,            -- 唯一允许非零的数量增减
  qty_before     REAL NOT NULL DEFAULT 0,
  reason         TEXT NOT NULL DEFAULT ''
);
```

审核通过才在 `BEGIN IMMEDIATE` 事务里用 `C.MOVEMENT_ADJUSTMENT`（`ref_id = adjustment_id`）过账，
保证「任何数量变化都有一张可追溯、可冲销的调整单」。

**库存转移与库存调整严格互斥**：库存转移行的 `qty` 只是「被修改的批次数量快照」，
不得当作 `delta_qty` 写入流水；接口层对库存转移单直接拒绝 `delta_qty` / `qty_adjust` 等字段，
防止前端把两类单据混用。盘点单如何按差异生成调整单、以及可配置盘点方案，见第 18 章。

---

## 8. 储区主数据表（用户已确认）

### 8.1 新表

```sql
CREATE TABLE zone (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  code             TEXT NOT NULL UNIQUE,       -- PK-01 / ST-A / QC-NG / VIR-01 / RET-01
  name             TEXT NOT NULL,
  warehouse_id     INTEGER,                   -- 空=全仓共享
  temp_class       TEXT NOT NULL DEFAULT '常温',  -- 常温/阴凉/冷藏/冷冻
  hazard_class     TEXT NOT NULL DEFAULT '',      -- 危化类别，空=不限
  owner_whitelist  TEXT NOT NULL DEFAULT '[]',   -- JSON；空=不限货主（货主专区用）
  flag_whitelist    TEXT NOT NULL DEFAULT '[]',  -- JSON：virtual / direct / cold_chain / hazard
                                                  -- 空=不限；冷链区只收 cold_chain 等
  external_doc_type_whitelist TEXT NOT NULL DEFAULT '[]', -- JSON；空=不限外部单据类别
  goods_property_whitelist TEXT NOT NULL DEFAULT '[]', -- 空=不限；不合格区只收「不合格」
  default_loc_type TEXT NOT NULL DEFAULT 'storage',
  allow_mix_product INTEGER,                  -- NULL=继承全局策略
  allow_mix_batch   INTEGER,
  allow_mix_owner   INTEGER,
  max_sku_per_location INTEGER,
  status           TEXT NOT NULL DEFAULT 'active',
  note             TEXT NOT NULL DEFAULT ''
);
ALTER TABLE location ADD COLUMN zone_id INTEGER REFERENCES zone(id);
-- location.zone（自由文本）保留为历史兼容列，不再作为业务判断依据
```

### 8.2 `loc_type` 枚举扩展

现有 `(receiving, storage, picking, returns)` → 新增：

| 值 | 用途 |
|---|---|
| `defective` | 不合格品隔离库位（红标、锁闭、不可分配） |
| `quarantine` | 待检 / 冻结暂存 |
| `virtual` | 虚拟货位（`is_virtual=1` 单据的分流目标，v3 口径见第 8.4 / 10.1 节） |
| `crossdock` | 直调 / 越库暂存位（`is_direct=1` 单据，不进常规存储区） |

### 8.3 迁移与播种

1. `init_db()` 建 `zone` 表、`location.zone_id` 幂等 ALTER；
2. 从现有 `SELECT DISTINCT zone FROM location` 生成 zone 记录，`code` 用文本规范化；
3. 播种预置储区：`PK-01` 拣货区、`ST-A` 存储区、`RET-01` 退货区、`QC-NG-01` 不合格隔离区、
   `QUAR-01` 待检/冻结区、`VIR-01` 虚拟货位区、`XDOCK-01` 直调暂存区、
   `COLD-01` 冷链区（`temp_class=冷藏`）、`HAZ-01` 危化区、`EXC-01` 异常兜底区；
   冷链/危化区同时设置 `flag_whitelist`，从储区侧二次拦截；
4. `routes_location.py` 的 `zone` 自由文本校验改为 `zone_id` 外键校验，列表页与新表单同步；
5. 老库 `zone_id` 为空时回退到「默认区」，不阻断现有 240 例测试。

**用途**：R01 指定储区、R02 不合格/虚拟隔离、货主专区、温区/危化硬约束都从 `zone` 取属性，
不再依赖自由文本字符串比较。

### 8.4 单据标识字段（v3 新增，用户明确需求）

用户原话：「入库单据 出库单据需要增加一个虚拟标识，直调标识，冷链标识，危险标识，
和外部单据类别字段；后续上架可以按这些字段来推荐上架货位」。

**这同时回答了 v2 的「虚拟单据歧义」**：虚拟不是一种**单据种类**，而是一个**单据标识位**。
因此**取消 `doc_kind` 枚举设计**，改为 4 个布尔标识 + 1 个类别字段，入库单与出库单都要加。

```sql
-- asn 与 dn 各加同一组 5 列（幂等 ALTER，旧库补列）
ALTER TABLE asn ADD COLUMN is_virtual        INTEGER NOT NULL DEFAULT 0;  -- 虚拟标识
ALTER TABLE asn ADD COLUMN is_direct         INTEGER NOT NULL DEFAULT 0;  -- 直调（越库）标识
ALTER TABLE asn ADD COLUMN is_cold_chain     INTEGER NOT NULL DEFAULT 0;  -- 冷链标识
ALTER TABLE asn ADD COLUMN is_hazard         INTEGER NOT NULL DEFAULT 0;  -- 危险（危化）标识
ALTER TABLE asn ADD COLUMN external_doc_type TEXT    NOT NULL DEFAULT ''; -- 外部单据类别
-- dn 同上 5 列；出库侧本版只落字段，供（暂不占编号的）出库分配与 M29 外部对接使用
CREATE INDEX IF NOT EXISTS idx_asn_ext_type ON asn(external_doc_type);
CREATE INDEX IF NOT EXISTS idx_dn_ext_type  ON dn(external_doc_type);
```

| 字段 | 含义 | 对上架的作用 | 归类 |
|---|---|---|---|
| `is_virtual` | 虚拟标识（账面/非实物或逻辑单据） | 条件组命中 → 虚拟货位区 `VIR-01`；未来出库分配默认排除 | **条件维度**（软分流） |
| `is_direct` | 直调 / 越库（cross-docking） | 条件组命中 → 直调暂存位 `XDOCK-01`，不进入常规存储区 | **条件维度**（软分流） |
| `is_cold_chain` | 冷链 | **硬约束**：候选储区 `temp_class` 必须为冷藏/冷冻；同时可作条件维度指定冷链区 | **硬约束 + 条件维度** |
| `is_hazard` | 危险 / 危化 | **硬约束**：候选储区 `hazard_class` 必须匹配且可收危化；同时可作条件维度指定危化区 | **硬约束 + 条件维度** |
| `external_doc_type` | 外部系统（OMS/ERP）下发的单据类别码 | 条件组维度，按类别分流到专属储区；与 M29 外部对接共用字典 | **条件维度**（软分流） |

**三条设计口径**

1. **冷链 / 危险是硬红线，不能被打分买通**：`is_cold_chain=1` 的单据若被推荐到常温库位，
   即使「同品集中」得分最高也必须排除，返回 `STORAGE_CLASS_MISMATCH`；`is_hazard=1` 同理。
   校验落在第 9.4 节流水线第 2 步（储区属性）。
2. **虚拟 / 直调 / 外部类别是条件组分流**：走哪个货位由规则行配置决定，**不写死在代码里**。
   v2 的「虚拟单据强制走 `VIR-01`」改为默认预置规则行，用户可在 `putaway_rules.html` 改目标储区。
3. **5 个字段全部进入 `putaway_rule.condition` 维度**（第 10.2 节），
   且 `zone.flag_whitelist` / `zone.external_doc_type_whitelist` 提供储区侧白名单做二次校验。

**`external_doc_type` 字典**：建议新建 `external_doc_type` 配置表（code / name / 启用 /
默认目标储区 / 备注），而不是自由文本，避免外部系统传来的类别码拼写漂移；
留空表示内部单据。M29 外部对接时由 `X-API-Key` 绑定的货主带入，
**外部写入的 `external_doc_type` 只接受字典内已启用的 code**。

**验收与前端**：`routes_asn.create_asn()` / `routes_dn.create_dn()` 接受并校验 5 个字段
（4 个标识位只接受 0/1，`external_doc_type` 校验字典）；
`web_app/asns.html` / `dns.html` 创建弹窗各加一组勾选框 + 外部类别下拉；
列表与详情展示标识徽标；`tools/seed_demo.py` 播种覆盖 4 种标识各 1 单。
后端新增 ≈8 测（默认值、0/1 校验、字典校验、列表过滤、详情带出、DN 同步）。

---

## 9. 库位混放策略（入库环节，用户已确认口径）

### 9.1 用户口径

> 「货位是否混发」＝**库位混放**，是**入库**环节，产品不允许混放或批号不能混放。

即：`receive()` / `putaway()` 把货放进库位时，要检查该库位现有库存与本批是否允许共存。
**出库侧不在此规则范围内**（出库分配另议）。

### 9.2 策略表与作用域继承

```sql
CREATE TABLE storage_policy (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  scope           TEXT NOT NULL,              -- global|warehouse|zone|group|location
  scope_key       TEXT NOT NULL DEFAULT '',
  allow_mix_product INTEGER,                 -- NULL=继承上层；1/0
  allow_mix_batch   INTEGER,
  allow_mix_owner   INTEGER,
  max_sku_per_location INTEGER,
  max_weight_kg     REAL, max_volume_cm3   REAL,
  goods_property    TEXT,                    -- 该库位只收某种质量状态
  owner_whitelist   TEXT NOT NULL DEFAULT '[]',
  note              TEXT NOT NULL DEFAULT '',
  updated_at        TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  UNIQUE(scope, scope_key)
);
```

解析顺序（具体 → 一般，取第一个非 NULL）：
`location > location_group > zone > warehouse > global`。
`zone` 升级为主数据表后，`scope='zone'` 直接用 `zone_id`，比自由文本可靠。

### 9.3 默认值（按用户口径调严，与 v1 不同）

| 字段 | v1 默认 | **v3 默认** | 理由 |
|---|---|---|---|
| `allow_mix_product` | 开（上限 2） | **关** | 用户明确「产品不允许混放」；医药 / GSP 追溯优先 |
| `allow_mix_batch` | 关 | **关**（不变） | 用户明确「批号不能混放」 |
| `allow_mix_owner` | 关 | **关**（不变） | 物权隔离，D365 `Prevent multiple owners` 同款 |
| `max_sku_per_location` | 2 | 1（=禁混产品） | 需要放开时按储区配置，不由全局默认放宽 |

**空库位永远可放**；非空库位必须「同 SKU + 同关键批次信息 + 同货主 + 同质量状态 + 同指定客户」
才可合并，否则按上表逐项拒绝。

### 9.4 校验流水线（先便宜后昂贵，短路返回）

```
1 库位状态 / loc_type / 启停                       → LOC_DISABLED / LOC_TYPE_MISMATCH
2 储区属性：温区(is_cold_chain)、危化(is_hazard)、货主专区、flag_whitelist、
  external_doc_type_whitelist                      → STORAGE_CLASS_MISMATCH
3 质量状态 / 库存状态（合格·待检·不合格·冻结）      → QUALITY_STATE_CONFLICT（硬红线）
4 空间：容量、体积、承重、长宽高                   → CAPACITY_EXCEEDED
5 产品混放 + SKU 数上限                            → MIX_PRODUCT_FORBIDDEN / MAX_SKU_EXCEEDED
6 批次混放（比 batch_key，或可配按 batch_no）      → MIX_BATCH_FORBIDDEN
7 货主混放                                          → MIX_OWNER_FORBIDDEN
8 指定客户混放                                      → DESIGNATED_CUSTOMER_CONFLICT
9 复杂业务（ABC 匹配、同品要求）                    → 交推荐规则处理
```

每次拦截返回「原因码 - 结果 - 建议下一步」，并落拦截日志（时间、操作人、库位、SKU、
现有 SKU/批次列表、违反的规则编号）。

**v3.3 配置化改造（用户第四轮答复 1A / 3B）**：上面 1–8 步是**可配置的检查项清单**，
不是产品代码里的硬编码常量。M25 落地时，每一步的「是否启用 / 允许值 / 拒绝原因码」
都来自命中的 `putaway_rule.limits`；10.3 的预置规则行只是**初始种子数据**，用户可在 M26
编辑器里增删改、调优先级，也可以完全删掉冷链 / 危险行。系统不再保留任何「绕不过配置」
的红线（3B），因此**不存在代码里写死的温区白名单、危化白名单或固定隔离库位**。
检查项仍以过滤（pass/reject）方式执行，不降级为打分权重。

**v3.3 过渡期（用户第四轮答复 2B）**：M20 剩余**只补单据标识位与 `external_doc_type`**，
`POST /api/asns/<id>/putaway` **不新增任何强制拦截**；M20→M25 之间完全依赖
`putaway_service.recommend()` 的只读排除提示，用户手工选货位不做校验。
M25 规则引擎上线时，才把「推荐提示」升级为「按规则行拦截」，一次性完成，避免写死再推倒。

**边界（借鉴 D365）**：盘点/移库不重新执行混放约束（约束只对**后续上架**生效），
但盘点发现历史混放时要生成人工移库任务；跨货主转移必须经「允许多货主」的暂存区。

---

## 10. 上架推荐规则引擎（含过滤条件配置）

### 10.1 执行模型：条件组分流 → 规则行内 fallback → 候选内打分

```
① 策略包匹配：货主+仓库精确 > 仓库默认(owner 空) > 系统默认
② 条件组匹配：明细行按 priority 升序，多维 AND，第一行命中即停
③ 该行的 fallback 链：按 steps 顺序尝试，第一个产出非空候选的步骤生效
④ 硬约束过滤：对候选库位跑第 9.4 节流水线，拒绝项带原因码
⑤ 候选内排序：score 规则加权（同批号/同品/同分类/动线/容积），取 Top-N
⑥ 全部失败 → 兜底行（priority 999 异常区）+ NO_CANDIDATE + 待人工处理
```

冲突消解：**规则行 limits（硬过滤） > 混放策略 > 条件组命中 > fallback 步骤 > score 权重**。
同分按 `location.code` 稳定排序，保证可复现、可测试。
**约束绝不能被打分买通**——一旦命中的规则行限制了某类库位，该库位就不能因为
「同品集中得分高」而被放行。

> **v3.3（3B）**：约束的**内容**全部来自配置（多条规则、按 `priority` 升序命中即停、
> 条件维度可由单号信息 / 产品信息组合），不再有系统级 hardcoded 红线；
> 但「命中的约束按过滤执行、不参与加权」这一**机制**不变。
> 用户要的「多个规则 + 优先级 + 按单号/产品推荐」即由本节的策略包 → 条件组 →
> fallback 链 → limits 过滤 → Top-N 完整链路承载，M25/M26 一次做完，M20 不提前写死。

> **v3 口径已明确（2026-11-05）**：用户把「虚拟」定义为**入库 / 出库单据上的一个标识位**，
> 而不是单据种类，并要求同批增加直调、冷链、危险标识与 `external_doc_type` 字段，
> 「后续上架可以按这些字段来推荐上架货位」。
> 因此**取消 v2 的 `doc_kind` 枚举**，改为第 8.4 节的 4 个标识位 + 1 个类别字段；
> 虚拟单据不再硬编码「强制走 `VIR-01`」，而是由可配置的预置规则行分流；
> `is_cold_chain` / `is_hazard` 两个标识同时是**硬约束**（不可被打分买通）。
> 原 (a) 无实物流转 / (b) 不合格品隔离区 / (c) 外部预占单据 三种猜测全部作废。

### 10.2 条件组：每维单选、维度间 AND

```json
{
  "doc_type": "销退",
  "goods_property": "不合格",
  "is_virtual": null,
  "is_direct": null,
  "is_cold_chain": null,
  "is_hazard": null,
  "external_doc_type": null,
  "abc_class": null,
  "owner_id": null,
  "is_designated": null,
  "zone_id": null,
  "product_id": null
}
```

`null` = 不限（通配）。匹配逻辑（富勒式，避免组合爆炸同时支持复合分流）：

```python
def matches(cond, ctx):
    return all(v is None or v == ctx.get(k) for k, v in cond.items())
```

**v3 条件维度清单（共 13 个，全部来自单据上下文 `ctx`）**

| 维度 | 取值 | 来源 | 备注 |
|---|---|---|---|
| `doc_type` | 调拨/销退/采购/期初/其他 | `asn.doc_type` | 已有 |
| `goods_property` | 合格/待检/不合格/冻结 | 明细 `extra` | v3 扩四值 |
| **`is_virtual`** | 0/1 | `asn.is_virtual` | **v3 新增**，虚拟标识 |
| **`is_direct`** | 0/1 | `asn.is_direct` | **v3 新增**，直调/越库 |
| **`is_cold_chain`** | 0/1 | `asn.is_cold_chain` | **v3 新增**，同时是硬约束 |
| **`is_hazard`** | 0/1 | `asn.is_hazard` | **v3 新增**，同时是硬约束 |
| **`external_doc_type`** | 字典 code | `asn.external_doc_type` | **v3 新增**，外部单据类别 |
| `abc_class` | A/B/C | `stock_movement` 聚合 | 可缓存计算字段 |
| `owner_id` | 货主 id | `asn.owner_id` | 货主专区 |
| `is_designated` | 0/1 | `asn_line.extra.designated_customer_id` | 指定客户专属库存 |
| `zone_id` | 储区 id | 上下文或规则指定 | 储区主数据 |
| `product_id` / `category_l1~l4` | 物料 / 分类 | `product` | 货品默认储区、同分类聚拢 |

`null` = 不限（通配）；每维单选、维度间 AND。**v3.3 起（3B）**：冷链与危险不再有
「条件组未命中也由系统兜底拦截」的第二条路径——是否限制、限制到哪些储区/温区，
**一律由命中规则行的 `condition` + `limits` 决定**；没配规则就不拦。
10.3 的预置行只是初始种子，可在 M26 编辑器里改。

**v3.3 维度范围（4A）**：首批就按上表 **13 维**落地，不提前扩「单号前缀 / 模糊匹配、
供应商、客户、货品温区要求、包装规格」等维度；M26 条件组编辑器的数据驱动渲染
为扩展位留好接口，后续按用户实际需要再加，避免一次性过度设计。

规则行结构：

```sql
CREATE TABLE putaway_rule_set (           -- 包头
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  warehouse_id INTEGER, owner_id INTEGER,      -- 空=该仓默认 / 系统默认
  status TEXT NOT NULL DEFAULT 'active', note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE putaway_rule (               -- 明细行
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  rule_set_id INTEGER NOT NULL REFERENCES putaway_rule_set(id) ON DELETE CASCADE,
  priority    INTEGER NOT NULL DEFAULT 10,  -- 10/20/30 留插入空间
  name        TEXT NOT NULL,
  condition   TEXT NOT NULL DEFAULT '{}',   -- 第 10.2 节条件组 JSON
  steps       TEXT NOT NULL DEFAULT '[]',   -- fallback 链，见 10.3
  limits      TEXT NOT NULL DEFAULT '{}',   -- 混放 / 空间 / 状态限制
  enabled     INTEGER NOT NULL DEFAULT 1,
  note        TEXT NOT NULL DEFAULT ''
);
```

包头匹配优先级、兜底行、命中率/驳回率统计、**分批上线**（先一个储区跑「建议确认」两周、
再逐步启用更多规则行）均按业界做法落 `putaway_recommendation` 日志。
注意这是「上线节奏」，与第 5.4 节已取消的「数据兼容灰度豁免」是两回事。

### 10.3 预置规则行（v3：覆盖用户举的场景 + 5 个新标识维度）

| 优先级 | 条件组 | fallback 链（按序） | 目标 | 限制 |
|---|---|---|---|---|
| **5** | `is_cold_chain = 1` | 指定储区 `COLD-01` → 该储区空库位 | 冷链区 | **硬约束**：`zone.temp_class ∈ (冷藏, 冷冻)` |
| **8** | `is_hazard = 1` | 指定储区 `HAZ-01` → 该储区空库位 | 危化区 | **硬约束**：`zone.hazard_class` 匹配 |
| **10** | `goods_property = 不合格` | 固定库位 `QC-NG-01` → 该储区空库位 | 不合格品隔离区 | 必须空位、禁混、不可分配 |
| **20** | `is_virtual = 1` | 指定储区 `VIR-01` → 该储区空库位 | 虚拟货位 | 未来出库分配默认排除 |
| **25** | `is_direct = 1` | 固定库位 `XDOCK-01` | 直调 / 越库暂存位 | 不进入常规存储区 |
| **30** | `goods_property ∈ (待检, 冻结)` | 指定储区 `QUAR-01` | 待检 / 冻结区 | 硬隔离 |
| **35** | `external_doc_type = <字典 code>`（可配多行） | 按类别字典配置的默认储区 | 外部单据专属区 | 由字典带出，可人工改 |
| **40** | `doc_type = 销退 且 goods_property = 合格` | 同 `internal_batch_no` 原库位 → 同品集中 → `RET-01` | 退货良品回流 | 沿用原内部批次号 |
| **50** | 正常单据（4 个标识全 0、`goods_property=合格`） | ① **货品默认储区**（`product.default_zone_id`）<br>② **同产品位置**（同 SKU 已占库位，优先填满）<br>③ **同产品分类推荐货位**（`category_l1~l4` 共现聚拢）<br>④ 指定储区空库位 | 常规上架 | 容量 / 温区 / 危化 / 禁混 |
| **60** | 正常 + A 类（可选） | 黄金拣货位 + 动线距离排序 | ABC 提效 | 同上 |
| **999** | 全部不限 | 固定库位 `EXC-01` | 兜底异常区 | 标记「待人工处理」 |

第 5/8 行的冷链与危险**同时**是硬约束：即使条件组没配、或打分再高，常温库位 / 非危化库位
也会被第 9.4 节流水线第 2 步直接排除。第 20/25/35 行证明用户要的「按这些字段推荐上架货位」
是**可配置的条件分流**，不是硬编码路径。

第 50 行就是用户说的「默认货品推荐储区，或者同产品位置推荐，或者同产品分类推荐货位」——
**三者是递降 fallback，不是并列打分**。每一步产出候选后先跑硬约束，全被拒才进入下一步。

`steps` 示例：

```json
[
  {"type": "zone_by_product", "source": "product.default_zone_id"},
  {"type": "same_product",    "order": "occupied_first"},
  {"type": "same_category",   "level": "category_l1"},
  {"type": "empty_in_zone",   "zone_code": "ST-A"}
]
```

六种预设寻址类型（业界共识）：指定储区找位 / 推荐空位 / 固定库位 / 最近一次上架位 /
同品集中（已占优先）/ 同品集中（空位优先）。`zone_by_product` 与 `same_category` 是本项目
在六种之上补的两类，用于覆盖用户明确提出的场景。

### 10.4 内置评分规则（只用于同一步骤内候选排序）

| 编号 | 规则 | 默认权重 | 数据来源 |
|---|---|---|---|
| R07 | 同关键批次信息（`batch_key` 一致） | 30 | `batch` / `stock_batch` |
| R08 | 同批号 / 灭菌批号 / UDI / 内部批次号 | 25 | `batch_receipt.info` |
| R09 | 同产品集中 | 25 | `stock` |
| R10 | 产品分类聚拢 | 15 | `product` / 库位已存 SKU |
| R11 | 效期分档（FEFO 友好） | 15 | `stock_batch.expiry_date` |
| R12 | ABC 周转 / 黄金位 | 20 | `stock_movement` 近 30 天出库频次 |
| R13 | 容积 / 承重 best-fit | 10 | `location.capacity_volume` / `product.volume` |
| R14 | 动线距离（巷道/层） | 10 | `location.aisle/bay/level` |
| R15 | 零散库位合并 | 10 | `stock_batch` |

权重是可调参数而非标准答案；经验值需按本仓数据重算。R07/R08 直接复用 M19 批次指纹。

### 10.5 可解释输出（前端与日志共用）

```json
{
  "line_id": 12, "qty": 4, "rule_set": "PUTAWAY-DEFAULT", "hit_rule": 50,
  "hit_condition": {"is_virtual": 0, "is_direct": 0, "is_cold_chain": 0,
                    "is_hazard": 0, "goods_property": "合格"},
  "hit_step": 1, "step_name": "zone_by_product",
  "top": [{"location_code": "A-01-02", "score": 87.5,
           "breakdown": {"R07": 30, "R09": 25, "R10": 15, "R13": 7.5, "R14": 10},
           "reasons": ["命中货品默认储区 PK-01", "同关键批次信息 batch_key 一致"]}],
  "rejected": [{"location_code": "A-01-03", "reason_code": "MIX_BATCH_FORBIDDEN",
                "message": "该库位已存批次 LOT-A，禁批号混放"}],
  "fallback": null
}
```

---

## 11. 数据模型汇总（新增 / 扩列）

| 表 | 类型 | 章节 | 一句话 |
|---|---|---|---|
| `zone` | 新增 | 8 | 储区主数据，带温区/危化/货主专区/`flag_whitelist`/`external_doc_type_whitelist` |
| `location.zone_id` | 扩列 | 8 | 旧 `zone` 文本保留兼容 |
| `asn` / `dn` 各 5 列 | 扩列 | 8.4 | `is_virtual` / `is_direct` / `is_cold_chain` / `is_hazard` / `external_doc_type` |
| `external_doc_type` 字典表 | 新增 | 8.4 | 外部单据类别 code / 名称 / 启用 / 默认目标储区 |
| `storage_policy` | 新增 | 9 | 五级作用域混放策略，NULL=继承 |
| `stock_batch` | 新增 | 5.2 | 批次级库存明细层（含 `designated_customer_id` / `allocated_qty` / `expiry_date`） |
| `batch_location` | 新增 | 5.5 | 一次收货拆多库位的批次实物分布 |
| `stock_movement` | 扩列 9 列 | 5.3 | 统一流水带批次/客户/质量状态维度 + `allocated_delta` |
| `asn_line.extra` | 扩字段 | 6.2 | `designated_customer_id` **默认空、只在明细行、验收时人工填写**（不设单据头字段） |
| `batch_receipt` | 扩列 | 6.2 | `designated_customer_id`；`location_id` 明确为首次收货位历史值 |
| `dn_pick` | 扩列 + UNIQUE | 15 | 批次 / 客户维度，供未来出库分配 |
| `inventory_transfer` / `_line` | 新增 | 7.2 | 库存转移单（**只改批次信息、数量净零**；有分配＝制单→分配→审核生效终态，未分配＝关闭/删除；审核为唯一过账点） |
| `inventory_change_log` | 新增 | 7.4 | 字段级变更文档 old→new |
| `stock_adjustment` / `_line` | 新增 | 7.6 | **唯一允许手工改变 `stock.qty` 的单据**；`source_type = manual / stocktake_diff`，审核后过账 |
| `stocktake_plan` | 新增 | 18.3 | 可配置盘点方案（全盘 / 部分范围条件组 / 盲盘模式 / 动碰基准 + **手工时间窗 `movement_from/to` + 交易类型 `movement_types`** / 自动调整默认关） |
| `stocktake` / `stocktake_line` | 扩列 | 18.3 / 18.7 | 加 `plan_id` / `scope_snapshot` / `blind_mode` / 批次维度列；`adjust()` 改为生成调整单 |
| `putaway_rule_set` / `putaway_rule` | 新增 | 10.2 | 策略包头 + 条件组/ fallback 链明细行 |
| `putaway_recommendation` | 新增 | 10.2 | 推荐日志、采纳率 / 驳回率 |
| `product.default_zone_id` | 扩列 | 10.3 | 货品默认推荐储区 |

**始终不改**：`stock` 的列与唯一键、`stock_movement.ref_type/ref_id/qty_delta` 语义、
`/api/stock/reconcile` 不变式（I1）。

---

## 12. API 提案

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST/PUT | `/api/zones` | 储区主数据 CRUD（温区/危化/货主专区/质量状态白名单） |
| GET | `/api/zones/<id>/locations` | 该储区库位与占用 |
| GET/PUT | `/api/storage-policies` | 作用域策略列表 / 编辑 |
| GET | `/api/locations/<id>/policy` | 单库位最终生效策略（含继承来源） |
| GET | `/api/locations/<id>/occupancy` | 库位当前 SKU / 批次 / 货主 / 质量状态 / 指定客户 / 占用率 |
| GET | `/api/stock/batch` | **批次级库存余额**（按批次/客户/质量状态/效期过滤） |
| GET | `/api/stock/batch-reconcile` | 校验 I2 + I3，**一律硬校验**，任何不一致直接 `consistent=false`（无灰度豁免） |
| GET/POST/PUT | `/api/external-doc-types` | 外部单据类别字典 CRUD（code / 名称 / 启用 / 默认目标储区） |
| GET/POST | `/api/inventory-transfers` | 列表 / **① 制单**（`draft` 空单，不含明细） |
| POST | `/api/inventory-transfers/<id>/allocate` | **② 分配库存**：选定待变更的 `stock_batch` 行并**真实占用**——主账 `stock.allocated_qty` 与明细 `stock_batch.allocated_qty` 同步 `+qty`，可用库存减少；写 `qty_delta=0` + `allocated_delta>0` 流水，`stock.qty` 不变 |
| POST | `/api/inventory-transfers/<id>/unallocate` | **②′ 取消分配（v3.2 新增）**：仅 `allocated` 未审核；两层 `allocated_qty` 对称减回 + 负 `allocated_delta` 流水，回 `draft`；已审核 409 |
| POST | `/api/inventory-transfers/<id>/approve` | **③ 审核（唯一过账点 / 生效终态 `approved`）**：`Σ qty_delta = 0` 硬校验 → 净零成对流水 + 明细维度迁移 + **释放分配占用** + 变更文档；**主账 `stock.qty` 零 UPDATE** |
| POST | `/api/inventory-transfers/<id>/close` | **④ 关闭单据**：**仅未分配的空单**（`draft` 且 `COUNT(line)=0`），**不产生任何流水**；已分配单返回 409 并提示先 `unallocate` |
| DELETE | `/api/inventory-transfers/<id>` | **⑤ 删除单据**：仅未分配的 `draft` / 已关闭空单，标记 `cancelled`；已分配 / `approved` 一律 409（只能冲销） |
| POST | `/api/inventory-transfers/<id>/reverse` | 生成冲销单（`reversal_of_id`），同样要求净零 |
| — | 数量字段拦截 | 转移类接口收到 `delta_qty` / `qty_adjust` / `to_qty` 等一律 400 `TRANSFER_QTY_MUST_BE_ZERO`；数量变化只走下面的调整单 |
| GET/POST | `/api/stock-adjustments` | 库存调整单列表 / 手工制单（`source_type=manual`）；**唯一允许改变 `stock.qty` 的手工入口**（7.6） |
| POST | `/api/stock-adjustments/<id>/approve` | 审核过账（`draft→approved`），单事务写 `MOVEMENT_ADJUSTMENT` 流水并回写盘点单状态 |
| GET/POST/PUT | `/api/stocktake-plans` | 盘点方案 CRUD（范围条件组 / 盲盘模式 / 动碰基准 / **手工时间窗 + 交易类型多选** / 自动调整默认关，18.3） |
| GET | `/api/stocktake-plans/<id>/preview` | 试算该方案会生成多少盘点行（含时间窗与交易类型过滤后的动碰集合） |
| POST | `/api/stocktakes/<id>/generate-adjustment` | **由盘点明细页显式按钮触发**（非自动）：按差异生成 `source_type=stocktake_diff` 调整单；原 `adjust()` 直接 `adjust_stock()` 的旁路移除（18.7） |
| GET | `/api/inventory-changes` | 字段级变更文档（按批次/字段/操作人/时间过滤） |
| GET/POST/PUT | `/api/putaway-rule-sets` | 策略包与规则行（条件组 / steps / limits / priority） |
| GET | `/api/putaway-rule-types` | 8 种寻址类型 + 评分规则字典（参数 schema 供前端动态渲染） |
| POST | `/api/asns/<id>/putaway/recommend` | **只读预览**：`line_id + qty` → 命中规则行 / 命中步骤 / Top-N / 原因码 |
| POST | `/api/asns/<id>/putaway` | 扩展：`to_location_id` 可省略（省略取推荐首位）、`recommendation_id`、`override_reason` |
| GET | `/api/putaway-recommendations` | 推荐日志、命中率 / 驳回率 |

**兼容性**：`to_location_id` 保持可选；无策略时沿用现有行为；`adjust_stock()` 不传 `batches`
走老链路；现有 240 例测试与 `/api/stock/reconcile` 不受影响。

---

## 13. 前端与接入点

| 页面 | 新增内容 |
|---|---|
| `web_app/zones.html`（新） | 储区主数据 CRUD、温区/危化/货主专区/质量状态白名单、`flag_whitelist` 与 `external_doc_type_whitelist` |
| `web_app/locations.html` | 「存储策略」区块（三开关、SKU 上限、容量/温区、当前占用可视化） |
| `web_app/batches.html` | 批次档案增加「实物分布」（`batch_location`）、「关联库存明细」（`stock_batch`）、「转移历史」 |
| `web_app/inventory_transfers.html`（新） | 库存转移单列表 + **6 个动作按钮**（制单 / 分配库存 / **取消分配** / 审核 / 关闭 / 删除，按状态显隐）、冲销、字段级变更前后对照；分配后展示「可用库存已减少」提示 |
| `web_app/stock.html` | 增加「批次余额」标签页 + `/batch-reconcile` 核对结果 |
| `web_app/asns.html` | 创建弹窗加 4 个标识勾选 + 外部类别下拉；**收货弹窗每行加「指定客户」（可留空、验收时人工填写）**；收货/上架弹窗加「推荐」按钮 → Top-N + 命中规则 + 拒绝原因 → 一键采纳；人工改位必填原因码 |
| `web_app/dns.html` | 创建弹窗同步加 4 个标识勾选 + 外部类别下拉；列表与详情展示标识徽标（本版只落字段，不参与分配） |
| `web_app/putaway_rules.html`（新） | 策略包切换、**13 维条件组编辑器**（含 5 个新标识维度）、fallback 链拖拽排序、试算面板、外部单据类别字典维护 |
| `web_app/app.html` | **工作台顶栏账号区 + 退出按钮**（v3.2 已确认范围、本轮已实现）：展示当前登录用户名，点击复用 `api.js` 的 `logout()` 清 `localStorage.miniwms_token` 并跳登录页；**不新增 `/api/auth/logout`**、不做服务端吊销 |
| `web_app/js/layout.js` | 侧边栏新增「储区」「库存转移」「上架规则」「盘点方案」「库存调整」入口 |

**代码接入点**

1. `main.init_db()`：新表 DDL + 幂等 ALTER（含 `asn` / `dn` 各 5 列）+ `external_doc_type`
   字典表 + 播种预置储区；注册 `zone_bp` / `putaway_bp` / `transfer_bp`(库存转移) / `doc_type_bp`。
2. `constants.py`：新增 `IT_TYPES` / `IT_STATUS` / **`IT_ACTIONS`（5 个动作权限码）** /
   `STOCK_STATUS` / **`DOC_FLAGS`（4 个标识位）** / `REASON_CODES` / `PUTAWAY_*` 枚举；
   **不设 `DOC_KIND`**；`GOODS_PROPERTIES` 扩为四值；`LOC_TYPES` 加
   `defective/quarantine/virtual/crossdock`。
3. `inventory_service.py`：`adjust_stock(..., batches=...)` 扩展 + `stock_batch` 同步钩子；
   五个库存动作统一调用，路由层禁止直写。
4. `batch_service.py`：销退口径调整（7.5）+ `designated_customer_id` 写入 + 新档案生成逻辑。
5. `putaway_service.py`（新）：上下文构建、条件组匹配、fallback 链（8 种寻址）、硬约束流水线、
   9 条评分规则（R07–R15）候选打分、可解释输出。
6. `inventory_transfer_service.py`（新）：状态机、成对流水、冲销、变更文档。
7. `routes_asn.putaway()`：在 `_check_location()` 之后插入混放校验 → `stock_batch` / `batch_location` 同步 → 推荐日志。
8. `product` 的 ABC 等级由 `stock_movement` 近 30 天出库频次聚合，做成可缓存计算字段。
9. `routes_asn.create_asn()` / `routes_dn.create_dn()`：接受 4 个标识位（0/1 校验）与
   `external_doc_type`（字典校验），列表支持按标识过滤，详情带出；`seed_demo.py` 播种 4 种标识各 1 单。
10. `routes_asn.receive()`：验收时读取明细 `designated_customer_id` 并写入
    `batch_receipt` / `stock_batch`；前端收货弹窗提供该输入（可留空）。

---

## 14. 分阶段实施计划（v3.2 编号定稿，2026-11-05 用户确认「好的」）

**编号已定稿并同步 `DEVELOPMENT.md` / `README.md` / 首页里程碑表**：新里程碑 **M20–M26**（7 个），
原 M20 预警 / M21 报表 / M22 外部对接 / M23 CSV 顺延为 **M27–M30**。
「库存调整单 + 盘点增强」按 18.9 排在 M21 之后，占 **M22**；原 C–F 依次顺延 M23–M26。
**G 出库分配口径未定（第 15 章「到时候沟通」），暂不占编号**——这样恰好仍是 7 个新里程碑，
用户确认的 M20–M26 / M27–M30 两组编号**一字不改**；G 沟通后再插入并顺延后续编号。
每阶段仍按铁律：后端 + unittest 一个提交、前端 + 文档一个提交、README 与代码严格一致、全量测试绿后 push。

| 阶段 | 里程碑 | 交付 | 验收标准 |
|---|---|---|---|
| **A** | **M20 储区与上架作业基础** | 阶段 A 已交付（`7d51963` / `8a3318e`）：`zone` / `product_location` / 仓库维度 / 混放开关 / 简版只读推荐 / 验收·上架独立页。**M20 剩余按 v3.3 收窄**：`asn`/`dn` 各 4 个标识位 + `external_doc_type`（字段、表单、测试），**不做混放/冷链/危险拦截、不建 `storage_policy` 五级继承、不建 `external_doc_type` 字典表** | 5 个字段创建·列表·详情返回正确、缺省值 0 / 空串、非法值 400；启用字段不影响既有收货/上架流程；`/api/stock/reconcile` 仍一致；老测全绿 |
| **B** | **M21 批次级库存明细层** | `stock_batch` + `stock_movement` 扩 9 列 + `adjust_stock(batches=)` + 五动作钩子 + `batch_location` + I2/I3 硬校验 + `init_db()` 内一次性回填 | 主账不变式 I1 保持；I2/I3 通过（**无灰度豁免**）；上架/移库/盘点后批次分布正确；老 240 测全绿；新增 ≈22 测 |
| **H** | **M22 库存调整单 + 盘点增强（v3.1 提出，v3.2 定号）** | `stock_adjustment` 两张表 + `stocktake_plan` 四类盘点（全盘 / 部分 / 盲盘 / 动碰；动碰支持**手工指定时间窗 + 交易类型**）+ 真盲盘分级接口 + 盘点差异**人工点按钮**生成调整单 + 调整单审核过账 + 三个新页面 | 任何数量变化都有一张可追溯调整单；按批号盘点依赖 M21 已具备；盲盘录入接口不返回账存/差异；差异按钮不自动过账；`/api/stock/reconcile` 仍一致；新增 ≈24 测 |
| **C** | **M23 指定客户专属库存 + 销退口径** | `asn_line.extra.designated_customer_id`（**只在明细行、默认空、验收时人工填写**）→ `batch_receipt` → `stock_batch` → 流水；出库校验规则（只落规则，不实现分配）；销退外部批次号校验与告警；质量状态扩四值 | 专属库存与通用库存不合并；验收时可填/可留空；换客户必须走转移单；销退缺外部号有告警；新增 ≈15 测 |
| **D** | **M24 库存转移单** | `inventory_transfer` **两条互斥分支**：有分配＝制单→分配库存（**真实占用可用库存**）→**审核（生效终态）**；未分配＝关闭 / 删除（无任何库存动作）。含 `Σ qty_delta = 0` 硬校验、主账 `stock.qty` 零 UPDATE、净零成对流水 + 冲销 + `inventory_change_log` + 6 个权限码钩子（含「取消分配」） | `approved` 不可改不可删；已分配单须先 `unallocate` 才能关闭 / 删除；接口层直接拒绝 `delta_qty` 等数量字段；分配期间可用库存确实减少、审核后释放；冲销后三份记录齐全；新增 ≈22 测 |
| **E** | **M25 上架推荐规则引擎** | `putaway_rule_set` / `putaway_rule` 两张配置表 + `putaway_service.py` 升级为**规则驱动** + **13 维条件组（含 5 个新标识）** + fallback 链 + 8 种寻址 + 9 条评分（R07–R15）+ `/putaway/recommend` 预览 + **把推荐提示升级为按规则行硬过滤拦截**（承接 v3.3 从 M20 移来的混放/冷链/危险） | 拦截清单完全来自规则行 `limits`，**代码中无任何 hardcoded 红线**；多规则按 `priority` 命中即停；虚拟/直调/外部类别分流、正常 fallback 逐级降级；命中的约束不被权重买通；同分稳定排序；新增 ≈26 测 |
| **F** | **M26 规则配置与推荐交互** | `putaway_rules.html` 条件组编辑器 + `asns.html` 推荐弹窗一键采纳 + 推荐日志与命中率 | 多规则增删改排序、试算面板可用、改位必填原因码、Playwright E2E 覆盖推荐→采纳→上架 |
| **G** | **（暂不占编号）出库分配，待专项沟通** | FEFO 排序 + 客户锁定 + 批次级 `dn_pick` + 跳批审批留痕 | 见第 15 章待沟通清单，口径未定前不实现；沟通后插入并顺延后续编号 |

**编号定稿说明（v3.2）**：H（M22 库存调整单 + 盘点增强）紧随 B（M21）之后，
因为按批号盘点与批次级差异调整硬依赖 `stock_batch`；**不要插在 M20 之前**，否则返工。
用户已确认本编号，故 `DEVELOPMENT.md` / `README.md` / 首页里程碑表同步为 M20–M30。

**硬依赖**：A → B → (H ∥ C) → D → E → F；A / B / D / E / F 顺序不可调换，H 与 C 都只依赖 B，可互换。
没有 `stock_batch`（B）就无从判断「库位里有哪几个批号」，M25 的批次混放规则行与
同批号推荐（E）都会退化成空逻辑；没有库存转移（D）就无法纠正批次数据，推荐结果不可信。
**v3.3 变更**：A 不再承担混放校验，因此 A 与 B 之间只剩「单据标识位供 E 使用」的弱依赖，
M20 剩余可独立收尾，不必等 `stock_batch`。

**建议上线默认值（仅种子数据，不是系统红线）**：系统建议 + 人工确认（不强制自动执行）；
跨货主/批号/产品混放全关；质量状态硬隔离；规则集预置 11 行，初期只启用
`5 / 8 / 10 / 20 / 25 / 50 / 999` 七行核心路径，外部单据类别分流（35 行）等接了 M29 外部对接后再启用。
按 3B，这些行**全部可在 M26 编辑器里改或删**；删掉即不再拦截，代码不做二次兜底。

### 14.1 M25 落地补注（2026-11-10，实现与本文 v3.3 上线建议的差异）

- 阶段 E 已交付（`DEVELOPMENT.md` M25 章）：三表 + 规则引擎 + 13 维条件组 +
  `priority` 命中即停 + fallback 链 + **9 种寻址**（8 预设 + M20 兼容型 `all_active`）
  + 9 条评分（默认不叠加，保持 M20 排序口径）+ 配置 API + 上架过账按 `limits` 拦截。
- **新增硬约束三态**：`limits` 每项除 `enabled`（推荐排除候选 + 过账拦截）外，增加
  `recommend_only`（只排除推荐候选、过账放行）。原因是 M20 的三类混放本就只在推荐阶段
  排除候选、用户可手工绕过推荐；若 M25 上线即全部设为过账硬拦截，既有演示与老单据
  （同一货位放不同货品 / 不同货主）会大面积 400。
- 因此上文「跨货主/批号/产品混放全关」的**上线建议**在 M25 种子里落地为：
  `mix_product` / `mix_owner` = `recommend_only`，`mix_batch` 连推荐阶段也不排除
  （M21 `stock_batch` 明细层支持同库位多批次，M22 按批号盘点与 M23 专属/通用分明细均依赖它）；
  `loc_status` / `storage_class` / `capacity` = `enabled`。M26 编辑器把 `enabled` 打开即达到
  本文建议的全关强度，**无需改代码**；强度始终全部来自配置，代码零 hardcoded 红线。
- 储区编码用库里真实存在的 `ZONE-SHARED` / `ZONE-COLD` / `ZONE-NG`，替代 §10.3 的占位 code，
  避免 fallback 链全空。规则集 11 行、启用 7 行与本文一致。
- `putaway_rule.rule_type` 分 `putaway` / `outbound` 两类，出库分配规则（M35）复用同一张表；
  §15 的边界与待沟通议题仍然有效，M25 **未**接入出库分配。
- 本文所有 v3.3 原文保留用于追溯；实现事实以 `DEVELOPMENT.md` M25 章与
  `docs/PRD_SOURCES.md` §2.9 / §3.9 为准。

---

## 15. 出库分配：本版边界与待沟通议题

用户明确「出库分配这个逻辑要额外处理，到时候沟通」。本版**只留数据地基与边界，不定义规则**。

**已留好的地基**

- `stock_batch` 带 `expiry_date / production_date / internal_batch_no` → FEFO 排序可用；
- `stock_batch.designated_customer_id` → 客户锁定可校验；
- `stock_batch.goods_property / stock_status / allocated_qty` → 可用状态过滤 + 明细级预占；
- `stock_movement` 带批次维度 → 出库流水可追溯；
- 现有 `allocated_qty` + `qty_delta=0` 的 `dn_pick` 流水已是「先锁定再拣货」的雏形。

**待沟通议题（阶段 G 前逐条定）**

1. 出库批次顺序：FEFO（效期优先）还是 FIFO（入库时间优先）？无失效日期时的兜底排序？
2. 专属库存与通用库存的**消耗顺序**（先专属后通用，还是严格按 FEFO 忽略专属）？
3. 一张出库单跨多个批次时，是否允许拆批 / 部分发货 / 替代批次？
4. 跳批（客户指定批次、质量特采）是否走审批流？审批人与留痕字段？
5. 不合格 / 冻结 / 待检库存是否绝对不可分配，还是允许「特采」例外？
6. 拣货是否必须指定批次（强管控）还是可只指定库位、系统事后按 FEFO 回填（弱管控）？
7. `dn_pick` 加 UNIQUE 后，同一行同一库位同一批次多次拣货如何合并？

---

## 16. 待确认口径

### 16.1 已确认（2026-11-05 用户逐条答复，不再讨论）

| 原问题 | 确认结果 | 落点 |
|---|---|---|
| 「虚拟单据」定义 | **不是单据种类，是单据标识位**；`asn` / `dn` 同加虚拟、直调、冷链、危险 4 个标识 + `external_doc_type` 外部单据类别，且这些字段用于上架货位推荐 | 8.4 / 10.2 / 10.3 |
| 库存转移审批流 | 需要权限控制，先做 **5 个动作位**（制单 / 分配库存 / 审核 / 关闭单据 / 删除单据），**详细权限分配后置** | 7.2 |
| 指定客户放哪 | **只放明细行、默认空、属于批次维度、验收时人工填写** | 6.2 |
| 老库回填 | 「你看着办，没事的，目前还在编码阶段」→ **不做灰度兼容，I2/I3 一律硬校验** | 5.4 |

**2026-11-05 第三轮确认（v3.2，用户逐条答复 7 项）**

| 原问题 | 确认结果 | 落点 |
|---|---|---|
| 9 退出登录范围 | **只补工作台顶栏「账号名 + 退出按钮」**，前端清 token 跳登录页；**不做服务端吊销**、不加 `/api/auth/logout`、不引入黑名单 | 13 章 / 本轮已实现 |
| 10 审核后是否还要关闭 | **审核即生效、`approved` 为终态**；「关闭」只留给未分配的空单 | 7.2 状态机终版 |
| 11 分配库存是否占用 `allocated_qty` | **推翻暂定：需要占用库存，可用库存减少** → 主账 + 明细双层占用，审核释放，补「取消分配」动作 | 7.2 / 7.3 / 12 章 |
| 12 里程碑编号 | **好的**：新 M20–M26 / 原四项 M27–M30；盘点增强插 M21 之后占 M22；G 出库分配口径未定暂不占编号 | 14 章 + 三处文档同步 |
| 18-A / 18-B 动碰盘点 | 定义＝只盘自基准以来发生变动的库位×物料；默认 `last_closed`，**另外支持手工指定时间和交易类型** | 18.3 / 18.5 |
| 18-C 盲盘范围 | **仅录入时隐藏账存，复核阶段可见差异**（不做全程隐藏） | 18.6 |
| 18-D 盘点差异 | **额外增加一个按钮**：人工点击生成独立库存调整单再审核过账；不自动过账 | 18.7 / 18.8 |

**2026-11-05 第四轮确认（v3.3，用户逐条答复 4 项；详见文首 v3.3 表）**

| 原问题 | 确认结果 | 落点 |
|---|---|---|
| M20 剩余怎么拆 | **1A**：只补 4 标识位 + `external_doc_type`；混放/冷链/危险拦截整体并入 M25/M26，**不写死** | 9.4 / 10.1 / 14 章 |
| 过渡期是否拦截 | **2B**：完全不拦截，只靠推荐页提示 | 9.4 |
| 硬约束可配边界 | **3B**：全部交给规则决定，不保留系统级 hardcoded 红线 | 9.4 / 10.1 / 10.3 |
| 条件维度首批范围 | **4A**：13 维起步，M26 编辑器预留扩展位 | 10.2 |

> 第 16.2 节此前「冷链/危险硬约束清单」「混放校验挂点」两项开工前疑问，
> 已由本轮 1A / 2B / 3B 一并消解：**M20 不做任何校验，M25 由规则行统一承载**。

### 16.2 仍待确认（按优先级）

| # | 问题 | 本版暂定 | 影响 |
|---|---|---|---|
| 1 | `external_doc_type` 用自由文本还是可配字典？ | **v3.3（4A）分两步**：M20 剩余只做**自由文本字段**（限长、去空格），不建字典表；M26 条件组编辑器落地时再补可配字典（code / 名称 / 启用 / 默认目标储区） | 8.4 / M20 / M26 |
| 2 | `is_direct` 是否意味着完整越库流程（收货后直接转出库、不进存储区），还是仅走直调暂存货位？ | 暂定**仅走 `XDOCK-01` 暂存位**，完整 cross-docking 后置 | 10.3 第 25 行 / M26 |
| 3 | `designated_customer_id` 是否纳入 M19 `batch_key` 关键字段指纹？ | 暂定**不纳入**，只作 `stock_batch` 明细维度；同批货分给两个客户仍是一个批次档案 | 6.2 / M19 |
| 4 | 专属库存与通用库存的消耗顺序 | 暂定先专属后通用 | 6.3 / 15 |
| 5 | 批次属性修正后，原 `batch` 档案是否标记「已被转移」？ | 暂定加 `superseded_by_id` 指针，不删档案 | 7.1 追溯链 |
| 6 | 里程碑是否按第 14 章顺延 M20–M26 / 原 M20–M23 → M27–M30？ | 待确认后再改 `DEVELOPMENT.md` / `README.md` / 首页里程碑表 | Roadmap 权威编号 |
| 7 | 第 3.4 节 6 个现有隐患哪些随本轮一并修？ | 建议只修 #3/#5/#6（属本设计地基），#1/#2/#4 单独提交；**#6 的 `allocated_delta` 已由 v3.2 分配占用口径正式采纳**，#3/#5 仍待定 | 提交粒度 |
| 8 | 5 个库存转移动作何时接 M9 角色矩阵？ | 用户明确「后续再考虑」，本版只留 `@require_permission` 钩子 | 7.2 / M9 |
> **v3.2 收敛**：原问题 9–12 已全部确认并移入 **16.1**；上表 1–8 均为非阻塞议题，
> 不影响 M20 开工，可随后续里程碑一并答复。其中问题 6（里程碑顺延）已由问题 12 的「好的」间接确认。

### 16.3 盘点专项待确认（原 18.10，编号 18-A ~ 18-F 保持不变）

**已确认 4 项（v3.2，结论见 16.1）**：18-A 是（只盘变动过的库位×物料）；
18-B 默认 `last_closed` 且**支持手工指定时间 + 交易类型**；
18-C 仅录入时隐藏、复核可见差异；18-D 人工按钮生成独立调整单再审核过账。

| # | 问题 | 暂定 |
|---|---|---|
| 18-E | 「按货位盘带能」是否为「按货位盘点」的笔误？是否还有别的含义？ | 按货位盘点 |
| 18-F | 部分盘点范围是否需要支持**排除条件**（如排除某储区）？ | 暂不支持，只做 AND 正向筛选 |

**风险与边界**

- 混放提高空间利用率但降低追溯性；医药 / GSP 默认偏保守，合规要求以适用法规为准，
  本文经验做法不能替代合规审查。
- 混放库位需配套「扫库位 + 扫商品条码」两步验证与盲盘；本项目暂无 RF / 扫码硬件，
  **建议先不放开产品混放**（与 v3 默认值一致）。
- 所有权重、SKU 上限、ABC 分档均为公开案例经验值，**无统一行业标准**，需按本仓订单结构重算。
- 并发抢位可选「库位软锁 30 秒」，仅影响推荐可见性、不扣减库存；本项目单进程 demo 可后置。
- 规则数单个策略包 ≤20 条；不引入表达式引擎。

---

## 17. 外部参考（本轮调研来源）

**上架策略 / 规则条件**
- 富勒 WMS 上架策略拆解（策略包头/明细行、筛选条件 AND 组合、限制条件校验顺序、六种预设规则、
  兜底行、命中率）：https://www.shangyexinzhi.com/article/27616615.html 、
  https://www.toutiao.com/article/7524556322495529523/
- 天工 WMS 智能入库上架策略（按单据类型/产品状态/货主限定生效范围、ABC、最短路径）：
  https://www.tgwms.cn/company/848.html
- 语雀上架策略配置（约束条件留空=通配、推荐规则三选一）：https://www.yuque.com/pysoft/yf25x0/xddr9di323h02pmx
- 大数跨境 WMS 全景（质检分支、不合格/特采进入虚拟库存区、容量预占）：
  https://www.10100.com/article/23254851

**批次级库存 / 库存转移 / 留痕**
- 领星 WMS 批次库存（批次颗粒度 = 入库单号+SKU+客户+上架时间+生产/失效日期+库位+库存属性；
  移库/调整可指定批次，不指定按 FIFO）：https://www.xlwms.com/help/12d0/d5f9/caff/ad3d
- 顶妙 WMS 批次库存「调整库存 / 修改仓位」并全量生成库存变动明细：
  https://www.topwms.com/warehouse_help_center/article_10026.html
- 库存台账与事件建模、批次粒度取舍：https://www.eshutong.com/archives/583018.html 、
  https://www.eshutong.com/archives/583666.html
- 账本模式（流水 append-only、余额是投影、纠错用冲销）：
  https://blog.csdn.net/a15242316443/article/details/165302098 、
  https://tsight.io/articles/17852752 、 http://www.eshutong.com/archives/212359.html
- 多租户 ERP 仓储表设计（明细层+汇总层、库存流水字段、批次动态属性）：
  https://blog.csdn.net/weixin_28717431/article/details/165369011
- WMS 数据库表设计（库存事务表字段清单）：https://juejin.cn/post/7530486096297672730
- 库存调整四类（批号调整 / 质量状态调整 / 报损报溢 / 销毁）与审批留痕：
  https://kknews.cc/tech/ae4a6e6.amp
- SAP 特殊库存与「专属库存=虚拟专属子库存」：https://smtpwww.clicksun.com.cn/bbs.asp?id=40295
- SAP EWM 批次主数据 / FEFO / 批次状态 / 库存类型分区：
  http://qinlox.net/en/insights/batch-management-with-sap-ewm 、 https://sap.gugulii.com/docs/s4-ewm/ewm-a08-01
- WMS/ERP 数据边界与对账：https://www.ittx.com.cn/article/wms-erp-finance-integration-steps.html

**客户专属库存**
- Dynamics 365 `Prevent multiple owners`（owner 作为上架分组维度、空 owner 视为独立 owner、
  盘点不执行约束、跨货主转移需经隔离暂存区）：
  https://learn.microsoft.com/en-in/dynamics365/supply-chain/warehousing/prevent-multiple-owners-locations
- Dynamics 365 batch disposition codes（按销售/调拨/生产分别封锁预留、拣货、发运）：
  https://learn.microsoft.com/en-ie/dynamics365/supply-chain/inventory/batch-disposition-codes
- SAP EWM 客户库存 stock usage B（所有权与存放位置分离）：
  https://www.ewmwiki.com/index.php/home/41-customer-stock-in-ewm-usage-b?format=amp
- Odoo owned stock / 寄售专属库位与 ownership 校验：
  https://www.erpartists.com/de/blog/manufacturing-3/stock-on-consignment-a-complete-odoo-erp-guide-163 、
  https://silverdaletech.odoo.com/blog/silverdale-apps-13/customer-owned-product-reservation-4085
- 3PL 多货主按货主差异化批次规则：https://www.ittx.com.cn/article/3pl-warehouse-inventory-management-2026.html

**出库分配（阶段 G 预留）**
- FIFO/FEFO 排序兜底、先锁定再拣货、跳批审批留痕：https://wap.kuguanyi.com/forum.php?mod=viewthread&tid=57896
- 规则引擎冲突消解（为何本项目不引入）：https://www.nected.ai/blog/what-happens-when-rules-conflict-rule-engine
- 表驱动策略规则表与执行器：https://blog.csdn.net/weixin_29288313/article/details/159029369

> 参考清单结束。第 18 章是 v3.1 追加的盘点增强设计，为保持编号连续放在参考之后。

---

## 18. 盘点增强：可配置盘点方案（v3.1 新增）

### 18.1 现状与缺口（已逐行核对 `routes_stocktake.py`）

| 环节 | 现有实现 | 缺口 |
|---|---|---|
| `create_stocktake()` `:50-82` | 只收 `code / owner_id / note` | 无盘点方案、无范围、无盘点类型 |
| `freeze()` `:132-165` | `SELECT owner_id, product_id, location_id, qty FROM stock WHERE owner_id=? AND qty != 0` → **永远全盘** | 无法按库位 / 产品分类 / 批号缩小范围；无动碰 |
| `count()` `:168-202` | 盲盘录入 `counted_qty` | 但 `_dict_line()` `:40-47` 返回 `book_qty` 与 `diff`，`get_stocktake()` `/stocktakes.html` 表格直接展示账存 → **盲盘其实不盲** |
| `adjust()` `:205-238` | 直接 `adjust_stock()` 写 `adjustment` 流水 | 按 7.6 节口径，应改为**按差异生成库存调整单**，审核后过账 |
| `close()` `:241-255` | 仅改状态 | 与调整单过账无联动 |
| `stocktake_line` DDL | `UNIQUE(stocktake_id, product_id, location_id)` | 无批次维度，无法按批号盘点 |
| 状态机 | `open → frozen → adjusted → closed` | 无「方案」「复盘」「调整单关联」 |

**结论**：现有 M18 是「按货主全量冻结 + 明盘式录入 + 直接改账」的最小实现，
用户要的四类盘点都需要扩展，且**按批号盘点硬依赖 M21 的 `stock_batch`**。

### 18.2 四类盘点的术语对齐

| 术语 | 定义 | 实现要点 | 依赖 |
|---|---|---|---|
| **全盘** | 该货主（或全仓）所有有货的「库位×物料」 | 现有 `freeze()` 逻辑，保留为默认方案 | 无 |
| **部分盘点** | 按范围筛选：库位 / 储区 / 库位组 / 产品分类 / 产品 / **批号** | 范围条件组 → 动态 `WHERE`；见 18.4 | 按批号需 `stock_batch`（M21） |
| **盲盘** | 录入人看不到账存与差异 | API 分级返回 + 前端按模式隐藏列；见 18.6 | 无 |
| **动碰盘点** | 只盘「自基准时间以来发生过库存变动」的库位×物料 | 从 `stock_movement` 反查动碰集合；见 18.5 | `stock_movement`（已有） |
| 明盘（对照） | 录入时可见账存、实时看差异 | `blind_mode=none`，现有行为 | 无 |

> 用户原话「按货位盘带能」按**按货位盘点**理解；若「盘带能」是另一个术语请指出（见待确认）。

### 18.3 配置化核心：`stocktake_plan` 盘点方案表

```sql
CREATE TABLE stocktake_plan (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  code          TEXT NOT NULL UNIQUE,       -- PLAN-FULL / PLAN-BY-ZONE / PLAN-DYNAMIC
  name          TEXT NOT NULL,
  scope_type    TEXT NOT NULL DEFAULT 'full',  -- full | partial | dynamic
  scope         TEXT NOT NULL DEFAULT '{}',    -- JSON 范围条件组（18.4）
  blind_mode    TEXT NOT NULL DEFAULT 'hide_book', -- none | hide_book | hide_diff
  dynamic_baseline TEXT NOT NULL DEFAULT 'last_closed', -- last_closed | since_days | explicit | manual_window
  lookback_days INTEGER NOT NULL DEFAULT 7,   -- since_days 时用
  movement_from TEXT NOT NULL DEFAULT '',     -- v3.2 手工时间窗起（用户「手工可以指定时间」）
  movement_to   TEXT NOT NULL DEFAULT '',     -- 手工时间窗止，空 = 至今
  movement_types TEXT NOT NULL DEFAULT '[]', -- v3.2 JSON 数组：交易类型 ref_type 白名单，空 = 全部
  auto_adjust   INTEGER NOT NULL DEFAULT 0,   -- v3.2 默认关：差异须人工点按钮生成调整单
  include_zero  INTEGER NOT NULL DEFAULT 0,   -- 是否盘 qty=0 的零行
  enabled       INTEGER NOT NULL DEFAULT 1,
  note          TEXT NOT NULL DEFAULT ''
);
ALTER TABLE stocktake ADD COLUMN plan_id INTEGER REFERENCES stocktake_plan(id);
ALTER TABLE stocktake ADD COLUMN scope_snapshot TEXT NOT NULL DEFAULT '{}'; -- 冻结时落盘，防方案被改
```

**两条口径**

1. **方案只是模板**：创建盘点单时选 `plan_id` 带出配置，也允许直接传 `scope` / `blind_mode`
   覆盖（一次性盘点）；`freeze()` 时把实际生效的范围**快照**写进 `stocktake.scope_snapshot`，
   之后改方案不影响历史单据（与 M19「改配置不重算历史指纹」同一原则）。
2. **配置与执行分离**：`stocktake_plan` 是「怎么选行、怎么盘」，`stocktake` 是「这一次的实例」，
   避免把范围条件散落在单据字段里。
3. **动碰的两个手工旋钮（v3.2 用户确认）**：`movement_from/to` 允许直接指定时间窗，
   `movement_types` 允许只挑特定交易类型（如「只看发货 + 移库」）。两者都是方案级默认值，
   创建盘点单时可一次性覆盖；`freeze()` 时连同 `scope` 一起写进 `scope_snapshot`，
   之后改方案不影响历史单据。`movement_types` 取值来自 `constants.py` 的 `MOVEMENT_*`，
   空数组＝不限制；时间窗非空时优先于 `dynamic_baseline`，为空才回落到 `last_closed`。

### 18.4 部分盘点的范围条件组（维度间 AND）

```json
{
  "owner_id": 3,
  "warehouse_id": 1,
  "zone_id": [12, 13],
  "location_group": ["A"],
  "location_ids": [101, 102],
  "loc_type": ["storage", "picking"],
  "product_ids": [],
  "category_l1": "医疗器械",
  "category_l2": null,
  "main_category": null,
  "product_type": null,
  "batch_no": ["LOT-2026-A"],
  "batch_key": null,
  "goods_property": "合格",
  "has_stock": 1
}
```

`null` / 空数组 = 不限；数组走 `IN`，标量走 `=`，维度间 AND。生成器把条件编译成
`SELECT ... FROM stock s JOIN product p ... JOIN location l ... [JOIN stock_batch sb ...] WHERE ...`，
只有出现批次维度时才 `JOIN stock_batch`，避免全盘方案多一次联表。

**去重口径**：`stocktake_line` 唯一键需从 `(stocktake_id, product_id, location_id)` 扩到
`(stocktake_id, product_id, location_id, batch_no)`，否则同一库位多个批次会被合并成一行。
这也意味着**按批号盘点必须在 M21 之后做**；在 M21 之前，方案里带批次维度的行应给出
`BATCH_DIMENSION_NOT_READY` 提示而不是静默忽略。

### 18.5 动碰盘点算法

```
① 定基准 baseline（默认 last_closed；v3.2 支持手工时间窗）
   - last_closed   : 同货主 + 同范围上一次 status=closed 的盘点单 closed_at（默认）
   - since_days    : now - plan.lookback_days
   - explicit      : 创建盘点单时手工传 baseline_at
   - manual_window : plan.movement_from ~ plan.movement_to（用户「手工可以指定时间」）
② 取动碰集合（发生过库存变动的维度组合）
   SELECT DISTINCT owner_id, product_id, location_id
   FROM stock_movement
   WHERE created_at >= :from AND created_at <= :to   -- 时间窗为空时回落 ① 的 baseline
     AND owner_id = ?
     [AND ref_type IN (:movement_types)]  -- 用户「和交易类型」：收货/上架/发货/移库/调整/盘点差异多选
     [AND qty_delta != 0]                -- 排除 dn_pick 这类 qty_delta=0 的纯分配流水
③ 与当前有货库存 JOIN，只保留 qty != 0（或按 plan.include_zero 保留零行）
④ 生成盘点行，book_qty 取冻结时点余额
```

M21 给 `stock_movement` 加了批次维度后，第 ② 步的 `DISTINCT` 可以扩到
`(owner, product, location, batch_no)`，动碰盘点自然升级为**按批次的动碰盘点**，无需改算法骨架。

**已确认（v3.2）**：默认 `last_closed`（保证两次盘点之间不漏盘），
并**必须支持手工指定时间和交易类型**（用户「手工可以指定时间 和交易类型」）。
`since_days` 保留作高频循环盘点的快捷方式。交易类型多选来自 `MOVEMENT_*` 枚举，
前端在盘点方案页提供复选框 + 时间窗控件，`freeze()` 时把实际生效条件写进 `scope_snapshot`。

### 18.6 真盲盘：API 分级返回

| 接口 | 视角 | 返回字段 |
|---|---|---|
| `GET /api/stocktakes/<id>` | 管理 / 复核 | `book_qty / counted_qty / diff` 全返回 |
| `GET /api/stocktakes/<id>/count-sheet` | **作业录入**（`blind_mode=hide_book`） | 只返回 `line_id / sku / product_name / location_code / batch_no`，**不返回账存、已录数、差异** |
| `POST /api/stocktakes/<id>/count` | 作业录入 | 只回「本次已录行数」，不回差异 |
| `GET /api/stocktakes/<id>/diff-preview` | 复核（差异生成前） | 才返回 `book_qty / counted_qty / diff` |

前端 `stocktakes.html` 按 `blind_mode` 切换列：`hide_book` 时明细表不渲染「账存」「差异」列，
输入控件也不带 `placeholder=账存`。**权限上录入人与复核人应分开**（M9 权限码
`stocktake:count` / `stocktake:review`），但按用户口径角色矩阵后置，本版只留钩子。

**已确认（v3.2）**：用户选「**仅录入时隐藏账存、复核阶段可见差异**」。
因此 `hide_book` 只作用于 `count-sheet` / `count` 两个录入接口与录入页；
`GET /api/stocktakes/<id>`（复核）与 `diff-preview` 照常返回 `book_qty / counted_qty / diff`，
**不做全程隐藏**——复核人必须看得见差异才能决定是否生成调整单。

### 18.7 状态机改造（与 7.6 库存调整单联动）

```
open ──freeze(按 plan 生成行)──> frozen ──count(盲盘录入)──> frozen
   ──[人工按钮] 生成库存调整单──> adjusted ──调整单审核过账──> closed
```

- `freeze()` 改为按 `plan.scope` + 动碰条件生成行（不再是全盘），把生效范围与时间窗/交易类型
  一起写入 `scope_snapshot`；
- `count()` 按 `blind_mode` 走 18.6 的分级接口；
- **盘点明细页新增显式按钮「生成库存调整单」（v3.2 用户「额外增加一个按钮」）**：
  人工点击后才按每行 `diff` 生成一张 `stock_adjustment`（`source_type=stocktake_diff`、
  `source_id=盘点单 id`），盘点单状态 → `adjusted`；**不自动触发**（`auto_adjust` 默认 0）。
  原 `adjust()` 直接 `adjust_stock()` 写流水的自动行为**移除**，避免留下
  「没有调整单却改了数量」的旁路；
- 差异全为 0 时不需要调整单，直接 `closed`；
- 调整单被驳回时盘点单保持 `adjusted`，可重新生成。

这样「库存数量只能做库存调整，或者盘点单根据差异生成库存调整」被严格落到代码结构上。

### 18.8 新增页面与权限

- `web_app/stocktake_plans.html`（新）：方案 CRUD，范围条件组编辑（维度多选）、盲盘模式、
  **动碰时间窗（起/止）+ 交易类型多选**、动碰基准；提供「试算行数」按钮预览会生成多少盘点行。
  `auto_adjust` 开关默认关（差异必须人工点按钮）。
- `web_app/stocktakes.html`：创建弹窗加「盘点方案」下拉 + 范围摘要 + 预估行数；
  明细区按 `blind_mode` 切列；**新增显式按钮「生成库存调整单」**（复核视角可见差异后才启用）
  与「查看调整单」入口。
- `web_app/stock_adjustments.html`（新）：库存调整单列表 / 明细 / 审核过账 / 冲销。
- `web_app/js/layout.js`：「出入库作业」组加「盘点方案」「库存调整」入口。
- 权限码 `stocktake:plan` / `stocktake:count` / `stocktake:review` / `adjustment:approve`，
  角色矩阵后置。

### 18.9 硬依赖与实施顺序

| 能力 | 依赖 | 最早可落地阶段 |
|---|---|---|
| 全盘 / 部分盘点（库位·储区·分类·产品）、真盲盘、方案配置 | 无（用现有 `stock` + `location` + `product`） | 可与 M20 并行或紧随 |
| 差异生成库存调整单 | 7.6 `stock_adjustment` | 与「库存调整单」同一里程碑 |
| 动碰盘点 | `stock_movement`（已有），基准取 `stocktake.closed_at` | 同上 |
| **按批号 / batch_key 盘点** | **M21 `stock_batch` + `stocktake_line` 扩批次列** | **必须在 M21 之后** |
| 按批次动碰盘点 | M21 给 `stock_movement` 加的批次维度列 | M21 之后 |

**编号已定稿（v3.2 用户「好的」）**：「盘点增强 + 库存调整单」＝ **M22**，紧随 M21 之后，
**不要插在 M20 之前**，否则按批号盘点和批次级差异调整都要返工。见第 14 章编号表。

### 18.10 本章待确认问题

6 个问题统一汇总在 **16.3 节**（编号 18-A ~ 18-F）。**v3.2 已确认 18-A / 18-B / 18-C / 18-D**，
结论已分别落到 18.3 / 18.5 / 18.6 / 18.7；**仅剩 18-E、18-F 两个非阻塞问题**待答复，
不影响 M22 开工。
