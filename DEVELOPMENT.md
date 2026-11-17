# MiniWMS 开发记录

每个里程碑记录：目标、改动清单、可验证结果。里程碑保持单目标内聚，按开发顺序演进。

## M1 物料主数据（2026-10-30）

**目标**：建立项目骨架与物料（商品）主数据完整 CRUD，作为后续收货/上架/拣货/盘点的参照数据。

**改动清单**：
- `main.py`：Flask 入口、`get_db()` 请求级连接、`init_db()` 建 `product` 表（SKU 唯一索引、分类/状态索引）、Blueprint 聚合
- `routes_product.py`：`/api/products` 增删改查 + 分页 + 关键词/分类/状态过滤 + SKU 去重 + 价格非负校验 + 库存非 0 禁删保护
- `tests/test_products.py`：12 个用例（创建校验/重复 SKU/分页搜索过滤/部分更新/删除保护）
- `README.md` / `requirements.txt`（仅 Flask）/ `.gitignore`

**可验证结果**：
- `python -m unittest discover -s tests` 全部通过
- SKU 重复创建返回 409；负价格返回 400；库存非 0 删除返回 409
- 库存字段建档默认 0，M1 不提供直接改库存入口（保证库存只能由单据驱动）

## 七天交付规划（2026-10-30 制定）

节奏：每天 1-2 个真实提交（后端+测试一个提交、页面一个提交），目标 7 天内同时满足
平台门槛（文件≥100 / commit≥30 / milestone≥6）与「功能能当真产品用」。前后端同步推进，
每个后端里程碑交付后紧跟对应 Web demo 页面，本地 `python main.py` 即可演示。

| 天 | 里程碑 | 内容 |
|---|---|---|
| D1 (10/30) | M3 | 库位主数据(后端+10测) + Web demo 骨架(首页/货主/物料/库位 4 页) |
| D2 (10/31) | M4 | ~~收货单+上架~~ 实际 11/03 完成（自动化未触发，手动补进度） |
| D3 (11/1) | M5 | ~~出库单+拣货+发货(超卖拦截) + 出库 demo 页~~ 实际 11/03 完成（M4 顺延后按序推进） |
| D4 (11/2) | M6 | ~~库存账查询+全部动作流水(余额=流水累加可核对) + 库存查询页~~ 实际 11/03 完成（顺延） |
| D5 (11/3) | M7 | 客户主数据 + 出库单关联客户（补「出库需要客户资料」遗漏维度） |
| D6 (11/4) | M8/M9 | 移库(原子一减一增) + 盘点(冻结/盲盘/差异调整) + 对应页面 |
| D7 (11/5) | M10/M11 | 补货预警 + 按货主报表统计 + 首页仪表盘 |
| D8 (11/6) | M12 | CSV 导入导出 + 简易鉴权(X-API-Key) + API 文档 + 全量回归收尾 |

> 注：D2/D3/D4 的 M4/M5/M6 实际在 11/03 完成（自动化未触发顺延）；D5 用户指出「出库需要客户资料」此前缺失，故插入 M7 客户主数据，原 M7 移库/M8 盘点顺延为 M8/M9。D6 用户要求补齐电商化基础资料字段并补充演示数据，故 M8 定为「基础资料字段增强」（新增 `tools/seed_demo.py` 一键演示数据播种）；原 M8 移库/M9 盘点顺延为 M9/M10，预警/报表/CSV 收尾依次顺延为 M11/M12/M13。另 11/03 用户要求「建立角色用户并供后续功能联动」，故插入 **M9 用户与角色管理**（RBAC 基础 + 登录），原移库及之后顺延为 M10–M15。

## 规划（Roadmap）

- M4 收货单（ASN 收货 → 上架 putaway：写 stock(货主×物料×库位) + 入库流水，库存增加）✅
- M5 出库单（拣货 pick → 发货：按库位扣减库存，超卖拦截）✅
- M6 库存账（按货主+库位的库存余额 + 全部动作流水，余额 = 流水累加可核对）✅
- M7 客户主数据（客户归属货主，出库单关联客户，多货主隔离）✅
- M8 基础资料字段增强（电商字段 + 一键演示数据播种）✅
- M9 用户与角色管理（登录/权限基础，供后续功能联动）✅
- M10 物料主数据深化（电商/医药级字段 + 多条码 + 多层级包装）✅
- M11 货主主数据深化（省市区 + 证照子表 + 经营范围）✅
- M12 客户主数据深化（省市区 + 详细地址 + 经营范围）✅
- M13 库位主数据深化（库位组 + 长宽高 + 体积库容）✅
- M14 承运商模块（新增子系统，供发快递/零担/整车）✅
- M15 主数据体验增强（2026-11-04 用户需求）：物料体积手填/自动换算 · 条码与包装可编辑+包装等级(1-6) · 承运商演示数据播种 · 货主合作起止时间+日期选择器
- M16 供应链与单据主数据增强（2026-11-04）：供应商主数据(归属货主/多货主隔离) + 多仓库主数据(全系统共享) + ASN/DN 自动单号(英文开头+日期流水) + ASN/DN 单据字段增强(单据类型/外部单号/订单来源/来源仓库/供应商快照/承运商/自定义字段/订单总数量·品类数汇总) ✅
- M17 移库（库位间转移：同数量一减一增两条流水，原子事务）✅
- M18 盘点（冻结 → 盲盘 → 差异生成调整流水 → 解冻）✅
- M19 批次与验收入库追溯（批次字段配置 + 批次档案 + 每次入库唯一批次流水）✅
- M20 储区与上架作业基础（**阶段 A 已交付 2026-11-05**：储区主数据 / 仓库维度 / 混放字段 / 推荐储区 / 固定货位 / 简版上架推荐 / 验收·上架作业页；**阶段 B 已交付 2026-11-05**：asn·dn 各 4 个单据标识 + external_doc_type 的字段 / 表单 / 17 例单测）✅ **M20 收尾完成**；混放·冷链·危险拦截改为配置化规则并入 M25/M26，过渡期不拦截 **（过渡期为 v3 历史口径，已随 M25 于 2026-11-10 交付结束，见文末 M25 章）**
- M21 批次级库存明细层（stock_batch + stock_movement 扩维度列 + I2/I3 硬校验 + 一次性回填）✅ **2026-11-06 已交付**：五动作同步钩子 + batch_location + I2/I3 端点 + 老库回填 + 批次库存页，详见文末同名章节；331 测全绿
- M22 库存调整单 + 盘点增强（stock_adjustment 唯一手工数量入口 / stocktake_plan 全盘·部分·盲盘·动碰；动碰支持手工时间窗 + 交易类型 / 差异由人工按钮生成调整单）✅ **2026-11-06 已交付**：3 张新表 + 真盲盘分级接口 + 按批号盘点 + 审核过账 / 取消 / 冲销 + 3 个页面，详见文末同名章节；356 测全绿
- M23 指定客户专属库存 + 销退口径（designated_customer_id 只在明细行 / 出库校验规则 / 销退外部批次号）✅ **2026-11-09 已交付**：指定客户贯穿单据→批次→明细→流水、销退外部批次号必填且允许 `/` 显式占位、出库校验只落纯规则、质量状态两字段独立；375 测全绿，详见文末同名章节
- M24 库存转移单（只改批次信息、数量净零 / 制单→分配库存（真实占用可用库存）→审核生效终态 / 未分配可关闭删除 / 取消分配 / 冲销留痕）✅ **2026-11-09 已交付**：3 张表 + 双层 `allocated_qty` 真实占用 + 净零成对流水 + 字段级变更文档 + 7 类统一搬迁 + 冲销往返，415 测全绿、E2E 35/35，详见文末同名章节
- M25 上架推荐规则引擎（条件组 13 维起步、**M32 起扩为 14 维（+产品类型）** + 多条规则 priority + fallback 链 + **配置化硬约束** + 可解释 Top-N）✅ **2026-11-10 已交付**：3 张表 + 规则引擎 + 11 行种子（启用 7 行）+ 规则配置 API + 推荐分发 + 上架过账按规则行 limits 拦截；**承接 M20 剩余移来的混放 / 冷链 / 危险拦截**。447 测全绿、E2E 34/34，详见文末同名章节
- M26 规则配置与推荐交互（条件组编辑器 + fallback 链拖拽 + 推荐弹窗一键采纳 + 推荐日志；13 维起步、预留扩展位，**M32 已启用首个扩展位：产品类型**）✅ **2026-11-13 已交付**：`putaway_rules.html` 编辑器页 + 采纳/驳回端点 + 拖拽排序 + 外部单据类别字典 + 命中率统计 + 可复跑 E2E，460 测全绿、E2E 23/23，详见文末同名章节
- M27 补货预警（min_stock 对比 + 拣货位缺料提示）— **降级暂缓**，编号保留不回收
- M28 报表统计（按货主的收发汇总、周转率、库位利用率）— 归入领域蓝图第 4 层「经营报表」
- M29 外部对接 API（X-API-Key 鉴权 + 出入库单据外部写入端点 + 数据回传 webhook + 文档）— **降级暂缓**，编号保留不回收
- M30 CSV 导入导出 — **降级暂缓**，编号保留不回收

### M31+ 新域（2026-11-05 用户整体规划，保留 M20–M30 不动）

- M31 司机与车辆主数据（承运商关联 / 证照效期 / 车牌车型载重）
- M32 产品类型字典（独立主数据 + 货品改引用 + 老数据回填）
- M33 序列号台账（序列号档案 + 收发绑定流水，`is_serial` 升级为台账驱动）
- M34 货位组主数据（独立表替代文本字段 + 拣货顺序 / 容积）
- M35 出库波次分配 + FEFO（**原「暂不占编号」的出库分配，本轮确认继续安排并落此号**）
- M36 出库复核（复核状态机 + 差异处理 + 留痕）
- M37 养护管理（温湿度 / 养护项目 / 周期 / 异常，GSP）
- M38 拆单合单（用户「之后可以考虑」，可选）
- M39 GSP 合规报表（批次追溯 / 效期 / 养护 / 验收复核留痕）
- M40 工作量统计（按人 / 按节点 / 按时间段）✅ **2026-11-16 已交付**：纯只读聚合 8 类现有留痕，16 类作业节点 / 7 端点 / 作业页，18 单测 + E2E 36/36，详见文末同名章节
- M41 打印模板配置 + PDF 预览与内容编辑（**方案 v2 已定稿**：零安装 / Chrome headless / 预览页可临时编辑单据内容 / 字段映射 + 可视化拖拽排版）✅ **2026-11-16 已交付**：6 节点默认模板 / 8 端点 / L1+L2 编辑器 / PDF 预览临时编辑不回写，18 单测 + E2E 31/31，详见文末同名章节
- M42 打印任务队列 + 操作节点自动触发 — **后置**，等全部做好后用户额外补充，暂不排期

> **领域蓝图（2026-11-05 用户提出「自上而下、后续细节再优化」，同日 v3 定稿）**：完整 5 层框架、
> 现状对照、实施顺序、M31+ 依赖与打印方案见 `docs/DOMAIN_BLUEPRINT.md`（v3）。
> 已确认：① **M20–M30 编号保留不动，新域从 M31 起追加**；
> ② 实施顺序＝**先收尾 M20 剩余 → 依次跑完 M21 → M22 → M23 → M24 → M25 → M26 → 再进 M31+ 第 1 层**
> （M22/M24/M35 硬依赖 M21 `stock_batch`，此顺序依赖链自洽）；
> ③ M25/M26 上架推荐规则引擎与 FEFO 出库分配继续安排，M27/M29/M30 降级暂缓但不删编号；
> ④ 打印采用**演示版零安装路线**：自动打印＝自动弹出已生成 PDF 的打印预览、人工点确认；
> PDF 用系统 Chrome headless 零新增依赖生成；预览页可临时微调单据内容（不回写业务单据）；
> 模板编辑器占位符+字段映射与可视化拖拽排版都做；**自动触发节点后置**。
> ⑤ **硬约束全部配置化（v3 第四轮答复 1A/2B/3B/4A）**：M20 剩余收窄为只补 4 标识位 +
> `external_doc_type`；混放 / 冷链 / 危险拦截整体并入 M25/M26 的**多条上架规则（priority 优先级 +
> 单号/产品信息条件组）**，不写死、不保留系统级 hardcoded 红线；过渡期 `putaway()` **完全不拦截**，
> 只靠推荐页提示；条件维度 **13 维起步**，M26 编辑器预留扩展位（**M32 已启用首个扩展位：产品类型，现为 14 维**）。
> **（v4 补注 2026-11-10：以上 ⑤ 为 v3 历史答复原文；「过渡期完全不拦截」已随 M25 交付结束，
> 当前 `putaway()` 按命中规则行 `limits` 拦截，见文末 M25 章。）**
> 口径细则见 `docs/PUTAWAY_RECOMMENDATION.md`（v3.3）§9.4 / §10.1 / §10.2 与
> `docs/DOMAIN_BLUEPRINT.md` 第 1.2 / 7.1 节。
> `DEVELOPMENT.md` Roadmap 仍是编号权威来源，`docs/DOMAIN_BLUEPRINT.md` 是框架级来源。
> 原「开工前待确认」两项（混放校验挂点、冷链/危险清单）**已由第四轮答复消解**，不再阻塞阶段 0。
>
> **架构增强（非里程碑，不占编号）**：2026-11-05 用户确认「直接做完整版」，已落地
> 「仓库 / 货主双维数据隔离」（中央库 / 业务库分层、MANDT 式 owner 注入、账号可见仓与可见货主授权、
> A 作业视角切仓、基础资料货主维度预留），详见文末同名章节。**Roadmap 编号与实施顺序不变**，
> 下一里程碑仍为 **M21 批次级库存明细层**。
> 同日第二次优化「货主 × 仓库业务关系格子」亦为**非里程碑架构增强**（用户确认继续方案 A、
> PostgreSQL 后续再考虑），详见文末同名章节与 `docs/DOMAIN_BLUEPRINT.md` §9；编号口径不变。

> 编号变更（2026-11-05，用户确认「好的」）：按 `docs/PUTAWAY_RECOMMENDATION.md` 第 14 章
> 插入 7 个新里程碑 **M20–M26**，原 M20 预警 / M21 报表 / M22 外部对接 / M23 CSV
> 顺延为 **M27–M30**。「库存调整单 + 盘点增强」硬依赖 M21 的 `stock_batch`，故排 M22；
> 出库分配口径未定（第 15 章「到时候沟通」），暂不占编号，这样两组编号无需再改。
> 此前 2026-11-04 插入 M19 批次追溯时原四项曾顺延为 M20–M23，本次为第二次顺延；
> 更早插入 M7 / M9 / M10–M14 / M16 时均按同一口径。**Roadmap 为编号权威来源**，
> README 规划段与首页里程碑表同步。
> **后续变更**：同日用户给出自上而下 5 层整体规划，确认出库分配 FEFO「继续安排」并纳入 **M35**；
> M27/M29/M30 降级暂缓（编号不回收）；新域从 M31 起追加；第二轮答复进一步定稿
> **实施顺序＝M20 剩余 → M21 → M22 → M23 → M24 → M25 → M26 → M31+**，
> 打印采用演示版零安装路线、自动触发节点（M42）后置。见上文 M31+ 段落与
> `docs/DOMAIN_BLUEPRINT.md`（v2）。本段保留作为编号演进的历史记录。

## 外部对接设计（2026-11-03 用户提出，2026-11-05 降级暂缓）

**背景**：出入库单据（ASN/DN）后续要对外开放，供外部系统（如电商/OMS/ERP）写入数据，
并需将单据状态变化**回传**给外部系统。核心诉求：外部写入必须做**身份验证**，不能裸接口直写。

**设计要点（零依赖、复用现有服务层）**：

1. **鉴权（X-API-Key，密钥绑定货主）**
   - 新增 `api_key` 表：`key_prefix`(前8位展示/检索)、`key_hash`(sha256，不存明文)、
     `owner_id`(绑定货主，多货主隔离)、`label`、`status`(active/disabled)、`created_at`、`last_used_at`。
   - 密钥服务端随机生成，仅创建时返回一次明文。
   - 中间件 `require_api_key`：校验 `X-API-Key` 头 → 查表比对 hash + status → 把
     `owner_id` 注入 `g.api_owner_id`，后续写入**强制以该 owner 为准**，杜绝越权写别家货主。

2. **外部写入端点（独立 blueprint，与内部 UI 路由分离、可审计）**
   - 新建 `routes_external.py`，前缀 `/api/ext`，全部路由先过 `require_api_key`。
   - `POST /api/ext/asns`、`POST /api/ext/dns`：创建收货/出库单，业务校验复用现有逻辑，
     但 `owner_id` 取自密钥绑定值（请求体里的 owner_id 被忽略或强制一致）；行内物料/客户必须属该货主。
   - `GET /api/ext/asns/<id>`、`GET /api/ext/dns/<id>`：外部只读自己 owner 的单据状态。
   - MVP 范围：外部推「单」+ 查状态；收货/上架/拣货/发货由内部 WMS 作业，作业完成即回传（见下）。

3. **数据回传（webhook 回调）**
   - `callback_config` 表：`owner_id`、`event`(如 `asn.closed`/`dn.shipped`)、`url`、`secret`、`status`。
   - `callback_log` 表：`callback_config_id`、`ref_type`、`ref_id`、`payload(json)`、`status`(pending/sent/failed)、
     `attempts`、`last_error`、`next_retry_at`、`sent_at`，用于留痕与重试。
   - 触发点：ASN 状态变 `closed`、DN 状态变 `shipped`（可扩展 putaway/receiving）时调用
     `notify_callbacks(ref_type, ref_id, event, payload)`。
   - 发送：零依赖用标准库 `urllib` POST 到配置 url，带 `X-Callback-Signature`(HMAC-SHA256(body, secret))；
     结果落 `callback_log`，失败标记 failed 并保留供重试（MVP 不引入消息队列/后台 worker，先尽力发送+落日志+可重试）。
   - 回传 payload 示例（DN 发货）：`{event:"dn.shipped", dn_code, owner_code, customer_code, lines:[{sku, shipped_qty}], shipped_at}`。

4. **安全/隔离**：外部密钥绑定 owner、写入强制 owner 维度；回传加签供外部验签；`api_key.last_used_at` 与
   `callback_log` 留审计痕迹；回传 secret 同样不存明文（hash 或加密）。

**与现有 Roadmap 关系**：原 M13 的「简易鉴权(X-API-Key)」升级为完整的外部对接里程碑（鉴权+写入+回传），
CSV 顺延为 M14。该需求不阻塞 M9-M12，但 M9-M12 实现时应注意：路由层保持纯净、避免把 owner_id 直接从请求体信任给外部场景。

## M7 客户主数据与出库关联（2026-11-03）

**目标**：补上此前遗漏的关键维度——**出库是发给客户的**，原 DN 仅挂货主（委托方），
缺少客户（收货方）主数据。落地客户主数据并让出库单强制关联客户，多货主下客户归属货主、
彼此隔离。

**改动清单**：
- `main.py`：`customer` 表（code 唯一/name/owner_id 归属货主/contact/phone/address/status/启用）
  + `dn.customer_id` 列（新建库 DDL 自带、旧库 ALTER 迁移）
- `routes_customer.py`：客户 CRUD + 编码去重 409 + 货主归属校验 + 列表 keyword/status/owner_id
  过滤 + 分页 + 被出库单引用时禁删 409
- `routes_dn.py`：创建必填 customer_id（存在且 owner 一致才接受）、列表/详情联表返回
  customer_code/customer_name
- `tests/test_customers.py`：10 例；`tests/test_dn.py` 补 3 例（必填/归属隔离/详情返回）、
  夹具同步自动建客户，存量用例按新校验顺序修正

**可验证结果**：
- `python -m unittest discover -s tests` 87 例全绿
- 客户编码重复 409；归属货主不存在 400；被 DN 引用禁删 409
- 出库必填客户：缺 customer_id 400；选了别家货主的客户 400（多货主隔离）
- 列表/详情正确返回 customer_code/customer_name

## M8 基础资料字段增强（电商场景，2026-11-03）

**目标**：补齐货主/客户/物料/库位四类主数据的电商化字段，并附一键演示数据播种脚本，便于本地模拟「收货上架 → 拣货发货」全链路。

**改动清单**（参考电商 WMS 主数据口径）：
- `main.py`：四类表 DDL 增加电商字段 + 旧库 ALTER 列迁移（幂等）
  - owner：email、address
  - customer：email、level（普通/银牌/金牌/VIP）、region
  - product：barcode、brand、spec、weight_kg、length_cm、width_cm、height_cm、shelf_life_days
  - location：aisle、bay、level、barcode
- `routes_owner.py` / `routes_customer.py` / `routes_product.py` / `routes_location.py`：校验层接受新字段、创建 INSERT 写入、详情/列表经 `dict(row)` 自动带出
- `web_app/owners.html` / `customers.html` / `products.html` / `locations.html`：表单补字段输入、表头补列、JS 行渲染补列
- `tests/test_owners.py` / `test_customers.py` / `test_products.py` / `test_locations.py`：各补「创建接受扩展字段并返回」「部分更新保留未动字段」用例
- `tools/seed_demo.py`（新增）：一键播种演示数据（3 货主 / 8 物料 / 8 库位 / 4 客户 + 2 张 ASN 收货上架 + 2 张 DN 拣货发货），支持 `--reset` 重置、`--check` 仅查看

**可验证结果**：
- `python -m unittest discover -s tests` 97 例全绿
- 创建返回含新字段；PUT 只改 name 时 email/品牌/巷道等未动字段保持不变
- 前端字段与后端一致；`python tools/seed_demo.py --reset` 跑通全链路（ASN 全 closed、DN 全 shipped、生成 20 条 stock_movement）

## M9 用户与角色管理（2026-11-03）

**目标（用户原话）**：「目前没有用户管理，需要有角色用户，后续增加功能的时候这个需要联动起来 别忘记」。
建立 RBAC 基础——用户归属角色、角色持权限清单、登录签发无状态 token；并约定**后续任何功能要做权限控制，只需给路由加 `@require_permission("xxx")` 即可联动本模块**（详见 `auth.py` / `routes_user.py`）。

**改动清单**：
- `auth.py`（新增）：`hash_password`（salt+sha256，不存明文）、`verify_password`、`make_token`/`verify_token`（HMAC-SHA256 签名、7 天有效、无外部依赖）、
  `get_current_user`（从 `Authorization: Bearer` 还原当前用户并带出角色权限）、`has_permission`、
  `require_permission(perm)` 装饰器（要求登录 + 具备 perm；`*`/资源`*` 通配；角色「管理员」默认全部权限）
- `main.py`：`role`(name 唯一/permissions JSON)、`user`(username 唯一/salt/hash/role_id/owner_id 可空/status)
  两表 DDL（幂等）+ `_seed_auth` 播种默认角色（管理员/操作员/只读）与 admin 账号（admin / admin123，仅空库）
  + 注册 `user_bp`
- `routes_user.py`（新增）：`POST /api/auth/login`（签发 token）、`/api/roles` 与 `/api/users` 的
  GET/POST/GET+PUT/DELETE 全套 CRUD，**全部要求 `user:manage` 权限**（管理员默认具备）
- 前端：`login.html`（登录页，token 存 localStorage）、`roles.html`/`users.html`（弹窗式 CRUD，
  owner 可空=全局）、`js/api.js` 自动附带 `Authorization` 头 + `requireLogin()`/`logout()`、
  `js/layout.js` 增「系统管理」分组（角色/用户）与退出登录入口
- `tests/test_users.py`（新增，11 例）：登录成功/失败、未登录 401、管理员列出角色、角色 CRUD +
  重名 409 + 有用户禁删 409、用户 CRUD + 改密后旧密失效、非管理员访问 403、删最后一个管理员 409、非法 token 拒

**联动约定（后续功能必读）**：
- 新建任何需要鉴权的接口，先决定权限点（如 `stock:read`、`asn:write`），再在函数上加
  `@user_bp.route(...); @require_permission("asn:write")`。`request.user` 即为当前操作人，可用于审计字段。
- 用户可绑定 `owner_id`（空=全局），后续多货主下的「某用户只能看某货主数据」据此过滤。
- 外部对接（M14）用 X-API-Key（按货主），与这里的内部 RBAC 是两套体系，互不影响；内部 UI/接口走 RBAC。

**可验证结果**：
- `python -m unittest discover -s tests` 108 例全绿（新增 11）
- 登录签发 token；无 token 访问 `/api/roles` 返回 401；操作员 token 访问返回 403
- 创建角色/用户 → 用新账号登录成功 → 改密后旧密失效；删除最后一个管理员被拒（409）
- 前端 `/login.html` 登录后 localStorage 持 token，受保护页面自动带 `Authorization` 头

## M10 物料主数据深化（2026-11-03）

**目标**：补齐物料（商品）的电商/医药级主数据，支持多条码扫码与多层级包装规格，让物料档案达到「可当真产品用」的深度。

**改动清单**：
- `main.py`：`product` 表新增 article_no/category_l1~l4/product_type/origin/business_scope/reg_cert_no/registrant/
  mfg_enterprise/mfg_license/external_code/main_category/temp_range/humidity_range/hazard_type/is_serial/
  is_shelf_control(0/1)/extra1~3，迁移幂等；新增 `product_barcode`（多条码、可设主条码）、
  `product_package`（多层级包装：pkg_level/长宽高/volume 自动计算/weight_kg/qty_per_package）子表
- `routes_product.py`：校验与 INSERT 同步扩展；新增
  `GET/POST /api/products/<id>/barcodes`、`DELETE /api/products/<id>/barcodes/<bid>`、
  `GET/POST /api/products/<id>/packages`、`DELETE /api/products/<id>/packages/<pid>`
- `web_app/products.html`：物料表单扩展全部新字段（弹窗加宽至 760）；新增「条码/包装」管理弹窗（多条码 + 多层级包装）
- `tests/test_products.py`：+7（扩展字段落库、部分更新保留、条码增删、包装体积自动计算、负数拦截）

**可验证结果**：全量 139 绿；创建返回含全部新字段；包装体积 = 长×宽×高 自动写入；条码列表主条码置顶。

## M11 货主主数据深化（2026-11-03）

**目标**：货主档案补充省市区、备用地址、授权仓库、经营范围与证照信息，贴近 3PL/医药 WMS 实务。

**改动清单**：
- `main.py`：`owner` 表新增 province/city/district/backup_address/authorized_warehouse/business_scope/extra1~2；
  新增 `owner_cert`（cert_type/cert_no/issuer/valid_from/valid_to/note）证照子表，迁移幂等
- `routes_owner.py`：校验与 INSERT 同步；新增 `GET/POST /api/owners/<id>/certs`、`DELETE /api/owners/<id>/certs/<cid>`
- `web_app/js/region.js`（新增）：省→市级联静态数据（34 省级单位主要地级市）；`modal.js` 新增 `region` 复合字段类型（省/市联动 + 区/县文本）
- `owners.html`：接入省市区下拉、新字段；新增「证照」管理弹窗
- `tests/test_owners.py`：+6

**可验证结果**：全量 139 绿；省市区下拉联动生效；证照增删可用。

## M12 客户主数据深化（2026-11-03）

**目标**：客户档案补充省市区、详细地址与经营范围（呼应「客户选择省市区和填写详细地址、客户也有经营范围」）。

**改动清单**：
- `main.py`：`customer` 表新增 province/city/district/detail_address/business_scope，迁移幂等
- `routes_customer.py`：校验与 INSERT 同步扩展
- `customers.html`：接入 `region` 复合字段、详细地址、经营范围；列表展示省/市

**可验证结果**：全量 139 绿；创建/更新正确落库新字段，未动字段保持不变。

## M13 库位主数据深化（2026-11-03）

**目标**：库位补充库位组（可手工维护或按编码前缀自动生成）与长宽高，库容按体积计算。

**改动清单**：
- `main.py`：`location` 表新增 location_group/length_cm/width_cm/height_cm/capacity_volume，迁移幂等
- `routes_location.py`：库位组建位时留空按编码前缀自动生成（含 `-` 取首段，否则取前两位）；
  体积库容 = 长×宽×高 / 1e6（m³，保留 6 位），创建/更新均重算；列表新增 `location_group` 过滤；
  新增 `GET /api/locations/groups` 库位组去重列表
- `locations.html`：表单接入库位组/长宽高、（按组）筛选下拉；列表展示库位组与体积库容

**可验证结果**：全量 139 绿；`A-01-02`→组 `A`、`STORE1`→组 `ST`；100×200×50cm → 1.0 m³；按组过滤正确。

## M14 承运商模块（2026-11-03）

**目标**：新增承运商子系统，作为后续发快递/零担/整车的物流履约资源目录（独立于多货主，全系统共享）。

**改动清单**：
- `main.py`：新增 `carrier` 表（code 唯一/name/carrier_type[快递·零担·整车·其他]/api_code/contact/phone/address/status）
  + 注册 `carrier_bp`
- `routes_carrier.py`（新增）：CRUD 全套 + 编码去重 409 + 关键词(编码/名称/外部编码)/类型/状态过滤 + 分页
- `web_app/carriers.html`（新增）+ `layout.js` 侧边栏「承运商」入口
- `tests/test_carrier.py`（新增）：12 例

**可验证结果**：全量 139 绿；创建校验/重码 409/类型非法 400/分页搜索/部分更新/删除均通过。

## M15 主数据体验增强（2026-11-04）

**目标**：落实用户四项体验增强需求——① 物料体积可手填、未填则按长宽高自动换算(cm³)；② 产品条码与包装层级可编辑，包装等级限定 1-6 级可选；③ 承运商模块播种演示数据（顺丰/京东/跨越/货拉拉/德邦/中通/圆通/安能）；④ 货主合作起止时间用日期选择器（可手填可选）。

**改动清单**：
- `main.py`：product 表加 `volume`(REAL)；owner 表加 `start_date`/`end_date`(TEXT)；旧库幂等 ALTER 迁移
- `routes_product.py`：体积校验 + create 自动换算(长×宽×高) + update 仅改长宽高时联动重算；新增 `PUT /barcodes/<id>`（改条码值/主条码）、`PUT /packages/<id>`（改全部字段、pkg_level 限定 1级-6级、长宽高变动重算体积）；`product_package` 默认等级改 `1级` 并校验
- `routes_owner.py`：起止时间校验 + create 落库
- `web_app/js/modal.js`：新增 `date` 字段类型（原生 input[type=date]，可手填可点选）
- `web_app/products.html`：物料加「体积(cm³)」字段 + 自动估算；条码/包装行加「编辑」按钮（openForm 改值）
- `web_app/owners.html`：加起止时间(date)、证照有效期改用 date 输入
- `tools/seed_demo.py`：新增 8 个承运商演示数据（--reset 时一并播种，carrier 纳入清空范围）
- `tests/`：volume 自动/手填/联动、条码 PUT、包装 PUT+等级校验、货主起止时间，共 +9 例

**可验证结果**：全量 148 绿；演示库 reseed 后含 8 承运商、物料体积按尺寸自动落值。

## M16 供应链与单据主数据增强（2026-11-04）

**目标（用户三项需求）**：① 收货单(ASN)/出库单(DN) 默认单号规则＝英文开头 + 日期流水号；收货需要供应商，供应商资料要建好并给基础数据；② ASN 需包含单据类型/外部单号/订单来源/订单总数量(明细汇总)/品类数(明细汇总)/来源仓库/客户名称/供应商名称·联系人·电话·地址/承运商/备注/自定义字段；③ 系统此前只支持多货主、未支持多仓库，需新增仓库主数据并让 ASN/DN 关联来源仓库。

**改动清单**：
- `main.py`：新增 `supplier` 表（code 唯一/name/owner_id 归属货主/contact/phone/address/status）+ `warehouse` 表（code 唯一/name/address/contact/phone/status，全系统共享不绑货主）；`asn`/`dn` 表扩展列 `doc_type`/`external_no`/`order_source`/`source_warehouse`(→warehouse)/`customer_name`/`carrier_name`/`total_qty`/`category_count`/`extra1~3`（dn 无 supplier 列；asn 另含 `supplier_id` 及 `supplier_name/contact/phone/address` 快照）；新增 `gen_doc_no(db, table, prefix)`（前缀-YYYYMMDD-NNN，当日前缀计数+1）；旧库 ALTER 幂等迁移上述列；注册 `supplier_bp`/`warehouse_bp`
- `constants.py`：新增 `ASN_DOC_TYPES`（调拨/销退/采购/期初/其他）、`DN_DOC_TYPES`（销售/调拨/退货/其他）
- `routes_supplier.py`（新增）：供应商 CRUD + 编码去重 409 + 货主归属校验 + 被 ASN 引用禁删 409 + 列表 keyword/owner_id/status 过滤 + 分页
- `routes_warehouse.py`（新增）：仓库 CRUD + 编码去重 409 + 被 ASN/DN 引用禁删 409 + 列表 keyword/status 过滤 + 分页（仓库全局共享，无货主隔离）
- `routes_asn.py`：`create_asn` 接受新字段；单号留空自动生成 `ASN-YYYYMMDD-NNN`（传入则用传入值）；校验 doc_type；source_warehouse 存在且启用；supplier_id 须属本货主（多货主隔离）并快照名称/联系人/电话/地址；total_qty=Σ预期、category_count=不同物料数；明细同物料重复拒绝 400；`list/get` 联表返回 `source_warehouse_code/name`、`supplier_code/name`
- `routes_dn.py`：对称增强（DN 无 supplier 维度；含 carrier_name）；自动单号 `DN-YYYYMMDD-NNN`
- `web_app/suppliers.html`/`warehouses.html`（新增）：CRUD 页（suppliers 含货主下拉并随货主过滤，warehouses 全局）；`layout.js` 侧边栏「基础主数据」新增「供应商」「仓库」入口
- `web_app/asns.html`/`dns.html`：新建表单加 单据类型/外部单号/订单来源/来源仓库(select 仓库)/供应商(随货主联动)/客户名称/承运商/自定义字段1~3；列表加 单据类型/总数量/品类数/来源仓库/供应商(ASN) 列；详情加字段展示块
- `tools/seed_demo.py`：新增 3 仓库（深圳/上海/北京）、4 供应商（各货主常用供货方）；FLOWS 的 ASN/DN 引用来源仓库与供应商；清空范围纳入 supplier/warehouse
- `tests/test_supplier.py`/`tests/test_warehouse.py`（新增）：各 12 例；`tests/test_asn.py`/`tests/test_dn.py` 补 自动单号/单据类型校验/汇总字段/供应商快照·隔离/仓库校验/明细重复拒绝 等

**可验证结果**：全量 197 绿（原 148 + 新增 49）；演示库 reseed 后含 3 仓库、4 供应商、2 张 ASN（closed，带供应商快照与来源仓库）、2 张 DN（shipped，带来源仓库）；单号留空自动生成如 `ASN-20261104-001`；前端 supplier/warehouse 页可访问。

## M17 移库单（Stock Transfer，2026-11-04）✅

**目标**：落实 D6 首条里程碑——把"库位间库存转移"做成单据驱动（与 ASN/DN 对称），
保证转移在单一事务内原子完成：来源位减少、目标位增加、两条流水（move_out/move_in）
一一对应，余额始终可由流水核对。

**改动清单**：
- `constants.py`：新增移库单状态机 `TRANSFER_OPEN/DONE/CANCELLED`；`MOVEMENT_MOVE_OUT`/`MOVE_IN`
  流水类型（服务层 `stock_move` 此前已存在，本里程碑将其对接到单据）
- `main.py`：新增 `transfer`（code 唯一/owner_id 归属/status 状态机）与
  `transfer_line`（product × from_location × to_location × qty，唯一键防重复行）两表 DDL
- `routes_transfer.py`（新增）：`POST /api/transfers` 创建（货主×物料归属一致性校验、来源/目标库位启用校验、
  来源=目标位拒绝、编码唯一 409）、`GET /api/transfers` 列表（keyword/status/分页）、
  `GET /api/transfers/<id>` 详情（联表带出 来源/目标库位编码）、
  `POST /api/transfers/<id>/move` 执行（BEGIN IMMEDIATE 事务，来源位按**可用量**=在手-已分配转出，
  `stock_move` 同事务一减一增写 move_out/move_in 流水，状态→done）、
  `POST /api/transfers/<id>/cancel` 取消（仅未执行）
- `tests/test_transfer.py`（新增，12 例）

**可验证结果**：
- `python -m unittest discover -s tests` 163 例全绿（+12）
- 执行移库：来源位 qty 10→6、目标位 0→4，两条流水 delta=-4/+4（ref_type=move_out/move_in，ref_id=单据），
  执行后 reconcile 仍一致（checked>0, consistent=true）
- 拒绝路径：编码重复 409、货主/物料不一致 400、来源=目标位 400、来源位停用 400、
  可用量不足 400、已执行重复 move 409、取消已执行单 409
- 不移动已分配（已拣货）库存：可用量 = 在手 - 已分配，扣减仅取可用部分

## M18 盘点单（Stocktake，2026-11-04）✅

**目标**：落实 D6 第二条里程碑——账实核对（与 ASN/DN/Transfer 对称，全部经 inventory_service 落流水）。
锁定单→冻结快照账存→盲盘录入实盘→按差异生成 adjustment 调整流水→解冻结案，全程余额仍可由流水核对。

**改动清单**：
- `constants.py`：新增盘点单状态机 `STOCKTAKE_OPEN/FROZEN/ADJUSTED/CLOSED`；复用 `MOVEMENT_ADJUSTMENT` 流水类型
- `main.py`：新增 `stocktake`（code 唯一/owner_id 归属/status 状态机）与 `stocktake_line`
  （owner×product×location 唯一，book_qty 快照/ counted_qty 实盘，可为空待盘）两表 DDL
- `routes_stocktake.py`（新增）：`POST /api/stocktakes` 创建（货主校验、编码唯一 409）、
  `GET /api/stocktakes` 列表（keyword/status/分页）、`GET /api/stocktakes/<id>` 详情（联表带出
  物料/库位编码 + diff=counted-book）、`POST /api/stocktakes/<id>/freeze` 冻结（BEGIN IMMEDIATE 快照货主
  有货库存到盘点行，状态→frozen）、`POST .../count` 盲盘（逐行录入 counted_qty，状态保持 frozen，
  负数 400）、`POST .../adjust` 差异调整（有未盘行 400；对 counted≠book 行以 adjustment 流水修正账存，
  状态→adjusted）、`POST .../close` 解冻结案（仅已调整单可结案，状态→closed）
- `tests/test_stocktake.py`（新增，14 例）

**可验证结果**：
- `python -m unittest discover -s tests` 212 例全绿（+14，总测试 212）
- 冻结快照：来源位账存 10 → 盘点行 book_qty=10；盲盘录 7 → diff=-3；调整 → 来源位账存变 7，
  产生 1 条 adjustment 流水（delta=-3），reconcile 仍 consistent=true
- 拒绝路径：编码重复 409、货主非法 400、非 open 冻结 409、非 frozen 盲盘/调整 409、
  盲盘负数 400、有未盘行调整 400、非 adjusted 结案 409
- 无差异（实盘=账存）仍正常置 adjusted，账存不变

## 工作台与收货单体验增强（2026-11-04 用户 4 点反馈，非里程碑）

**目标**：① 修「进入工作台」可无限点击导致页面嵌套错乱；② 左侧菜单改垂直一级 + 内嵌可折叠子菜单；
③ 收货单默认不填货主，选货主后客户/供应商/商品联动，但不再「只让选合规数据」，而是**全量可选 + 选错即时报错**；
④ 单据类型默认采购、仓库必填且默认有内容、下单可改供应商/客户联系方式、明细带出商品档案与批次信息、布局优化。

**改动清单**：
- `web_app/index.html`：工作台入口标记 `data-embed-hide`，embed 模式由 `layout.js` 隐藏；即便被点击也改为顶层跳转
- `web_app/app.html`：embed 守卫（工作台自身不被塞进 iframe，`openTab` 也拒绝以 `/app.html` 为标签内容）；侧边栏由扁平分组改为垂直一级菜单 + 内嵌子菜单（accordion），图标条状态点击一级自动固定展开
- `web_app/js/modal.js`：新增 `section` 分节、`select.default`/空选项/`required`、`setFieldError()` 实时报错、`lines.autoFill + optionData + onLineChange/onLineAdd + cardLayout`；`openForm()` 返回 overlay 供页面做联动
- `web_app/asns.html`：表单按「单据信息 / 往来单位 / 收货明细 / 其他」分节（宽 1000）；货主默认空，选定后三处下拉按归属排前打 ✓ 但保留全部选项，选错即时红字；选供应商/客户自动带出联系方式（仍可改）；选商品自动带出商品档案并留快照；明细表格 17 列展示规格/货号/批号/效期/注册证号等
- `main.py`：`asn` 增 `customer_id`/`customer_contact`/`customer_phone`/`customer_address`；`asn_line` 增 `extra`（JSON 明细快照）；均含旧库幂等 ALTER；`MINIWMS_PORT` 可覆盖监听端口
- `routes_asn.py`：`customer_id` 归属校验（选错 400 明确报错）、供应商/客户联系方式允许下单覆盖、明细快照落库并在详情返回（老单据回退物料主数据）、详情保留名称快照并补 `customer_code`
- **顺带修复真实 bug**：全新库 `customer` 表缺 `province`/`city`/`district`/`detail_address`/`business_scope` —— 该表在 `init_db()` 中段才创建，而 M8/M12 迁移用函数开头的 `tables` 快照判断 `if "customer" in tables`，导致全新库跳过迁移、`/api/customers` 插入报 `no column named province`。改为实时 `sqlite_master` 判断，并新增 `tests/test_fresh_db_schema.py` 回归
- `tests/test_asn.py`：+5（仓库/客户/联系方式覆盖/明细快照/默认采购），夹具同步；新增 `tests/test_fresh_db_schema.py` +2

**口径说明**：来源仓库在**表单层必填**（`required` + 默认选中第一个；无仓库时自动播种 `WH-DEFAULT`），API 层仍允许省略以保持内部作业链路与后续外部对接兼容；一旦提供则严格校验存在且启用。

**可验证结果**：
- `python -m unittest discover -s tests` **219 例全绿**（原 212 + ASN 5 + 全新库 schema 2）
- Playwright 端到端 **31/31 通过**：默认采购/仓库有值/货主默认空、全量可选与 ✓ 标记、选错即时报错且改回清除、商品档案自动带出、创建成功、明细 17 列与档案/批号/联系人展示、工作台 3 组一级菜单与折叠、概览标签隐藏工作台入口、重复点击不重复开标签、防嵌套守卫

## 多层内嵌导航与菜单区滚动（2026-11-05 用户反馈，非里程碑）

**目标（用户原话）**：「导航菜单栏支持内嵌子菜单，可以做多层内嵌，你可以做同类型的合并，不然展示太多了；太多建议是要做上下移动，不然看不到部分内容了」。
用户确认 **1A＝菜单区垂直滚动**，并确认按下述 3 层树实施。原状是 `layout.js` 扁平「3 个分组 + 18 个平铺项」的两层结构，工作台 `app.html` 自己再包一层 accordion；独立页 `.sidebar` 固定 `100vh` 但 `.nav` 无 `overflow-y`，小屏下底部菜单被裁切。

**导航树**（一级 3 / 二级 7 / 三级 18，渲染器递归，后续加到第 4 层无需改代码）

| 一级 | 二级 | 三级页面 |
|---|---|---|
| 基础主数据 | 客商档案 | 货主 / 客户 / 供应商 / 承运商 |
| 基础主数据 | 仓储布局 | 仓库 / 储区 / 库位 |
| 基础主数据 | 货品档案 | 物料 |
| 仓储作业 | 入库单据与作业 | 收货 / 验收作业 / 上架作业 |
| 仓储作业 | 出库单据 | 出库 |
| 仓储作业 | 库存与追溯 | 移库 / 盘点 / 库存账 / 批次 |
| 系统管理 | 权限 | 角色 / 用户 |

**改动清单**：
- `web_app/js/layout.js`：扁平 NAV 改为递归树；新增 `window.MWS_NAV_TOOLS.mount()` 作为**唯一**交互实现——渲染、折叠/展开、祖先链自动展开、当前页高亮、localStorage 记忆；同时暴露 `MWS_NAV`（树）与 `MWS_NAV_FLAT`（18 个扁平叶子）。独立页 `render()` 以 `mode:"link"` 挂载，叶子直接跳转；工作台以 `mode:"tabs"` 挂载，叶子改为开标签。
- `web_app/app.html`：删除本地 `GROUP_ICONS` / 分组拼装 / 两处重复事件绑定，改为调用 `MWS_NAV_TOOLS.mount(navEl, { mode, activeHref, beforeToggle, onLeaf })`；`highlightNav()` 改为 `navCtl.reveal(url)`，切换标签时自动展开所在分支并高亮。侧栏伸缩、收起态点击一级先展开侧栏、localStorage 记忆均保持不变。
- `web_app/css/style.css`：`.sidebar .nav` 与 `.ws-side .nav` 统一 `min-height:0; overflow-y:auto` + 细滚动条，brand / 收起按钮 / `side-foot` 固定、中间菜单区独立上下滚动；`.nav-sub` 缩进 16px 递归累加、二级分组标题降一档字重；折叠改为 `collapsed > .nav-sub` 只隐藏直属子菜单；修正原全局 `.nav-group-toggle .nav-label { opacity:0 }` 使独立页分组文字不可见的问题（现仅工作台收起态淡出）。
- **展示策略**：默认只展开「当前页所在分支」，其余折叠（概览页无对应叶子时展开第一个一级）；用户手动展开/收起记在 `miniwms_nav_override`，再次访问该分支时清除覆盖，保证当前页一定可见。折叠只切 class 不重绘 DOM，滚动位置不丢。

**口径说明**：本次只改导航结构与展示，**不新增任何业务页面**。「分配 / 拣货 / 复核 / 发货」目前仍集中在 `dns.html` 明细区（按钮触发），无独立作业页；等蓝图 M35（波次 + FEFO）、M36（复核）落地后再往树里追加节点。

**可验证结果**：
- `./.venv/Scripts/python.exe -m unittest discover -s tests` **285 例全绿**；`node --check` 校验 `layout.js` / `api.js` 通过
- Playwright 真实 Chrome（独立端口 5102 + 临时 DB + 注入 admin token）**23/23**：3 一级 / 10 分组 / 18 叶子、默认折叠策略、折叠交互、菜单区上下滚动（工作台 `scrollTop=650`、独立页 `639`）、侧栏伸缩与收起态点击、叶子开标签与祖先链展开高亮、**19 个独立页全部渲染 3 层**、业务 404 与 JS 运行时错误均为 0

## M19 批次与验收入库追溯（2026-11-04）✅

**目标（用户原话）**：「目前收货明细你都带出来，但是设计的一些信息都没置灰的无法编辑，需要能够人工编辑」；
「入库单的明细需要有这些，额外增加灭菌批号字段，产地，患者名称，内部批次号，货品属性（合格/不合格）,udi,生产企业」；
「验收时增加批次的概念……如果验收入库后需要把这些信息都写入到批次信息中，这个需要有批次的概念，然后有地方可以配置批次，
如果配置的部分批次信息内容一致，这个关键批次信息是一致的，每次入库都要有唯一的批次号，内部批次号的含义是如果是第一次入库和批次号一致，
如果是退货的按出库的内部批次号一致，一般都是数据传过来的」。

**设计口径（三层模型，医疗器械 / GSP）**：

| 层 | 表 | 作用 |
|---|---|---|
| 配置 | `batch_config` | 20 个批次字段，可改「是否关键 / 启用 / 默认值 / 标签 / 备注」；默认 7 个关键字段：批号、生产日期、失效日期、灭菌批号、UDI、产地、生产企业 |
| 档案 | `batch` | 关键批次信息主档：`SHA-256(货主 + 物料 + 配置的关键字段值)` 指纹唯一；值一致即同一档案（关键批次信息一致），可手动预配置 |
| 流水 | `batch_receipt` | 每次验收入库一条，`batch_no` 全局唯一（`BAT-YYYYMMDD-NNN`）；存完整批次信息、数量、货品属性、关联收货单/明细/库位 |

- **内部批次号规则**：外部数据传入优先（明细或收货接口 `internal_batch_no`）→ 销退沿用同一档案最近一次入库的内部批次号 →
  首次入库 = 本次批次号；`internal_source` 记录来源 `external / return_trace / first_in`。
- **验收写入点**：`routes_asn.receive()` 在原有 `BEGIN IMMEDIATE` 事务里，先 `stock_up()` 再调
  `batch_service.record_receipt()`，库存增加与批次写入原子完成；任何异常一起回滚。
- **配置变更不重算历史**：改关键字段只影响之后的验收，既有档案指纹保持不变（历史留痕不被配置篡改）。
- 设计依据与「暂不采纳维度表」说明见 `docs/REFERENCES.md` 第 9 节。

**改动清单**：
- `batch_fields.py`（新增）：20 个批次字段字典（前后端唯一口径）、配置幂等播种、关键字段读取、指纹计算
- `batch_service.py`（新增）：服务层 `prepare_info()`（套配置默认值）、`record_receipt()`（批次档案去重 +
  唯一批次号 + 内部批次号规则）、`create_profile()`（手动预配置）
- `routes_batch.py`（新增）：`GET /api/batch/fields`、`GET/PUT /api/batch/config`、
  `GET/POST /api/batch/profiles`、`GET /api/batch/profiles/<id>`、
  `GET /api/batch/receipts`、`GET /api/batch/receipts/<id>`；含分页、过滤、
  至少保留一个关键字段的守卫、多货主归属校验；批次流水**不提供手工新增接口**（必须由收货单驱动）
- `main.py`：`batch_config` / `batch` / `batch_receipt` 三表 DDL（含索引与级联清理）+ 默认配置播种 +
  注册 `batch_bp`
- `constants.py`：批次号前缀 `BAT` / 档案号前缀 `BP`、`GOODS_PROPERTIES`、内部批次号来源枚举
- `routes_asn.py`：`LINE_SNAPSHOT_FIELDS` 扩到 20 个字段；`goods_property` 合法值校验；
  `_dict_line()` 全字段返回（老单据回退物料主数据，人工编辑值不被覆盖）；
  `receive()` 同事务写批次并在响应返回 `batch`；`get_asn()` 明细行带出本行 `batch_receipts`
- `web_app/asns.html`：明细**去掉全部 `readonly`**（自动带出只是默认值，均可人工编辑）；
  新增灭菌批号 / 产地 / 患者名称 / 内部批次号 / 货品属性(select) / UDI / 生产企业；
  明细下方增加「扩展档案字段 + 验收入库批次」展示行；收货可填内部批次号并展示生成的批次号
- `web_app/batches.html`（新增）：批次配置编辑、批次档案列表/预配置/详情、验收入库批次流水三块；
  `layout.js` / 工作台导航加入「批次」入口；`index.html` 里程碑表同步
- `tests/test_batch.py`（新增 18 例）+ `tests/test_asn.py`（+2）+ `tests/test_fresh_db_schema.py`（+1）

**可验证结果**：
- `python -m unittest discover -s tests` **240 例全绿**（原 219 + 批次 18 + ASN 2 + 全新库 schema 1）
- 首次验收入库：内部批次号 = 批次号；同一 ASN 二次收货：`batch_no` 不同但 `batch_id` 相同（关键批次信息一致）
- 把 UDI 从关键字段摘掉后，仅 UDI 不同的两批归入同一档案；销退沿用出库内部批次号；
  外部数据传入的内部批次号优先；配置默认值补齐空字段；非法货品属性 400；`/api/stock/reconcile` 仍一致
- Playwright 端到端 **39/39 通过**：配置 20 行 / 7 关键字段可开关、验收批次写入、
  档案与流水列表、档案详情、收货明细 7 个新字段与无置灰字段、货品属性下拉、工作台「批次」入口

## M20 储区与上架作业基础（阶段 A，2026-11-05）✅

**目标（用户原话）**：「侧边菜单栏的伸缩消失了 需要恢复，做完你要检查 不要做完一个 另外一个就出问题」；
「增加储区的基础资料，然后货位新增修改的时候能够选择这个储区」；「储区货位这些是和仓库有关系的 我看你没有做区分」；
「只做基础资料：编码 / 名称 / 温区分类 / 所属仓库（可空＝全仓共享）/ 启用 / 备注；location 加 zone_id，库位新增修改改成下拉选储区，旧 zone 文本保留兼容，额外增加是否混放的字段」；
「验收、上架各开一个工作台新标签页（独立页，URL 带 ?asn_id=，可同时开多张单）」；
「货品需要有地方选择推荐的储区，和增加一个绑定货位的地方」；
「简版可用：推荐储区 + 绑定货位优先 + 同产品/同批号聚拢 + 启用/容量/温区过滤 → 返回 Top-N 货位与理由，上架页一键采纳」；「就改这些」。

**设计口径**：
- 仓库 → 储区 → 库位三级：`zone.warehouse_id` 为空＝全仓共享；库位 `warehouse_id` 与储区 `warehouse_id` 必须一致，或储区全仓共享。
- 混放三级作用域：库位自身非 NULL 优先 → 继承储区 → 默认全部禁止；三个开关＝产品 / 批号 / 货主混放。
- 上架推荐只读：硬过滤（启用 / 目标位类型 storage·picking / 温区 / 剩余容量 / 三类混放）后打分
  「绑定货位 100+优先级 > 推荐储区 60 > 同产品聚拢 30 > 同批号聚拢 20 > 空库位 10 > 容量贴合 5」；
  返回 Top-N + 理由 + 被排除原因；**不写库存**，采纳仅把 `location_id` 填进原有 `/api/asns/<id>/putaway`。
- 条件组 / fallback 链 / 权重配置仍属 M25，本版不做；`asn`·`dn` 单据标识位与 `external_doc_type` 属 M20 剩余范围；**按蓝图 v3 口径，混放 / 冷链 / 危险拦截已从 M20 剩余移出，改为 M25/M26 配置化规则**（见「规划」段 ⑤）。

**改动清单**：
- `main.py`：`zone` / `product_location` 两表 DDL + 索引、`_migrate_m20_columns()` 幂等列迁移
  （location 加 `zone_id`/`warehouse_id`/三个可空混放列，product 加 `default_zone_id`）、`_seed_zones()`
  播种 3 个全仓共享储区与每仓默认储区、注册 3 个新 Blueprint；旧 `location.zone` 文本列保留兼容
- `constants.py`：`TEMP_CLASSES`、`MIX_FIELDS`、`PUTAWAY_R_*` 理由码与中文标签
- `routes_zone.py`（新增）：`/api/zones` CRUD，仓库存在性 / 温区枚举 / 编码唯一 / 改仓库时校验已挂库位 /
  删除保护（已挂库位或已被货品设为推荐储区），`GET ?warehouse_id=` 返回「本仓专属 + 全仓共享」
- `routes_location.py`：新增字段校验、`_check_zone_warehouse()` 一致性校验、`_row_to_dict()` 返回
  `zone_name`/`warehouse_name`/`effective_mix`（含来源）、列表支持 `warehouse_id`/`zone_id` 过滤
- `routes_product.py`：`default_zone_id` 校验与 `default_zone_name` 返回
- `routes_product_location.py`（新增）：`/api/product-locations` 绑定 CRUD（一个 SKU 多货位、
  仅启用中的 storage/picking、重复绑定 409、联表带出 SKU/库位/储区/仓库）
- `putaway_service.py`（新增）：`recommend(db, asn_id, line_id, qty, top_n)`；同批号判断只依据
  `batch_receipt.info` 中明确出现的批号，避免「有库存但查不到批次」被误判为混批而误杀聚拢排序
- `routes_putaway.py`（新增）：`GET /api/putaway/recommend`（只读）
- `web_app/zones.html`（新增）：储区列表/筛选/CRUD/启停，含三个混放开关与库位数
- `web_app/locations.html`：仓库/储区筛选、表单下拉联动（选仓库后只列本仓 + 共享储区）、
  混放继承字段、表格显示仓库/储区/生效混放
- `web_app/products.html`：推荐储区下拉 + 「绑定货位」弹窗（新增/解绑/优先级）
- `web_app/asn_receive.html` / `web_app/asn_putaway.html`（新增）：独立作业页，`?asn_id=`+可选 `&line_id=`；
  验收页录数量/收货位/内部批次号并展示批次流水；上架页展示 Top-N 推荐与排除清单、一键采纳
- `web_app/asns.html`：明细行验收/上架按钮改为打开新标签页，移除行内输入框与库位下拉；
  `web_app/js/api.js` 增 `openWorkTab()`（工作台内开标签、独立打开时退化为新窗口）
- `web_app/js/layout.js`：导航加入「储区 / 验收作业 / 上架作业」入口（工作台复用同一份 NAV）
- `web_app/app.html` + `css/style.css`：侧栏恢复「默认展开 + 显式收起/展开按钮 + localStorage 记住状态」，
  收起后 hover 不再弹开；移动端隐藏该按钮
- `tools/seed_demo.py`：`--reset` 增加清理 `product_location`；播种时把库位挂到全仓共享储区、
  给物料设推荐储区、按 `PUTAWAY_PLAN` 建固定货位绑定，使推荐页开箱即有可解释结果
- `tests/test_zone.py` / `tests/test_putaway.py`（新增 28 例）

**可验证结果**：
- `./.venv/Scripts/python.exe -m unittest discover -s tests` **268 例全绿**（原 240 + M20 28）
- 覆盖：储区 CRUD / 编码唯一 / 非法温区 / 仓库一致性 / 全仓共享 / 混放继承与覆盖 / 删除保护 /
  推荐排序（绑定货位第一、推荐储区、同产品、空库位）/ 温区·容量·混放硬过滤 / 采纳后真实上架成功
- 浏览器回归：工作台侧栏默认展开、收起后不因 hover 弹开、分组折叠、标签页开关、顶栏退出登录、
  储区/仓库联动下拉、验收与上架各自新标签、推荐货位一键采纳

## M20 剩余：单据标识位（阶段 B，2026-11-05）✅

**目标**：按蓝图 v3 第四轮答复 **1A / 2B / 3B / 4A** 收尾 M20——只落 5 个单据字段，
不写死混放 / 冷链 / 危险拦截，把拦截能力整体留给 M25/M26 的可配置上架规则。

**设计口径**：
- `asn` / `dn` 各加 `is_virtual` / `is_direct` / `is_cold_chain` / `is_hazard` 四个 0/1 标识位
  与 `external_doc_type`（自由文本，≤32 字符）；全部带 DEFAULT，老库自动补列为 0 / 空串。
- `constants.parse_doc_flags(payload)` 集中解析，兼容整数 / 布尔 / 字符串 `"0"`/`"1"`
  （前端 select 提交的是字符串）；非法值只报字段格式错误，**不附加任何业务约束**。
- **2B 过渡期**：`receive()` / `putaway()` 均不因这些标识位拦截；`putaway()` 保持原有
  状态 / 数量 / 库位类型校验，冷链 + 危险单据仍可落到常温 `storage` 位（专项测试锁死该行为）。
  **（v4 补注 2026-11-10：本条为 M20 时点历史口径，过渡期已随 M25 交付结束；`putaway()`
  现在按命中规则行 `limits` 拦截，`tests/test_doc_flags.py` 相应用例已改写为「命中种子冷链规则
  → 400、停用规则行 → 放行」，原先「专项测试锁死该行为」的表述不再成立，见文末 M25 章。）**
- **1A / 3B**：拦截清单（混放 / 温区 / 危化等）将在 M25 由 `putaway_rule.limits` 配置，
  M26 提供条件组编辑器；本版代码中**无 hardcoded 红线**。
- **4A**：M25 条件组首批 13 维起步；本版不建 `external_doc_type` 字典表，字典维护留 M26。

**改动清单**：
- `constants.py`：`DOC_FLAG_FIELDS` / `DOC_FLAG_LABELS` / `EXTERNAL_DOC_TYPE_MAXLEN` / `parse_doc_flags()`
- `main.py`：`asn` / `dn` DDL 加 5 列；`_migrate_m20_columns()` 扩展为同时迁移单据列（幂等）
- `routes_asn.py` / `routes_dn.py`：创建接口解析并写入 5 字段；列表 / 详情由 `SELECT *` 自动带出
- `web_app/asns.html` / `dns.html`：新建表单加「单据标识」分组（4 个是/否下拉 + 外部单据类别），
  明细页展示外部单据类别与标识标签
- `web_app/js/api.js`：`docFlagsText()` 把 0/1 标识渲染成中文标签，两个单据页共用
- `tests/test_doc_flags.py`（新增 17 例）

**可验证结果**：
- `./.venv/Scripts/python.exe -m unittest discover -s tests` **285 例全绿**（原 268 + 本版 17）
- 覆盖：解析兼容性 / 非法值与超长拒绝 / ASN·DN 创建·详情·列表字段一致 / 缺省值 /
  前端 select 字符串值 / 旧库 ALTER 迁移与幂等 / **冷链+危险单据仍可正常收货并上架（2B）**
- 未改动任何库存账逻辑，`/api/stock/reconcile` 不变式不受影响
- 提交：`1304dd2`（设计口径 v3.3 / 蓝图 v3 定稿）→ 本提交（M20 阶段 B 实现）

## M6 库存账与流水查询（2026-11-03）

**目标**：交付只读「库存账房」——余额查询、全部动作流水查询、余额与流水累加的
独立核对接口，让 M4/M5 以来的「每笔变动必落流水」承诺可被事后验证。

**改动清单**：
- `routes_stock.py`：`/api/stock`（余额列表，含在手/已分配/可用三数量，
  owner_id/product_id/location_id/has_stock 过滤 + 分页）、
  `/api/movements`（流水列表，联表带货主编码/SKU/库位编码/单据引用，
  同维度 + ref_type/ref_id 过滤 + 分页）、
  `/api/stock/reconcile`（按 货主×物料×库位 核对余额 = 流水累加，
  覆盖有行无流水/无行有流水两类异常，浮点容差 1e-9）
- `main.py`：注册 stock 蓝图
- `tests/test_stock.py`：14 例

**可验证结果**：
- `python -m unittest discover -s tests` 77 例全绿
- 余额口径正确：可用 = 在手 - 已分配（拣 4 后 10/4/6）；暂存位零行保留可见，
  `has_stock=1` 可只看有货行
- 入库链路流水可追溯：receive(+10) / putaway_out(-10) / putaway_in(+10)
  联表带出 owner_code/sku/location_code/ref_id
- 收货→上架→拣货→发货全程后 reconcile 一致（checked=2，净流水 = 余额）；
  篡改 stock.qty=99 或删除库存行后 reconcile 精确报出不一致与两侧数值

## M5 出库单与拣货发货（2026-11-03）

**目标**：落地第一个出库单据——出库单（DN）拣货分配、发货扣减与超卖拦截，
库存出库从此只能经「拣货→发货」单据链路完成。

**改动清单**：
- `constants.py`：DN 状态机（open→picking→shipped/cancelled）
- `inventory_service.py`：`adjust_stock` 新增分支——无库存行时禁止扣减/纯分配
  （防负库存行/超配行凭空产生）
- `main.py`：`dn`/`dn_line`/`dn_pick` 表（拣货明细记录每行从哪些库位各拣多少）
- `routes_dn.py`：创建（货主×物料归属一致性校验）、拣货（可用量=在手-已分配，
  BEGIN IMMEDIATE 锁行，分配落 `allocated_qty` + `dn_pick` 明细 + qty_delta=0 审计流水）、
  发货（要求所有行拣满，按明细逐库位同步扣减 qty 与 allocated_qty，写 dn_ship 流水）、
  取消（仅未拣货）；状态自动推进
- `tests/test_dn.py`：15 例

**可验证结果**：
- `python -m unittest discover -s tests` 63 例全绿
- 拣货只分配不动在手：拣 4 后库位 qty=10 不变、allocated_qty=4，流水 dn_pick qty_delta=0
- 超卖拦截：拣超可用量 400；两张单抢同一份库存，第二张可用归零被拒
- 发货按明细逐库位扣减：qty 与 allocated_qty 同步清减，一行拆多库位拣货各自正确扣减
- 状态保护：未拣满发货 400、已发货重复发货 409、已拣货取消 409

## M4 收货单与上架（2026-11-03）

**目标**：落地第一个库存单据——ASN 收货单与上架，库存从此只由单据驱动并可由流水核对。
设计依据 `docs/REFERENCES.md`（GreaterWMS 的 ASN 口径 + Sentry 的服务层分层/暂存位流转/零行保留）。

**改动清单**：
- `constants.py`：ASN 状态机（open→receiving→putaway→closed/cancelled）与流水类型枚举显式集中
- `inventory_service.py`：库存变更服务层——`BEGIN IMMEDIATE` 事务、stock upsert（零行保留）、
  `allocated_qty` 字段、每笔变动写 `stock_movement` 流水
- `main.py`：`asn`/`asn_line`/`stock_movement` 表 + 旧库 `stock.allocated_qty` 列迁移
- `routes_asn.py`：创建（货主×物料归属一致性校验）、收货（限 receiving 位、部分收货）、
  上架（暂存位→storage/picking，一减一增同事务）、取消（仅未收货）；状态自动推进
- `web_app/asns.html`：收货管理页（建单/明细/收货/上架/取消）+ 首页导航
- `tests/test_asn.py`：11 例

**可验证结果**：
- `python -m unittest discover -s tests` 48 例全绿
- 端到端冒烟：建货主→物料→收货位/存储位→ASN(100)→收货 100→上架 100→
  暂存位 qty=0（零行保留）、存储位 qty=100、流水 3 条（receive +100 / putaway_out -100 / putaway_in +100）
- 越界保护：收超剩余 400、错库位类型 400、货主不一致 400、已收货取消 409

## M3 库位主数据 + Web demo 骨架（2026-10-30）

**目标**：建立库位主数据（收发货坐标）与前后端一体 demo 骨架，本地一键启动可演示。

**改动清单**：
- `main.py`：`location` 表（编码唯一/库区/类型/容量/状态）+ `stock` 表预建
  （货主×物料×库位 唯一，M3 供库位删除保护，M4 起写入）+ Flask 静态托管 `web_app/` +
  **修复 `python main.py` 直跑时的 `__main__`/`main` 双实例循环导入**（入口模式改为以 main
  模块身份重导入自身，测试路径不受影响）
- `routes_location.py`：`/api/locations` 增删改查 + 分页 + 关键词/库区/类型/状态过滤 +
  编码去重 409 + 有库存禁删 409 + 类型中文标签
- `web_app/`：index（导航+里程碑进度）/ owners / products / locations 四页 +
  共享 css/style.css + js/api.js（原生 fetch 封装、HTML 转义防注入）
- `tests/test_locations.py`：10 例
- README 补 demo 启动方式

**可验证结果**：
- `python -m unittest discover -s tests` 37 例全绿
- 本地冒烟：`python main.py` 后首页/4 静态页 200；建货主→建库位→建物料挂货主链路 API 全通
  （物料响应带 owner_code/owner_name）

## M2 货主主数据与物料归属（2026-10-30）

**目标**：落地多货主（3PL）核心模型——货主主数据 CRUD + 物料挂接货主，后续所有库存单据将带 owner 维度。

**改动清单**：
- `main.py`：`owner` 表（编码唯一）+ `product.owner_id` 列迁移（PRAGMA 检查 + ALTER，NULL=自营）
- `routes_owner.py`：`/api/owners` 增删改查 + 分页 + 关键词/状态过滤 + 编码去重 + 名下有物料禁删
- `routes_product.py`：物料支持 `owner_id`（创建/更新时校验货主存在且启用；列表支持 `owner_id` 过滤，`none`=自营；响应附带 `owner_code`/`owner_name`）
- `tests/test_owners.py`：10 例；`tests/test_products.py` 补 5 例（挂接/不存在/停用/过滤/转移与清空）
- README 同步多货主口径

**可验证结果**：
- `python -m unittest discover -s tests` 27 例全绿
- 货主编码重复 409；名下有物料删除 409；物料挂接不存在的货主 400；挂接停用货主 400
- `owner_id=none` 过滤出自营物料；物料可在货主间转移或显式清空回自营

**测试隔离教训（与 BabyTalk M44 同源）**：`main.DB_PATH` 首次 import 冻结——多测试模块必须用
`os.environ.setdefault("MINIWMS_DB", ...)` 共享同一临时库路径，且每个 setUp 需清空所有关联表（先子后主）。

## M1 物料主数据（2026-10-30）

---

## 仓库 / 货主双维数据隔离（2026-11-05，非里程碑架构增强）

> **编号说明**：本项是用户临时提出的架构级增强，**不占用任何 M 编号**，
> `DEVELOPMENT.md` Roadmap 的 M20–M30 与 M31+ 编号一律不动。它对应领域蓝图 v3
> 第 3 层「用户角色」，但不新增里程碑；后续 M31+ 第 1 层基础资料仍按既定顺序推进。

### 目标与业务背景（用户原话与确认口径）

- 用户生产系统经验：「不同仓库的数据通过数据库库隔离」，「做视图会很卡」，会「通过配置把数据放到哪个库」。
  结论：视图过滤会把天然分区键（`warehouse_id`）降级成 WHERE 条件，单仓作业查询性能差，生产按仓分库合理。
- 用户提问：「整体系统是否可以按 SAP 的 MANDT 方式执行」「不同仓库 / 不同货主数据能否隔离」
  「一个账号能否看到所有仓库或部分仓库，并配置自定义货主查看权限（部分 / 全部）」。
- 用户最终指令：**「直接做完整版」**；「看所有仓库」＝ **A 作业视角**（能切换到任意有权限仓，
  一次只看 / 操作一个仓；不做一屏跨仓汇总）；**「预留一些功能，比如承运商、客户、供应商这些信息最好带上货主的，后期好做权限管控」**。
- 演示系统约束（用户原话）：「虽然我只是演示的系统，但是想要别人直接下载下来就能用」——
  因此默认配置必须零安装、零配置，独立分库只是**可选进阶能力**。

### 设计依据（架构讨论结论）

对齐 SAP HANA MDC / `MANDT` 与 SaaS WMS「租户上下文切面」的分层：

| 层 | SAP / 行业做法 | MiniWMS 落地 | 隔离维度 |
|---|---|---|---|
| ① 连接层 | HANA 1 个 SystemDB + N 个 Tenant DB，每租户自含目录/持久化/日志/备份 | `main.get_db()` 按 `warehouse.db_path` 选 SQLite 文件 | **仓库** |
| ② SQL 层 | ABAP 数据库接口自动注入 `sy-mandt`；ORM 拦截器自动追加 `WHERE tenant_id=?` | `scope.owner_where()` 生成条件 + `before_request` 两道集中守门 | **货主** |
| ③ 数据层 | client-dependent 表首列 `CLNT(MANDT)` | 业务表带 `owner_id`；公共表走白名单 | 标识列 |

仓库已天然按容器 / 库隔离，因此**不再注入 `WHERE warehouse_id=?`**（避免把路由键降级成过滤条件）；
货主在同一仓库内共享表，才需要 MANDT 式行级注入。

### 数据模型变更（全部带 DEFAULT，老库 `_migrate_scope_columns()` 幂等 ALTER）

```sql
warehouse.db_path        TEXT    NOT NULL DEFAULT ''   -- 空=共用主库；非空=独立 SQLite 文件
carrier.owner_id         INTEGER REFERENCES owner(id)  -- NULL=全系统共享；非空=归属货主
user.warehouse_scope     TEXT    NOT NULL DEFAULT 'grant'  -- 'all' | 'grant'
user.owner_scope         TEXT    NOT NULL DEFAULT 'grant'  -- 'all' | 'grant'
CREATE TABLE user_warehouse_grant (user_id, warehouse_id, PRIMARY KEY(user_id, warehouse_id));
CREATE TABLE user_owner_grant     (user_id, owner_id,     PRIMARY KEY(user_id, owner_id));
```

- 老库升级时**一次性**给既有 `admin` 显式 `all/all`，其余账号保持 `grant`（最小权限）；
  列已存在时不再 UPDATE，避免覆盖管理员后续收紧的授权。
- 新库 `admin` 由 `_seed_auth()` 插入时即带 `'all','all'`。
- 货主 / 仓库主数据是**跨仓主数据**：`routes_owner.py` / `routes_warehouse.py` 全部改走
  `get_control_db()`（中央库唯一权威写入点）；业务库持只读副本，由 `sync_control_tables()` 补齐，
  保证 `owner_id` / `warehouse_id` 外键在分库模式下不断链。

### 核心代码挂点

- `main.py`：`get_control_db()` / `get_db()`（默认复用同一连接对象，避免双连接锁）、
  `resolve_warehouse_db_path()`（只许项目目录内 `.db`，防路径穿越）、`normalize_warehouse_db_path()`（存相对路径）、
  `ensure_business_db()` / `init_warehouse_db()`（懒加载 + 配置即建库）、`sync_control_tables()`、
  `all_business_db_paths()`（跨库引用检查）、`@app.before_request _bind_scope()`、
  `@app.errorhandler(ScopeError)`、`RESOURCE_OWNER_TABLES` / `BATCH_RESOURCE_TABLES`。
- `scope.py`（新）：`resolve_scope()` / `current_scope()` / `owner_where()` / `scoped_where()` /
  `assert_owner_allowed()` / `check_warehouse_access()` / `visible_warehouses()` / `visible_owners()` /
  `scope_summary()`；`OWNER_SCOPED_TABLES` 与 `PUBLIC_TABLES` 白名单。
- `routes_scope.py`（新）：`/api/scope/context`、`/api/scope/warehouses`、`/api/scope/owners`、
  `/api/scope/warehouse`、`/api/users/<uid>/grants` (GET/PUT)。
- `auth.py`：`get_current_user()` 改查中央库；`has_permission()` 删除「角色名＝管理员」隐式提权，
  改由 `permissions` 中显式的 `*` 放行。
- 13 个业务列表接口注入 `S.scoped_where()`：owner(按 `id`) / product(允许 NULL) / customer /
  supplier / carrier(允许 NULL) / asn / dn / stock / movements / transfer / stocktake /
  batch profiles / batch receipts。
- `before_request` 两道集中守门：① 写请求体顶层 `owner_id` 必须在授权集合内；
  ② 按 ID 访问的资源（含单据动作、`/api/batch/profiles|receipts/<id>` 二级路径）先查归属再放行。
  `/api/auth/login` 与 `/api/scope/context` 为软解析端点，带失效旧仓 header 时不直接 403。

### 权限解析规则（关键纪律）

| 场景 | 解析结果 |
|---|---|
| 匿名 + `MINIWMS_ALLOW_ANONYMOUS=1`（默认） | **显式** `mode='anonymous-demo'` 的 ALL 上下文 |
| 匿名 + `=0` | 401 未登录 |
| 登录 + `warehouse_scope='all'` | 可见并切换到全部仓库（A 作业视角，一次一个仓） |
| 登录 + `warehouse_scope='grant'` | 按 `user_warehouse_grant` 显式 id 集合 |
| 登录 + `owner_scope='all'` | 不注入 owner 条件 |
| 登录 + `owner_scope='grant'` | 注入 `owner_id IN (...)`；`product/carrier` 的 NULL 视为公共数据仍可见 |
| 登录 + scope=grant 但无任何授权行 | **403 拒绝**（不是全量放行） |
| 上下文缺失 / 解析失败 | **403 拒绝**，绝不返回 ALL 兜底 |

> 这是多租户系统最常见的越权漏洞点：`ALL` 必须由授权解析**显式**给出，
> 不能用「上下文缺失 ⇒ 不加条件」实现。`scope.current_scope()` 在 `g.scope` 缺失时直接抛 `ScopeError`。
> `ALL` 时授权行会被清空（`save_grants`），即 ALL 靠标志而非「空集合」表示。
> 授权改动实时生效：token 只含 `{uid, exp}`，每次请求从中央库重新解析。

### 前端交互

- `api.js` 新增 `currentWarehouseId()` / `setCurrentWarehouseId()`（localStorage `miniwms_warehouse_id`）、
  `mountWarehousePicker(el, onChange)`；`api()` 自动附加 `X-WMS-Warehouse`（登录接口除外）；
  `logout()` 同时清除当前仓。
- **一处实现、两处挂载**：独立页侧栏由 `layout.js::render()` 挂载到 `#whPickBox`；
  工作台顶栏由 `app.html` 挂载到 `#wsWhPick`。iframe 内的 embed 模式不重复渲染。
- 切仓后工作台调用 `refreshAllTabs()` 重载全部 iframe，避免旧仓数据残留；
  独立页 `location.reload()`。
- **首屏竞态修复**：页面业务请求常早于切仓器异步初始化发出（那时还没有 header），
  因此首次确定当前仓后自动重载一次页面数据；后续不再重复（localStorage 已有值）。
- `users.html`：新增「数据范围」列与「数据权限」弹窗（仓库 / 货主双列勾选 +
  `all/grant` 切换时自动禁用勾选框）。
- `warehouses.html`：表单加「独立数据文件」`db_path` 分组，列表加「数据隔离」列。
- `carriers.html`：表单加「归属货主」下拉（留空＝全系统共享），列表加「归属货主」列。

### 兼容性与默认行为（clone 即用）

- 全部仓库 `db_path` 默认为空 → 所有请求复用同一个 `miniwms.db`，行为与此前版本完全一致；
  299 个既有 + 新增单测全绿，未登录仍可浏览（匿名演示模式显式 ALL）。
- 只有显式给某仓配置 `db_path` 才会创建独立 SQLite 文件；路径存相对路径，换机器 clone 不绑死本机目录。
- 分库启用后，跨仓主数据（货主 / 仓库）由中央库同步到业务库副本；仓内业务数据（物料、
  客户、供应商、承运商、单据、库存）各仓独立。
- 跨仓汇总报表不在本次范围，需要未来独立的只读汇总通道（A 作业视角不做一屏跨仓）。

### 验证结果

- `./.venv/Scripts/python.exe -m unittest discover -s tests` → **299 例全绿**（原 285 + 新增 14）。
- `tests/test_scope.py` 14 例：DDL 与显式 ALL、匿名模式开关、无授权账号 403、部分货主列表过滤、
  all/grant 切换、写路径越权 403、详情守门 403、承运商归属货主与过滤、部分仓授权与切仓拒绝、
  全部仓、`db_path` 路径校验、**按仓分库物理隔离**、管理员 `*` 显式权限。
- `node --check web_app/js/api.js` / `layout.js` 通过。
- 真实 Chrome E2E（独立临时库 + 独立端口 5107）**15/15**：侧栏 / 顶栏切仓器、
  header 确实带出、工作台不重复渲染、数据权限弹窗与勾选禁用联动、仓库 `db_path` 字段、
  承运商归属货主字段、7 页冒烟、无 JS 运行时错误、业务 404 为 0（favicon 噪声除外）。
- `/api/stock/reconcile` 未受影响（未改库存过账路径）。

### 已知边界 / 后续（PRD 素材提示）

- 授权按「账号」维度，未做角色级数据权限模板；`user.owner_id` 单值字段仍保留为历史预留，
  实际隔离以 `user_owner_grant` 为准。
- 跨仓报表、缓存 key 前缀、外部对接 X-API-Key 绑定 owner_id 属后续议题（M28 / M29）。
- `OWNER_SCOPED_TABLES` 白名单新增表时必须同步接入 `S.scoped_where()`，
  或纳入 `before_request` 的按 ID 守门；`PUBLIC_TABLES` 里的表不按货主隔离。
- 「看所有仓库」是 A 作业视角；若未来要 B 报表视角（一屏跨仓汇总），需另建只读汇总通道。

---

## 货主 × 仓库业务关系「格子」（2026-11-05，非里程碑架构增强 · 第二次优化）

> **用户口径（原文）**：「A 能够实现我之前的数据隔离 账号隔离也是可以，那你就继续优化，
> 后续在考虑其他的数据库」——确认**继续方案 A**（单运营方 + SQLite + 应用层隔离），
> 不引入 `Tenant`、不换 PostgreSQL。本轮只落实上一轮建议里「最值钱的一块」：
> 把可见范围从「可见仓 × 可见货主」笛卡尔积改成货主与仓库的**业务关系格子**。
> **不占用 M 编号**，Roadmap 与实施顺序不变，下一里程碑仍是 **M21 批次级库存明细层**。

### 业务背景与要解决的问题

用户在讨论 3PL SaaS 架构时给出矩阵，并强调「货主和仓库之间不是一对一关系，而是多对多关系」：
货主 A 在杭州仓 / 上海仓有库存、南京仓无业务；货主 B 在杭州仓 / 南京仓有库存、上海仓无业务。

上一轮交付（`6741cc7`）的账号授权是**两个集合相乘**：账号被授权「杭州 + 上海仓」和「货主 A + B」，
则 `B × 上海仓` 这个业务上根本不存在的组合也会被放行。`owner.authorized_warehouse` 是 M11 的
自由文本字段，既不是关系表也不是外键，无法承担这个判断。

- **权限回答的是**：这个人能不能看货主 B？
- **业务关系回答的是**：货主 B 在这个仓到底有没有业务？
- 两者必须**同时成立**，可见范围才能既安全又符合业务事实。

### 设计决策（含取舍理由）

| # | 决策 | 理由 |
|---|---|---|
| 1 | 新增 `owner_warehouse` 多对多关系表，放**中央库（控制面）** | 关系本身是跨仓的全局定义；业务库只是 `init_db()` 幂等带出的空壳，权威数据只有一份 |
| 2 | 过滤落点选 `before_request` 里统一收紧 `g.scope`，**13 个业务路由一行未改** | 授权解析 → 格子求交集 → 写守门 / 按 ID 守门，顺序固定；`S.scoped_where()` 与 `assert_owner_allowed()` 自动读到收紧后的 `owner_ids` |
| 3 | 启用条件 = 该仓至少 1 条 `active` 登记行 **且** 请求明确带当前仓 | 零配置 / 老库升级行为完全不变，保住「clone 即用」；不指定仓的脚本与后端调用走原行为 |
| 4 | 格子启用后 `owner_all` **强制置 False** | 「全部货主」也必须被业务关系约束，否则管理员绕过格子，优化等于没做 |
| 5 | 保存按仓 `PUT` 全量替换，请求体只有 `owner_ids` 数组 | 避免逐格 PATCH 留下半保存中间态；不带顶层 `owner_id` 也就不会误触发写路径归属守门 |
| 6 | 配置端点挂 `require_permission("user:manage")` | 与「用户 → 数据权限」弹窗同级，都是数据权限配置动作 |
| 7 | 保留 `owner.authorized_warehouse` 自由文本（M11 历史字段） | 不删、不改写、不迁移历史值，避免破坏既有 PRD 素材与老数据 |

### 数据模型

```sql
CREATE TABLE IF NOT EXISTS owner_warehouse (
    owner_id     INTEGER NOT NULL REFERENCES owner(id) ON DELETE CASCADE,
    warehouse_id INTEGER NOT NULL REFERENCES warehouse(id) ON DELETE CASCADE,
    status       TEXT    NOT NULL DEFAULT 'active',
    note         TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (owner_id, warehouse_id)
);
CREATE INDEX IF NOT EXISTS idx_owner_warehouse_wh
    ON owner_warehouse(warehouse_id, status);
```

DDL 放在 `main._migrate_scope_columns()` 的 `executescript` 里，与 `user_*_grant` 同批：
新库 CREATE 自带、旧库 `init_db()` 幂等补齐，无需人工迁移脚本。`status` / `note` 预留给
「暂停合作但保留关系」的后续场景，当前只有 `active` 参与过滤。

### 代码挂点

| 文件 | 改动 |
|---|---|
| `scope.py` | 新增 `owner_warehouse_grid()`（按仓读 active 登记）与 `apply_owner_warehouse_grid()`（原地收紧 scope）；`scope_summary()` 增加 `grid_enabled` / `grid_warehouse_id` / `grid_owner_ids`；`PUBLIC_TABLES` 纳入 `owner_warehouse`；模块 docstring 补第 ④ 层 |
| `main.py` | `_migrate_scope_columns()` 建表 + 索引；`_bind_scope()` 在 `g.warehouse_id` 确定后、写守门之前调用 `S.apply_owner_warehouse_grid(s, control, g.warehouse_id)`；注册 `ow_bp` |
| `routes_owner_warehouse.py`（新） | `GET /api/owner-warehouses` 返回矩阵（货主 / 仓库 / cells / enabled_warehouse_ids）；`PUT /api/owner-warehouses/<wid>` 按仓全量替换，含 id 存在性与类型校验 |
| `tests/test_owner_warehouse.py`（新） | 10 例，见下方验证清单 |
| `tests/test_scope.py` | `_clean()` 先清 `owner_warehouse`，避免跨测试残留触发外键约束 |
| `web_app/owner_warehouses.html`（新） | 矩阵配置页 |
| `web_app/js/layout.js` | 「系统管理 → 权限」新增叶子「货主仓库关系」；三级页 18 → 19 |

### 页面交互（PRD 素材 · 系统管理 → 权限 → 货主仓库关系）

- **查询条件**：无独立查询区；矩阵一次列出全部货主 × 全部仓库。表头每列显示仓库编码 / 名称、
  「已启用 / 未启用」状态与「全选」勾选框；全选框在每次加载后按该列实际勾选情况回显。
- **列表**：行 = 货主（编码 · 名称），列 = 仓库，单元格 = 原生 checkbox。
  勾选＝该货主在该仓有业务；无货主 / 无仓库时给出文字提示。
- **操作**：「刷新」重新拉取矩阵；「保存」逐仓调用 `PUT`，全部成功后再拉一次矩阵回显状态。
- **页面说明文案**（已写在页面上，评审时可直接引用）：某仓只要有一个勾就启用格子过滤，
  即便账号有「全部货主」权限，在该仓也只能看到勾过的货主；把某仓勾全部取消即关闭，
  退回纯账号授权；账号授权与格子取**交集**；未切换仓库的请求不启用格子。
- **权限**：需登录且具备 `user:manage`；未登录由 `requireLogin()` 引导到登录页。

### 兼容边界（PRD 权限章必须写明）

1. **未指定仓不启用格子**：不带 `X-WMS-Warehouse` 的请求（后端脚本 / 直接调 API）保持原行为。
   A 作业视角下前端一定会带 header，因此交互路径不受影响。
2. **零登记 = 功能未启用**：这是**业务关系功能开关**，不是授权上下文。账号授权层仍由
   `resolve_scope()` 严格执行，「授权行缺失 / 上下文缺失一律 403」的铁律没有被放宽；
   `owner_warehouse` 表缺失只表示「还没配置过关系」，不会把账号权限放大成 ALL。
3. **有登记即严格白名单**：该仓未登记的货主，列表 / 详情 / 写入三条路径全部被拒；
   清空该仓所有勾选立即关闭，无需重启、无需清缓存（授权每次请求实时解析）。
4. **按仓独立**：A 仓启用格子不影响 B 仓；`/api/scope/context` 会回显当前仓的
   `grid_enabled` 与 `grid_owner_ids`，便于前端提示与排障。

### 验证结果

- `./.venv/Scripts/python.exe -m unittest discover -s tests` → **309 例全绿**（原 299 + 新增 10）。
- `tests/test_owner_warehouse.py` 10 例：DDL 表与索引、零配置保持旧行为、ALL 账号被格子收紧、
  业务列表过滤、写路径 403、按 ID 守门 403、账号授权与格子取交集、格子按仓独立、
  配置端点回读与三类校验（不存在的货主 / 非数组 / 不存在的仓库）、清空后关闭过滤。
- `node --check web_app/js/layout.js` / `api.js` / 新页面内联脚本全部通过。
- 真实 Chrome E2E（独立临时库 + 独立端口 5109 + admin token）**13/13**：
  页面标题、矩阵表头 / 行数、初始未启用状态、侧栏切仓器、导航入口、格子渲染、
  保存后「已启用」与勾选回读、该仓视角只看到登记货主、清空后关闭并恢复两货主可见、无 JS 运行时错误。
- `/api/stock/reconcile` 等库存过账路径未改；本次只动上下文切面与主数据配置端点。

### 与未来路线（方案 B）的关系

- 用户确认 PostgreSQL / 多 Tenant / RLS「后续再考虑」，已写成 `docs/DOMAIN_BLUEPRINT.md` §9
  **未来演进路线、非当前实现**，明确触发条件为「第二家物流公司入驻」或「上真实生产」。
- `owner_warehouse` 本身就是方案 B 里 `Owner ↔ Warehouse` 多对多关系的落地，
  届时只需补 `tenant_id`；`scope.py` 切面、两张授权表、切仓协议均可复用，方案 A 不算白做。

---

## M21 批次级库存明细层（2026-11-06）✅

> **依据**：`docs/PUTAWAY_RECOMMENDATION.md` §5（两层模型 / 流水维度 / 三个不变式 / 回填）、
> §11 数据模型汇总、§14 阶段 B 实施计划（编号定稿，无未决疑问）。
> **用户口径**：2026-11-06「继续」＝按推荐先按 Roadmap 开 M21，PRD 逐页记录随里程碑追加。
> 设计文档是**提案**，本章只记录代码里真实落地的内容，差异处单独说明（见下）。

### 业务背景与要解决的问题

- M19 已能追溯「这批货从哪张 ASN、哪次验收入库来」，但库存账 `stock` 只有
  `(owner, product, location)` 一行，**同一个库位里混了哪几个批号、各剩多少、效期先后、
  是否专属某个客户**都答不出来。
- 后续 M22（按批号盘点 + 差异调整）、M24（库存转移只改批次信息）、M35（FEFO 波次）
  全部硬依赖「批次级库存明细」这一层。
- 用户已明确「老库存你看着办，目前还在编码阶段」→ 取消 v2 的灰度降级方案，
  **I2 / I3 一律硬校验**，演示库可 `--reset` 重建。

### 设计决策（含与提案的差异，取证纪律）

| # | 决策 | 理由 |
|---|---|---|
| 1 | `stock` 汇总层的**列与唯一键一个不动**，新增 `stock_batch` 作明细层 | 改主键会让 `/api/stock/reconcile` 的 I1 分组、`pick()` 单行可用量拦截、240+ 老测试全部崩塌（设计 §5.1） |
| 2 | `stock_movement` 扩 9 列，全部带默认值 | 老流水自动填 `''` / `0` / `NULL`，不参与批次分组但仍参与主账 SUM，I1 语义不变 |
| 3 | 一次物理动作涉及 N 个批次写 **N 条流水**，主账只 `UPDATE` 一次 | `Σ(N 条 qty_delta) == 该库位主账变动量`，I1 与 I2 同时成立 |
| 4 | **差异点**：提案 §5.3 写 `batches=None` 时「保留无维度老流水」；实现改为由 service 层**自动推断维度** | 「I2/I3 一律硬校验无豁免」与「老链路不写明细」在同一库里互相矛盾：只要有一次过账不同步明细，I3 立即 false。改后老链路也写带维度流水（入库用占位、出库按 FEFO 分摊、分配按已占用明细），主账 UPDATE 次数与流水总 `qty_delta` 不变，**309 例旧测试零改动全绿** |
| 5 | 兜底维度统一 `LEGACY-UNKNOWN`，与 `init_db()` 回填历史流水时用的维度一致 | 占位明细也能满足 I2，不需要 `LEGACY_NO_BATCH_DETAIL` 豁免标志，代码只有一套逻辑 |
| 6 | `align_stock_batch()` 在每次过账前把明细层对齐到汇总层的**变动前**余额 | 防御性自愈：兼容老测试夹具 / 手工造数，不让明细层漂移；正常链路为空操作 |
| 7 | **禁止路由层直写 `stock_batch`** | 只有 `routes_asn.receive` 显式传 `batches`（需要真实批号），其余四个动作由 `inventory_service` 内部自动推断，路由零改动 |

### 数据模型

```sql
CREATE TABLE IF NOT EXISTS stock_batch (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id                INTEGER NOT NULL REFERENCES owner(id),
    product_id              INTEGER NOT NULL REFERENCES product(id),
    location_id             INTEGER NOT NULL REFERENCES location(id),
    batch_no                TEXT    NOT NULL DEFAULT '',
    batch_key               TEXT    NOT NULL DEFAULT '',
    internal_batch_no       TEXT    NOT NULL DEFAULT '',
    designated_customer_id  INTEGER,
    goods_property          TEXT    NOT NULL DEFAULT '',
    stock_status            TEXT    NOT NULL DEFAULT '',
    expiry_date             TEXT    NOT NULL DEFAULT '',
    production_date         TEXT    NOT NULL DEFAULT '',
    qty                     REAL    NOT NULL DEFAULT 0,
    allocated_qty           REAL    NOT NULL DEFAULT 0,
    updated_at              TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_stock_batch ON stock_batch (
    owner_id, product_id, location_id, batch_no, batch_key,
    IFNULL(designated_customer_id, 0), goods_property, stock_status
);
CREATE TABLE IF NOT EXISTS batch_location (... 批次实物分布 ...);
-- stock_movement 幂等 ALTER 9 列：
-- batch_no / batch_key / internal_batch_no / designated_customer_id / goods_property /
-- stock_status / expiry_date / allocated_delta / operator
```

- 表达式唯一索引用 `IFNULL(designated_customer_id, 0)`，让 `NULL`（通用库存）与 `0` 视为
  同一维度，避免 SQLite 对 NULL 不加唯一约束导致重复行。
- 明细层同样**零行保留**：扣到 0 不删行，与汇总层口径一致。
- `production_date` 只在明细层，不进流水（流水 9 列口径）。

### 代码挂点

| 文件 | 改动 |
|---|---|
| `inventory_service.py` | 重写 `adjust_stock(..., batches=None, operator="")`；新增 `upsert_stock_batch()` / `align_stock_batch()` / `spread_batches()`（FEFO）/ `_release_allocation_items()` / `_insert_movement()`；`stock_up()` / `stock_move()` 支持批次并让 `stock_move()` 返回实际分摊列表 |
| `main.py` | 新增 `_migrate_m21_columns(conn)`：两张新表 + 表达式唯一索引 + 9 列幂等 ALTER + 一次性回填；在 `init_db()` 的 `_migrate_scope_columns()` 之后调用；`conn.row_factory = sqlite3.Row`（裸连接默认是 tuple，回填需按列名取值） |
| `routes_asn.py` | `receive()` 把 `record_receipt()` 提前，用返回的 `batch_no / batch_key / internal_batch_no / goods_property / expiry_date / production_date` 组装 `batches` 传给 `stock_up()`；`putaway()` 接收 `stock_move()` 返回的分摊列表写 `batch_location` |
| `routes_stock.py` | 新增 `GET /api/stock/batch`（批次余额，接入 `S.scoped_where()` 货主隔离）与 `GET /api/stock/batch-reconcile`（I2 + I3 硬校验） |
| `routes_dn.py` / `routes_transfer.py` / `routes_stocktake.py` | **零改动**：pick / ship / move / adjust 由 service 自动推断批次维度 |
| `tests/test_m21_stock_batch.py`（新） | 22 例，见验证清单 |
| 8 个既有测试文件 | `setUp()` 夹具补 `DELETE FROM batch_location` / `DELETE FROM stock_batch`，避免共享物理库下跨用例残留明细 |
| `web_app/stock_batch.html`（新） | 批次库存查询页（见下） |
| `web_app/js/layout.js` | 「仓储作业 → 库存与追溯」新增叶子「批次库存」；三级页 19 → 20 |

### 五个库存动作如何接钩子

| 动作 | 入口 | 维度来源 | 路由是否改动 |
|---|---|---|---|
| 验收入库 | `routes_asn.receive()` | `record_receipt()` 返回的真实批号与批次信息 | **是**：显式传 `batches` |
| 上架 | `routes_asn.putaway()` | `stock_move()` 从收货位按 FEFO 分摊，原样搬到目标位 | 只加 `batch_location` 写入 |
| 拣货分配 | `routes_dn.pick()` | `qty_delta=0` + `allocated_delta>0` → 按明细占用分摊 | 否 |
| 发货 | `routes_dn.ship()` | `qty_delta<0` → 按 FEFO 分摊，`qty` 与 `allocated` 同步释放 | 否 |
| 移库 / 盘点差异 | `routes_transfer.move()` / `routes_stocktake.adjust()` | 移库复用 `stock_move()`；盘盈走占位、盘亏按 FEFO 分摊 | 否 |

### 三层不变式与硬校验

| # | 不变式 | 端点 |
|---|---|---|
| I1 | `stock.qty == SUM(stock_movement.qty_delta)`，按 (owner, product, location) | `/api/stock/reconcile`（M6 已有，**语义原样保持**） |
| I2 | `stock_batch.qty == SUM(stock_movement.qty_delta)`，按 (owner, product, location, batch_no, designated_customer_id, goods_property) | `/api/stock/batch-reconcile`（新增） |
| I3 | `stock.qty == SUM(stock_batch.qty)`，按 (owner, product, location) | 同上 |

- 容差统一 `1e-9`；`allocated_delta` 只做审计，**不进 reconcile**。
- 端点返回 `consistent` + `i2` / `i3` 分段 `checked` / `mismatches`，
  任何不一致直接 `consistent=false`，**不返回任何豁免标志**。
- 明细层与流水若被人为篡改，分别由 I3 / I2 报出（有单测锁死）。

### 老库回填口径

- `init_db()` 里仅当 `stock_batch` 为空时执行一次：
  ① 把 `batch_no=''` 的历史流水统一改为 `LEGACY-UNKNOWN`；
  ② 按流水维度聚合生成明细行（`SUM(qty_delta)` / `SUM(allocated_delta)`）；
  ③ 对有 `stock` 行但无明细的三元组补齐占位明细（**含 qty=0 的零行**），
     并把 `allocated_qty` 双层对齐。
- 回填幂等；演示数据仍可用 `tools/seed_demo.py --reset` 重建。
- 好处：没有「灰度期豁免 → 收紧」两套逻辑，测试从第一天就能断言三个不变式。

### 页面（PRD 素材 · 批次库存 `stock_batch.html`）

- **用途 / 背景**：仓库管理员与质检演示需要看到「同库位里各批号剩多少、效期先后、
  专属客户与质量状态」，并一键确认主账与明细账是否自洽；M22 按批号盘点以此为基础。
- **查询条件**：批号（精确）、货品属性、仅非零库存、分页（20 条/页）。
- **列表字段**：ID / 批号 / 物料 / 库位 / 货主 / 在手 / 已分配 / 可用 / 失效日期 /
  货品属性 / 质量状态 / 指定客户（空显示「通用」）。
- **操作**：「查询」刷新列表；「账目核对」并行调 `/api/stock/reconcile` 与
  `/api/stock/batch-reconcile`，分三段显示 I1 / I2 / I3 是否通过及异常条数，
  不一致时用第一条 issue 提示。
- **页面说明文案**：汇总层与明细层的分工、三条不变式、「任何不一致都不豁免」。
- **权限**：列表接口接入 `S.scoped_where(..., table="sb")`，受限账号看不到别人的批次明细。

### 验证结果

- `./.venv/Scripts/python.exe -m unittest discover -s tests` → **331 例全绿**
  （原 309 + `tests/test_m21_stock_batch.py` 22 例）。
- 22 例覆盖：① DDL 表与表达式唯一索引、② 流水 9 列、③ `stock` 主键未变；
  ④ 老库回填生成占位明细、⑤ 回填幂等、⑥ 零行保留也有明细；
  ⑦ 收货写真实批次维度、⑧ 两次收货两行明细、⑨ 上架搬运 + `batch_location`、
  ⑩ 拣货分配下沉明细、⑪ 发货 qty/allocated 同步释放、⑫ 移库保持批次维度、
  ⑬ 盘盈走占位、⑭ 盘亏按 FEFO 分摊；
  ⑮ 完整链路 I1/I2/I3 全通过、⑯ 篡改明细被 I3 发现、⑰ 篡改流水被 I2 发现、
  ⑱ 零行明细不删；
  ⑲ 列表 API 过滤、⑳ 货主隔离、㉑ 批次数量之和校验抛 `StockError`、㉒ FEFO 顺序。
- `node --check web_app/js/layout.js` / 新页内联脚本通过。
- 真实 Chrome E2E（独立库 + 端口 5110）**10/10**：页面标题、明细行数、真实批号、
  切仓器、导航入口、I1/I2/I3 全通过、批号过滤、仅非零库存过滤、空态、无 JS 错误。
- 诊断脚本独立库跑「收货 → 上架 → 拣货 → 发货」后三个 reconcile 全部 `consistent=true`，
  `stock_batch` 两端批号一致、`batch_location` 落一条。

### 已知边界 / 后续里程碑

- `designated_customer_id` 本轮**只建列与维度**，不提供录入入口、不参与
  M19 `batch_config` 的 `is_key` 指纹；录入与出库校验规则在 **M23**。
- `stock_status` / `goods_property` 四值扩展、质量状态变更、库存转移单的净零成对流水在 **M24**。
- 「分配后可用库存减少」已由明细层 `allocated_qty` 同步；「取消分配」按钮与状态机在 **M22**。
- 盘点差异本轮仍直接过账（M18 行为），**M22 改成人工点按钮生成库存调整单**，
  并移除 `adjust()` 旁路；本轮先保证明细层与流水跟着动。
- `batch_location` 目前只在上架时记录，跨库位拆批 / 回溯查询在后续章节按需扩展。

---

## M22 库存调整单 + 盘点增强（2026-11-06）✅

> **用户口径**：2026-11-06「继续完善」＝按推荐沿 Roadmap 开 M22；
> 18-E / 18-F 两个非阻塞口径按设计文档默认值执行（「按货位盘带能」＝按货位盘点；
> 部分盘点范围暂不做排除条件，只做维度间 AND 正向筛选）。
> 规格来源：`docs/PUTAWAY_RECOMMENDATION.md` v3.3 §7.6 / §14 / §18（18-A~18-D 早已确认）。

### 交付内容

| # | 决策 | 理由 |
|---|---|---|
| 1 | **数量变化的唯一手工入口＝库存调整单**，`/api/stocktakes/<id>/adjust` 直接过账旁路**移除**（保留 410 明确提示） | 用户原话「库存数量只能做库存调整，或者盘点单根据差异生成库存调整」；旁路会让「没有调整单却改了数量」继续存在 |
| 2 | 盘点差异**人工点按钮**生成 `draft` 调整单，`auto_adjust` 默认 0；审核通过才在 `BEGIN IMMEDIATE` 里过账 | v3.2 18-D 定稿；「差异按钮不自动过账」是验收标准 |
| 3 | 状态机 `open → frozen → adjusted → closed`；差异全 0 时**直接 closed**（不生成空单）；调整单审核成功把来源盘点单自动置 `closed` | §18.7 状态机原文；避免「已过账但盘点单还挂在待结案」 |
| 4 | 调整单被取消后盘点单保持 `adjusted`，**可重新生成** | §18.7「调整单被驳回时盘点单保持 adjusted，可重新生成」 |
| 5 | 冲销＝生成一张反向 `draft` 单（`source_type=reversal`），**仍需人工审核**；原单保持 `approved` | 冲销本身也是数量变化，必须同样可追溯、可审核，不能靠改原单状态回滚 |
| 6 | 方案配置在**创建盘点单时即快照**进 `config_override`，`freeze()` 再把生效范围 + 时间窗 + 行数写进 `scope_snapshot` | 「方案只是模板」；与 M19「改配置不重算历史指纹」同一原则，方案后续被改不影响历史单据 |
| 7 | 指定批号的调整单过账时，从 `stock_batch` **补齐完整批次维度**再传 `batches=` | 只传 `batch_no` 会让 `upsert_stock_batch()` 误判为「新维度」并插入一条负数明细，I3 虽仍相等但实物分布全错（本轮实测踩坑，见下） |

### 数据模型（`main.py::_migrate_m22()`，幂等）

- 新增 `stock_adjustment`：`code`（`ADJ-YYYYMMDD-NNN`）/ `source_type`（`manual | stocktake_diff | reversal`）/ `source_id` / `reason_code` / `status`（`draft → approved | cancelled`）/ `creator` / `approver` / `created_at` / `approved_at` / `note`。
- 新增 `stock_adjustment_line`：`owner_id / product_id / location_id / batch_no / delta_qty / qty_before / reason`。调整单头**不带** `owner_id`，列表隔离按明细行 `EXISTS` + `S.owner_where(scope, table="al")`。
- 新增 `stocktake_plan`：`scope_type`（`full | partial | dynamic`）/ `scope`（JSON 条件组）/ `blind_mode`（`none | hide_book | hide_diff`）/ `dynamic_baseline`（`last_closed | since_days | explicit | manual_window`）/ `lookback_days` / `movement_from` / `movement_to` / `movement_types`（JSON）/ `auto_adjust` / `include_zero` / `enabled`。
- `stocktake` 幂等扩 7 列：`plan_id / scope_type / blind_mode / config_override / scope_snapshot / adjustment_id / closed_at`。
- `stocktake_line` 唯一键从 `(stocktake_id, product_id, location_id)` 扩到 **4 列含 `batch_no`**：SQLite 不能 ALTER 表级 UNIQUE，检测旧 `sqlite_master.sql` 后按「RENAME 旧表 → 建新表 → 复制（`batch_no=''`）→ DROP 旧表」重建，历史行不丢；`init_db()` 重复执行不会再重建。

### 代码挂点

| 文件 | 职责 |
|---|---|
| `stocktake_service.py`（新） | `effective_config()` 解析方案 + 一次性覆盖；`clean_scope()` 范围维度白名单（**强制忽略 `owner_id` 覆盖**，防越权扩范围）；`collect_lines()` 编译条件组 SQL；`resolve_baseline()` / `_dynamic_keys()` 动碰时间窗与交易类型；`trial()` 试算 |
| `adjustment_service.py`（新） | `create_adjustment()` 制单（校验非零 delta、货主授权、主数据存在、记录 `qty_before`）；`approve_adjustment()` 逐行 `adjust_stock()`（`ref_id = adjustment_id`）；`cancel_adjustment()`；`reverse_adjustment()`；`_batches_for_line()` 补齐批次维度、盘亏在该批号内按 FEFO 分摊 |
| `routes_stock_adjustment.py`（新） | `/api/stock-adjustments` 列表 / 手工制单 / 详情 / `approve` / `cancel` / `reverse` |
| `routes_stocktake_plan.py`（新） | `/api/stocktake-plans` CRUD + `/trial`、`/<id>/trial` 试算；被盘点单引用时拒绝删除，引导改用「停用」 |
| `routes_stocktake.py`（重写） | `freeze()` 按方案生成行 + 落 `scope_snapshot`；新增 `count-sheet` / `diff-preview` / `generate-adjustment`；`count()` 只回 `counted_lines`；`close()` 作兜底结案；`/adjust` → 410 |
| `constants.py` | 新增调整单状态 / 来源 / 前缀、盘点范围 / 盲盘 / 动碰基准枚举、`SCOPE_DIMENSIONS`、`DYNAMIC_MOVEMENT_TYPES` |

### 四类盘点与真盲盘

- **全盘**：默认 `full`，主表 `stock a JOIN product p JOIN location l`，`book_qty = a.qty`，默认排除零行。
- **部分盘点**：`scope` 维度间 AND，数组走 `IN`、标量走 `=`；可用维度 `warehouse_id / zone_id / location_group（M34 前按 location.zone 文本）/ location_ids / loc_type / product_ids / category_l1 / category_l2 / main_category / product_type / batch_no / batch_key / goods_property / has_stock`。
- **按批号盘点**：出现 `batch_no` / `batch_key` / `goods_property` 任一条件时切 `batch_mode`，主表改 `stock_batch a`，按 `(owner, product, location, batch_no)` 分组取 `SUM(qty)`，同一库位多批次生成多行。
- **动碰盘点**：从 `stock_movement` 取 `DISTINCT` 动碰集合，排除 `ABS(qty_delta) <= 1e-9` 的纯分配流水；时间窗优先级＝手工 `movement_from/to` > 创建时 `baseline_at` > `since_days` > `last_closed`（同货主最近 `closed_at`）；`movement_types` 白名单来自 `MOVEMENT_*`；M21 的批次维度让动碰可自然升级为按批次动碰。
- **真盲盘分级接口（18-C 口径：仅录入时隐藏账存，复核可见差异）**：
  `GET /<id>/count-sheet` 在 `blind_mode != none` 时只返回 `line_id / sku / product_name / location_code / batch_no`；
  `POST /<id>/count` 只回 `counted_lines`；
  `GET /<id>`（复核）与 `GET /<id>/diff-preview` 照常返回 `book_qty / counted_qty / diff`。
  权限码 `stocktake:plan` / `stocktake:count` / `stocktake:review` / `adjustment:approve` 按规格「角色矩阵后置」，本版只留钩子。

### 页面（三级导航 20 → 22 页）

- `web_app/stocktake_plans.html`（新）：方案 CRUD、范围条件组 JSON 编辑、盲盘模式、**动碰时间窗 + 8 种交易类型多选**、试算行数。
- `web_app/stock_adjustments.html`（新）：列表 / 明细 / 手工制单（动态多行）/ 审核过账 / 取消 / 生成冲销单；支持 `?detail=` 从盘点页跳转。
- `web_app/stocktakes.html`（增强）：列表加方案列；创建弹窗加方案下拉并在创建后试算预估行数；明细按 `blind_mode` 切列（盲盘不渲染账存 / 差异列）；复核「查看差异」面板；显式「生成库存调整单」按钮；「查看调整单」入口。

### 踩坑与修正（本轮真实调试记录）

1. `_validate()` 创建方案时 `has()` 恒为真，未传 `enabled` 被写成 0 → 改为「仅当请求体显式给出时才覆盖，新建才填默认值」；PUT 漏传字段不再误置 0。
2. `update_plan()` 的 `SET` 拼接误用未定义变量 `v` → 改写成 `updated_at = datetime('now','localtime')` 字面量表达式，不参与参数绑定。
3. **批次维度补齐**：调整单行只带 `batch_no` 时，`upsert_stock_batch()` 因 `batch_key / goods_property` 为空而新建一条负数明细，测试 `test_25` 出现 `-3.0`；修 `_batches_for_line()` 从 `stock_batch` 真实行取完整 `BATCH_DIMS`，盘亏在该批号内按 FEFO 分摊、盘盈落到该批号已有明细。修后 I1/I2/I3 与实物分布同时正确。
4. M22 有意改变盘点行为，`tests/test_stocktake.py` 8 例断言与 `tests/test_m21_stock_batch.py` 2 例改为「生成调整单 → 审核」链路，并新增断言旧 `/adjust` 返回 410；这是**规格要求的语义变更**，不是回归。

### 验证

- `./.venv/Scripts/python.exe -m unittest discover -s tests` → **356 例全绿**（331 基线 + 新增 25 例 `tests/test_m22_adjustment.py`）。
- `node --check`：`layout.js` 与三个页面内联脚本通过。
- 真实 Chrome E2E：M22 新链路 **28/28**；M21 批次库存 **10/10**、格子矩阵 **13/13**、数据隔离全站 **ok=15 fail=[]** 均复跑无回归。
- E2E 覆盖：方案编辑回填 + 试算、盲盘表头无账存/差异、冻结按方案生成 1 行、保存盲盘不回差异、复核看差异、生成 draft 库存不变、审核后 10→7、盘点单自动结案、手工制单 draft 不过账、I2/I3 通过、无 JS 错误。

### 已知边界 / 后续里程碑

- 「取消分配」属于 **M24 库存转移单**（`inventory_transfer:unallocate`），本轮不含；M21 章末尾把它记为 M22 系笔误，以此处为准。
- `location_group` 在 M34 货位组主数据落地前按 `location.zone` 文本近似；部分盘点暂不支持排除条件（18-F 默认口径）。
- 录入人与复核人的权限分离只留钩子，角色矩阵后置；`auto_adjust` 字段已落库但**代码不实现自动生成**（规格要求人工按钮）。
- 盘点差异按批号分摊时，同一批号若存在多条不同 `goods_property` 明细，按 FEFO 选取；专属客户 / 质量状态四值 / 净零库存转移留 M23 / M24。

---

## M23 指定客户专属库存 + 销退口径（2026-11-09）✅

### 目标与用户确认口径

用户 2026-11-09 对计划中的 4 个编号疑问给出答复：

| # | 问题 | 答复 | 实现口径 |
|---|---|---|---|
| 1 | 质量状态「扩四值」扩哪个字段 | **B** | `goods_property`（批次维度，合格/不合格）与 `stock_status`（库位维度，非限制/质检/冻结）两字段独立，不改 `GOODS_PROPERTIES` 枚举；M23 只接链路与转换矩阵 |
| 2 | 出库校验「只落规则」的边界 | **A** | 纯函数 + 常量 + 单测，不接 `routes_dn.py`、不做页面提示，M35 分配时再接 |
| 3 | 销退缺外部批次号告警形态 | **A 变体** | 创建 / 验收一律 400 拒绝；确实没有可显式填 `/` 作为合法占位，走历史回溯兜底并写告警 |
| 4 | PRD 回溯节奏 | 按推荐 A | 回溯 #3–#21 不混进代码提交，另起独立提交；本轮只随里程碑补 M23 增量素材 |

**设计文档口径冲突的消解（重要）**：`docs/PUTAWAY_RECOMMENDATION.md` §5.2 / §204 写
「把 `goods_property` 扩为合格·待检·不合格·冻结四值」，但 §7.4 与 §420 又明确
「`stock_status`（库位维度）与 `goods_property`（批次维度）是两个独立概念，不能合成一个字段」，
§763 硬约束清单把四值并列。M21 代码里两列都已存在。按用户选 B，**以两字段独立为准**，
`GOODS_PROPERTIES` 保持 `("合格", "不合格")` 不变；M22 章末尾「质量状态四值留 M23」
的表述按本节修正（增量追加、不改原文）。

**§7.5 销退口径覆盖**：设计定稿写「销退但外部没传 → 沿用历史内部批次号 + 告警」（静默兜底）。
用户 2026-11-09 确认改为**缺号直接 400**，必须显式填 `/` 才允许走兜底；
设计 §7.5 原行保持定稿可追溯，实现以本节为准。

### 数据模型增量（不新增表，全部幂等补列）

`main.py::_migrate_m23(conn)` 复用 M22 的 `_ensure_columns()`，新库 DDL 已带列、
老库 ALTER 补齐，两条路径同一语义：

| 表 | 新增列 | 用途 |
|---|---|---|
| `asn` | `external_batch_no TEXT NOT NULL DEFAULT ''` | 销退单头登记外部传入的原内部批次号，作为明细行默认值 |
| `batch_receipt` | `designated_customer_id INTEGER` | 验收时的指定客户（批次维度留痕） |
| `batch_receipt` | `external_batch_no TEXT NOT NULL DEFAULT ''` | 本次验收实际采用的外部批次号（含 `/`） |
| `batch_receipt` | `warning TEXT NOT NULL DEFAULT ''` | `MISSING_EXTERNAL_BATCH_NO` 告警码 |

`stock_batch` / `stock_movement` 的 `designated_customer_id` / `stock_status` 两列
**M21 已建**，本轮只是把值真正写进去，唯一键与 I1/I2/I3 一条未改。
`asn_line.extra` 继续用 JSON 快照承载 `designated_customer_id` / `external_batch_no`，
不新增单据头字段、不建 `doc_kind` 枚举。

### 链路：验收时取值，创建时只是草稿

取值优先级 `payload > 明细行快照 > 单据头默认值`，与 §6.2「验收时人工填写」一致：

1. `create_asn()`：逐行跑 `validate_external_batch_no(doc_type, 行值 or 单头值)`；销退缺号 400；
   `/` 归一保留；指定客户可选，且必须 `customer.owner_id == asn.owner_id`。
   单头值单独 `UPDATE asn SET external_batch_no`，**不动 M16 起沿用至今的 INSERT 列序**。
2. `receive()`：在既有 `BEGIN IMMEDIATE` 事务内取三个维度（`designated_customer_id` /
   `external_batch_no` / `stock_status`），销退缺号直接 `raise ValueError` → 400 且整单回滚；
   `stock_status` 非法值同样 400。
3. `batch_service.record_receipt()`：写入 `batch_receipt` 三列；`/` 时不把占位值当内部批次号，
   交给 `_resolve_internal()` 的历史回溯兜底并落 `warning`；真实值则
   `internal_source=external`、无告警。
4. `inventory_service.stock_up(batches=[...])`：把 `designated_customer_id` 与 `stock_status`
   一起写进 `stock_batch` + `stock_movement`，**禁止路由层直写明细**这一 M21 纪律不变。
5. 上架 `stock_move()` 由 M21 `spread_batches()` 自动继承维度，专属归属不丢失。

专属与通用库存**永不合并**：`uq_stock_batch` 唯一键含 `IFNULL(designated_customer_id, 0)`，
两次收货（一次指定、一次留空）自然分成两行，I3 仍等于主账。指定客户**不加入**
M19 `batch_key` 指纹（设计 §6.2 口径），同一批货分给两个客户仍是一个批次档案。

### 规则层：`reservation_rules.py`（只落规则，用户选 A）

纯函数模块，不读 DB、不写 DB、不注册蓝图、不接 `routes_dn.py`、不做前端提示：

- `check_customer_reservation(stock_customer_id, dn_customer_id)` → `allowed / code / reason`；
  规则表：通用→任意允许、专属→同客户允许、专属→异客户或未指定客户拒绝
  `CUSTOMER_RESERVED_FORBIDDEN`。
- `reservation_rule_matrix()` / `preferred_consumption_order()`（先专属后通用，
  `implemented=False`）/ `quality_transition_matrix()` /
  `can_change_goods_property()`（不合格单向，回合格须特采审批标记）/ `stock_status_allowed()`。
- **边界固化测试** `test_16_reservation_rule_not_wired_to_dn`：专属库存发给别的客户，
  当前出库路由仍不拦截，明确留 M35 接入，避免把「只落规则」误读成「已实现校验」。

### 前端交互（用户授权「看着优化」，参考成熟 WMS 的条件字段 / 就地校验）

| 页面 | 增量 |
|---|---|
| `asns.html` | 单据头「原内部批次号」只在 `doc_type=销退` 时显示，切换其他类型自动隐藏并清空；「填 /（确实没有）」一键登记；提交前内联拦截缺号（抛错保留弹窗）；明细行加「指定客户」下拉（留空＝通用）；单据详情补「原内部批次号」与专属口径说明；明细批次流水加专属 / 缺号徽章，客户 ID 显示为「编码 · 名称」 |
| `asn_receive.html` | 销退上下文提示条（写明 `/` 与告警）、单据类型 chip；工具栏拆成「作业参数 / 批次维度」两段，新增指定客户、质量状态下拉与 `/` 快捷按钮；切换明细行自动带出草稿值；提交前内联校验；成功后回显告警；明细表新增「指定客户」列与徽章 |
| `stock_batch.html` | 指定客户由「客户 #N」裸 ID 改为彩色专属徽章（显示客户名）/ 灰色通用徽章；质量状态徽章化；新增专属客户与质量状态两个筛选下拉；说明补充「换客户须走 M24 转移单」 |

不引第三方依赖，全部原生 JS；`dns.html` 按用户选 A **不加任何提示或拦截**。

### 验证

- `./.venv/Scripts/python.exe -m unittest discover -s tests` → **375 例全绿**
  （356 基线 + 新增 19 例 `tests/test_m23_designated.py`）。
- 按新口径适配 1 例既有用例：`tests/test_batch.py::test_return_receipt_reuses_internal_batch_no`
  改为 `test_return_with_absent_marker_traces_and_warns`，显式填 `/` 并断言回溯结果 + 告警；
  「销退静默兜底」不再是无输入合法路径。
- `node --check`：`asns.html` / `asn_receive.html` / `stock_batch.html` / `asn_putaway.html`
  内联脚本全通过。
- 真实 Chrome E2E（独立库 + 端口 5112）：M23 **28/28**，覆盖条件字段显隐、`/` 一键登记、
  缺号内联拦截、草稿值带出、验收告警、专属 / 通用 / 质量状态徽章、客户筛选、无 JS 错误。
- 基线 E2E 复跑无回归：数据隔离 **15/15**、格子矩阵 **13/13**、M21 批次库存 **10/10**、
  M22 全链路 **28/28**。

### 已知边界 / 后续里程碑

- 出库校验规则**未接入** `routes_dn.py`，专属库存当前不阻止发给其他客户；M35 实现批次级
  `dn_pick` 时接入，并在 `dn_pick` 记录实际消耗的批次归属（设计 §6.2 落点）。
- 「优先专属后通用」与 FEFO 的优先级冲突仍是第 16 章待确认项，本轮不决定。
- 专属绑定 / 解绑 / 换客户必须走 **M24 库存转移单**（`customer_bind` / `customer_unbind` /
  `customer_rebind`），页面不允许直接改 `stock_batch.designated_customer_id`；
  `can_change_goods_property()` 只判定不写库，质量状态变更同属 M24。
- 销退单的 `original_dn_id` / `original_internal_batch_no` 交叉校验（设计 §7.5 补充建议）
  本轮未实现；当前只校验「必填 / `/` 占位」，不校验该外部号在本系统存在。
- PRD 回溯 #3–#21 共 19 页仍待补，按用户节奏另起独立提交，不与代码提交混在一起。

### 同日口径修订补充：专属库存风险提示 + 放行日志（2026-11-09，仍属 M23）

> **触发**：M23 交付后用户修订第 2 条确认口径：「专属库存发给其他客户是需要提示，
> 让用户操作是否要继续，可以选择继续也可以不继续。但要补事前提示 + 风险放行日志。」
> 并确认「填 `/` = 确实没有（放行 + 回溯兜底 + 告警）」理解无误，销退口径不动。
> **仍不拦截、不实现批次级分配、不改 FEFO 顺序**；M35 接入分配的边界不变。
> 本节覆盖上文「出库校验规则未接入 `routes_dn.py`」中「不接路由」的 v1 时点表述：
> 路由现接入**只读预检与留痕**，但**不接入拒绝逻辑**。

**[业务] 口径**

| 项 | 口径 |
|---|---|
| 风险判定 | 以「本单客户」对比「将消耗批次明细的 `designated_customer_id`」；通用库存或同客户＝无风险，异客户／未指定客户＝有风险 |
| 事前提示 | 明细页常驻提示条 + 点「发货」后的二次确认；两者都列出风险条数与明细 |
| 用户选择 | 「取消」＝不发任何写请求，库存不动、状态不变、日志不产生；「继续」＝正常扣减并逐条写审计日志 |
| 放行日志 | 每次继续都逐条留痕，含批号／物料／库位／专属客户／本单客户／数量／规则码／原因／动作／操作人／时间；只读可查，带货主隔离 |
| 确认标记 | 界面确认后继续 `risk_allowed`；API 直调继续 `risk_allowed_unack`，两者都放行，仅审计标记不同 |

**[功能] 页面增量（`dns.html`，进度表 #12 增量记在 PRD §2.7，不改 #12 原行）**

- 明细卡顶部 `#riskNotice`：无风险显示绿色「风险预检通过」；有风险显示黄色提示条，
  写明条数、不拦截、继续会留痕、取消不产生库存动作；已发货单据显示已发货说明。
- `doShip()`：发货前调只读预检；有风险时 `confirm()` 列出最多 8 条明细（超出显示
  「另有 N 条」），由用户决定继续／取消；继续才 POST `ship` 并带 `risk_acknowledged: true`。
- 「风险放行记录」表格：按当前 DN 过滤，JOIN 出物料／库位／客户名；动作显示
  「确认后继续」／「API 直调继续」；成功发货后自动刷新。

**[实现] 后端**

| 文件 | 增量 |
|---|---|
| `constants.py` | `RESERVATION_RISK_ALLOWED = "risk_allowed"` / `RESERVATION_RISK_ALLOWED_UNACK = "risk_allowed_unack"` |
| `reservation_audit.py`（新） | `inspect_dn(db, dn_id)` 只读预检；`record_risk(db, dn_id, risks, operator, acknowledged)` 与发货同事务落库；`_local_balances()` / `_consume_local()` 做内存余额快照的顺序模拟 |
| `routes_reservation_log.py`（新） | `log_bp`：`GET /api/reservation-logs`，支持 `dn_id` / `keyword` / 分页，JOIN 客户与物料名称，`S.owner_where(table="rl")` 货主隔离；**只读、无写端点** |
| `routes_dn.py` | 新增 `GET /api/dns/<id>/reservation-check`；`ship()` 读 `risk_acknowledged`，扣减前 `inspect_dn()` + `record_risk()`，响应带 `risk_count` / `risk_logged` / `warnings`；不抛风险拒绝 |
| `main.py` | `_migrate_m23()` 幂等建 `customer_reservation_log`（含 `idx_resv_log_dn` / `idx_resv_log_owner`）；注册 `log_bp` |

**[实现] 两个一致性修复（回归测试暴露的既有缺陷，非本次功能引入）**

1. **预检批次与实扣批次对齐**：`ship()` 按 `dn_pick` **逐条** `adjust_stock()` 扣减，
   而一次性预检若直接对每条 pick 调 `spread_batches()`，第二条仍看到未扣减的账，
   会把风险重复记到第一批（或漏报第二批）。`reservation_audit.inspect_dn()` 改为在
   **本地余额快照**上按发货顺序逐条模拟扣减；`inventory_service.spread_batches()` 新增
   可选 `rows=` 参数，让预检与实际扣减共用同一个 FEFO 分摊函数，默认路径行为不变。
2. **分次拣货的分配／释放对称**：`spread_batches(for_allocation=True)` 原来按在手
   `qty` 分摊，不看已分配余量，同一明细行会被重复分配；发货按在手顺序扣减时会去释放
   一条 `allocated_qty = 0` 的明细，直接 400。现改为按 `qty - allocated_qty` 计算
   可分配容量，扣减动作仍按 `qty`。修复后「同一出库单行分两次拣同一货位」可正常发货，
   明细层 `allocated_qty` 归零，I1 / I2 / I3 仍全绿。

**[实现] 事务与隔离**

- 日志写入与扣减、状态流转在 `BEGIN IMMEDIATE` 同一事务内；发货失败／重复发货（409）／
  用户取消均不产生脏日志。
- 预检端点只读，不写库；日志查询按货主归属隔离，与业务列表同一套授权集合。
- `reservation_rules.py` **保持纯规则层**，不读库、不写库、不接分配；风险判定只是
  调用它的 `check_customer_reservation()`。

### 验证（补充修订）

- `./.venv/Scripts/python.exe -m unittest discover -s tests` → **390 例全绿**
  （375 基线 + 新增 15 例 `tests/test_m23_reservation_audit.py`）。
- 专项 `tests.test_m23_reservation_audit` **15/15**：表结构、只读预检、放行留痕、
  取消不动账、重复发货不重复记、查询过滤、事务回滚、分次拣货两向一致性。
- 真实 Chrome E2E（独立库 + 端口 5115）：**26/26**，覆盖事前提示条、二次确认
  「继续／不继续」两条路径、取消后库存与状态不变、继续后发货成功与日志回显、
  客户名映射、无风险绿色提示、I1 / I2 / I3、分配归零、无 JS 错误与业务 404。
- 基线 E2E 复跑无回归：数据隔离 **15/15**、格子矩阵 **13/13**、M21 **10/10**、
  M22 **28/28**、M23 v1 **28/28**；`node --check` 五个页面内联脚本全通过。

---

## M24 库存转移单（2026-11-09）✅

### 开工前口径确认（用户 2026-11-09 四条全选推荐 A）

| # | 疑问 | 确认 |
|---|---|---|
| 1 | 「主账零 UPDATE」与跨库位转移冲突 | **A**：只做**同库位内**的批次维度变更，跨库位一律走 M17 移库单；`from/to_location_id` 不落库位差，主账 `stock.qty` 真的零 UPDATE |
| 2 | 转移类型范围 | **A**：7 类一次做全，但底层统一为「源维度组合 → 目标维度组合」的净零搬迁，类型只作展示与校验标签，不写独立分支 |
| 3 | 「取消分配」到底指哪个 | **A**：只做 IT 单自身的 `unallocate`；DN 拣货分配取消不做（留 M35 或另立需求） |
| 4 | 权限码与「特采审批」深度 | **A**：6 个权限码只落常量 / 注释不挂装饰器（与 M20 一致）；特采只接布尔标记 + `reason_code=特采`，不做独立审批流 |

另外用户确认「填 `/` = 确实没有（放行 + 回溯兜底 + 告警）」理解正确，销退口径不动。
设计文档 line 47 修订表仍写「关闭单据才过账」，与 §7.2 / §16.1「审核即 `approved` 终态」矛盾；
本轮按 **v3.2 定稿（审核即终态）** 实现，设计原文保留用于追溯。

### 实现

**[实现] 三张表（`main.py::_migrate_m24()`，幂等建表，老库零迁移）**

| 表 | 作用 |
|---|---|
| `inventory_transfer` | 单据头：`code`（`IT-YYYYMMDD-NNN`）/ `transfer_type` / `reason_code` / `status` / `reversal_of_id` / 四个动作人与四个时间戳 / `business_at` / `note`；**无 `owner_id`** |
| `inventory_transfer_line` | 源维度 8 列 + 目标维度 8 列 + `owner_id` / `product_id` / `location_id` / `stock_batch_id` / `qty` / `qty_before` / `qty_after` / `allocated` |
| `inventory_change_log` | 字段级变更文档（`field_name` / `old_value` / `new_value` / 操作人 / 审核人 / 业务时间 / 原因码） |

维度 8 列与 `inventory_service.BATCH_DIMS` / `legacy_dims()` 对齐：
`batch_no` / `batch_key` / `internal_batch_no` / `designated_customer_id` /
`goods_property` / `stock_status` / `expiry_date` / `production_date`。

**[实现] 常量（`constants.py`）**：5 个状态、7 类 `IT_TYPES`、7 个原因码、
6 个 `IT_PERMISSIONS`、3 个流水类型（`it_allocate` / `it_unallocate` / `it_transfer`）、
`IT_ERROR_QTY_NOT_ZERO` 与 4 个禁用数量字段。

**[实现] 服务层 `inventory_transfer_service.py`（唯一过账入口仍为 `inventory_service`）**

| 动作 | 前置 | 过账 |
|---|---|---|
| 制单 | — | 只建 `draft` 空单，**不含明细、无流水** |
| 分配库存 | `draft` + 无行 | 逐行 `adjust_stock(qty_delta=0, allocated_delta=+Q, batches=[源维度 qty=0/allocated=Q])`；主账与明细两层 `allocated_qty` 同时增加，可用库存真实减少 |
| 取消分配 | `allocated` | 对称释放两层占用、写负 `allocated_delta` 流水、删明细行、回 `draft` |
| **审核** | `allocated` + 至少一行 | `adjust_stock(qty_delta=0, allocated_delta=-Q, batches=[源维度 qty=-Q/allocated=-Q, 目标维度 qty=+Q/allocated=0])`；`Σ qty_delta = 0` 硬校验，释放分配，逐字段写 `inventory_change_log`，进入终态 `approved` |
| 关闭 | `draft` + `COUNT(line)=0` | 无任何库存动作；`closed` |
| 删除 | `draft` 或 `closed` + `COUNT(line)=0` | 无任何库存动作；标记 `cancelled`（不物理删除，保留审计） |
| 冲销 | `approved` | 生成 `reversal_of_id` 反向 `draft` 空单 + `suggested_lines`（源/目标维度对调），仍需分配 + 审核 |

- **主账 `stock.qty` 零 UPDATE**：`inventory_service.adjust_stock()` 增加分支，
  `abs(qty_delta) <= EPS` 时只 `UPDATE stock SET allocated_qty = ?`，不 SET `qty` 列；
  其它调用数值行为不变（原分支只是把 qty 写成同值）。
- **A2 统一模型**：`_validate_line()` 从 `stock_batch` 读源维度，目标维度＝源维度 + 覆盖传入的
  `to_*` 字段；7 类 `transfer_type` 只影响展示与原因码，不进入过账分支。
- **A1 跨库位**：出现 `to_location_id` / `from_location_id` 且与源库位不一致 → 400
  「M24 只支持同库位内的批次维度变更；跨库位请走 M17 移库单」。
- **A4 特采**：`approve` 前的行校验调用 `reservation_rules.can_change_goods_property()`
  与 `stock_status_allowed()`；`special_approval` 只由 `reason_code == '特采'` 推导，不建审批表。
- **数量字段拦截**：`check_forbidden_qty_fields()` 在顶层与每行拒绝
  `delta_qty` / `qty_adjust` / `to_qty` / `qty_delta`，返回 400 +
  `code=TRANSFER_QTY_MUST_BE_ZERO`；数量变化只走 M22 库存调整单。
- **隔离**：单据头无 `owner_id`，列表按 `EXISTS (明细行 owner_where)` 注入，
  与 M22 调整单同一口径；行校验走 `assert_owner_allowed()`。
- **事务**：`routes_inventory_transfer._run()` 统一 `BEGIN IMMEDIATE`，任何异常整单回滚。

**[实现] 路由 `routes_inventory_transfer.py`**：`GET/POST /api/inventory-transfers`、
`GET /api/inventory-transfers/<id>`、`POST /<id>/{allocate,unallocate,approve,close,delete,reverse}`。
6 个权限码按 A4 只在常量与注释中出现，**不挂 `@require_permission`**。

**[功能] 前端 `web_app/inventory_transfers.html`**：列表（状态 / 类型 / 单号筛选）、
制单表单（7 类 + 原因码含特采 + 业务时间）、明细卡（12 项元数据、按状态显隐动作按钮）、
分配表单（源批次明细下拉显示可用量 + 数量 + 目标客户 / 货品属性 / 库存状态 / 批号 / 效期，
留空＝保持不变）、转移明细表（源→目标维度 chip）、字段级变更文档表。
冲销单可用「使用冲销建议」一键带入反向维度。导航 `layout.js` 新增「库存转移」节点（23 页）。

### 验证

- `./.venv/Scripts/python.exe -m unittest discover -s tests` → **415 例全绿**
  （390 基线 + 新增 25 例 `tests/test_m24_inventory_transfer.py`）。
- 专项覆盖：三表与索引、常量与权限码、空单分支、双层分配占用与超卖可感知、
  跨库位 / 跨货主客户 / 维度相同 / 可用量不足 / 数量字段拦截、
  `unallocate` 对称释放、已分配单禁止关闭删除、审核净零与主账不变、
  I1 / I2 / I3、变更文档、`approved` 终态、特采两向、冲销往返复原。
- 真实 Chrome E2E（独立库 + 端口 5116）：**35/35**，覆盖制单、分配、取消分配、再分配、
  审核、变更文档、冲销建议一键分配、冲销复原、空单关闭、数量字段 400、无 JS 错误与业务 404。
- 基线 E2E 复跑无回归：数据隔离 **15/15**、格子矩阵 **13/13**、M21 **10/10**、
  M22 **28/28**、M23 v1 **28/28**、M23 风险放行 **26/26**；`node --check` 六个页面内联脚本
  与 `layout.js` 全通过。

### 已知边界 / 后续里程碑

- **不新建 `batch` 档案**：A2 的统一搬迁只在 `stock_batch` 明细层把数量从旧维度组合搬到新维度组合，
  并写净零流水与变更文档；设计 §7.1 A 类提到的「`build_batch_key` 重算指纹 → 生成/复用新 `batch` 档案」
  本轮未实现，`to_batch_key` 由单据显式给出。批次档案联动留后续。
- `location_move` 类型保留为展示标签，但 M24 内库位恒等；真正跨库位走 M17 移库单（M21 已同步 `stock_batch`）。
- **单据头无 `owner_id`**：列表隔离依赖明细行 `EXISTS`，因此**受限账号看不到自己创建的空单**；
  与 M22 调整单同一口径，未在本轮改变。
- 冲销单仍需走完「分配 → 审核」才生效，不自动过账；`reversal_of_id` 不可重复冲销。
- DN 拣货分配取消、M35 批次级出库分配与 FEFO 仍未实现；「先专属后通用」优先级冲突未决定。
- PRD 回溯 #3–#21 共 19 页仍待补，按用户节奏另起独立提交，不与代码提交混在一起。

---

## M25 上架推荐规则引擎（2026-11-10）✅

> 用户 2026-11-10 口径：**1A** 编号一个不动，只把实施顺序改为 `M25 → M26 → M35 → M36`；
> **2A** M25/M26 一次做全，拦截完全来自规则行 `limits`，代码零 hardcoded 红线，
> 种子只启用 7 行核心路径；**3A** 出库分配规则与上架规则共用一张 `putaway_rule` 表，
> 用 `rule_type` 分两类（M35 直接复用）；**4B** 独立复核单 + 作业页 + 登记差异按钮，
> 差异只留痕、不生成调整单（属 M36）。
> 本里程碑先交付 M25 后端（规则引擎 + 配置 API + 上架拦截），M26 编辑器另起提交。

### 交付清单

| 层 | 文件 | 内容 |
| --- | --- | --- |
| DDL | `main.py::_migrate_m25()` | `putaway_rule_set` / `putaway_rule` / `putaway_recommendation` 三张幂等表 + **5 个索引**（`idx_prs_status` / `idx_pr_set` / `idx_pr_type` / `idx_prec_asn` / `idx_prec_action`）；老库零迁移成本 |
| 常量 | `constants.py` | 2 类 `rule_type`、13 维条件组、9 种寻址步骤、9 条评分规则 R07–R15、8 项 limits 与拒绝码、3 种推荐日志 action |
| 引擎 | `putaway_rules.py`（新增，≈860 行） | 上下文构造 / 条件组匹配 / 策略包解析 / 规则行命中 / fallback 链寻址 / 8 步硬约束过滤 / 评分 / 可解释输出 / 推荐日志 / 过账校验 / 配置 CRUD / 种子规则 |
| API | `routes_putaway_rules.py`（新增） | 策略包与规则行 CRUD、编辑器数据字典、条件组试算、推荐日志查询 |
| 接入 | `putaway_service.py` | `recommend()` 改为分发器：有启用规则 → 规则引擎；零启用规则 → M20 简版；两分支都带 `engine` 字段 |
| 接入 | `routes_putaway.py` | 推荐日志 INSERT 后 `db.commit()`；异常 `db.rollback()` |
| 接入 | `routes_asn.py::putaway()` | 过账前调 `validate_location()`，按命中规则行的 `limits` 硬拦截 |
| 隔离 | `scope.py` | `putaway_recommendation` 归入 `PUBLIC_TABLES`（控制面统计，按 `asn_id` / `action` 过滤） |
| 测试 | `tests/test_m25_putaway_rules.py`（新增） | 32 例，覆盖 DDL / 常量 / 种子 / 条件组 / 优先级 / 策略包 / 推荐可解释 / 日志 / CRUD / 试算 / 拦截 / 三态 / 零配置退回 |

### 执行模型（设计 §10.1）

1. **策略包匹配**：货主+仓库精确 > 仓库默认（`owner_id` 空） > 系统默认（仓库空）；
2. **条件组匹配**：13 维 AND、每维单选/多选、`null` 与空串通配；按 `priority` 升序，**第一行命中即停**；
3. **fallback 链**：按 `steps` 顺序，第一个产出「过滤后非空候选」的步骤生效；
4. **硬约束过滤**：只执行规则行 `limits` 中已配置的检查项；命中约束按 pass/reject 执行、
   **不参与打分**（约束不能被权重买通）；
5. **候选内排序**：M20 六项打分保持不变，R07–R15 只有规则行 `score` 显式配置权重才叠加；
   绑定货位 +100 仍优先；同分按 `location.code` 稳定排序；
6. 全部失败 → 兜底行 priority 999 + `NO_CANDIDATE`。

13 维条件组：`doc_type / goods_property / is_virtual / is_direct / is_cold_chain / is_hazard /
external_doc_type / abc_class / owner_id / is_designated / zone_id / product_id / category_l1`。
9 种寻址：8 种预设（`zone_by_product / zone_by_code / fixed_location / empty_in_zone /
same_product / same_category / last_putaway / batch_location`）+ 第 9 种 `all_active`
（M20 兼容型，全部启用库位一起打分；删掉它即启用 v3.3 递降 fallback）。
8 项 limits：`loc_status / storage_class / quality_state / capacity /
mix_product / mix_batch / mix_owner / designated_customer`，各有独立拒绝码。

### 硬约束三态（本轮新设计，代码零 hardcoded 红线）

M20 的三类混放只是**推荐阶段排除候选**，用户仍可手工选目标货位绕过；过账从不拦截。
若 M25 上线就把混放设成过账硬拦截，既有演示与老单据（同一货位放不同货品 / 不同货主）
会大面积 400。因此 `filter_location(..., for_post=)` 让每项 limits 支持三态，
强度完全由规则行配置决定：

| 配置 | 推荐阶段 | 上架过账 |
| --- | --- | --- |
| `enabled=true` | 排除候选 | **拦截** |
| `recommend_only=true` | 排除候选 | 放行 |
| 两者皆假 | 不检查 | 不检查 |

`validate_location()` 传 `for_post=True` 只执行 enforce 级；推荐阶段默认 `False`。
种子 `normal` 行把 `mix_product` / `mix_owner` 设为 `recommend_only`（保留 M20 已验证的
推荐排序，同时老链路过账不回归），`mix_batch` 连推荐阶段也不排除（M21 明细层本身支持
同库位多批次，M22 按批号盘点与 M23 专属/通用分明细都依赖它）；
`loc_status / storage_class / capacity` 为 enforce 级。M26 编辑器把 `enabled` 打开即升级为
过账硬拦截，无需改代码。

### 种子规则（11 行，启用 7 行）

启用优先级 `5 / 8 / 10 / 20 / 25 / 50 / 999`：冷链分流（`ZONE-COLD`）、危险分流（`ZONE-NG`）、
不合格隔离、虚拟货位、直调暂存（`XDOCK-01`）、常规上架（默认）、兜底异常区。
另 4 行（30 待检冻结 / 35 EDI 分流 / 40 销退良品回流 / 60 A 类黄金拣货位）配置好但
`enabled=0`，M26 编辑器可直接启用。储区编码用库里真实存在的
`ZONE-SHARED / ZONE-COLD / ZONE-NG`，不引用设计 §10.3 的占位 code，避免 fallback 全空。
`seed_rules()` 只在策略包不存在时插入：**用户删掉规则行后重启不复活**，删光规则行即
`has_enabled_rules()` 为假，推荐退回 M20 简版、过账不再拦截。

### API（新增）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/putaway-rule-types` | 编辑器数据字典：2 类 rule_type / 13 维 / 9 种寻址 / 8 项 limits / 9 条评分 |
| GET/POST | `/api/putaway-rule-sets` | 策略包列表 / 新建 |
| PUT | `/api/putaway-rule-sets/<id>` | 修改策略包 |
| GET/POST | `/api/putaway-rules` | 规则行列表（可按 `set_id` / `rule_type` 过滤）/ 新建；校验 13 维与寻址类型合法性 |
| PUT/DELETE | `/api/putaway-rules/<id>` | 修改 / 删除规则行（删除即不再拦截） |
| POST | `/api/putaway-rules/trial` | 条件组试算，只读比对，不写库、不查规则表 |
| GET | `/api/putaway-recommendations` | 推荐日志分页查询 + action 统计 |

既有 `GET /api/putaway/recommend` 响应新增 `engine / rule_set / rule_set_id / hit_rule /
hit_priority / hit_condition / hit_step / step_name / fallback / recommendation_id`，
M20 原有字段（`recommendations / rejected / candidate_count / bound_location_count / sku` 等）
全部保留。

### 验证

- 全量 unittest **447 例全绿**（M25 新增 32 例；`test_doc_flags.py` 原「过渡期不拦截」
  用例改为「命中种子冷链规则 → 400；停用规则行 → 放行」）。
- 真实 Chrome E2E（独立库 + 端口 5117）：**34/34**，覆盖数据字典、种子 11 行、
  outbound 复用同表、试算、上架推荐页无回归、一键采纳、可解释字段、推荐日志落库、
  冷链 400 拦截、停用规则即放行、零配置退回简版、无 JS 错误与业务 404。
- 基线 E2E 复跑无回归：数据隔离 15/15、格子矩阵 13/13、M21 10/10、M22 28/28、
  M23 v1 28/28、M23 风险放行 26/26、M24 35/35。

### 已知边界 / 后续里程碑

- **M26 才有编辑器页面**：本轮只有配置 API 与数据字典，没有 `putaway_rules.html`；
  策略包拖拽排序、fallback 链可视化、推荐弹窗采纳/驳回、命中率报表留 M26。
  推荐日志的 `adopt` / `reject` action 常量已预留，写入端点未实现。
- **推荐日志未做货主隔离**：与规则配置同属控制面统计，按 `asn_id` / `action` 过滤；
  若 M26 需要按货主统计，需补 `owner_id` 列并注入 `owner_where()`。
- **种子混放默认不过账拦截**：需 M26 编辑器把 `mix_product` / `mix_owner` 的 `enabled`
  打开；`mix_batch` 默认连推荐也不排除（M21 明细层口径）。
- **`abc_class` 与 R12 无数据源**：13 维里 `abc_class` 恒为空，R12 评分恒不命中，
  不臆造分数；待 M40 工作量统计或周转率缓存落地后接入。
- **评分扩展默认关闭**：R07–R15 权重只在规则行 `score` 显式配置时叠加，
  避免 M25 上线改变 M20 已验证的排序口径。
- `putaway_rule.rule_type='outbound'` 只落了表结构与 CRUD 校验，**M35 才接入出库分配逻辑**。
- M36 复核按用户 4B 修订：独立复核单 + 作业页 + 「登记差异」只留痕，
  不生成调整单、不改库存；数量纠偏仍走 M22 调整单。
- PRD 回溯 #3–#21 共 19 页仍待补，按用户节奏另起独立提交，不与代码提交混在一起。

---

## M26 规则配置编辑器与推荐交互（2026-11-13）✅

### 目标与口径

用户 2026-11-13 指令「根据规划持续完善系统，如果有测试数据也写到系统里面，方便后续排查和查询」，
按 v4 实施顺序接 M25 之后的**下一棒 M26**（编号不动，其后 M35 → M36）。
设计依据 `docs/PUTAWAY_RECOMMENDATION.md` §14 阶段 F：多规则增删改排序、试算面板可用、
**改位必填原因码**、E2E 覆盖「推荐 → 采纳 → 上架」；以及 §8.4 v3.3 4A 两步法第二步
（M20 只做自由文本 `external_doc_type`，M26 补可配字典）。

本轮不做：出库分配逻辑（M35）、推荐日志货主隔离、打印、报表。M25 的过账拦截链路一行未改。

### 交付清单

| 层 | 文件 | 内容 |
| --- | --- | --- |
| DDL | `main.py::_migrate_m26()` | `external_doc_type_dict`（code / name / enabled / default_zone_id / note）+ 1 索引；6 条种子 `INSERT OR IGNORE`，老库零迁移成本 |
| 常量 | `constants.py` | `PUTAWAY_ACTION_*` 三态动作、`PUTAWAY_OVERRIDE_REASONS` 6 条改位原因码清单、`EXTERNAL_DOC_TYPE_DICT_SEED` |
| 引擎 | `putaway_rules.py` | `set_recommendation_action()` / `recommendation_rates()` / `reorder_rules()` / `delete_set()` / 字典 CRUD 五个函数 |
| API | `routes_putaway_rules.py` | 新增 5 类端点；`_run()` 支持 `NOT_FOUND → 404`；日志查询扩过滤与 `rates` |
| 隔离 | `scope.py` | `external_doc_type_dict` 归入 `PUBLIC_TABLES`（控制面配置，不按货主隔离） |
| 页面 | `web_app/putaway_rules.html`（新增） | 规则配置编辑器：策略包 / 规则类型 / 13 维条件组 / fallback 链 / limits 三态 / 评分 / 试算 / 字典 / 日志统计 |
| 页面 | `web_app/asn_putaway.html` | 一键采纳写日志、驳回与改位原因码、可解释字段上屏 |
| 导航 | `web_app/js/layout.js` | 三级导航 23 → **24 页**，「仓储作业 → 入库单据与作业 → 上架规则配置」 |
| 测试 | `tests/test_m26_rule_editor.py`（新增） | 13 例：字典 CRUD / 字符串 `"0"` 兼容 / 采纳驳回校验 / 同一行更新 / 404 / rates 与过滤 / 排序 UNIQUE / 删包保护 / 试算与字典回归 |
| E2E | `tools/e2e_m26.py`（新增） | 可复跑 Playwright 回归，23 项断言，独立端口 5118 + 临时库，绝不碰 5100 demo |
| 演示数据 | `tools/seed_module_demo.py` | 从「未跟踪」纳入版本管理，新增 M26 模块（见下） |

### 数据模型与配置语义

- **不改 M25 三表结构**。`putaway_recommendation.action` 的 `preview → adopt/reject` 是**同一行 UPDATE**，
  不插入第二条日志：命中率 / 采纳率 / 驳回率按日志最终状态统计，重复点击不会把一次推荐算成多次动作。
- `external_doc_type_dict` 只是**可选值清单 + 默认目标储区**：`asn.external_doc_type` 仍是自由文本，
  不建外键、不做强制枚举校验；字典不反写单据，避免老单据与 API 直调被卡住。
- `limits` 编辑器以当前规则行的原 JSON 为基底，只覆盖 `enabled` / `recommend_only` 两个键，
  保证种子里的 `loc_types` / `allowed_zone_codes` / `temp_class_from_product` 等参数不被抹掉。
- `reorder_rules()` 先把目标行 `priority` 挪到 `-1000-id` 负区间，再写目标值：
  `UNIQUE(rule_set_id, rule_type, priority, name)` 会让逐行 UPDATE 在中间态撞键，
  整个过程复用 `_run()` 的单个 `BEGIN IMMEDIATE` 事务。

### API（新增 / 扩展）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/putaway-recommendations/<id>/action` | `adopt` 必填整数 `chosen_location_id`；`reject` 必填 `override_reason`；不存在 id → 404 |
| POST | `/api/putaway-rules/reorder` | `{items:[{id,priority}]}` 批量写优先级，返回 `updated` |
| DELETE | `/api/putaway-rule-sets/<id>` | 默认策略包 / 含规则行 → 400；空包 → 200 |
| GET/POST | `/api/external-doc-types` | 列表另带 `override_reasons`；新建统一大写 code、重复 400 |
| PUT/DELETE | `/api/external-doc-types/<id>` | 修改（兼容浏览器 select 的 `"0"` / `"1"` 字符串）/ 删除 |
| GET | `/api/putaway-recommendations` | 扩 `asn_line_id` / `date_from` / `date_to` / `has_hit`，新增 `rates`（total / hit / preview / adopt / reject 与三个比率）；M25 的 `stats` 保持原样 |

### 页面与交互（PRD 素材 · 追加页 #26）

- **规则配置页**：顶部策略包下拉 + 新建 / 删除 + 规则类型切换（上架推荐 / 出库分配，后者 M35 复用）；
  规则行表展示优先级、条件组摘要、fallback 链、已启用硬约束、状态，支持拖拽行首手柄与 ↑/↓ 排序、
  编辑 / 启停 / 删除。编辑器四段：① 13 维条件组（布尔维度是/否下拉、多值逗号分隔、留空通配）、
  ② fallback 链增删与上下移（每步附加参数保留原 JSON）、③ 8 项 limits 三态勾选矩阵（显示拒绝码）、
  ④ 9 条 R07–R15 权重输入（留空 / 0 = 不叠加，保持 M20 排序口径）；另有只读试算面板。
  下方是外部单据类别字典维护表、推荐日志与 5 张统计卡（日志总数 / 规则命中率 / 采纳率 /
  驳回改位率 / 仅预览），日志支持 ASN / 动作 / 日期区间过滤并查看 Top-N 明细。
- **上架作业页**：推荐卡片顶部展示 `engine / rule_set / hit_rule / hit_priority / hit_step /
  recommendation_id` 可解释字段；「一键采纳」填目标货位并写 `adopt` 日志；「驳回本次推荐」与
  **人工改位**（有推荐候选却选了非采纳货位）都要求先选 / 填原因码，写 `reject` 日志后再过账。
  简版引擎（零启用规则，无日志 id）只提示、不阻断作业；过账接口本身保持 M25 原样。

### 演示数据（用户「写到系统里方便排查」）

`tools/seed_module_demo.py` 新增 `m26_rule_editor_data()`：通过**真实 API**（不直接写业务表）补
① 一条演示字典项（`DEMO-EDI`，带默认储区）、② 一条停用示例规则行（13 维条件组示例，不参与拦截）、
③ 把已有 preview 日志按哨兵标记为 `adopt` / `reject`（带原因码与操作人），使统计卡与日志页
打开即有可查数据；每步可重复执行、不 `--reset`、不删现有数据。执行前已备份 `miniwms.db`
到 `miniwms_backup_20261113.db`（该文件被 `.gitignore` 忽略，不入库）。

### 验证

- 全量 unittest **460 例全绿**（M25 447 + M26 新增 13），`test_m26_rule_editor.py` 单跑 13/13。
- 真实 Chrome E2E `tools/e2e_m26.py` **23/23**（端口 5118 / 5119 两次复跑）：编辑器渲染、13 维 / 8 项 /
  9 条控件、试算命中、规则行增→删、拖拽排序落库、字典 6→7、推荐日志 id、上架页可解释字段、
  一键采纳写 `adopt`、统计卡与日志表格、无 JS 错误、无业务 4xx。
- 前端 `node --check` 校验 `putaway_rules.html` / `asn_putaway.html` 内联脚本与 `layout.js` 全部通过。
- 演示库补数后 `I1 / I2 / I3` 一致性体检保持 OK（播种脚本 `consistency()`）。

### 踩坑与修正（真实调试记录）

- `jparse()` 原本只接受 JSON 字符串；编辑器「新增规则行」传入的是**数组对象**，
  `JSON.parse` 抛错后静默回退成 `[]`，导致 fallback 链空白。E2E 第 5 项断言抓到，
  改为「已是对象/数组则直接返回」后 23/23。
- `_migrate_m26()` 用到 `C.EXTERNAL_DOC_TYPE_DICT_SEED`，`main.py` 原先没有 `import constants as C`，
  补上顶层导入（constants 不反向 import main，无循环）。
- 浏览器 select 提交的 `enabled: "0"` 用 `bool()` 会误判为真，字典端点统一走 `_enabled_flag()` 兼容。

### 已知边界 / 后续里程碑

- 推荐日志仍属控制面统计，**未做货主隔离**；若 M35 或报表需要按货主统计，须补 `owner_id` 列并注入 `owner_where()`。
- `abc_class` 与 R12 无数据源、`rule_type='outbound'` 仍只落配置，**M35 才接入出库分配逻辑**。
- `external_doc_type` 字典不反写单据字段、不做强制校验；`default_zone_id` 目前只是配置项，
  尚未接入上架推荐的条件组自动填充。
- PRD 回溯 #3–#21 共 19 页仍待补，按用户节奏另起独立提交；M26 追加页 #26 单独提交，不与代码提交混在一起。

---

## M35 出库波次分配与 FEFO（2026-11-13）✅

> 用户 2026-11-10 **3A** 定稿：出库分配规则与上架规则**共用 `putaway_rule` 表**，
> `rule_type` 分 `putaway` / `outbound`；M35 范围＝轻波次 / FEFO / 先专属后通用 /
> 强管控批次维度 / 允许拆批与部分发货 / 跳批留痕 / 不合格冻结不可分配。
> 用户 2026-11-13 指令「依次功能做下去，不明确的先登记，明确的一直做」——
> 存疑项统一登记在 `docs/OPEN_QUESTIONS.md`，不中途打断、不回收编号。
> 本里程碑是 `M26 → M35 → M36` 顺序的第三棒；**下一棒 M36（独立复核单 + 作业页）**。

### 交付清单

| 类别 | 文件 | 内容 |
|---|---|---|
| 服务层 | `outbound_allocation.py`（新） | 候选批次快照、`outbound` 规则命中、四类红线、排序策略、拆批、跳批留痕、试算 / 实分配 / 取消分配、日志与波次查询 |
| 路由 | `routes_outbound_allocation.py`（新） | 6 个端点，`allocate` / `unallocate` 统一 `BEGIN IMMEDIATE`，`plan` 只读不开写事务 |
| 常量 | `constants.py` | `WAVE_PREFIX` / `OUTBOUND_STRATEGIES` / 4 类跳批原因码 / 动作与引擎标签 / 6 个权限码（只落常量、不挂装饰器，沿用 M24 口径） |
| 迁移 | `main.py::_migrate_m35()` | `dn_pick` 扩 10 列批次维度 + 波次号 + 规则 id、`dn.wave_no`、新建 `outbound_allocation_log` + 4 个索引；老库逐列 ALTER |
| 发货一致性 | `routes_dn.py` / `reservation_audit.py` | 新 `dn_pick` 带批次 → `ship()` 显式传 `batches=` 按记录扣减；老行（批号为空）回退 M21 FEFO 推断；风险预检直接用记录值，不再重算 |
| 隔离 | `scope.py` | `outbound_allocation_log` 与 M25/M26 日志同属控制面 PUBLIC（Q09 待答复） |
| 前端 | `web_app/outbound_allocation.html`（新） | 波次分配作业页；`dns.html` 加「波次分配作业」入口；`layout.js` 三级导航 24 → **25** 页 |
| 测试 | `tests/test_m35_outbound_allocation.py`（14 例）、`tools/e2e_m35.py`（27 项） | 单测 + 可复跑真实 Chrome E2E |
| 演示数据 | `tools/seed_module_demo.py` | 新增 `m35_outbound_allocation()`，幂等走真实 API 写本地 `miniwms.db` |
| 登记 | `docs/OPEN_QUESTIONS.md`（新） | Q01–Q13 待确认口径 + M35 落地复核，用户统一答复后再调整 |

### 服务层设计（三层不变式不动）

- **候选**：`stock_batch` 明细行 JOIN `location`，只看 `qty - allocated_qty > EPS`；
  `steps` 里的 `zone_by_code` / `fixed_location` 只解释候选库位范围，不寻址（Q02）。
- **红线统一收口** `_skip_reason()`：`goods_property` 非合格、`stock_status` 质检/冻结、
  库位停用或类型不符、`designated_customer_id` 与 DN 客户不匹配 → 4 类原因码，
  **跳批留痕与剩余量解耦**：先全量扫描红线批次再按排序分配，避免需求被前面的批次满足后
  `break` 导致后面的不合格批次静默消失。
- **排序**：默认 `own_first`（专属分组优先、组内 FEFO、空效期最后、货位/id 稳定），
  可切 `strict_fefo` 忽略专属分组；策略既可前端传参，也由命中规则行 `score.strategy` 覆盖。
- **规则复用**：零启用 `outbound` 规则行 → `engine='legacy'`，仍按安全默认分配，
  **不会因为没配规则就允许不合格库存出库**；命中则 `engine='rules'` 并写
  `rule_id` / `rule_priority` / `strategy` / `engine` 到日志与响应，便于按规则排查。
- **数量**：`qty_delta=0`，只改 `allocated_qty`；`stock.qty` 一次都不 UPDATE；
  显式 `batches=` 精确落到目标明细行；`dn_pick` 写完整 8 维 + `wave_no` + `rule_id`；
  `dn_line.picked_qty` 同步累加，DN 状态按「是否有已拣行」在 `open` / `picking` 间切换。
  允许拆批与不足量分配，缺量写 `short` 日志；**不新增部分发货状态**（Q06）。
- **取消分配**：对称释放 `allocated_qty`、删 `dn_pick`、回退 `picked_qty`，写 `unallocated` 日志；
  已发货 / 已取消的单据拒绝。
- **轻波次**：一次批量分配动作生成 `WV-YYYYMMDD-NNN`，**不建波次主表**（Q01），
  只在 `dn_pick` / `dn` / `outbound_allocation_log` 上记 `wave_no`；支持一次传多张 DN，
  试算时用本地快照按 `(owner, product)` 共享余额，实分配每行重新查库看最新占用。

### 端点（`routes_outbound_allocation.py`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/outbound/dict` | 策略 / 动作 / 跳批原因码 / 引擎 / 可分配质量 / 规则字典 / 权限码 |
| POST | `/api/outbound/plan` | 只读试算，与实分配同形结果，不写任何表 |
| POST | `/api/outbound/allocate` | 生成波次并真实占用，201 |
| POST | `/api/outbound/unallocate` | 按 `dn_id` 整单或 `pick_ids` 部分取消，对称释放 |
| GET | `/api/outbound/logs` | 分页 + `wave_no` / `dn_id` / `action` / 日期区间过滤 + `stats` |
| GET | `/api/outbound/waves` | 轻波次汇总（`dn_count` / `allocated_qty` / `log_count`） |

### 前端作业页

`web_app/outbound_allocation.html` 四块：① 分配参数（策略下拉、波次号选填、只读试算 / 执行分配），
② 待分配出库单（勾选、波次列、逐单取消分配），③ 分配结果（5 张统计卡 + 分配明细 / 跳批留痕 /
短拣三张表，展示批号、效期、专属客户、引擎与命中规则），④ 波次汇总 + 分配留痕查询
（波次 / 出库单 / 动作 / 日期过滤、分页、动作统计）。执行分配与取消分配都有二次确认。
`dns.html` 卡片头加「波次分配作业」按钮（`openWorkTab()`），工作台 iframe 与独立页都能打开。

### 演示数据（用户「写到系统里方便排查」）

`tools/seed_module_demo.py` 新增 `m35_outbound_allocation()`，通过真实 API 造：
① 一条**停用**的 `outbound` 示例规则行（编辑器素材，不改变既有分配），
② 一个「无库存演示物料」，③ 三张演示 DN —— `DEMO-M35-FEFO`（客户不匹配 → `skipped`）、
`DEMO-M35-OWN`（专属优先 → `allocated` 后再 `unallocated`）、`DEMO-M35-SHORT`（无库存 → `short`）。
本地 `miniwms.db` 现有 **6 条分配留痕**（allocated 2 / skipped 2 / short 1 / unallocated 1）、
**3 个波次**、`putaway_rule` 13 行；播种前备份 `miniwms_backup_20261113_m35.db`（`*.db` 不入库）。
`--check` 只读盘点各模块行数 + I1 / I2 / I3 一致性体检；表未迁移时显示「未迁移」而不是崩。

### 验证

- M35 单测 **14/14**（DDL / 参数 / 只读试算 / FEFO / 质量与状态 / 专属跳批 / 拆批短拣 /
  批次维度与波次 / 规则命中可解释 / 发货按记录批次 / 取消分配 / 老链路回归 / 已发货拒绝 /
  **老库迁移回归**）。
- 真实 Chrome E2E `tools/e2e_m35.py` **27/27**（独立端口 5120 + 临时库）：页面渲染、字典、
  DN 候选、试算不写库、跳批留痕、波次号、日志四类、批次维度、发货按记录扣减、
  I1 / I2 / I3、取消分配、`dns.html` 入口、无 JS 错误与业务 4xx。
- 全量 unittest、演示库一致性体检、`node --check`（新页面 / `dns.html` / `layout.js`）均通过。

### 踩坑与修正（真实调试记录）

1. **跳批静默消失**：`plan()` 原来在 `remaining <= EPS` 时 `break`，排在后面的专属批次
   根本没走到 `_skip_reason()`，`test_06` 抓到「专属跳批未记录」。改为先把红线候选全量扫进
   `skip_map` 并留痕，再按排序分配；诊断脚本确认 `check_customer_reservation()` 本身判定正确。
2. **零行明细取证**：`test_08` 用 `SELECT ... FROM stock_batch` 取到上架后暂存位保留的
   `qty=0` 零行，误判分配改了在手量；改为按 `location_id = 存储位 AND qty > 0` 过滤。
3. **老库迁移崩溃（关键）**：`_migrate_m35()` 给 `dn_pick` 加
   `created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))`，SQLite 的
   `ALTER TABLE ADD COLUMN` **不允许函数型默认值**，新库空表侥幸通过、
   有数据的 `miniwms.db` 直接报 `Cannot add a column with non-constant default`。
   M35 不依赖 `dn_pick.created_at`（留痕时间统一取日志表），移除该列并补
   `test_14_legacy_db_migration` 用「5 列 + 1 行」老结构锁住回归。

### 已知边界 / 下一棒

- 分配留痕仍属控制面统计、**未做货主隔离**（Q09）；`abc_class` 仍无数据源。
- 权限码只落常量、未挂 `@require_permission`（Q08）；`outbound` 规则的 `limits` 只作可解释标记，
  **不放松**质量/专属红线（Q02）。
- 「部分发货」未新增状态机：`ship()` 仍要求所有行拣满，短拣需人工补拣或取消余量（Q06）。
- M23 人工拣货/发货链路**仍不拦截**专属不匹配（只提示 + 风险放行日志），
  自动分配才跳过（Q04 优先级最高的待确认项）。
- `docs/OPEN_QUESTIONS.md` Q01–Q13 待用户统一答复；答复后以答复为准调整服务层/常量，
  不改 `stock` 主账粒度、不改 I1/I2/I3 硬校验、不回收里程碑编号。
- **下一棒 M36**：独立复核单 + 独立作业页 + 登记差异只留痕（不生成调整单、不改库存）。

---

## M36 出库复核（2026-11-16）✅

按 v4 实施顺序接 M35 之后的下一棒；用户 2026-11-10 **4B** 定稿:
独立复核单 + 独立作业页 + 「登记差异」只留痕,**不生成调整单、不改库存**。
Q17（按 `dn_pick` 行逐条复核）/ Q18（复核独立于发货）/ Q19（原因码常量）/
Q20（复核单与日志按货主隔离）四个新分歧登记在 `docs/OPEN_QUESTIONS.md`,按「当前默认」落地、不打断用户。

### 交付内容

| 类别 | 内容 |
|---|---|
| 表结构 | `main.py::_migrate_m36()` 三表:`dn_review`(复核单头 + 进度计数)、`dn_review_line`(复核行快照 8 维批次 + 应拣/实拣/判定 + UNIQUE(review_id,pick_id))、`dn_review_diff_log`(差异留痕);老库反复 `init_db()` 幂等 |
| 服务层 | `review_service.py`:`create_review()` 快照 `dn_pick`、`record()` 逐行复核、`complete()` / `cancel()`、差异只写日志;重复复核只留最新一条不重复计数,改判为通过另写 `diff_cleared` 留痕 |
| 路由 | `routes_dn_review.py` 8 端点:`/dict`、`POST /`(建单)、`GET /`(列表+状态统计)、`GET /<id>`、`POST /<id>/check`、`POST /<id>/complete`、`POST /<id>/cancel`、`GET /logs`;写操作统一 `BEGIN IMMEDIATE` |
| 前端 | `web_app/dn_review.html`(三级导航 25→26):生成复核单 / 逐行实拣+判定+原因码 / 进度统计 / 差异留痕 / 查询;`dns.html` 加入口 |
| 隔离 | `scope.py` 把 `dn_review` / `dn_review_diff_log` 列入 `OWNER_SCOPED_TABLES`(Q20 比控制面日志更严);`dn_review_line` 通过 `review_id` 继承父单隔离 |
| 演示 | `tools/seed_module_demo.py` 新增 M36 模块,本地库 2 张复核单(1 完成含差异 / 1 待复核)+ 1 条差异留痕 |

### 关键决策与踩坑

- **Q17 粒度**:按 `dn_pick` 行逐条复核(最细粒度),复核行快照完整 8 维 + `wave_no` + `rule_id`,
  差异日志直接定位到批次 + 库位;DN/行号同时保留便于汇总查询。
- **Q18 独立性**:复核单**不接** `dn.ship()` 状态机,发货无需等复核;E2E 验证复核前后 DN 状态一致。
- **差异只留痕**:`record()` 只 UPDATE 复核行 + INSERT `dn_review_diff_log`,
  绝不调 `adjust_stock()`、不生成 `stock_adjustment`、不写 `stock_movement`;
  数量纠偏仍走 M22 库存调整单。E2E 逐项验证「库存/调整单/库存流水/DN 状态」四不变。
- **重复复核不重复计数**:同一复核行再次登记差异时先 DELETE 旧日志再 INSERT 最新,
  `dn_review_diff_log` 按 pick_id 只保留一条当前差异;改判为通过则清掉差异日志另写一条 `diff_cleared` 审计轨迹。
- **冒烟暴露的字段错误**:建单 SQL 最初误从 `dn_line` 取 `dl.line_id`,实际 `dn_pick` 自带 `line_id`,已修正。
- **老库迁移**:三表都是全新 `CREATE TABLE IF NOT EXISTS`(无 ALTER),函数型默认值只在 CREATE 中,不踩 M35 的 ALTER 坑。

### 验证

- M36 单测 **17/17**(`tests/test_m36_dn_review.py`,含批次快照 / 状态机 / 差异只留痕 / 库存不变 / 老库迁移幂等 / 货主隔离)。
- 真实 Chrome E2E `tools/e2e_m36.py` **26/26**(独立端口 + 临时库,复跑 5126/5127)。
- 全量 unittest 全绿,I1 / I2 / I3 一致;演示库一致性体检 OK。

### 已知边界 / 下一棒

- 复核单与差异日志按货主隔离(Q20,与 M25/M26/M35 控制面日志口径不同,待用户统一答复);
  权限码只落常量未挂装饰器;原因码是常量非字典表(Q19);匿名模式 `operator` 留空串(Q14)。
- 复核差异**只留痕**,不自动生成调整单、不回填库存;「部分发货」仍需人工补拣(M35 Q06)。
- `docs/OPEN_QUESTIONS.md` Q01–Q20 待用户统一答复;答复后以答复为准调整服务层/常量,
  不改 `stock` 主账粒度、不改 I1/I2/I3 硬校验、不回收里程碑编号。
- **下一棒 M37 养护 / M38 拆单合单**(蓝图第 2 层剩余作业),M31–M34 基础资料新域仍后移。

---

## M37 养护管理（2026-11-16）✅

用户指令「全托管模式，能做的现做，不能做的整理好一起沟通」：按 Roadmap 第 2 层剩余作业推进 M37。
蓝图只定框架（温湿度 / 养护项目 / 周期 / 异常，GSP），细节未拍板项按 `docs/OPEN_QUESTIONS.md`
**Q21–Q25 当前默认**落地、编号登记，**全程未打断用户**。

### 交付内容

| 类别 | 内容 |
|---|---|
| 表结构 | `main.py::_migrate_m37()` 三表:`care_plan`(计划:货主/仓/库位范围 + 项目 + 周期 + 温湿度阈值 + 下次到期)、`care_task`(任务:快照阈值 + 执行结果 + 处置)、`care_log`(生成/执行/异常/处置/取消全链路留痕);全 CREATE IF NOT EXISTS,老库反复 `init_db()` 幂等 |
| 服务层 | `care_service.py`:计划 CRUD / 启停、按到期日生成任务(`UNIQUE(plan_id,due_date)` 幂等)、执行(温湿度必填+越界强制异常)、异常原因码必填、处置与取消;异常**只写日志**,不改库存/不生成调整单/不写流水 |
| 路由 | `routes_care.py` 14 端点:`/api/care/{dict,overview,logs}` + `/api/care-plans/{列表,新建,详情,编辑,启停}` + `/api/care-tasks/{生成,列表,详情,执行,处置,取消}`;写操作统一 `BEGIN IMMEDIATE`,按 ID 资源先过 owner 归属守门 |
| 前端 | `web_app/care.html`(三级导航 26→27 页):统计卡 / 计划表格与建改表单 / 到期任务生成 / 任务表格与执行面板 / 异常处置面板 / 留痕查询 |
| 隔离 | `scope.py` 把 `care_plan` / `care_task` / `care_log` 列入 `OWNER_SCOPED_TABLES`;主表带 `owner_id`,按 ID 动作由 `RESOURCE_OWNER_TABLES` + `_guard()` 双守门 |
| 演示 | `tools/seed_module_demo.py` 新增 M37 模块,本地库 2 计划 / 2 任务 / 5 条留痕(超限异常 + 处置 + 正常),`--check` 可只读盘点 |

### 关键决策与踩坑

- **周期生成**:目标日默认今天,逾期计划的任务到期日记为**目标日**而不是补造历史任务(Q22),生成后按 `cycle_days` 步进推进 `next_due_date` 到超过目标日,同一计划同一天幂等。
- **GSP 边界**:`TEMP_HUMIDITY` 项目实测温湿度必填;值越界时**不允许判定正常**(`THRESHOLD_EXCEEDED`),必须选异常原因码;异常处置只补说明与 handler,不纠偏库存(延续 M36 4B 精神)。
- **原因码 / 权限码**:8 类异常原因与 6 个权限码均为常量,不做字典表、不挂装饰器(Q19/Q08 口径延续)。
- **真实调试修正 4 处**:① 编辑计划时 `_resolve_warehouse()` 误取不存在的 `warehouse_code` 列 → 500,改为复用 `warehouse_id`;② 路由 `_run()` 成功返回 `(result, status)` 后错误分支不能叠加 status,改 keyword-only `status=` 参数;③ 前端执行面板缺 `btnCancel` id 导致 `openTask()` 抛 `Cannot set properties of null`,异常处置面板不出现,由 E2E 抓到并补 id;④ 播种幂等哨兵按 `code LIKE` 匹配不到系统生成的 `CP-` 单号,改为按 `note` 中的 DEMO 标记匹配。

### 验证

- M37 单测 **18/18**(`tests/test_m37_care.py`:字典 / 计划 CRUD / 参数校验 / 生成快照与幂等 / 正常与异常执行 / 越界拒绝 / 原因码必填 / 处置 / 取消 / 库存四不变 / 日志统计 / 货主隔离 / 老库迁移幂等 / 分页)。
- 真实 Chrome E2E `tools/e2e_m37.py` **30/30**(独立端口 5131 + 临时库,含越界 API 边界与处置面板)。
- 全量 unittest **509/509 全绿**;历史 E2E 复跑 M26 **23/23**、M35 **27/27**、M36 **26/26**;I1/I2/I3 一致,演示库一致性体检 OK。

### 已知边界 / 下一棒

- Q21–Q25 待统一答复(养护项目字典化 / 温湿度必填 / 逾期任务记目标日 / 异常不生成调整单 / 阈值来源),答复后以答复为准,不改 `stock` 主账粒度与 I1/I2/I3 硬校验。
- 养护任务不与库存批次联动:没有「按批号抽检」或「临期批次生成养护任务」的自动关联,只有计划范围(库位)与执行留痕;GSP 报表联动留 M39。
- 权限码只落常量未挂装饰器;匿名模式 `operator` 留空串;原因码常量非字典表。
- `docs/OPEN_QUESTIONS.md` Q01–Q25 待统一答复;PRD 回溯 #3–#21 共 19 页仍另起独立提交。
- **下一棒 M38 拆单合单**(蓝图第 2 层剩余可选作业),M31–M34 基础资料新域仍后移。

---

## M38 拆单合单（2026-11-16）✅

继续全托管无人值守推进：按 Roadmap 第 2 层最后一项作业实现「出库单据层拆单 / 合单」。
蓝图只写「拆单合单（可选）」未定业务细节，按 `docs/OPEN_QUESTIONS.md`
**Q26–Q29 当前默认**落地、编号登记，**全程未打断用户**。

### 交付内容

| 类别 | 内容 |
|---|---|
| 表结构 | `main.py::_migrate_m38()` 新增 `dn_order_log`(拆合留痕:动作/货主/客户/源单/目标单/逐行明细 JSON/操作人/备注) + 3 索引;`dn` 补 `origin_action` / `origin_parent_id` 两溯源列(常量默认值,避开 M35 函数默认值坑),老库反复 `init_db()` 幂等 |
| 服务层 | `dn_order_service.py`:`split_order`(按 parts 拆成多子单,源单扣减、拆空行删除、全拆空源单取消)、`merge_orders`(多单按物料汇总、总量守恒、源单作废保留追溯)、`_assert_splittable`(仅 open 且未拣货且无 dn_pick);数量容差 1e-9,与 I1/I2/I3 一致 |
| 路由 | `routes_dn_order.py` 6 端点:`/api/dn-order/{dict,candidates,split,merge,log,<id>/lineage}`;写操作统一 `BEGIN IMMEDIATE`,按 ID 资源 owner 守门,错误码分 400/404/409 |
| 边界 | 只改 dn / dn_line 数量与状态,**不动库存、不写 stock_movement、不生成调整单、不改 DN 拣货状态**(Q27);拆单可跨多次、同一物料累计拆出量受源单量约束;合单强制同货主 + 同客户(Q28) |
| 前端 | `web_app/dn_order.html`(三级导航 27→28 页):候选单列表(可拆合标识 + 多选)、拆单面板(源单/模式/动态明细行/数量)、合单面板、留痕查询(动作/单号过滤)+ 血缘 |
| 隔离 | `scope.py` 把 `dn_order_log` 列入 `OWNER_SCOPED_TABLES`,留痕查询注入 `owner_where()`;源单写路径 `assert_owner_allowed()`,子单继承货主 |
| 演示 | `tools/seed_module_demo.py` 新增 M38 模块:2 张 open DN → 拆 2 子单 → 合 1 单,本地库 2 条留痕 / 3 张溯源单;`--check` 可只读盘点,重复跑幂等 |

### 关键决策与踩坑

- **状态范围**:仅 `open` 且所有行 `picked_qty=0` 且 `dn_pick` 无记录可拆合(Q26),已发货 / 已取消 / 已分配一律拒绝(`STATUS_NOT_ALLOWED` / `ALREADY_PICKED` / `ALREADY_ALLOCATED`),避免破坏 M35 批次级拣货分配与 M36 复核快照。
- **守恒口径**:拆单 Σ 拆出 = 源单被拆行拆前量;合单 Σ 源单量 = 目标单量,容差 1e-9;全链路零库存流水(E2E 验证 stock / stock_movement / stock_adjustment / stock_batch 四表不变 + I1/I2/I3)。
- **溯源**:dn 加 `origin_action` + `origin_parent_id`,子单 note 写明「由 XXX 拆出 / 合并 XXX 生成」;源单合单后 `cancelled` 但**不删除**,留痕可查血缘(`/lineage` 以逗号边界精确匹配源单/目标单 ID)。
- **真实调试修正 4 处**:① `_clone_dn` INSERT 占位符多写 3 个(溯源字段在调用处单独 UPDATE),改对齐参数数量;② `_clone_dn` 漏写 `code` 列导致 `NOT NULL constraint failed`,补入 code 占位符;③ 单测 `_post` 默认期望 201,而拆合动作返回 200,按路径前缀区分;④ 演示脚本按 `DEMO-` 前缀找物料/客户,但演示库前缀是 `OW-TMALL`/`SKU-001`,改为按目标货主直接 SQL 查同货主主数据。
- **前端**:明细行按物料 id 动态生成输入框(`qty_<pid>`),E2E 首屏默认选中列表第一行(1 行明细),断言改为「选中 d1 后 2 行」而非首屏 2 行——业务正确,断言位置问题。

### 验证

- M38 单测 **19/19**(`tests/test_m38_dn_order.py`:字典 / 按量拆守恒 / 部分拆 / 全拆关单 / 越量拒绝 / 未知物料 / 重复物料 / 空 parts / 源单不存在 / 合单守恒 / 至少两单 / 跨货主 / 跨客户 / 已分配拒拆 / 已发货拒拆 / 留痕血缘 / 候选过滤 / 货主隔离 / 迁移幂等)。
- 真实 Chrome E2E `tools/e2e_m38.py` **29/29**(独立端口 5133 + 临时库,含跨货主/跨客户/越量 API 边界与库存四不变)。
- 全量 unittest **528/528 全绿**;历史 E2E 复跑 M37 **30/30**、M36 **26/26**、M35 **27/27**;I1/I2/I3 一致,演示库一致性体检 OK。

### 已知边界 / 下一棒

- Q26–Q29 待统一答复(拆合是否仅限未拣货 / 是否动库存 / 合单同货主同客户 / 子单是否独立波次),答复后以答复为准;不改 `stock` 主账粒度与 I1/I2/I3 硬校验。
- 拆单后子单是全新 open 单,**不自动继承**源单的分配/波次/复核状态(本就不允许已分配单拆合);合单后源单作废,不会自动合并历史出库流水(因未发货前拆合,本就无流水)。
- 权限码只落常量未挂装饰器;匿名模式 `operator` 留空串;拆合原因存储留痕 note,非字典表。
- `docs/OPEN_QUESTIONS.md` Q01–Q29 待统一答复;PRD 回溯 #3–#21 共 19 页仍另起独立提交。
- **下一棒 M39 GSP 合规报表**(第 4 层,批次追溯 / 效期 / 养护 / 验收复核留痕),M31–M34 基础资料新域仍后移。

---

## M39 GSP 合规报表（2026-11-16）✅

用户指令「继续完善」，全托管模式按 Roadmap 第 4 层推进 M39。蓝图只定框架
（批次追溯 / 效期 / 养护 / 验收复核留痕），细节未拍板项按 `docs/OPEN_QUESTIONS.md`
**Q30–Q33 当前默认**落地、编号登记，**全程未打断用户**。

### 交付内容

- **纯只读、零新表**：复用现有 `batch` / `batch_receipt` / `stock_batch` /
  `stock_movement` / `care_log` / `dn_review_diff_log`，不改库存、不写流水、不建调整单。
- **服务层** `gsp_service.py`：7 个只读查询函数 + 字典：
  - `expiry_report()` 效期四级分级（`expired` / `soon` / `normal` / `none`），
    近效期阈值 `days` 默认 30、可 0–365 覆盖；只统计 qty>0 在库批次，
    四级统计 + 剩余天数 + 货主隔离。
  - `trace_report()` 批次追溯：按 batch_no / batch_key / product_id 定位批次档案，
    三段链路（收货验收 / 在库批次 / 库存流水）各自独立注入货主条件。
  - `care_report()` / `receipt_report()` / `review_report()` 三类留痕（过滤 + 分页 +
    统计），`receipt` 含 M23 缺外部批号告警统计、`review` 含少货/多货统计。
  - `overview()` 概览四张卡 + 效期分级 + 养护异常 + 验收告警。
- **7 个端点**（`routes_gsp.py`）：字典 / 概览 / 追溯 / 效期 / 养护 / 验收 / 复核，
  统一 `GspError → 400`，全部走 `S.scoped_where()` 注入货主条件。
- **作业页** `web_app/gsp.html`（导航 28→29 页，新增一级「统计报表 / 合规与追溯」）：
  概览卡 + 五个 tab + 效期分级可视化 + 追溯三段链表 + 三类留痕过滤分页。
- **演示数据**：`tools/seed_module_demo.py` 新增 M39 模块，独立货主下写一组**做平**
  的批次 + 同维度流水 + 主账（效期四级 + 追溯链），I1/I2/I3 不破；本地库
  `stock_batch` 20 行、`--check` 两核对 OK、重跑幂等。
- **验证**：M39 单测 **15/15**、真实 Chrome E2E `tools/e2e_m39.py` **34/34**、
  全量 unittest **543/543**、历史 E2E M38 29/29 + M37 30/30 + M36 26/26 + M35 27/27
  复跑通过；I1/I2/I3 一致、报表零库存副作用。

### 关键决策与踩坑

- **只读边界**：GSP 报表不写任何业务表（Q32），也不做跨仓汇总（延续 A 作业视角），
  全部从当前业务库的现有表读，按货主隔离；上下文缺失由 scope 403 统一拒绝。
- **效期分级**：`expiry_date` 空串 / NULL / 非法日期一律归入 `none`（不误判过期）；
  `days_left<0=expired`、`<=days=soon`、其余 `normal`；qty=0 的零行保留不进预警。
- **追溯链按 batch_key 聚合**：`batch` 档案为主入口，三段明细各自独立查询并隔离；
  可按 batch_no 精确过滤（避免同 batch_key 下其他批号混入收货明细）。
- **E2E 做平账**：最初手工造数缺主账/流水导致 I1/I2/I3 FAIL（非报表问题），
  改为每条 `stock_batch` 配同维度 `stock_movement` + 补 `stock` 主账 = 批次累加后全过。
- **前端 tab 定位**：Playwright `button:has-text('追溯')` 命中侧边栏分组和 tab 多元素，
  改用 `get_by_role('button', name='追溯', exact=True)`。

### 已知边界 / 下一棒

- Q30–Q33 待统一答复（报表清单范围 / 近效期天数默认 / 追溯链深度 / 只读不改库存），
  答复后以答复为准；不改 `stock` 主账粒度与 I1/I2/I3 硬校验。
- 只汇总当前业务库（A 作业视角），不做跨仓合规汇总；效期分级基于 `stock_batch.expiry_date`
  与当天日期，不接 GSP 温区或养护计划的联动判定。
- 权限码 `GSP_PERM_VIEW` / `GSP_PERM_EXPORT` 只落常量未挂装饰器（Q08 统一处置）；
  无导出端点（CSV/Excel 仍归 M30 暂缓编号）。
- `docs/OPEN_QUESTIONS.md` Q01–Q33 待统一答复;PRD 回溯 #3–#21 共 19 页仍另起独立提交。
- **下一棒 M40 工作量统计**（第 4 层，按人 / 按节点 / 按时间段，依赖 M36 复核），
  M31–M34 基础资料新域仍后移；M28 经营报表编号保留可插入（**该棒已于 2026-11-16 交付，见下章**）。

---

## M40 工作量统计（2026-11-16）✅

用户指令「按计划自己优化、不用问、做完一个做下一个、无人值守」；按 Roadmap 第 4 层
推进 M40。蓝图只定「按人 / 按节点 / 按时间段」框架，细节未拍板项按
`docs/OPEN_QUESTIONS.md` **Q34–Q37 当前默认**落地、编号登记，**全程未打断用户**。

### 交付内容

- **纯只读、零新表**：`workload_service.py` 用一条 `WITH events AS (UNION ALL …)` 事件流
  统一 8 类现有留痕（stock_movement / care_log / dn_review_line / dn_review_diff_log /
  dn_order_log / putaway_recommendation / outbound_allocation_log / inventory_change_log），
  映射 16 类作业节点；不改库存、不写流水、不新建表。
- **三个视角 + 概览**：`by_operator()` 按人（次数 / 涉及数量 / 覆盖节点 / 活跃天数 /
  最近作业）、`by_node()` 按节点（次数 / 占比 / 操作人数 / 货主数）、`by_period()` 按
  day / week（ISO 周一起）/ month 分组并给节点分布；`overview()` 四张卡 + Top5 +
  近 14 天趋势；`events_report()` 明细分页用于下钻核对。
- **7 个只读端点**（`routes_workload.py`）：字典 / 概览 / 操作人下拉 / 按人 / 按节点 /
  按时间段 / 事件明细；统一 `WorkloadError → 400`，全部经 `S.scoped_where(table="e")`
  注入货主条件，上下文缺失由 scope 403 拒绝。
- **作业页** `web_app/workload.html`（导航 29→30 页，新增「统计报表 / 运营分析」）：
  概览 + 四 tab + 公共筛选（日期 / 操作人 / 节点）+ 日周月粒度切换 + 明细分页，
  所有视图复用同一份过滤口径。
- **演示数据**：M40 不新增业务数据，直接复用 M17–M39 已写入的留痕；
  `tools/seed_module_demo.py` 新增 M40 **只读验证**模块，本地库现有 78 条事件 /
  4 位操作人 / 16 类节点，`--check` 两核对 OK、重跑零副作用。
- **验证**：M40 单测 **18/18**、真实 Chrome E2E `tools/e2e_m40.py` **36/36**、
  全量 unittest **561/561**、历史 E2E M39 34/34 + M38 29/29 + M37 30/30 + M36 26/26 +
  M35 27/27 复跑全绿；I1/I2/I3 一致、报表零库存副作用；`node --check` 通过。

### 关键决策与踩坑

- **事件口径**：系统没有工时表（Q35），因此「工作量」= **留痕事件数 + 涉及数量**；
  涉及数量对库存流水取 `ABS(qty_delta)`，纯分配流水（qty_delta=0）取
  `ABS(allocated_delta)`，避免把拣货分配算成 0。
- **控制面日志的货主归属**：M25/M26/M35 日志表本身没有 `owner_id`，从父单据
  `asn.owner_id` / `dn.owner_id` 推导；`inventory_change_log` 先从 line 取、再回退
  同单据首行。这样报表比原日志的 PUBLIC 口径更严（Q34 登记）。
- **匿名操作人**：老数据与匿名演示 `operator` 为空，统一归并到「未记录」标签，
  仍可用于筛选与分组（Q37）。
- **时间段**：按 `created_at` 本地时间字符串切分，不做时区转换；周用 ISO
  `YYYY-Www`（周一起），月用 `YYYY-MM`（Q36）。
- **E2E 造数**：手写 `stock_movement` 时把 `dn_ship` 写成 `qty_delta=+4`、
  `move_out` 写成 `+2`，导致 I1/I2 不平；修正为 `-4 / -2` 并补做平的主账与
  批次明细后全绿——这是测试数据问题，不是报表问题。
- **前端 URL 拼接**：过滤串为空时把查询参数直接接到路径后（`/by-periodperiod=day`）
  造成 404；改为始终以 `?` 开头、参数用 `&` 追加。

### 已知边界 / 下一棒

- Q34–Q37 待统一答复（数据源范围 / 工时口径 / 时间段粒度 / 未记录操作人），
  答复后以答复为准；不改 `stock` 主账粒度与 I1/I2/I3 硬校验。
- 只汇总当前业务库（A 作业视角），不做跨仓对比；没有独立工时表与计时功能，
  不输出「人天 / 工时」估算；无导出端点（CSV/Excel 归 M30 暂缓编号）。
- 事件流为只读视图，历史留痕被物理删除则统计同步减少；不做快照归档。
- 权限码 `WL_PERM_VIEW` 只落常量未挂装饰器（Q08 统一处置）；控制面日志按父单据
  推导货主后纳入隔离，若用户希望与 M25/M26 的 PUBLIC 口径一致可调整 Q34。
- `docs/OPEN_QUESTIONS.md` Q01–Q37 待统一答复;PRD 回溯 #3–#21 共 19 页仍另起独立提交。
- **下一棒 M41 打印模板配置 + PDF 预览与内容编辑**（方案 v2 已定稿：零安装 /
  Chrome headless / 预览页可临时编辑单据内容 / 字段映射 + 可视化拖拽排版），
  M42 打印任务队列与自动触发仍后置；M31–M34 基础资料新域仍后移
  （**该棒已于 2026-11-16 交付，见下章**）。

---

## M41 打印模板配置 + PDF 预览与内容编辑（2026-11-16）✅

用户指令「按计划自己优化、不用问、做完一个做下一个、无人值守」；按 Roadmap 第 5 层
推进 M41。方案 v2 五项已在 `docs/DOMAIN_BLUEPRINT.md` §6 定稿，新增细节分歧按
`docs/OPEN_QUESTIONS.md` **Q38–Q40 当前默认**落地、编号登记，**全程未打断用户**。

### 交付内容

- **新表 `print_template`**：6 类节点（receive / putaway / pick / check / ship / label）
  各挂多模板、`is_active` 每节点唯一；`layout_json` 同一份 JSON 同时存 L1
  `content_template` 与 L2 `blocks`，`field_map` 存标签 / 列宽 / 可编辑开关。
  `main._migrate_m41()` 幂等建表 + `print_service.seed_templates()` 播种 6 张默认模板；
  用户删光某节点模板后不再复活，渲染回退内置默认排版。
- **服务层 `print_service.py`**：模板 CRUD / 生效切换；ASN / DN / 复核单三类单据取值
  （全部 `S.scoped_where()` 货主隔离，只读）；`merge_overrides()` 只认可编辑字段白名单；
  `render_html()` 生成内联 CSS 的 A4 HTML（L1 占位符优先，清空走 L2 区块）；
  `generate_pdf()` 调系统 Chrome headless `--print-to-pdf --no-pdf-header-footer`，
  临时目录用完即删，零新增 Python 依赖。
- **8 个端点**（`routes_print.py`）：字典 / 模板列表 / 新建 / 编辑 / 删除 / 生效 /
  预览 / PDF；`PrintError → 400`，PDF 返回 `application/pdf` 字节流。
- **两个页面**：`web_app/print_templates.html`（L1 字段 chip 插入占位符 + L2 原生
  HTML5 拖拽区块排序 / 增删 / 字段显隐 + 字段映射表）；`web_app/print_preview.html`
  （左侧镜像可编辑表单、右侧 PDF iframe，改字段 400ms 后自动重生成 PDF，点「打印」
  调 iframe 的 `window.print()` 弹系统对话框人工确认）。导航 30→32 页，新增一级
  「打印中心 / 打印配置」；`asns.html` / `dns.html` 列表行新增「打印」入口。
- **不回写边界**：预览 / 导出 PDF / 临时改数量·备注·收货人**不写任何业务表**，
  库存与主账零副作用；模板配置表是唯一写入对象。
- **验证**：M41 单测 **18/18**、真实 Chrome E2E `tools/e2e_m41.py` **31/31**
  （含 Chrome 生成 `%PDF-` 字节流、拖拽排序、临时编辑刷新 PDF）、全量 unittest
  **579/579**、历史 E2E M40 36/36 + M39 34/34 + M38 29/29 + M37 30/30 + M36 26/26 +
  M35 27/27 全绿；I1/I2/I3 一致；`node --check` 四个变更页面脚本通过。

### 关键决策与踩坑

- **一份 JSON 两层视图**：L1 有 `content_template` 时直接替换 `{{field}}` /
  `{{lines_table}}`，否则按 L2 区块渲染；两个编辑器改的是同一份 `layout_json`，
  避免两套模板互转丢数据（Q38 登记）。
- **临时编辑白名单**：只允许 `note / receiver / qty / line_note`，其余字段即使
  POST 覆盖也被忽略（单测验证 `code` / `product_name` 不会被篡改）。
- **Chrome 路径**：本机 `chrome` 不在 PATH，实际路径为
  `C:\Program Files\Google\Chrome\Application\chrome.exe`；服务层按
  `MINIWMS_CHROME → PATH → 常见 Windows 路径` 顺序探测，未找到时返回明确错误码。
- **老版本兼容**：先试 `--headless=new`，若未产出 PDF 再退回经典 `--headless`；
  PDF 头校验 `%PDF-` 防止把错误页当打印件返回。
- **E2E 造数**：DN 创建必须带 `customer_id`；默认模板被测试清空后要显式
  `seed_templates()` 重建基线（与 M25 规则种子同坑）；Playwright `drag_to()`
  在 headless 下未稳定触发 HTML5 drop，改用显式派发 `dragstart/dragover/drop`
  事件验证拖拽处理器。

### 已知边界 / 下一棒

- Q38–Q40 待统一答复（模板是否按货主隔离 / 临时编辑字段范围 / 打印快照留存），
  当前默认是全局模板 + 4 个可编辑字段 + 不留快照；答复后以答复为准。
- M42 打印任务队列与操作节点自动触发**仍后置**（蓝图 §6.5：等全部做完后用户额外补充），
  本里程碑只有手动「打开预览 → 改字段 → 生成 PDF → 人工确认打印」链路；
  不引 QZ Tray、不静默打印、不写 `print_job` 表。
- PDF 生成依赖本机 Chrome；无 Chrome 时预览与模板配置仍可用，仅 PDF 端点报错。
- 打印页不做像素级自由定位（蓝图 §6.4 推荐），只做区块级拖拽与字段显隐；
  临时编辑不生成快照、不留审计日志（Q40）。
- 权限码 `PRINT_PERM_CONFIG/VIEW/PDF` 只落常量、未挂装饰器（Q08 统一处置）。
- `docs/OPEN_QUESTIONS.md` Q01–Q40 待统一答复;PRD 回溯 #3–#21 共 19 页仍另起独立提交。
- **下一棒**：M31 司机与车辆主数据 / M32 产品类型字典 / M33 序列号台账 /
  M34 货位组主数据（基础资料新域，用户 11-10 1A 后移至今）；M28 经营报表编号保留、
  M29/M30 仍降级暂缓；M42 只在用户额外补充后启动。

---


## M31 司机与车辆主数据（2026-11-17）✅

用户指令「按计划自己优化、不用问、做完一个做下一个、无人值守」；按 Roadmap 第 1 层
「基础资料」推进 M31（蓝图 `docs/DOMAIN_BLUEPRINT.md` §4 第 1 层第 1 行：司机 / 车辆，
2026-11-10 1A 后移至今）。新增细节分歧按 `docs/OPEN_QUESTIONS.md` **Q41–Q44 当前
默认**落地、编号登记，**全程未打断用户**。

### 交付内容

- **两张新表**（`main._migrate_m31()` 幂等建表，无新增外部依赖）：
  - `driver`：编码 / 姓名 / 电话 / 证件号 / 驾驶证号 / 驾照类型（A1–E 八类）/
    驾驶证效期 / 承运商 / 归属货主 / 状态（启用·停用）/ 备注。
  - `vehicle`：编码 / 车牌号 / 车型（六类）/ 最大载重 kg / 长宽高 cm /
    保险效期 / 道路运输证效期 / 承运商 / 归属货主 / 状态 / 备注。
  - 两表 `owner_id` **可空**：`NULL`＝全系统共享运力，非空＝该货主专属（Q41）；
    归入 `scope.OWNER_SCOPED_TABLES` 与 `main.RESOURCE_OWNER_TABLES`。
- **服务层 `fleet_service.py`**：CRUD + 列表分页 + 效期报表；字段级校验（编码必填
  ≤32 位、日期格式、数值非负、`carrier_id` 外键存在性、状态枚举）；唯一校验
  （`code` 全局唯一；驾驶证号 / 车牌号非空唯一，空串可重复，更新排除自身，Q44）；
  `_expiry_level()` 只读计算四级效期分级。
- **9 个端点**（`routes_fleet.py`）：字典 / 效期报表 / 司机列表·新建·详情·编辑·删除 /
  车辆列表·新建·详情·编辑·删除；`FleetError → 400`，scope 缺失 → 403。
- **两个页面 + 共享脚本**：`web_app/drivers.html`、`web_app/vehicles.html`，
  共用 `web_app/js/fleet.js`（列表 + 弹窗表单 + 效期预警四档卡 + 证照类型切换）。
  导航 32→34 页，二级分类 10→11（新增一级「基础主数据 / 运力档案」）。
- **只读预警**：三类证照（驾驶证 / 车辆保险 / 道路运输证）× 四级分级
  （已过期 / 临近到期 / 正常 / 未登记），临近阈值默认 30 天、可配置（≤365 天）；
  **只读预警，不拦截业务、不生成任务、不自动停用档案**（Q43）。
- **验证**：M31 单测 **18/18**、真实 Chrome E2E `tools/e2e_m31.py` **44/44**
  （含弹窗建档、效期四档卡、证照类型切换、货主隔离、I1/I2/I3 一致、
  主数据零库存副作用、无 JS 运行时错误）、全量 unittest **597/597**、
  历史 E2E M41 31/31 + M40 36/36 + M39 34/34 + M38 29/29 + M37 30/30 + M36 26/26 +
  M35 27/27 + M26 23/23 全绿。
- **演示数据**：`tools/seed_module_demo.py` 新增 `m31_fleet()`（幂等、走真实 API），
  写入 3 名司机 + 3 辆车（含 1 名共享司机 / 1 辆共享车辆 / 已过期 / 临近到期
  各若干），证照 9 条分级 `expired 3 / soon 2 / normal 2 / none 2`；
  `--check` 表清单新增 `driver` / `vehicle`；播种后一致性两核对 OK。
  本地库备份 `miniwms_backup_20261117_pre_m31.db`。

### 关键决策与踩坑

- **共享运力用「可空 owner_id」而不是新表**：司机/车辆既可能是某货主专属运力，
  也可能是仓配共享运力；`owner_id NULL` 复用既有 `allow_null=True` 隔离口径，
  不引入第三种共享语义（与 M23 专属客户库存的 `uq_stock_batch` 唯一键同理）。
- **按 ID 访问也要守门**：列表用 `S.scoped_where(..., allow_null=True)`，
  但详情 / 编辑 / 删除是按主键直取，必须显式过 `assert_owner_allowed()`，
  否则受限账号可凭 id 越权读改他人运力档案（单测专门覆盖）。
- **证照效期不落库**：只存日期字符串，分级在查询时按 `today` 现算，
  避免「预警级别」随时间过期而陈旧、也不污染档案数据。
- **承运商单值外键**：`carrier_id` 单个可空外键而非多对多中间表 —— 本里程碑
  只做主数据，不接 ASN/DN 派工，建中间表属于过度设计（Q42）。
- **E2E 造数顺序**：司机/车辆要挂在真实货主与承运商下，先建承运商再建运力；
  效期报表按「证照类型」切换时行数会变（驾驶证 3 行 / 保险 3 行 / 运输证 3 行），
  断言要按实际口径写死而非按总数猜。

### 已知边界 / 下一棒

- Q41–Q44 待统一答复（归属隔离 / 承运商关联 / 效期是否硬拦截 / 唯一键范围），
  当前默认是可空 owner + 单值承运商 + 只读预警 + code 全局唯一；答复后以答复为准。
- 本里程碑**只做主数据**：不接 ASN/DN 派车、不做运力调度、不做运费计算；
  证照过期**不拦截**任何业务单据（Q43）。
- 司机与车辆之间**没有绑定关系**（谁开哪台车），需要时按「车牌号」在单据上手工选。
- 车辆不做年检 / 保养等更多证照类型，只做保险与道路运输证两类。
- 权限码 `FLEET_PERM_VIEW / FLEET_PERM_CONFIG` 只落常量、未挂装饰器（Q08 统一处置）。
- `docs/OPEN_QUESTIONS.md` Q01–Q44 待统一答复；PRD 回溯 #3–#21 共 19 页仍另起独立提交。
- **下一棒**：M32 产品类型字典 / M33 序列号台账 / M34 货位组主数据
  （基础资料新域剩余三棒）；M28 经营报表编号保留、M29/M30 仍降级暂缓、
  M42 只在用户额外补充后启动。

---

## M32 产品类型字典（2026-11-17）✅

用户指令「按计划自己优化、不用问、做完一个做下一个、无人值守」；按 Roadmap 第 1 层
「基础资料」推进 M32（蓝图 §4 第 1 层第 1 行：产品类型字典 / 序列号）。
新增细节分歧按 `docs/OPEN_QUESTIONS.md` **Q45–Q48 当前默认**落地、编号登记，
**全程未打断用户**。

### 交付内容

- **新表 `product_type`**（`main._migrate_m32()` 幂等建表 + 索引）：
  编码（UNIQUE）/ 名称 / 上级 `parent_id`（自引用，单父级）/ 排序 / 状态（启用·停用）/
  归属货主（可空）/ 备注；预置 8 个种子类型（成品·半成品·原料·包装材料·辅料·
  备品备件·耗材·其他），**用户删行不复活**（`INSERT OR IGNORE` 只补缺，不复活删除）。
- **服务层 `product_type_service.py`**：CRUD + 列表分页 + 字典 + 引用计数；
  层级校验（自引用 `PT_HIERARCHY_CYCLE`、第三级 `PT_MAX_DEPTH`）；
  唯一校验（编码全局唯一，更新排除自身）；
  引用保护（被货品引用时删除 `PT_IN_USE`、改编码 `PT_IN_USE`；有子类型删除 `PT_HAS_CHILDREN`）。
- **7 个端点**（`routes_product_type.py`）：字典 / 引用计数 / 列表 / 新建（201）/
  详情 / 编辑 / 删除；`ProductTypeError → 400`，scope 缺失 → 403。
- **货品改引用**（`routes_product.py`）：`product.product_type` 文本列语义从
  「自由文本」升级为「引用字典 code」；新建 / 编辑时未命中字典则**自动建行**
  （`ensure_type()`，继承该货品归属）；读取时额外返回 `product_type_name` 与
  `product_type_known`（查不到字典回落原值，不吞信息）。
- **老数据回填**（`product_type_service.backfill_from_products()`）：
  迁移时扫描货品上的历史自由文本，字典里没有的**原文本直接当 code 建行** ——
  货品取值一个字符都不改，`stocktake_service` 的 `p.product_type` 盘点维度与
  报表分组继续匹配（Q47）。
- **一个页面** `web_app/product_types.html`：列表（编码·名称·上级·排序·引用数·
  归属·状态）+ 两级缩进 + 上级下拉 + 引用计数列 + 使用说明卡；导航 34→35 页。
  `web_app/products.html` 的「产品类型」由文本框改为**字典下拉**，列表新增
  「产品类型」列（显示字典名）。
- **验证**：M32 单测 **25/25**、真实 Chrome E2E `tools/e2e_m32.py` **48/48**
  （含九类 API 校验、字典页弹窗 CRUD、删除被引用时的提示、货品页下拉联动、
  老数据回填、库存零副作用、I1/I2/I3 一致、无 JS 运行时错误）、全量 unittest
  **622/622**、历史 E2E M41 31/31 + M40 36/36 + M39 34/34 + M38 29/29 + M37 30/30 +
  M36 26/26 + M35 27/27 全绿。
- **演示数据**：`tools/seed_module_demo.py` 新增 `m32_product_type()`（幂等、
  走真实 API）：播种「成品（演示）」顶级类型 + 「瓶装成品（演示）」子类型，
  并给前 3 个演示货品挂上顶级类型；`--check` 表清单新增 `product_type`；
  播种后一致性两核对 OK。本地库备份 `miniwms_backup_20261117_pre_m32.db`。

### 关键决策与踩坑

- **不动 `product` 表结构**：M32 只加字典表，`product.product_type` 仍是 TEXT 列，
  语义改成「存字典 code」。这样 M1–M41 的既有迁移全部不用动，
  且**盘点方案里存的产品类型文本仍能命中**（维度口径零漂移）。
- **回填时老文本当 code，而不是重新编号**：如果给老类型生成 `PT-xxx` 新编码并
  改写货品，历史盘点方案（`stocktake_plan` 里存的是产品类型文本）会全部失配，
  报表分组也会漂。**老文本即 code** 是唯一零破坏方案。
- **自动建行而不是拒绝录入**：老前端 / 老脚本 / 人工仍可能填字典外的类型，
  直接 400 会阻塞业务。`ensure_type()` 先查后建，字典随业务逐步收敛。
- **不静默改写货品**：删字典行 / 改编码时若仍有货品引用，一律 400 拒绝，
  而不是把货品的类型悄悄改空 —— 静默改写会制造「无类型」的隐性脏数据。
- **层级只做两级**：产品类型实际用不到三层；限制两级后校验逻辑简单、
  前端缩进渲染也只有一层子行，比无限树更不容易出错。
- **错误返回体统一 `error` 字段**：与 M31 `routes_fleet._error()` 保持一致
  （前端 `api.js` 两种都兼容），避免同一项目里两种错误格式并存。
- **测试踩坑**：单测里 `PTS.backfill_from_products(_conn())` 若不 `closing()` 包住，
  连接不关会持有写锁 → 后续用例连锁 `database is locked`（共享物理库老坑，
  与 M25 同源）；老文本字典行若已被前一次运行建过，`made` 计数会是 0，
  断言要先清行再回填。

### 已知边界 / 下一棒

- Q45–Q48 待统一答复（归属与唯一键 / 层级深度 / 老数据回填 / 写入与删除保护），
  当前默认是「全局编码唯一 + 可空归属 + 两级层级 + 老文本即 code + 引用即保护」；
  答复后以答复为准。
- 本里程碑**不拆 `product` 表**：`product_type` 仍是 TEXT 列存 code，
  没有加 `product_type_id` 外键列（避免两套真值源）；若将来需要按 id 引用
  再评估加列。
- 字典**不做货主自动建行隔离**：受限账号写货品时自动建出的类型继承该货主归属，
  但编码仍是全局唯一 —— 跨货主同名类型会共用一行（编码全局唯一是刻意取舍，
  便于跨货主对齐分类口径）。
- 产品类型**已接入上架规则引擎**（2026-11-17 补做，见文末「产品类型接入规则引擎」章）：
  条件组新增第 14 维 `product_type`，取值是产品类型字典的编码；既有规则行不受影响。
- 权限码 `PT_PERM_VIEW / PT_PERM_CONFIG` 只落常量、未挂装饰器（Q08 统一处置）。
- `docs/OPEN_QUESTIONS.md` Q01–Q48 待统一答复；PRD 回溯 #3–#21 共 19 页仍另起独立提交。
- **下一棒**：M33 序列号台账（序列号档案 + 收发绑定流水，`is_serial` 升级为台账驱动）
  / M34 货位组主数据（独立表替代文本字段 + 拣货顺序 / 容积）；
  M28 经营报表编号保留、M29/M30 仍降级暂缓、M42 只在用户额外补充后启动。

---

## M33 序列号台账（2026-11-17）✅

用户指令「按计划自己优化、不用问、做完一个做下一个、无人值守」；按 Roadmap 第 1 层
「基础资料」推进 M33（蓝图 §4 第 1 层第 1 行：序列号台账，`is_serial` 升级为台账驱动）。
新增细节分歧按 `docs/OPEN_QUESTIONS.md` **Q49–Q52 当前默认**落地、编号登记，
**全程未打断用户**。

### 交付内容

- **两张新表**（`main._migrate_m33()` 幂等建表 + 索引）：
  - `serial_no` 序列号档案：序列号（全局 UNIQUE）/ 货品（必填 FK）/ 批次 /
    归属货主（可空）/ 状态（四态）/ 当前库位 / 入库时间 / 出库时间 / 备注。
  - `serial_transaction` 序列号流水：档案 ID / 动作（入库·出库·状态变更）/
    前后状态 / 单据来源（manual·asn·dn）+ 单据 ID / 前后库位 / 操作人 / 备注 / 发生时间。
- **服务层 `serial_service.py`**：建档（同时写 `in` 流水）、详情（含流水）、
  编辑（**只能改批次·库位·备注·归属**，状态必须走 `change_status()` 以留痕）、
  出库、状态变更、列表过滤分页、按货品聚合统计、单项摘要。
- **8 个端点**（`routes_serial.py`）：字典 / 列表 / 建档（201）/ 详情 / 编辑 /
  出库 / 状态变更 / 统计；`SerialError → 400`，scope 缺失 → 403。
- **状态机受控**（`constants.SERIAL_TRANSITIONS`）：在库 → 已出库 / 冻结 / 报废；
  冻结 → 在库 / 报废；**报废是终态**；非法流转 400 `SERIAL_BAD_TRANSITION`（Q50）。
- **一个页面** `web_app/serials.html`：两个 tab（台账列表 / 按货品统计）+
  弹窗登记 + 行内出库 / 状态变更 + 流水详情弹窗 + 状态筛选 + 产品筛选 +
  「待登记缺口」列；导航 35→36 页。
- **台账与库存解耦**：登记 / 出库 / 状态变更**不写 `stock`、不写 `stock_movement`**；
  只读统计出「待登记缺口」＝当前库存 − 在库台账数，供人工核对（Q52）。
- **不接发货硬拦截**：ASN 收货 / DN 发货主流程完全不变，序列号只做**手工台账 +
  与单据的只读关联**（流水可记 `ref_type=asn/dn` + `ref_id`），避免为序列号硬拦截
  而推翻 M1–M41 已稳定的库存链路（Q51）。
- **验证**：M33 单测 **21/21**、真实 Chrome E2E `tools/e2e_m33.py` **34/34**
  （含八端点、弹窗登记、界面出库、流水弹窗、统计 tab、状态机拒绝、
  库存零副作用、I1/I2/I3 一致、无 JS 运行时错误）、全量 unittest **643/643**、
  历史 E2E M32 48/48 + M31 44/44 + M35–M41 与 M26 复跑全绿。
- **演示数据**：`tools/seed_module_demo.py` 新增 `m33_serial()`（幂等、走真实 API）：
  把首个普通货品标记为 `is_serial=1`，登记 5 个序列号（在库 3 / 已出库 1 / 冻结 1，
  含 1 个全系统台账），共 7 条流水；`--check` 表清单新增 `serial_no` /
  `serial_transaction`；播种后一致性两核对 OK。
  本地库备份 `miniwms_backup_20261117_pre_m33.db`。

### 关键决策与踩坑

- **台账不等于库存**：序列号是「一件实物」的标识，但 WMS 的库存账仍按数量记
  （`stock.qty`）。若让序列号参与库存计算，就要给 `stock` 加序列号维度，
  那等于重做 M21 的批次明细层。**M33 只做台账**：档案 + 流水 + 只读缺口提示，
  库存账保持原样。
- **不接发货拦截**：给序列号硬拦截看似严谨，实则会让「发一批序列号货品」
  在 DN 发货环节被卡住，而现有演示单据根本没有序列号登记动作 —— 直接上线
  会让 M35–M41 的发货链路不可用。因此 M33 明确**只做台账**，拦截待用户
  确认业务规则后再加（Q51）。
- **状态不能走编辑接口**：`update()` 只允许改批次 / 库位 / 备注 / 归属；
  状态必须走 `change_status()` / `outbound()`，保证每一次状态迁移都有流水，
  不会出现「状态被改了但没留痕」的情况。
- **报废是终态**：不做「反报废」回滚。实物报废后要重新入库，正确做法是
  **新建一条档案**（序列号仍唯一，所以要改号或先删旧档案），而不是复活旧记录。
- **缺口可以为负**：演示库里台账在库数可能大于库存数（登记了但库存已出），
  所以 `gap = stock_qty − in_stock` 允许负数，只作提示不作校验。
- **测试踩坑**：动作端点（出库 / 状态变更）返回 **200** 而建档返回 **201**，
  单测要按端点区分期望状态码（与 M38 拆合单同坑）；服务层函数脱离
  `app_context` 调用会抛 `Working outside of application context`，
  `product_summary()` 必须在 `test_request_context()` + 有效 `g.scope` 下调。

### 已知边界 / 下一棒

- Q49–Q52 待统一答复（归属隔离 / 状态机 / 与单据关系 / 与库存关系），
  当前默认是「可空归属 + 四态终态机 + 只做台账不拦截 + 不写库存账」；
  答复后以答复为准。
- **ASN / DN 不与序列号联动**：收货时不会自动建档、发货时不会自动出库；
  序列号与单据目前只能靠人工在流水里记 `ref_type` + `ref_id`。
- `is_serial` 字段**仍只是货品上的标记位**：M33 提供了台账驱动能力，但
  没有强制「标记为序列号的货品必须登记台账」，也没做「未登记台账不许发货」。
- 台账**不删除**：档案一旦建档只能改状态（报废），没有物理删除端点 ——
  实物序列号的历史要可追溯。（需要清理错误档案时由用户决定是否补删除。）
- 序列号**不参与 GSP 报表 / 工作量统计**的事件流（M39/M40 的 8 类留痕不含台账流水）。
- 权限码 `SERIAL_PERM_VIEW / SERIAL_PERM_CONFIG` 只落常量、未挂装饰器（Q08 统一处置）。
- `docs/OPEN_QUESTIONS.md` Q01–Q52 待统一答复；PRD 回溯 #3–#21 共 19 页仍另起独立提交。
- **下一棒**：M34 货位组主数据（独立表替代 `location.location_group` 文本字段 +
  拣货顺序 / 容积）；M28 经营报表编号保留、M29/M30 仍降级暂缓、
  M42 只在用户额外补充后启动。

---

## M34 货位组主数据（2026-11-17）✅

用户指令「按计划自己优化、不用问、做完一个做下一个、无人值守」；按 Roadmap 第 2 层
「仓库相关」推进 M34（蓝图 §4 第 2 层第 1 行：货位 / 储区 / 货位组）。
至此**基础资料新域 M31–M34 全部收口**。新增细节分歧按
`docs/OPEN_QUESTIONS.md` **Q53–Q56 当前默认**落地、编号登记，**全程未打断用户**。

### 交付内容

- **新表 `location_group`**（`main._migrate_m34()` 幂等建表 + 4 索引）：
  编码（UNIQUE）/ 名称 / 所属仓库（可空 FK）/ 所属储区（可空 FK）/
  拣货顺序 `pick_seq` / 容积上限 `capacity_volume`（cm³，0＝不限）/
  重量上限 `capacity_weight`（kg，0＝不限）/ 三档混放标志（产品·批号·货主）/
  状态 / 备注 / 时间戳。
- **服务层 `location_group_service.py`**：CRUD + 列表分页 + 字典 +
  `ensure_group()`（自动建行）+ `backfill_from_locations()`（老数据回填）+
  `pick_order()`（拣货顺序视图，带组内库位）+ `resolve_name()`。
- **7 个端点**（`routes_location_group.py`）：字典 / 列表 / 新建（201）/ 详情 /
  编辑 / 删除 / 拣货顺序；`LocationGroupError → 400`。
- **库位改引用**（`routes_location.py`）：`location.location_group` 文本列语义从
  「自由文本」升级为「引用 `location_group.code`」；建库位 / 编辑库位时未命中的
  分组**自动建行**（继承该库位的仓库 / 储区）；读取时补 `location_group_name` 与
  `location_group_known`；`/api/locations/groups` 改为读字典表并并入字典外的
  历史取值，保证老库位的筛选项不丢。
- **老数据回填**：`location_group` 字典里没有的**老文本直接当 code 建行** ——
  库位取值一个字符都不改（Q55）。
- **盘点维度口径**（`stocktake_service.SCOPE_COLUMNS`）：`location_group` 维度改为
  「优先取真实货位组，为空回落 `location.zone` 文本」—— 存量库位的老盘点方案取值
  仍能命中，空分组的库位沿用旧口径，历史方案零失配（Q56）。
- **一个页面** `web_app/location_groups.html`：两个 tab（货位组列表 / 拣货顺序）+
  弹窗新建编辑 + 组属性（拣货顺序 / 容积 / 重量 / 三档混放）+ 组库位数统计 +
  拣货顺序视图（按组带库位）；导航 36→37 页。
- **验证**：M34 单测 **18/18**、真实 Chrome E2E `tools/e2e_m34.py` **37/37**
  （含七端点、库位自动建组、引用保护、弹窗 CRUD、拣货顺序 tab、
  库存零副作用、I1/I2/I3 一致、无 JS 运行时错误）、全量 unittest **661/661**、
  历史 E2E M33 34/34 + M32 48/48 + M31 44/44 + M35–M41 与 M26 复跑全绿。
- **演示数据**：`tools/seed_module_demo.py` 新增 `m34_location_group()`（幂等、
  走真实 API）：给前 3 个货位组补上拣货顺序 / 容积 / 重量 / 混放默认，
  拣货顺序视图覆盖 9 个库位；`--check` 表清单新增 `location_group`；
  播种后一致性两核对 OK。本地库备份 `miniwms_backup_20261117_pre_m34.db`。

### 关键决策与踩坑

- **不动 `location` 表结构**：与 M32 产品类型字典同款策略 —— 只加字典表，
  `location.location_group` 仍是 TEXT 列，语义改成「存 code」。既有迁移全部不动。
- **老文本即 code**：若给老分组生成新编码并改写库位，历史盘点方案里存的分组文本
  会失配。**老文本即 code** 是唯一零破坏方案（与 M32 完全同构）。
- **盘点维度用 COALESCE 而不是直接换**：`location_group` 维度如果直接改成
  `l.location_group`，那些分组为空、原本靠 `zone` 文本匹配的库位会全部脱靶。
  用 `COALESCE(NULLIF(l.location_group, ''), l.zone)` 让新旧两种口径在同一维度里
  共存，老方案一条都不丢（Q56）。
- **组级混放与容积只是「默认提示」**：M20 / M25 的混放判定走 `putaway_rule` 的
  13 维条件组 + 库位自身 `allow_mix_*`；如果 M34 把组级值做成强制，就会和已
  定稿的规则引擎口径打架。因此组级值只登记、只展示、只兜底（Q54）。
- **拣货顺序是「视图」不是「引擎」**：`pick_order()` 只按 `pick_seq` 排序列出
  组与组内库位，**不改 M35 的 FEFO 分配算法** —— 出库分配仍按既有规则跑。
- **测试踩坑**：播种脚本的 `raw()` 返回 **dict 列表**（不是 `sqlite3.Row`），
  `for gid, code in rows` 会解包出**键名**（`'id'` / `'code'`）而不是值，
  导致 PUT 打到 `/api/location-groups/id` 返回 405；正确写法是 `r["id"]`。
- 库位留空分组时按编码前缀生成的是 **`-` 前的首段**（`E34-C-01` → `E34`），
  不是「首段+第二段」，断言时别按直觉写。

### 已知边界 / 下一棒

- Q53–Q56 待统一答复（归属与唯一键 / 组级属性口径 / 老数据回填 / 盘点维度），
  当前默认是「仓库布局控制面 + 默认提示 + 老文本即 code + COALESCE 维度」；
  答复后以答复为准。
- **拣货顺序尚未接入出库分配**：M35 的 FEFO / 波次分配仍按既有规则跑，
  `pick_order()` 只是只读视图；要把拣货路径改成「按组走」需要用户确认规则。
- 组级容积 / 重量上限**不参与上架过账判定**，也不做库存占用校验。
- 货位组**不按货主隔离**（与储区 / 库位一致）；若要做货主专属货位组，
  需另加 `owner_id` 与守门口径。
- **基础资料新域 M31–M34 已全部收口**；下一棒按 Roadmap 回到第 4 层：
  **M28 经营报表**（编号保留，可插入）。M29 外部对接 / M30 CSV 导入导出仍降级暂缓、
  不回收编号；M42 打印任务与节点自动触发只在用户额外补充后启动。
- 权限码 `LOC_GROUP_PERM_VIEW / LOC_GROUP_PERM_CONFIG` 只落常量、未挂装饰器（Q08 统一处置）。
- `docs/OPEN_QUESTIONS.md` Q01–Q56 待统一答复；PRD 回溯 #3–#21 共 19 页仍另起独立提交。

---

## M28 经营报表（2026-11-17）✅

用户指令「按计划自己优化、不用问、做完一个做下一个、无人值守」；M31–M34 基础资料
新域收口后，按 Roadmap 第 4 层回到本轮插入的 **M28 经营报表**（编号自 2026-11-05
起一直保留，蓝图 §4 第 4 层「经营报表」落点：按货主的收发汇总 / 周转率 / 库位利用率）。
新增细节分歧按 `docs/OPEN_QUESTIONS.md` **Q57–Q60 当前默认**落地、编号登记，
**全程未打断用户**。

### 交付内容

- **服务层 `operation_report_service.py`**（**纯只读**，不新建业务表）：
  - `io_summary()`：按货主的收货量 / 发货量 / 净变化 / 流水条数 + 收发 Top 货品；
  - `turnover()`：按货主的期初 / 期末 / 平均库存金额、期间出入库金额、周转次数与周转天数；
  - `location_utilization()`：库位利用率（可按仓库 / 储区 / 货位组分组）+ 闲置库位明细；
  - `overview()`：一次拿齐三张报表关键数字的总览；
  - `dictionary()`：期间快捷方式 / 成本字段 / 利用率维度 / 转移排除口径。
- **5 个端点**（`routes_operation_report.py`）：字典 / 总览 / 收发汇总 / 周转率 /
  库位利用率；`OperationReportError → 400`，scope 缺失 → 403。
- **收发口径排除库位间转移**（Q57）：`asn_putaway_in/out`、`move_in/out`、
  `it_transfer` 五类流水**不计入收发**——它们只是库位之间搬运，计入会把收货量算两遍；
  入库量＝`qty_delta > 0`、出库量＝`qty_delta < 0`（均取绝对值）。
- **成本与周转口径**（Q57–Q58）：默认用**采购价**（可切销售价）；周转次数＝
  期间出库成本 ÷ 平均库存金额；平均库存金额＝（期初 + 期末）/ 2，期初＝期末 − 净变化
  （由期末反推，不做历史快照）；平均库存为 0 时不计算周转（返回空值）。
- **库位利用率**（Q59）：按**库位数**口径——已占用库位（`stock.qty <> 0`）÷
  启用库位总数；四个维度可切；不做体积利用率、不做容量占用校验。
- **区间与隔离**（Q60）：`date_from` / `date_to` 闭区间（缺省最近 30 天）+
  `period=day|week|month` 快捷覆盖；货主维度走既有 `scope` 隔离；**不建缓存**。
- **一个页面** `web_app/operation_report.html`：统计区间工具条 + 8 张经营总览卡片 +
  三个 tab（收发汇总 / 库存周转 / 库位利用率）；导航 37→38 页。
- **验证**：M28 单测 **16/16**、真实 Chrome E2E `tools/e2e_m28.py` **36/36**
  （含五端点、转移排除、货主过滤、周转口径四档断言、利用率维度切换、
  三个 tab 切换、库存零副作用、I1/I2/I3 一致、无 JS 运行时错误）、全量 unittest
  **677/677**、历史 E2E M34 37/37 + M33 34/34 + M32 48/48 + M31 44/44 + M35–M41 与 M26 全绿。
- **演示数据**：`tools/seed_module_demo.py` 新增 `m28_operation_report()`（幂等、
  走真实 API）：给前 5 个演示货品补采购价 / 销售价（只改价格、不动库存数量），
  再只读验证三张报表；本地库备份 `miniwms_backup_20261117_pre_m28.db`。

### 关键决策与踩坑

- **收发必须排除库位间转移**：流水表没有 `movement_type` 列，只有 `qty_delta` 正负
  和 `ref_type`。若直接按正负求和，上架（`asn_putaway_out` −262 / `asn_putaway_in` +262）
  会把收货量翻一倍。排除五类转移流水后，演示库近 30 天入库 309 / 出库 70，
  与 ASN 收货、DN 发货的实际单据量对得上。
- **期初库存由期末反推**：没有历史库存快照表，做真实期初快照等于给每个历史时刻
  存一份库存。用「期初＝期末 − 净变化」是仓储报表的标准简化口径，
  代价是期末为 0 且净变化为负时可能出现负期初（已在文档里如实说明）。
- **周转为 0 时返回空值而不是 0**：平均库存为 0 时算出的周转次数是无穷大，
  返回 `null` 比返回 0 更诚实，前端显示「—」。
- **缺价不猜价**：演示货品大多没填采购价，若按 0 折算会让报表看着有数其实缺价；
  因此单独返回 `no_price_items` 标注有几条库存行没价格，播种时给前 5 个货品补价。
- **不做缓存**：M25 曾因规则缓存踩过坑；经营报表每次实时查库更简单也更可靠，
  数据量在这个阶段完全撑得住。
- **测试踩坑**：`stock` 表**没有 `note` 列**（只有 `owner_id / product_id /
  location_id / qty / allocated_qty / updated_at`），单测造数与清理只能按
  `product_id` / `location_id` 标记，不能靠 note；E2E 手工种子还必须同步补
  `stock_batch` 行，否则 I2/I3 不一致（与 M39 同坑）。
- **页面 tab 样式**：新页面的 `.tabbar` / `.tab` / `.panel` 需要内联 `<style>`
  （`style.css` 里没有），且面板必须用 `class="panel active"` 切换 ——
  若用 `style.display` 切换，遇到 `.panel { display:none }` 会永远显示不出来。

### 已知边界 / 下一棒

- Q57–Q60 待统一答复（收发与成本口径 / 周转口径 / 利用率口径 / 区间与缓存），
  当前默认是「排除转移 + 采购价 + 反推期初 + 库位数口径 + 实时查库」；
  答复后以答复为准。
- **期初库存是反推值**，不是历史快照；期末为 0 且净变化为负时可能出现负期初。
- 报表**不做跨仓合并**：多仓场景下按仓库维度分别看，没有「集团合计」视图。
- 库位利用率**只按库位数**，不做体积 / 重量利用率，也不做容量占用校验。
- **不做图表**：只有表格与数字卡，没有趋势图 / 饼图（M41 的打印模板也不支持图表）。
- 权限码 `OP_REPORT_PERM_VIEW` 只落常量、未挂装饰器（Q08 统一处置）。
- `docs/OPEN_QUESTIONS.md` Q01–Q60 待统一答复；PRD 回溯 #3–#21 共 19 页**已于 2026-11-17 补齐**（`docs/PRD_SOURCES.md` §2A.1–§2A.19，回溯进度 21/21）。
- **下一棒**：Roadmap 第 4 层已全部收口（M28 / M39 / M40）。
  剩余可做方向：**M29 外部对接 API**（X-API-Key + `/api/ext` + HMAC webhook，
  编号保留）、**M30 CSV 导入导出**（编号保留）、**M42 打印任务队列与节点自动触发**
  （需用户额外补充）。三者均按 Roadmap 处于「降级暂缓 / 后置」状态，
  未获用户明确指示前不启动。

---

## 前端巡检工具与缺陷修复（2026-11-17）

> 用户指令「PRD 可以先不做，先把功能完善，然后可以基于功能更新 PRD」——
> 因此暂停最终 PRD 撰写，转向功能侧缺陷清理。本章记录新增的巡检工具与本轮修掉的 5 处缺陷。

### 新增 `tools/audit_ui.py`：真实浏览器遍历巡检

**动机**：既有缺陷（供应商弹窗打不开、客户地区联动失效、库存物料下拉空白）都是
「页面能打开、但点进交互才暴露」的问题，单测与 E2E 都不覆盖 —— 现有 E2E 只验证
特定里程碑的页面，没有一个**全页面**的交互巡检。

**能力**（独立端口 + 临时库，绝不碰 5100 demo）：

| 检查项 | 判据 |
|---|---|
| 页面加载错误 | 捕获 `pageerror` 与 `console.error`（过滤 favicon / 静态资源 404 噪声） |
| 工具栏下拉可用 | 所有 `select` 至少有一个非空选项（省市联动的「市」除外，它未选省时本就为空） |
| 新建弹窗可打开 | 点「+ 新建 XX」后必须出现弹窗 DOM，且不抛 JS 错误 |
| 弹窗下拉可用 | 弹窗内 `select` 必须有真实选项（显式 placeholder 除外） |
| 页内卡片型表单 | 对不用 `openForm` 的页面，改查页内是否出现可填写表单区 |

配套两点：需要登录的页面（角色 / 用户 / 货主仓库授权）先换 token 注入本地存储；
巡检只开弹窗、填必填项并关闭，**不提交、不改业务数据**。

### 本轮修掉的 5 处缺陷

| # | 缺陷 | 影响 | 根因 | 修法 |
|---|---|---|---|---|
| 1 | **供应商「新建 / 编辑」弹窗打不开** | 功能不可用 | `owner_id` 的 `options` 传成了**函数** `() => ownerOptions`，而 `modal.js` 按数组 `.map` 处理 | 改传数组；同时给 `modal.js` 增加**惰性求值**支持（见下） |
| 2 | **客户所在地区三级联动失效** | 地区无法录入 | `customers.html` **漏引 `js/region.js`**，`modal.js` 找不到 `bindRegionSelects`，省 / 市下拉只有占位项 | 补上 `<script src="/js/region.js">` |
| 3 | **客户 / 货主的「地区」列恒为空** | 列表地区列永远空白、无法检索 | 前端三级联动只提交 `province/city/district` 三个键，**从不提交 `region`**，而 `region` 列是列表读取的字段 | 后端在未显式传 `region` 时按「省/市/区」自动拼装写入（`customer` 表有 `region` 列；`owner` 表无此列，故只改 `customer`） |
| 4 | **库存账的物料下拉是空白** | 无法按物料筛选 | 下拉统一按 `x.code` 渲染，但 `product` 表**没有 `code` 列**（只有 `sku`），渲染出空串 | 物料下拉改按 `sku · 名称` 渲染，其余两个下拉仍用 `code` |
| 5 | **`modal.js` 的「模块级 FIELDS + 异步数据」陷阱** | 隐患 | `FIELDS` 常量在模块加载时求值，若页面用**重新赋值**替换选项数组，常量仍引用旧空数组（`carriers.html` 用就地修改所以侥幸没踩到） | `modal.js` 支持 `options` 为**函数**（渲染时惰性求值），传数组的行为完全不变；`suppliers.html` 同时改为就地修改，与项目既有风格一致 |

### 巡检结果

```
首次运行：21 个问题（含误报）
修正巡检逻辑（弹窗选择器 / 按钮文案 / 登录注入 / 区域联动例外）后：7 个
补足种子数据（区分「数据空」与「真缺陷」）+ 修复 5 处缺陷后：0 个问题
```

### 验证

- 巡检：`tools/audit_ui.py` → **0 问题**（全部页面加载正常、弹窗可开、下拉非空）。
- 全量单元测试 **677/677** 全绿。
- 全量端到端回归：M26 / M28 / M31–M41 共 13 个 E2E 脚本复跑（`modal.js` 是公共组件，
  必须全量回归）。

### 遗留与下一步

- 巡检只覆盖「加载 + 开弹窗 + 下拉非空」，**不覆盖弹窗内的字段联动与提交**；
  后续可再加「填必填项 → 提交 → 校验返回」的深度巡检。
- `owner` 表没有 `region` 列（只有省 / 市 / 区三列），货主列表也没有「地区」列，
  因此第 3 项修复只落在 `customer` 表 —— 这是按表结构与页面实际读取字段做的取舍，
  不是遗漏。
- `batches.html` 里有一段 `["pfOwner","rcOwner"]` 的循环引用了页面上不存在的
  `rcOwner`，但有 `if (el)` 保护、无实际影响，**不改动**（避免无意义 diff）。
- 最终 PRD（`docs/PRD_v1.0.md`）已建骨架（§1–§7 + 附录）与 §3.1–§3.26 共 26 页，
  按用户指示**暂停续写**，待功能侧收口后基于最终实现统一更新。

---

## 产品类型接入上架规则引擎（2026-11-17 补做）

> 用户指令「先把功能完善」后的第一处「**已建能力未打通**」修补：
> M32 建好了产品类型字典，但上架规则引擎的条件组里没有这个维度 ——
> 字典建了却用不上。补上后，M32 的字典才真正进入业务链路。

### 改动

| 层 | 改动 | 说明 |
|---|---|---|
| 常量 | `constants.PUTAWAY_CONDITION_DIMS` 加 `product_type`；`PUTAWAY_CONDITION_LABELS` 加「产品类型」 | 条件组由 13 维 → **14 维** |
| 引擎 | `putaway_rules.build_context()` 的 ctx 增加 `product_type` | 取货品的 `product.product_type`（M32 起该列存字典编码） |
| 编辑器 | `web_app/putaway_rules.html` 文案 13 维 → 14 维；新增 `DIM_HINTS` 提示 | 维度格子由 `condition_dims` **动态渲染**，加维度即自动多一格 |
| 测试 | M25 / M26 断言与 E2E 的维度数改 14；M32 新增 5 例联动测试 | 新增 `M32RuleEngineLinkTest` |

### 为什么这样改是安全的

1. **既有规则行零影响**：条件组是 JSON 对象，**没有 `product_type` 键即视为通配**（不限），
   匹配逻辑 `matches()` 逐维遍历，未出现的维度根本不参与比对。
   所以「从 13 维变 14 维」不会改变任何存量规则行的命中结果。
2. **匹配逻辑本身不用改**：`matches()` 是通用的逐维比对，新增维度只影响 ctx 里多一个键。
3. **取值口径一致**：该维的值是**产品类型字典的编码**（如 `PT-FG`），
   与 `product_id` / `owner_id` / `zone_id` 填 id、`category_l1` 填文本的既有风格一致；
   编辑器给该维加了 placeholder 提示「产品类型编码，如 PT-FG」。
4. **播种数据不重复**：演示库里的示例规则行名叫「DEMO 规则示例（13 维条件组）」，
   **刻意不改名** —— 改名会让幂等播种识别不到旧行、重复插一条。只更新注释。

### 业务价值

产品类型是比「一级分类」更适合做业务分流的维度（成品 / 半成品 / 原料 / 危险品…）。
接入后，运营可以配出「危险品走 A 储区、原料走 B 储区」这类规则 —— 此前只能用
一级分类这种粗粒度或写死规则。

### 验证

- 单测：M25 规则 22 例 + M26 编辑器 23 例 + M32 联动 5 例，全绿；
  全量 unittest **682/682**。
- E2E：M26 规则编辑器 E2E 的「14 维条件组控件」断言通过；
  M25–M41 全量 E2E 复跑无回归。

### 遗留

- `abc_class`（ABC 分类）维度**仍无数据源**，条件组里恒为空 —— 它是 13 维里
  唯一一个「有维度无取值」的，要真正可用需要给货品加 ABC 分级字段并维护（另立任务）。
- `product_type` 维度目前**只参与条件匹配**，不参与评分（R07–R15 九条评分规则未加类型权重）。

---
