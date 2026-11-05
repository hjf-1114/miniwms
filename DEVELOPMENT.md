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
- M20 储区与上架作业基础（**阶段 A 已交付 2026-11-05**：储区主数据 / 仓库维度 / 混放字段 / 推荐储区 / 固定货位 / 简版上架推荐 / 验收·上架作业页；**阶段 B 已交付 2026-11-05**：asn·dn 各 4 个单据标识 + external_doc_type 的字段 / 表单 / 17 例单测）✅ **M20 收尾完成**；混放·冷链·危险拦截改为配置化规则并入 M25/M26，过渡期不拦截
- M21 批次级库存明细层（stock_batch + stock_movement 扩维度列 + I2/I3 硬校验 + 一次性回填）
- M22 库存调整单 + 盘点增强（stock_adjustment 唯一手工数量入口 / stocktake_plan 全盘·部分·盲盘·动碰；动碰支持手工时间窗 + 交易类型 / 差异由人工按钮生成调整单）
- M23 指定客户专属库存 + 销退口径（designated_customer_id 只在明细行 / 出库校验规则 / 销退外部批次号）
- M24 库存转移单（只改批次信息、数量净零 / 制单→分配库存（真实占用可用库存）→审核生效终态 / 未分配可关闭删除 / 取消分配 / 冲销留痕）
- M25 上架推荐规则引擎（13 维条件组 + 多条规则 priority + fallback 链 + **配置化硬约束** + 可解释 Top-N）**继续安排**；**承接 M20 剩余移来的混放 / 冷链 / 危险拦截**
- M26 规则配置与推荐交互（条件组编辑器 + fallback 链拖拽 + 推荐弹窗一键采纳 + 推荐日志；13 维起步、预留扩展位）**继续安排**
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
- M40 工作量统计（按人 / 按节点 / 按时间段）
- M41 打印模板配置 + PDF 预览与内容编辑（**方案 v2 已定稿**：零安装 / Chrome headless / 预览页可临时编辑单据内容 / 字段映射 + 可视化拖拽排版）
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
> 只靠推荐页提示；条件维度 **13 维起步**，M26 编辑器预留扩展位。
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

