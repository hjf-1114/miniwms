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
- M20 储区主数据 + 单据标识 + 入库混放校验（**阶段 A 已交付 2026-11-05**：储区主数据 / 仓库维度 / 混放字段 / 推荐储区 / 固定货位 / 简版上架推荐 / 验收·上架作业页；**剩余**：asn·dn 各 4 个单据标识 + external_doc_type + 入库混放硬校验）
- M21 批次级库存明细层（stock_batch + stock_movement 扩维度列 + I2/I3 硬校验 + 一次性回填）
- M22 库存调整单 + 盘点增强（stock_adjustment 唯一手工数量入口 / stocktake_plan 全盘·部分·盲盘·动碰；动碰支持手工时间窗 + 交易类型 / 差异由人工按钮生成调整单）
- M23 指定客户专属库存 + 销退口径（designated_customer_id 只在明细行 / 出库校验规则 / 销退外部批次号）
- M24 库存转移单（只改批次信息、数量净零 / 制单→分配库存（真实占用可用库存）→审核生效终态 / 未分配可关闭删除 / 取消分配 / 冲销留痕）
- M25 上架推荐规则引擎（13 维条件组 + fallback 链 + 硬约束 + 可解释 Top-N）**继续安排**
- M26 规则配置与推荐交互（条件组编辑器 + 推荐弹窗一键采纳 + 推荐日志）**继续安排**
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
- M41 打印模板配置 + PDF 预览与内容编辑（方案沟通中）
- M42 打印任务队列 + 操作节点自动触发

> **领域蓝图（2026-11-05 用户提出「自上而下、后续细节再优化」）**：完整 5 层框架、
> 现状对照、实施顺序、M31+ 依赖与打印方案候选见 `docs/DOMAIN_BLUEPRINT.md`（v1）。
> 已确认：① **M20–M30 编号保留不动，新域从 M31 起追加**；② 实施顺序＝**先收尾 M20 剩余**
> （单据标识位 + 入库混放硬校验），**再按蓝图从第 1 层基础资料补齐**；
> ③ M25/M26 上架推荐规则引擎与 FEFO 出库分配继续安排，M27/M29/M30 降级暂缓但不删编号；
> ④ 打印期望 PDF 预览 + 内容可编辑 + 调用打印机 + 自动触发，技术方案待沟通。
> **待确认**：M21–M26 与 M31+ 第 1 层的先后顺序（M35 FEFO 硬依赖 M21 `stock_batch`）。
> `DEVELOPMENT.md` Roadmap 仍是编号权威来源，`docs/DOMAIN_BLUEPRINT.md` 是框架级来源。

> 编号变更（2026-11-05，用户确认「好的」）：按 `docs/PUTAWAY_RECOMMENDATION.md` 第 14 章
> 插入 7 个新里程碑 **M20–M26**，原 M20 预警 / M21 报表 / M22 外部对接 / M23 CSV
> 顺延为 **M27–M30**。「库存调整单 + 盘点增强」硬依赖 M21 的 `stock_batch`，故排 M22；
> 出库分配口径未定（第 15 章「到时候沟通」），暂不占编号，这样两组编号无需再改。
> 此前 2026-11-04 插入 M19 批次追溯时原四项曾顺延为 M20–M23，本次为第二次顺延；
> 更早插入 M7 / M9 / M10–M14 / M16 时均按同一口径。**Roadmap 为编号权威来源**，
> README 规划段与首页里程碑表同步。
> **后续变更**：同日用户给出自上而下 5 层整体规划，确认出库分配 FEFO「继续安排」并纳入 **M35**；
> M27/M29/M30 降级暂缓（编号不回收）；新域从 M31 起追加。见上文 M31+ 段落与
> `docs/DOMAIN_BLUEPRINT.md`。本段保留作为编号演进的历史记录。

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
- 条件组 / fallback 链 / 权重配置仍属 M25，本版不做；`asn`·`dn` 单据标识位与入库混放硬校验仍属 M20 剩余范围。

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
