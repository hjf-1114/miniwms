# MiniWMS PRD 素材库（回溯 M1–M20 · 逐页详细记录）

> 版本 **v0.2** · 2026-11-05 · 状态：**回溯进行中**（第 1 批：基础主数据 2 页，已完成自审 + 独立交叉审阅修正）。
> **定位**：后续生成完整 PRD 的**唯一素材来源**。每页按 `docs/PRD_TEMPLATE.md` 的 11 项模板详细记录，
> 分 `[业务]` / `[功能]` / `[实现]` 三层书写，兼顾质检演示、内部开发交付、业务方评审三类读者。
> **范围**：回溯补齐 M1–M20 已交付的 21 个页面，之后每个新里程碑按同一模板同步追加。
> **交付格式**：Markdown；最终 PRD 另起独立版本文件（如 `docs/PRD_v1.0.md`），不覆盖历史版本。
> **权威来源**：编号与设计口径见 `DEVELOPMENT.md` Roadmap、`docs/DOMAIN_BLUEPRINT.md` v3、
> `docs/PUTAWAY_RECOMMENDATION.md` v3.3；本文件只记录**页面功能事实**，取证自当前代码。

---

## 0. 全局总览

### 0.1 [业务] 项目定位与业务背景

MiniWMS 是一个**轻量多货主 WMS（3PL 仓储管理）演示系统**。第三方仓储企业同时为多个货主保管货物，
因此「货主」是贯穿所有单据与库存的强制维度：物料、客户、供应商归属货主，收货/出库单绑定货主，
库存账按「货主 × 物料 × 库位」唯一记帐，跨货主选错参照数据会被「多货主隔离」校验直接拒绝。

业务主线（从 M4 起逐步打通）：**收货单 ASN → 验收入库（生成批次流水）→ 上架（推荐货位）→ 库存账与流水 →
出库单 DN → 拣货分配 → 发货**；旁路作业为移库、盘点、库存查询、账实核对。
M19 引入批次追溯（批号 / 效期 / UDI / 灭菌批号 / 内部批次号），面向医疗器械 GSP 场景；
M20 引入储区、温区、库位混放策略与上架货位推荐，解决「货该放哪里、能不能混放」的仓储现场问题。

### 0.2 [业务] 主线流程与角色

- 主线：`建立货主/物料/库位 → 创建 ASN → 验收（写批次）→ 上架（暂存货位 → 存储/拣货位）→ 库存账 → 创建 DN → 拣货 → 发货`
- 旁路：`移库（库位间一减一增）` · `盘点（冻结 → 录入 → 差异调整 → 结案）` · `库存余额/流水查询` · `账实核对 reconcile`
- **角色由用户角色模块自定义**（`role` / `user` 表，`/api/roles`、`/api/users`）；`main._seed_auth()` 在空库时
  预置「管理员 / 操作员 / 只读」3 个**系统级角色**及 `admin` 账号，权限码为 `模块:动作` 形式。
  业务上可对应收货、上架、拣货、盘点、主数据维护等岗位，但系统**未按仓储作业岗位做页面级隔离**
  （鉴权现状见 3.1）。

### 0.3 [功能] 五层蓝图与页面分组

按 `docs/DOMAIN_BLUEPRINT.md` v3 的 5 层框架，当前 21 个页面分布：

| 层 | 已交付页面 | 缺口（后续里程碑） |
|---|---|---|
| ① 基础资料 | 货主、物料、库位、客户、供应商、承运商、仓库、储区、批次 | 司机车辆 M31 / 产品类型字典 M32 / 序列号台账 M33 / 货位组 M34 |
| ② 仓储作业 | 收货、验收作业、上架作业、出库、移库、盘点、库存账 | 库存转移 M24 / 调整单 M22 / 波次 FEFO M35 / 复核 M36 / 养护 M37 / 拆单合单 M38 |
| ③ 用户角色 | 角色、用户 | 作业节点级细粒度权限（细节层再定） |
| ④ 报表 | 库存账（只读查询） | 经营报表 M28 / GSP 报表 M39 / 工作量统计 M40 |
| ⑤ 打印 | 无 | 模板 + PDF 预览 M41 / 自动打印 M42（后置） |
| 系统页 | 登录、工作台、首页 | — |

### 0.4 [实现] 技术栈与工程约束

- 后端：Python + Flask + SQLite，**唯一 Python 依赖 Flask**；`main.DB_PATH` 由 `MINIWMS_DB` 覆盖，
  `main.init_db()` 负责 DDL 与幂等旧库 ALTER 迁移。
- 前端：`web_app/` 零依赖原生 HTML / JS / CSS，由 Flask 静态托管；公共组件 `js/api.js`（请求/提示/转义/工作台标签）、
  `js/modal.js`（零依赖表单引擎）、`js/layout.js`（侧栏导航，暴露 `window.MWS_NAV`）、`js/region.js`（省市区联动）。
- 工作台 `app.html`：iframe 多标签，`?embed=1` 隐藏内层侧栏，含防嵌套守卫。
- 规模：21 个 HTML 页、17 个 `routes_*.py` Blueprint、约 28 张表。
- 质量基线：全量 `./.venv/Scripts/python.exe -m unittest discover -s tests` **285 例全绿**；
  `/api/stock/reconcile` 不变式 `stock.qty == SUM(stock_movement.qty_delta)`。
- 门槛：文件 76/100、提交 49/30、里程碑 20/6（截至 M20 阶段 B 交付）。

---

## 1. 页面覆盖进度表

> 状态只在本表维护；正文只写「已记录」页，未写页**不在正文留占位**。

| # | 分组 | 页面 | 文件 | 主要里程碑 | 状态 | 章节 |
|---|---|---|---|---|---|---|
| 1 | 基础主数据 | 货主 | `owners.html` | M2 / M11 / M15 | **已记录** | [2.1](#21-货主管理ownershtml) |
| 2 | 基础主数据 | 物料 | `products.html` | M1 / M10 / M15 / M20 | **已记录** | [2.2](#22-物料管理productshtml) |
| 3 | 基础主数据 | 库位 | `locations.html` | M3 / M13 / M20 | 待补 | — |
| 4 | 基础主数据 | 客户 | `customers.html` | M7 / M12 | 待补 | — |
| 5 | 基础主数据 | 供应商 | `suppliers.html` | M16 | 待补 | — |
| 6 | 基础主数据 | 承运商 | `carriers.html` | M14 | 待补 | — |
| 7 | 基础主数据 | 仓库 | `warehouses.html` | M16 | 待补 | — |
| 8 | 基础主数据 | 储区 | `zones.html` | M20-A | 待补 | — |
| 9 | 出入库作业 | 收货 | `asns.html` | M4 / M16 / M19 / M20-B | 待补 | — |
| 10 | 出入库作业 | 验收作业 | `asn_receive.html` | M20-A（承接 M19 批次追溯） | 待补 | — |
| 11 | 出入库作业 | 上架作业 | `asn_putaway.html` | M20-A | 待补 | — |
| 12 | 出入库作业 | 出库 | `dns.html` | M5 / M16 / M20-B | 待补 | — |
| 13 | 出入库作业 | 移库 | `transfers.html` | M17 | 待补 | — |
| 14 | 出入库作业 | 盘点 | `stocktakes.html` | M18 | 待补 | — |
| 15 | 出入库作业 | 批次 | `batches.html` | M19 | 待补 | — |
| 16 | 出入库作业 | 库存账 | `stock.html` | M6 | 待补 | — |
| 17 | 系统管理 | 角色 | `roles.html` | M9 | 待补 | — |
| 18 | 系统管理 | 用户 | `users.html` | M9 | 待补 | — |
| 19 | 系统页 | 登录 | `login.html` | M9 | 待补 | — |
| 20 | 系统页 | 工作台 | `app.html` | 非里程碑（M18 后 UI 增强；M20-A 改侧栏伸缩） | 待补 | — |
| 21 | 系统页 | 首页 | `index.html` | 全程 | 待补 | — |

---

## 2. 页面详细记录

### 2.1 货主管理（owners.html）

**[业务] 用途与背景**

多货主（3PL）是 WMS 的核心模型：每个货主拥有独立的物料档案与库存，后续收货、拣货、库存账、盘点等
所有单据都挂 `owner` 维度。本页是这个维度的**唯一建档入口**，也是全系统「多货主隔离」校验的参照表。
M2 先落地基础档案并把物料挂接货主；M11 面向电商 / 3PL 补齐省市区、备用地址、授权仓库、经营范围、
备用字段与**货主证照子表**（营业执照、医疗器械经营许可等可挂多证）；M15 补合作起止时间，用于
标识合作有效期。**没有货主档案就无法区分物权、无法按货主拆分库存账、也无法校验客户/供应商归属。**

**[业务] 适用角色与场景**

| 场景 | 说明 |
|---|---|
| 引入新货主 | 主数据维护人员创建档案，填写编码、名称、联系方式 |
| 资质管理 | GSP / 医疗器械场景维护营业执照、经营许可证及有效期 |
| 合作变更 | 联系人/地址变更走编辑；合作结束改为「停用 active → inactive」，不删除 |
| 建单参照 | 收货、出库、物料、客户、供应商页的货主下拉数据源均来自 `/api/owners` |

**[功能] 页面布局**

- **查询条件**：关键字输入框（占位「编码/名称搜索」，模糊匹配 `code` 与 `name`）、状态下拉
  （全部状态 / 启用 active / 停用 inactive）、「查询」按钮。
- **列表字段**：ID、编码、名称、联系人、电话、邮箱、状态（中文标签「启用 / 停用」）、操作。
- **行操作**：`编辑`、`证照`、`停用/启用`（按当前状态切换按钮文案）、`删除`（危险色）。
- **页操作**：`+ 新建货主`。
- **分页**：上一页 / 下一页 + 「第 N 页 / 共 M 条」；`page_size = 20`，首/末页自动禁用按钮。
- **布局**：侧栏（`layout.js` 渲染）+ 顶栏标题「货主管理」+ 面包屑「MiniWMS / 基础主数据」+ 单卡片承载列表与工具栏。

**[功能] 表单字段表**（新建 / 编辑共用 `FIELDS`，弹窗宽 760）

| 字段 | 控件 | 必填 | 校验 | 默认值 | 来源 / 联动 |
|---|---|---|---|---|---|
| `code` 编码 | 文本框（自动生成编码，前缀 `OW`） | 是 | 非空、≤32 字符、**全表唯一** | 空 | 前端 `autocode` |
| `name` 名称 | 文本框 | 是 | 非空、≤128 字符 | 空 | — |
| `contact` 联系人 | 文本框 | 否 | 去首尾空格 | `''` | — |
| `phone` 电话 | 文本框 | 否 | 去首尾空格 | `''` | — |
| `email` 邮箱 | `type=email` 文本框 | 否 | 仅前端 email 控件；**后端只去空格，不校验格式** | `''` | — |
| `region` 所在地区 | 省 / 市 / 区三级联动（`region.js`），整行 | 否 | 拆成 `province` / `city` / `district` 三个字段提交 | `''` | 地区组件 |
| `address` 详细地址 | 文本框，整行 | 否 | 去首尾空格 | `''` | — |
| `backup_address` 备用地址 | 文本框 | 否 | 去首尾空格 | `''` | M11 |
| `authorized_warehouse` 授权仓库 | 文本框 | 否 | 去首尾空格，**自由文本，非仓库外键** | `''` | M11 |
| `business_scope` 经营范围 | 文本框，整行 | 否 | 去首尾空格 | `''` | M11 |
| `extra1` 备用字段1 | 文本框 | 否 | 去首尾空格 | `''` | M11 |
| `extra2` 备用字段2 | 文本框 | 否 | 去首尾空格 | `''` | M11 |
| `start_date` 合作起始 | 日期选择器（`nullable`） | 否 | 去首尾空格，按字符串存储 | `''` | M15 |
| `end_date` 合作截止 | 日期选择器（`nullable`） | 否 | 同上 | `''` | M15 |
| `note` 备注 | 文本域，整行 | 否 | **不去空格**（保留用户换行） | `''` | — |
| `status` 状态 | **不在新建表单内**，由行操作切换 | — | 枚举 `active` / `inactive` | `active` | 行操作 `PUT {status}` |

> 后端另有 `created_at` / `updated_at`，由 SQLite `datetime('now','localtime')` 生成，页面不暴露。

**[功能] 子表 / 弹窗：货主证照管理**

- 列表列：类型、证号、发证机构、有效期（`valid_from ~ valid_to`）、操作（`删`）；无数据时显示「暂无证照」。
- 录入行：证照类型（**必填**，前端为空时静默不提交）、证号、发证机构、起始日期、截止日期 + `+ 添加`。
- 一个货主可挂**多张证照**；删除仅删该证照行，不影响货主档案。
- 端点：`GET /api/owners/<id>/certs`、`POST /api/owners/<id>/certs`、`DELETE /api/owners/<id>/certs/<cert_id>`。

**[功能] 业务规则与状态机**

1. **编码唯一**：新建时 `_code_exists()` 命中 → 409「货主编码已存在: xxx」；编辑改编码时排除自身 ID，仍按唯一校验。
2. **状态流转**：`启用 active ⇄ 停用 inactive`，由行操作提交 `PUT /api/owners/<id> {"status": ...}` 切换，**可逆**。
   停用不影响已有历史单据；但 `product` 新建/编辑的 `_check_owner()` 要求货主为 `active`，停用货主不能再挂新物料。
3. **删除保护**：`SELECT COUNT(*) FROM product WHERE owner_id = ?`，只要名下有物料即拒绝，
   409「该货主名下还有 N 个物料档案，禁止删除；请先转移或停用」。删除前有浏览器 `confirm`。
4. **部分更新语义**：`PUT` 只更新请求体中出现的字段；空请求体 → 400「没有可更新的字段」。
5. **时间戳**：每次 `PUT` 自动刷新 `updated_at`；`created_at` 只在建档时生成。
6. **默认值**：新建默认 `status=active`，其余字段缺省为空串。
7. **无级联删除**：删除货主不会删除其证照记录（当前代码只 `DELETE FROM owner`）。

**[功能] 异常与提示**

| 触发 | 前端提示 | 后端响应 |
|---|---|---|
| 编码 / 名称为空 | 表单 `required` 拦截 | 400 `code 必填` / `name 必填` |
| 编码 > 32 / 名称 > 128 | — | 400 `code 最长 32 字符` / `name 最长 128 字符` |
| 编码重复 | `showMsg` 显示后端错误 | 409 `货主编码已存在: xxx` |
| `status` 非法值 | — | 400 `status 只能是 active/inactive` |
| 编辑 / 删除 / 证照时货主不存在 | `showMsg` 显示错误 | 404 `货主不存在` |
| 删除时名下有物料 | `confirm` 通过后失败提示 | 409 `该货主名下还有 N 个物料档案，禁止删除；请先转移或停用` |
| 证照 `cert_type` 为空 | 前端直接 return，不提交 | 400 `cert_type 必填`（API 直调时） |
| 证照 ID 不属于该货主 | — | 404 `证照不存在` |
| `page` / `page_size` 非整数 | — | 400 `page/page_size 必须为整数` |
| 列表加载失败 | `加载失败：<错误信息>` | — |

**[实现] 关联单据与数据流向**

`owner` 被以下表引用：`product.owner_id`、`customer.owner_id`、`supplier.owner_id`、`asn.owner_id`、
`dn.owner_id`、`stock.owner_id`，以及 `owner_cert.owner_id`。
新建货主后，物料页 / 收货页 / 出库页的货主下拉可立即选用；库存账按 `owner_id` 拆分。
**删除前检查只覆盖 `product`**，其他引用表未做删除前检查（如实记录，PRD 描述时不可写成「全量引用保护」）。

**[实现] 权限**

本页及 `/api/owners/*` **未挂 `@require_permission`**；项目也**没有全局 `before_request` 鉴权钩子**。
`js/api.js` 会在请求头附 `Authorization: Bearer <token>`，但后端不校验；全项目仅 `routes_user.py` 的
角色 / 用户管理端点（`/api/roles*`、`/api/users*`）挂了 `@require_permission("user:manage")`，
其中 `/api/auth/login` 未挂。因此本页当前为「登录页可跳转、但接口无强制鉴权」状态。

**[功能] 与其他页面的关系**

- **物料 `products.html`**：货主下拉数据源 `GET /api/owners?page_size=100`；`owner_id` 为空显示「自营」。
- **收货 `asns.html` / 出库 `dns.html`**：货主必填，物料/客户/供应商与单据货主不一致时后端 400「多货主隔离」。
- **客户 / 供应商页**：客户与供应商归属货主，建单时校验归属一致。
- **工作台 `app.html`**：本页以内嵌 iframe 标签打开，`?embed=1` 隐藏内层侧栏；独立打开时显示自身侧栏。
- **公共组件**：`api.js`（`api` / `showMsg` / `esc` / `statusTag`）、`modal.js`（`openForm`）、
  `region.js`（省市区联动）、`layout.js`（侧栏）。

**[实现] 验收要点**

- 对应测试：`tests/test_owners.py` —— 创建校验、重复编码 409、分页 / 关键字 / 状态过滤、部分更新、删除保护。
- 可观察行为：新建 → 列表可见 → 编辑保存 → 证照增删 → 停用后启用 → 挂物料后删除被 409 拒绝。
- 数据落点：`owner` 表；`updated_at` 每次编辑刷新；证照写 `owner_cert`。

**[实现] 对应里程碑与提交**

M2 货主主数据 → M11 省市区 / 证照 / 经营范围深化 → M15 合作起止时间。M20 阶段 A/B 未改动本页。
里程碑明细见 `DEVELOPMENT.md`；提交索引见 `git log`（不在此编造早期哈希）。

---

### 2.2 物料管理（products.html）

**[业务] 用途与背景**

物料（商品 / SKU）是 WMS 的核心主数据，后续收货、上架、拣货、盘点、批次追溯都以 `product` 为参照。
M1 建立基础档案（SKU 唯一、分类、价格、库存字段）；M10 面向电商 / 医药级场景补齐多条码、多层级包装、
四级分类、注册证号 / 注册人 / 生产企业和许可证、温湿度与危险品类型、序列号与保质期标志；
M15 补体积手填与长宽高自动换算；M20 阶段 A 补**推荐储区**与**固定货位绑定**，让上架推荐有据可依。

业务痛点：没有统一 SKU 档案，收货明细无法带出规格 / 厂家 / 注册证等 GSP 追溯字段，
上架无法按品类或温区推荐货位，包装规格无法换算体积与库容，库存账也无法按物料维度汇总。

**[业务] 适用角色与场景**

| 场景 | 说明 |
|---|---|
| 新品建档 | 主数据维护人员填写 SKU、名称、规格、分类、货主归属、价格 |
| 医药 / GSP 建档 | 补注册证号、注册人、生产企业许可证、温湿度范围、危险品类型、保质期天数 |
| 电商 / 多包装 | 维护多条码（含主条码）与 1–6 级包装规格、每箱数量 |
| 上架策略配置 | 设推荐储区、绑定固定货位与优先级，供上架推荐引擎使用 |
| 停用替代删除 | 有库存的物料不能删，改为「停用 inactive」，历史单据仍可追溯 |

**[功能] 页面布局**

- **查询条件**：关键字输入框（占位「SKU/名称搜索」，模糊匹配 `sku` 与 `name`）、
  货主下拉（全部货主 + 各货主，含「自营」选项）、「查询」按钮。
- **列表字段（14 列）**：ID、SKU、名称、条码、品牌、规格、分类、货主（无货主显示「自营」）、
  推荐储区（无则显示 `—`）、采购价、销售价、当前库存（加粗）、状态、操作。
- **行操作**：`编辑`、`绑定货位`、`条码/包装`、`删除`（危险色）。
- **页操作**：`+ 新建物料`。
- **分页**：上一页 / 下一页 + 「第 N 页 / 共 M 条」；`page_size = 20`。
- **初始化并行加载**：`Promise.all([loadOwners(), loadZones()]).then(load)`；
  货主下拉取 `/api/owners?page_size=100`，储区取 `/api/zones?page_size=100`（失败静默忽略）。

**[功能] 表单字段表**（新建 / 编辑共用 `productFields()`，弹窗宽 760，共 41 个可编辑字段）

*基础标识*

| 字段 | 控件 | 必填 | 校验 | 默认值 | 来源 / 联动 |
|---|---|---|---|---|---|
| `sku` | 文本框（自动生成前缀 `SKU`） | 是 | 非空、≤64 字符、**全表唯一** | 空 | 前端 `autocode` |
| `name` 名称 | 文本框 | 是 | 非空、≤128 字符 | 空 | — |
| `article_no` 货号 | 文本框 | 否 | 去空格 | `''` | — |
| `category` 默认分类 | 文本框 | 否 | 去空格，空则落 `未分类` | `未分类` | — |
| `unit` 单位 | 文本框 | 否 | 去空格，空则落 `件` | `件` | — |
| `barcode` 主条码 | 文本框 | 否 | 去空格 | `''` | 与「条码/包装」弹窗的多条码**相互独立** |
| `brand` 品牌 | 文本框 | 否 | 去空格 | `''` | — |
| `spec` 规格型号 | 文本框 | 否 | 去空格 | `''` | — |
| `external_code` 外部编码 | 文本框 | 否 | 去空格 | `''` | M10 |

*分类与经营范围*

| 字段 | 控件 | 必填 | 校验 | 默认值 | 来源 / 联动 |
|---|---|---|---|---|---|
| `product_type` 产品类型 | 文本框 | 否 | 去空格（**自由文本，M32 才升级为字典**） | `''` | M10 |
| `main_category` 产品主分类 | 文本框 | 否 | 去空格 | `''` | M10 |
| `category_l1` ~ `category_l4` 一至四级分类 | 4 个文本框 | 否 | 去空格 | `''` | M10；**当前上架推荐引擎未消费**，仅作档案字段（同分类条件组属 M25 规划） |
| `origin` 产地 | 文本框 | 否 | 去空格 | `''` | M10 |
| `business_scope` 经营范围 | 文本框，整行 | 否 | 去空格 | `''` | M10 |

*医药 / GSP 合规字段（M10）*

| 字段 | 控件 | 必填 | 校验 | 默认值 |
|---|---|---|---|---|
| `reg_cert_no` 注册证号 | 文本框 | 否 | 去空格 | `''` |
| `registrant` 注册人/备案人 | 文本框 | 否 | 去空格 | `''` |
| `mfg_enterprise` 生产企业 | 文本框 | 否 | 去空格 | `''` |
| `mfg_license` 生产企业许可证 | 文本框 | 否 | 去空格 | `''` |
| `temp_range` 温度范围 | 文本框 | 否 | 去空格 | `''` |
| `humidity_range` 湿度范围 | 文本框 | 否 | 去空格 | `''` |
| `hazard_type` 危险品类型 | 文本框 | 否 | 去空格 | `''` |

*标志位与备用字段*

| 字段 | 控件 | 必填 | 校验 | 默认值 | 说明 |
|---|---|---|---|---|---|
| `is_serial` 是否序列号 | 是否下拉（0/1） | 否 | 后端按真值转 0/1 | `0` | 当前仅标志位，M33 才升级为台账驱动 |
| `is_shelf_control` 是否保质期控制 | 是否下拉（0/1） | 否 | 同上 | `0` | — |
| `extra1` / `extra2` / `extra3` | 文本框 | 否 | 去空格 | `''` | 备用字段 |

*物流尺寸与价格库存*

| 字段 | 控件 | 必填 | 校验 | 默认值 | 来源 / 联动 |
|---|---|---|---|---|---|
| `weight_kg` 重量(kg) | 数字 | 否 | 数字、≥0 | `0` | — |
| `length_cm` 长(cm) | 数字 | 否 | 数字、≥0 | `0` | 参与体积自动换算 |
| `width_cm` 宽(cm) | 数字 | 否 | 数字、≥0 | `0` | 同上 |
| `height_cm` 高(cm) | 数字 | 否 | 数字、≥0 | `0` | 同上 |
| `volume` 体积(cm³) | 数字 | 否 | 数字、≥0 | `0` | **手填优先**；为空且长宽高齐全时前端先估算、后端再权威重算（长×宽×高，保留 4 位小数） |
| `shelf_life_days` 保质期(天) | 数字 | 否 | 整数、≥0 | `0` | M19 批次效期参照 |
| `purchase_price` 采购价 | 数字 | 否 | 数字、≥0 | `0` | — |
| `sale_price` 销售价 | 数字 | 否 | 数字、≥0 | `0` | — |

*上架推荐（M20 分组）与归属*

| 字段 | 控件 | 必填 | 校验 | 默认值 | 来源 / 联动 |
|---|---|---|---|---|---|
| `default_zone_id` 推荐储区 | 下拉（未设置推荐储区） | 否 | 整数或留空；**储区必须存在且启用**，否则 400 | `NULL` | 选项来自启用中的储区，标签含「编码 · 名称〔仓库｜温区〕」；上架推荐给该储区库位加分 |
| `owner_id` 货主 | 下拉（可空＝自营，`nullable`） | 否 | 整数或留空；**货主必须存在且启用** | `NULL` | 选项来自 `/api/owners?page_size=100` |
| `status` 状态 | 下拉（启用 active / 停用 inactive） | 否 | 枚举 | `active` | — |
| `note` 备注 | 文本域，整行 | 否 | 不去空格 | `''` | — |

> **表单未暴露但后端存在**：`stock_qty`（当前库存，列表展示，后端可写入但前端表单无输入）、
> `min_stock`（最低库存，后端可写入、前端无输入）、`id` / `created_at` / `updated_at`。
> 列表「当前库存」取 `product.stock_qty`，与 `stock` 主账是**两个不同的量**，不可混为一谈。

**[功能] 子表 / 弹窗**

*① 绑定货位（M20，弹窗宽 820）*

- 说明文案（页面原文）：`绑定货位在上架推荐中得分最高（100 + 优先级），高于「推荐储区」。只能绑定启用中的存储位 / 拣货位。`
- 列表列：库位、储区、仓库、优先级、备注、操作（解绑）；无绑定时提示（页面原文）：`暂无固定货位，推荐将按「推荐储区 + 聚拢规则」计算`。
- 录入：固定货位下拉（来源 `/api/locations?status=active&page_size=100`，前端再过滤 `storage` / `picking`，
  选项含储区与仓库）、优先级（数字，默认 0，越大越靠前）、备注 + `+ 添加绑定`。
- 端点：`GET /api/product-locations?product_id=`、`POST /api/product-locations`、
  `DELETE /api/product-locations/<id>`；错误显示在弹窗内 `#bindErr`。

*② 条码与包装（M10，弹窗宽 780）*

- **产品条码**：列表列 条码 / 主（★）/ 操作（编辑、删）；录入「输入条码」+「设为主条码」复选 + `+ 添加`；
  编辑弹窗可改条码值与主标记。设为主条码时后端先把同 SKU 其他条码 `is_primary` 置 0，保证**唯一主条码**。
- **包装层级**：列表列 层级 / 长 / 宽 / 高 / 体积 / 重 / 每箱数量 / 操作；
  录入下拉 `1级`~`6级` + 长宽高、重量、每箱数量 + `+ 添加包装`；
  体积由后端 `round(长×宽×高, 6)` 计算；编辑时长宽高任一变动则重算体积，否则沿用原值。

**[功能] 业务规则与状态机**

1. **SKU 唯一**：新建命中 → 409「SKU 已存在: xxx」；编辑改 SKU 排除自身。
2. **状态流转**：`启用 active ⇄ 停用 inactive`（表单下拉直接改），可逆；删除被拒时以停用替代。
3. **删除保护**：`stock_qty != 0` → 409「库存不为 0，禁止删除；请改用停用(status=inactive)」；删除前有 `confirm`。
   **注意**：删除只检查 `product.stock_qty`，未检查 `stock` 主账或单据引用。
4. **多货主隔离**：`owner_id` 非空时必须存在且 `active`（`_check_owner`），否则 400；留空＝自营 / 无货主。
5. **推荐储区校验**：`default_zone_id` 非空时必须存在且 `active`（`_check_zone`），否则 400。
6. **体积自动换算（前后端双算，后端为准）**：
   - 新建：未显式传 `volume` 且长宽高都非 0 → `round(长×宽×高, 4)`，否则 `0.0`。
   - 编辑：未显式传 `volume` 且改动了长宽高任一 → 按「新值 + 原值」混合重算；长宽高不全时**保持原体积**。
7. **部分更新**：`PUT` 只更新出现过的字段；空请求体 → 400「没有可更新的字段」。
8. **绑定货位**：一个 SKU 可绑多个货位，`UNIQUE(product_id, location_id)` 防重复（409）；
   目标库位必须 `active` 且 `loc_type ∈ (storage, picking)`；排序按 `priority DESC, id`。
9. **包装层级**：`pkg_level` 只能是 `1级`~`6级`；数值不可为负；体积由后端算，前端不传。
10. **列表过滤**：`keyword`（SKU/名称模糊）、`category` 精确、`status`、`owner_id`
    （特殊值 `none` 表示 `owner_id IS NULL`）、分页（默认 20 / 上限 100）。
11. **返回增强**：`_row_to_dict()` 补 `default_zone_name`（`编码 名称`）、`owner_code` / `owner_name`。

**[功能] 异常与提示**

| 触发 | 前端提示 | 后端响应 |
|---|---|---|
| SKU / 名称为空 | 表单 `required` 拦截 | 400 `sku 必填` / `name 必填` |
| SKU > 64 / 名称 > 128 | — | 400 `sku 最长 64 字符` / `name 最长 128 字符` |
| SKU 重复 | `showMsg` 显示错误 | 409 `SKU 已存在: xxx` |
| 数字字段非数字 / 为负 | 数字控件 | 400 `xxx 必须为数字` / `xxx 不能为负数` |
| `status` 非法 | 下拉限定 | 400 `status 只能是 active/inactive` |
| 货主不存在 / 已停用 | — | 400 `货主不存在: id=N` / `货主已停用: id=N` |
| 推荐储区不存在 / 已停用 | — | 400 `推荐储区不存在` / `推荐储区已停用` |
| 删除时库存非 0 | `confirm` 通过后失败提示 | 409 `库存不为 0，禁止删除；请改用停用(status=inactive)` |
| 绑定货位：未选货位 | 弹窗内「请先选择货位」 | — |
| 绑定货位：货位类型/状态不符 | 弹窗内显示错误 | 400 `只能绑定启用中的存储位/拣货位（上架目标位仅接受这两类）` |
| 绑定货位：重复绑定 | 弹窗内显示错误 | 409 `该货品已绑定此货位` |
| 条码为空 | 前端 return 不提交 | 400 `barcode 必填` |
| 包装层级非法 / 数值为负 | 下拉限定 | 400 `pkg_level 只能是 1级/2级/3级/4级/5级/6级` / `数值不能为负数` |
| 商品不存在（各子端点） | — | 404 `商品不存在` |
| 分页参数非整数 | — | 400 `page/page_size 必须为整数` |

**[实现] 关联单据与数据流向**

`product` 被 `asn_line.product_id`、`dn_line.product_id`、`stock.product_id`、
`stock_movement.product_id`、`batch.product_id`、`batch_receipt.product_id`、
`product_barcode` / `product_package` / `product_location` 子表引用。

- 收货明细选商品时，由 `asns.html` 的 `autoFill` 自动带出 **12 个**档案字段（名称 / 规格 / 货号 / 单位 /
  品牌 / 厂家 / 生产企业 / 产地 / 注册证号 / 注册人 / 生产许可证 / 效期）；明细行共 **13 个商品档案快照字段
  + 7 个批次字段 = 20 个**，其中 `udi` 可人工填写但不在自动带出映射内。**带出值只是默认值，全部可人工改写**，
  随单据留档并在验收入库时写入批次信息（M19）。
- `default_zone_id` → 上架推荐「推荐储区 60 分」；`product_location` → 「绑定货位 100 + 优先级」；
  `stock` 主账的库位占用量 → 「同产品聚拢 +30」（按 `product_id`，**非同分类**）；
  `batch_receipt.info` 中明确出现的批号 → 「同批号聚拢 +20」。
- **如实说明当前引擎未消费的字段**：`category_l1~l4`（同分类聚拢属 M25 条件组规划）、`product.volume`
  （剩余容量过滤依据 `location.capacity` 与本次上架数量，与商品体积无关）均未进入 `putaway_service.py`。
  当前打分只有 6 项：绑定货位 / 推荐储区 / 同产品聚拢 / 同批号聚拢 / 空库位 / 容量贴合。
- 列表「当前库存」取 `product.stock_qty`；真实库位库存以 `stock` 主账 + `stock_movement` 流水为准。

**[实现] 权限**

本页及 `/api/products/*`、`/api/product-locations/*` **未挂 `@require_permission`**，项目无全局鉴权钩子；
`api.js` 附 `Authorization` 头但后端不校验。全项目仅 `routes_user.py` 的角色 / 用户管理端点
（`/api/roles*`、`/api/users*`）使用 `@require_permission("user:manage")`。

**[功能] 与其他页面的关系**

- **货主 `owners.html`**：货主下拉数据源；货主停用后不能再挂新物料。
- **储区 `zones.html`**：推荐储区下拉数据源；储区停用后已设推荐储区的物料在编辑保存时会被 400 拒绝。
- **库位 `locations.html`**：绑定货位下拉数据源（仅启用的 storage / picking 位）。
- **收货 `asns.html`**：明细行含 13 个商品档案快照字段 + 7 个批次字段，其中 12 个档案字段自动带出；物料货主与 ASN 货主不一致时 400。
- **验收作业 `asn_receive.html` / 批次 `batches.html`**：验收写入批次信息，档案字段作为快照来源。
- **上架作业 `asn_putaway.html`**：推荐结果消费 `default_zone_id` 与 `product_location`。
- **工作台 `app.html`**：以内嵌 iframe 标签打开，`?embed=1` 隐藏内层侧栏。

**[实现] 验收要点**

- 对应测试：`tests/test_products.py`（建档校验、SKU 重复、分页搜索、部分更新、删除保护）、
  `tests/test_zone.py` / `tests/test_putaway.py`（推荐储区与绑定货位相关）。
- 可观察行为：新建 → 列表可见 → 编辑 → 体积自动换算 → 绑定货位 / 解绑 → 条码设主互斥 →
  包装层级增删 → 有库存时删除被 409 拒绝 → 停用后可查历史。
- 数据落点：`product` + 三个子表 `product_barcode` / `product_package` / `product_location`。

**[实现] 对应里程碑与提交**

M1 物料主数据 → M10 电商/医药级字段与多条码/多包装 → M15 体积手填与自动换算 →
M20 阶段 A 推荐储区 + 固定货位绑定（`7d51963` 后端 / `8a3318e` 前端）。
M20 阶段 B（`46e5038`）未改动本页。其余里程碑明细见 `DEVELOPMENT.md`。

---

## 3. API 与数据模型（单列一章）

> 用户口径 4「单列一章」：**端点清单与数据表字典独立成章**，页面记录里只引用端点名，不复制字段定义。
> 本章随批次增量补齐；当前仅收录第 1 批两页相关端点与表。

### 3.1 [实现] 全局约定

| 项 | 约定 |
|---|---|
| 响应体 | 成功 `{"ok": true, ...}`；失败 `{"ok": false, "errors": [...]}` 或 `{"ok": false, "error": "..."}` |
| HTTP 码 | 创建 201；校验失败 400；不存在 404；唯一冲突 / 删除保护 409 |
| 分页 | `page`（默认 1）、`page_size`（默认 20，上限 100）；返回 `total` / `page` / `page_size` |
| 关键字 | `keyword` 模糊匹配，SQL 用 `LIKE %kw%` |
| 时间 | `created_at` / `updated_at` 由 `datetime('now','localtime')` 生成；`PUT` 刷新 `updated_at` |
| 部分更新 | `PUT` 只更新请求体中出现的字段，空体 400 |
| 鉴权 | **无全局 `before_request` 钩子**；仅 `routes_user.py` 的角色 / 用户管理端点（`/api/roles*`、`/api/users*`）挂 `@require_permission("user:manage")`；`main._seed_auth()` 预置「管理员 / 操作员 / 只读」3 个系统级角色与 `admin` 账号，但业务接口不据此鉴权 |
| 多货主隔离 | 单据创建时校验物料 / 客户 / 供应商的 `owner_id` 与单据一致，不一致 400 |

### 3.2 [实现] 端点索引（第 1 批）

**货主 `/api/owners`**

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/owners` | 新建货主；编码唯一、默认值填充 |
| GET | `/api/owners` | 列表；`keyword` / `status` / `page` / `page_size` |
| GET | `/api/owners/<id>` | 详情 |
| PUT | `/api/owners/<id>` | 部分更新；刷新 `updated_at`；改编码时唯一校验排除自身 |
| DELETE | `/api/owners/<id>` | 删除；名下有物料 409 |
| GET | `/api/owners/<id>/certs` | 证照列表（按 id 升序） |
| POST | `/api/owners/<id>/certs` | 新增证照（`cert_type` 必填） |
| DELETE | `/api/owners/<id>/certs/<cert_id>` | 删除证照（校验归属） |

**物料 `/api/products`**

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/products` | 新建物料；SKU 唯一、货主/储区校验、体积换算 |
| GET | `/api/products` | 列表；`keyword` / `category` / `status` / `owner_id`（`none`＝无货主）/ 分页 |
| GET | `/api/products/<id>` | 详情（补 `default_zone_name` / `owner_code` / `owner_name`） |
| PUT | `/api/products/<id>` | 部分更新；体积联动重算 |
| DELETE | `/api/products/<id>` | 删除；`stock_qty != 0` 时 409 |
| GET | `/api/products/<id>/barcodes` | 条码列表（主条码优先） |
| POST | `/api/products/<id>/barcodes` | 新增条码；设主时同 SKU 其他条码置 0 |
| PUT | `/api/products/<id>/barcodes/<bid>` | 编辑条码 / 主标记 |
| DELETE | `/api/products/<id>/barcodes/<bid>` | 删除条码 |
| GET | `/api/products/<id>/packages` | 包装层级列表 |
| POST | `/api/products/<id>/packages` | 新增包装；体积后端计算 |
| PUT | `/api/products/<id>/packages/<pid>` | 编辑包装；长宽高变动则重算体积 |
| DELETE | `/api/products/<id>/packages/<pid>` | 删除包装 |

**关联端点（本页调用）**

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/owners?page_size=100` | 货主下拉 |
| GET | `/api/zones?page_size=100` | 推荐储区下拉 |
| GET | `/api/locations?status=active&page_size=100` | 绑定货位下拉 |
| GET | `/api/product-locations?product_id=` | 绑定列表（`priority DESC, id`） |
| POST | `/api/product-locations` | 新增绑定（货位须 active 且 storage/picking，唯一约束） |
| DELETE | `/api/product-locations/<id>` | 解绑 |

### 3.3 [实现] 数据表字典（第 1 批相关 7 张表）

> 格式：`列名 类型 [默认值]`；完整 DDL 与索引见 `main.py`。
> **演进说明**：下表是**最终可观测形态**＝`CREATE TABLE IF NOT EXISTS` 的基础列
> **＋ `init_db()` 里按 `PRAGMA table_info` 判断的幂等 `ALTER TABLE` 补列**。
> 基础 CREATE 并不包含后续里程碑的扩列（如 `owner` 的 M11 八列、`product` 的 M2 `owner_id` /
> M10 二十二列 / M20 `default_zone_id`），所以**新建库同样会执行这些 ALTER**；
> 仅 `product.volume` 因已写进基础 CREATE，其 M15 ALTER 只对更早的旧库生效。
> 因此「列在最终形态里」与「某里程碑才引入该功能」并不矛盾，列的**历史引入里程碑以第 4 章为准**，
> 判断依据是 `main.py` 中对应 ALTER 段落的注释，而非 CREATE 语句。

**`owner`（货主，M2 建表；M11 / M15 扩列）**
`id INTEGER PK` · `code TEXT UNIQUE NOT NULL` · `name TEXT NOT NULL` · `contact/phone/email/address TEXT ['']` ·
`start_date/end_date TEXT ['']` · `status TEXT ['active']` · `note TEXT ['']` ·
`created_at/updated_at TEXT [本地时间]`；M11 扩 `province` / `city` / `district` / `backup_address` /
`authorized_warehouse` / `business_scope` / `extra1` / `extra2`，均 `TEXT ['']`。索引 `idx_owner_status`。

**`owner_cert`（货主证照，M11）**
`id INTEGER PK` · `owner_id INTEGER NOT NULL REFERENCES owner(id)` ·
`cert_type/cert_no/issuer/valid_from/valid_to/note TEXT ['']` · `created_at TEXT [本地时间]`。索引 `idx_owner_cert_owner`。

**`product`（物料，M1 建表；M2 / M10 / M15 / M20 扩列）**
基础 CREATE（M1 / M15 已合并）：`id INTEGER PK` · `sku TEXT UNIQUE NOT NULL` · `name TEXT NOT NULL` ·
`category TEXT ['未分类']` · `unit TEXT ['件']` · `barcode/brand/spec TEXT ['']` ·
`weight_kg/length_cm/width_cm/height_cm/volume REAL [0]` · `shelf_life_days INTEGER [0]` ·
`purchase_price/sale_price/stock_qty/min_stock REAL [0]` · `status TEXT ['active']` ·
`note TEXT ['']` · `created_at/updated_at TEXT [本地时间]`。
- **M2** 扩 `owner_id INTEGER REFERENCES owner(id)`（NULL＝自营 / 未指定货主）；索引 `idx_product_owner`。
- **M10** 扩 `article_no` / `category_l1~l4` / `product_type` / `origin` / `business_scope` / `reg_cert_no` /
  `registrant` / `mfg_enterprise` / `mfg_license` / `external_code` / `main_category` / `temp_range` /
  `humidity_range` / `hazard_type` / `extra1~3`（`TEXT ['']`）、`is_serial` / `is_shelf_control`（`INTEGER [0]`）。
- **M15** 引入 `volume REAL [0]`（可手填；未填按长宽高自动换算），**已合并进基础 CREATE**，
  其 ALTER 仅对早期旧库生效。
- **M20** 扩 `default_zone_id INTEGER REFERENCES zone(id)`。
索引 `idx_product_category` / `idx_product_status`（基础 CREATE）。

**`product_barcode`（多条码，M10）**
`id INTEGER PK` · `product_id INTEGER NOT NULL` · `barcode TEXT NOT NULL` · `is_primary INTEGER [0]` ·
`created_at TEXT`。索引 `idx_pb_product` / `idx_pb_barcode`。

**`product_package`（包装层级，M10）**
`id INTEGER PK` · `product_id INTEGER NOT NULL` · `pkg_level TEXT ['主箱']` ·
`length_cm/width_cm/height_cm/volume/weight_kg/qty_per_package REAL [0]` · `created_at TEXT`。
索引 `idx_pp_product`。后端枚举为 `1级`~`6级`（与 DDL 默认 `主箱` 不同，以代码校验为准）。

**`product_location`（固定货位绑定，M20）**
`id INTEGER PK` · `product_id INTEGER NOT NULL REFERENCES product(id) ON DELETE CASCADE` ·
`location_id INTEGER NOT NULL REFERENCES location(id) ON DELETE CASCADE` · `priority INTEGER [0]` ·
`note TEXT ['']` · `created_at TEXT` · `UNIQUE(product_id, location_id)`。

**`zone`（储区，M20，本页只作参照）**
`id INTEGER PK` · `code TEXT UNIQUE NOT NULL` · `name TEXT NOT NULL` ·
`warehouse_id INTEGER REFERENCES warehouse(id)`（空＝全仓共享） · `temp_class TEXT ['常温']` ·
`allow_mix_product/allow_mix_batch/allow_mix_owner INTEGER [0]` · `status TEXT ['active']` ·
`note TEXT ['']` · `created_at/updated_at TEXT`。索引 `idx_zone_warehouse` / `idx_zone_status`。

---

## 4. 里程碑与页面归属索引

| 里程碑 | 主题（以 `DEVELOPMENT.md` Roadmap 为准） | 主要页面 | 状态 |
|---|---|---|---|
| M1 | 物料主数据 | `products.html` | 已完成 |
| M2 | 货主主数据（含 `product.owner_id` 多货主挂接） | `owners.html` / `products.html` | 已完成 |
| M3 | 库位主数据 | `locations.html` | 已完成 |
| M4 | 收货单与上架 | `asns.html` | 已完成 |
| M5 | 出库单与拣货发货 | `dns.html` | 已完成 |
| M6 | 库存账与流水 | `stock.html` | 已完成 |
| M7 | 客户主数据 | `customers.html` | 已完成 |
| M8 | 基础资料字段增强 + 演示数据播种 | 多页 | 已完成 |
| M9 | 用户与角色管理 | `users.html` / `roles.html` / `login.html` | 已完成 |
| M10 | 物料主数据深化 | `products.html` | 已完成 |
| M11 | 货主主数据深化 | `owners.html` | 已完成 |
| M12 | 客户主数据深化 | `customers.html` | 已完成 |
| M13 | 库位主数据深化 | `locations.html` | 已完成 |
| M14 | 承运商模块 | `carriers.html` | 已完成 |
| M15 | 主数据体验增强（体积换算 / 条码包装可编辑 / 承运商播种 / 货主合作起止） | `products.html` / `owners.html` / `carriers.html` | 已完成 |
| M16 | 供应链与单据主数据增强 | `suppliers.html` / `warehouses.html` / `asns.html` / `dns.html` | 已完成 |
| M17 | 移库单（库位间一减一增，原子事务） | `transfers.html` | 已完成 |
| M18 | 盘点单（冻结 → 盲盘 → 差异调整 → 解冻） | `stocktakes.html` | 已完成 |
| M19 | 批次与验收入库追溯（批次配置 / 批次档案 / 每次入库唯一批次流水） | `batches.html` / `asns.html` | 已完成 |
| M20 | 储区与上架作业基础（阶段 A 基础资料与推荐作业页 / 阶段 B 单据标识位） | `zones.html` / `asn_receive.html` / `asn_putaway.html` / `locations.html` / `products.html` / `asns.html` / `dns.html` / `app.html` | 已完成 |
| 非里程碑 | 工作台与收货单体验增强（M18 后 UI；M20-A 再改侧栏伸缩） | `app.html` | 已完成 |

> 里程碑编号权威来源为 `DEVELOPMENT.md` Roadmap；本表只做「里程碑 ↔ 页面」映射，不重述交付细节。
> **编号校准说明**：早期提交信息里出现过 `M16 移库单`（`71d3c58`）、`M16 移库前端`（`915d496`）、
> `M17 盘点单`（`546e9dc`）等旧口径；`97137e2` 已完成「里程碑编号校准（移库=M17 / 盘点=M18）」。
> 本表及全文**一律以校准后的 Roadmap 编号为准**，不按历史提交信息推断。
> 后续里程碑 M21–M26、M31+ 见 `docs/DOMAIN_BLUEPRINT.md` v3。

---

## 5. 记录变更日志

| 版本 | 日期 | 变更 | 依据 |
|---|---|---|---|
| v0.1 | 2026-11-05 | 建立模板与素材库骨架；回溯记录第 1 批 2 页（货主、物料）；建立 API / 数据模型单列章与 21 页覆盖进度表 | 用户第四轮规格确认 1A / 2A / 3D / 4 单列一章 / 5 是 / 6 Markdown；取证自 `web_app/owners.html`、`web_app/products.html`、`routes_owner.py`、`routes_product.py`、`routes_product_location.py`、`main.py` |
| v0.2 | 2026-11-05 | 自审 + 独立交叉审阅修正：① 里程碑编号校准（M15 不含移库 / M17 移库 / M18 盘点 / M19 批次与验收入库追溯；移库页归属 M15→M17；工作台改列「非里程碑」）；② 删除 2 处「把未实现写成已实现」（同分类聚拢、`product.volume` 参与容量过滤），补记当前推荐引擎 6 项打分；③ 修正 DDL 演进机制表述、`product.owner_id` 归属 M2 与 `volume` 重复标注；④ 补齐 `pkg_level` 错误串全文、前端文案原文、明细行 20 字段口径（13+7、自动带出 12）、预置 3 个系统级角色与鉴权范围 | 取证自 `DEVELOPMENT.md` Roadmap、`git log --diff-filter=A`、`main.py`、`putaway_service.py`、`web_app/asns.html`、`web_app/products.html`、`routes_product.py`；交叉审阅为独立只读核对，未改代码 |
| v0.3 | 2026-11-05 | 追加 §6「全局数据权限与隔离约束」（架构增强，非里程碑）：三层隔离、账号可见仓/货主授权、ALL 显式化、页面控件增量、端点与 DDL 增量、验收要点。**按增量追加规则不改写 §1–§5 已有内容**；21 页逐页记录进度不变（仍 2/21） | 取证自 `main.py`、`scope.py`、`routes_scope.py`、`routes_owner.py`、`routes_warehouse.py`、`routes_carrier.py`、`auth.py`、`web_app/js/api.js`、`web_app/js/layout.js`、`web_app/app.html`、`web_app/users.html`、`web_app/warehouses.html`、`web_app/carriers.html`、`tests/test_scope.py`、`DEVELOPMENT.md` 文末同名章节 |
| v0.4 | 2026-11-05 | 追加 §6.5「货主 × 仓库业务关系格子」（架构增强第二次优化，非里程碑）：多对多关系表、可见范围取交集、矩阵配置页、兼容边界、验收基线更新为 309 例 + E2E 13/13；追加数据库选型口径（继续方案 A，PG/RLS 为未来路线）。**按增量追加规则不改写 §1–§6.4 已有内容**；21 页逐页记录进度仍为 2/21 | 取证自 `scope.py`、`main.py`、`routes_owner_warehouse.py`、`web_app/owner_warehouses.html`、`web_app/js/layout.js`、`tests/test_owner_warehouse.py`、`docs/DOMAIN_BLUEPRINT.md` §9、`DEVELOPMENT.md` 文末同名章节 |

---

## 6. 全局数据权限与隔离约束（2026-11-05 架构增强，非里程碑）

> **取证纪律**：本章只记录代码里真实存在的实现，不把设计文档中的规划写成现状（见 v0.2 交叉审阅教训）。
> **对 PRD 的影响**：这是**全局约束章**，后续 21 页逐页记录中「权限」一项只引用本章 + 写出本页特有差异，
> 不重复展开三层架构。**不占用任何 M 编号**，Roadmap 与实施顺序不变（下一里程碑仍 M21）。

### 6.1 [业务] 用途与背景

- 用户生产系统经验：仓库数据按数据库分库隔离，并用配置决定某仓落到哪个库；「做视图会很卡」——
  视图过滤会把天然分区键 `warehouse_id` 降级成 WHERE 条件，单仓作业查询性能差。
- 3PL WMS 里两个隔离维度不同：**仓库**是运营方资产、天然按容器 / 库隔离；**货主**在同一仓内共享表，
  需要行级隔离。因此仓库走「连接层选库」，货主走「SQL 层自动注入」，不混用一套机制。
- 用户诉求：① 不同仓库数据隔离；② 不同货主数据隔离；③ 一个账号可看全部 / 部分仓库，
  并可配置自定义货主查看权限（部分 / 全部）。
- 用户确认的最终口径：**「直接做完整版」**；「看所有仓库」＝ **A 作业视角**（可切换到任意有权限仓，
  一次只看一个仓，不做一屏跨仓汇总）；基础资料（承运商 / 客户 / 供应商）预留货主维度便于后期权限管控；
  演示系统必须「下载下来就能用」。

### 6.2 [功能] 权限模型与页面影响

| 维度 | 规则 | 页面 / 控件 |
|---|---|---|
| 仓库（连接层） | `warehouse.db_path` 留空＝共用主库（默认）；非空＝该仓业务数据独立 SQLite 文件 | `warehouses.html`：表单「数据隔离」分组的 `db_path`、列表「数据隔离」列（独立库 / 共用主库） |
| 仓库（切换） | 前端统一附加 `X-WMS-Warehouse`；一次只看一个仓 | 独立页侧栏 `#whPickBox`、工作台顶栏 `#wsWhPick` 的「当前仓库」下拉（共用 `api.js::mountWarehousePicker()`）；工作台切仓后重载全部 iframe 标签 |
| 货主（SQL 层） | 账号授权集合自动注入 `owner_id IN (...)`；`product/carrier` 的 NULL 视为公共数据仍可见 | 13 个业务列表页的可见数据范围；无独立控件 |
| 授权配置 | `all`＝全部 / `grant`＝按勾选；授权行缺失即 403 拒绝 | `users.html`：新增「数据范围」列 +「数据权限」弹窗（仓库 / 货主双列勾选，切到「全部」时勾选框自动禁用） |
| 基础资料预留 | `carrier.owner_id` 新增（NULL＝全系统共享） | `carriers.html`：表单「归属货主」下拉、列表「归属货主」列；`owner_id=none` 只看共享承运商 |

**关键纪律（PRD 权限章必须写明）**：`ALL` 是授权解析后**显式**给出的标志（`user.warehouse_scope` /
`user.owner_scope` = `all`），不是「上下文缺失 ⇒ 不加条件」；缺失上下文一律 403 拒绝。
`auth.py::has_permission()` 同样去掉「角色名＝管理员」的隐式提权，改由角色 `permissions` 里显式的 `*` 放行。
匿名演示模式由 `MINIWMS_ALLOW_ANONYMOUS`（默认 `1`）显式给出 `mode='anonymous-demo'` 的 ALL 上下文；
设为 `0` 强制登录，保证 clone 即用与生产可收紧两者兼顾。

### 6.3 [实现] 端点与数据模型增量

**DDL 增量**（老库由 `main._migrate_scope_columns()` 幂等 `ALTER` 补列，新库写进 CREATE）：

| 表 | 新增 | 口径 |
|---|---|---|
| `warehouse` | `db_path TEXT NOT NULL DEFAULT ''` | 空＝共用主库；只许项目目录内 `.db` 相对路径（防路径穿越） |
| `carrier` | `owner_id INTEGER REFERENCES owner(id)` | NULL＝全系统共享，非空＝归属货主 |
| `user` | `warehouse_scope` / `owner_scope` `TEXT NOT NULL DEFAULT 'grant'` | `all` / `grant` |
| 新增 | `user_warehouse_grant(user_id, warehouse_id)` | 复合主键 |
| 新增 | `user_owner_grant(user_id, owner_id)` | 复合主键 |

**控制面 / 数据面**：`get_control_db()` 管 `warehouse` / `owner` / `role` / `user` / 两张授权表
（`routes_owner.py`、`routes_warehouse.py`、`routes_user.py` 全部走它）；`get_db()` 是业务库唯一挂点，
17 个业务 Blueprint 未改连接代码。分库时 `sync_control_tables()` 把货主 / 仓库主数据同步为业务库只读副本。

**新增端点**（`routes_scope.py`）：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/scope/context` | 权限摘要 + 可切换仓清单 + 当前仓；软解析端点 |
| GET | `/api/scope/warehouses` | A 作业视角可见仓清单 |
| GET | `/api/scope/owners` | 可见货主清单 |
| POST | `/api/scope/warehouse` | 校验切仓合法性并回显目标仓 |
| GET / PUT | `/api/users/<id>/grants` | 读取 / 配置账号仓库·货主范围（PUT 需 `user:manage`） |

**集中守门**（`main.py::@app.before_request _bind_scope()`）：① 写请求体顶层 `owner_id` 归属校验；
② 按 ID 访问资源的归属校验（`RESOURCE_OWNER_TABLES` 9 类 + `BATCH_RESOURCE_TABLES` 2 类二级路径），
覆盖详情 / 更新 / 删除 / 单据动作端点。货主主数据归属守门查中央库，避免业务库副本滞后漏判。

### 6.4 [实现] 验收要点与边界

- `tests/test_scope.py` 14 例：DDL、admin 显式 ALL、匿名模式开关、无授权账号 403、部分货主过滤、
  all/grant 切换、写路径越权、详情守门、承运商归属货主、部分仓授权与切仓拒绝、全部仓、
  `db_path` 路径校验、按仓分库物理隔离、管理员 `*` 显式权限。全量 **299 例绿**。
- 真实 Chrome E2E **15/15**：侧栏 / 顶栏切仓器与 header、工作台不重复渲染、授权弹窗勾选联动、
  仓库 `db_path` 字段、承运商归属货主字段、7 页冒烟、无 JS 错误、业务 404 为 0。
- **边界**：跨仓汇总报表不在本次范围（需未来独立只读汇总通道）；授权按账号维度，无角色级模板；
  `user.owner_id` 单值字段保留为历史预留，实际隔离以 `user_owner_grant` 为准；
  新增受控表必须同步接入 `S.scoped_where()` 或按 ID 守门白名单。

> **时点说明**：§6.4 记录的「299 例绿 / E2E 15/15」是 **v0.3 时点基线**，按增量追加规则保留原文；
> 最新基线见 §6.5.4。

### 6.5 货主 × 仓库业务关系「格子」（2026-11-05 第二次优化，非里程碑）

#### 6.5.1 [业务] 用途与背景

- 用户口径：「货主和仓库之间不是一对一关系，而是多对多关系」。典型矩阵：
  货主 A 在杭州仓 / 上海仓有库存、南京仓无业务；货主 B 在杭州仓 / 南京仓有库存、上海仓无业务。
- §6 初版隔离回答的是「**这个人能不能看货主 B**」（账号授权），但没有回答
  「**货主 B 在这个仓到底有没有业务**」（业务关系）。两者必须同时成立，
  否则账号被授权「杭州 + 上海仓」和「货主 A + B」时，`B × 上海仓` 这个并不存在的组合也会被放行。
- 本页用途：由管理员登记货主与仓库的业务关系，让**可见范围 = 账号授权 ∩ 当前仓业务关系**，
  用于业务评审、越权演示与后续货主门户的数据边界。

#### 6.5.2 [功能] 页面与权限增量

| 页面 | 归属 | 新增功能点 |
|---|---|---|
| 「系统管理 → 权限 → 货主仓库关系」`owner_warehouses.html`（新页） | 全局约束配套页，**不计入 21 页回溯范围** | 行＝货主、列＝仓库的勾选矩阵；表头显示仓库编码 / 名称、「已启用 / 未启用」与「全选」；保存逐仓提交；刷新重拉；每次加载按该列实际勾选回显全选框 |
| 全部业务列表页 | 无页面改动 | 当前仓启用格子后，列表自动只显示登记货主的数据；详情 / 新建 / 编辑 / 单据动作同样被收紧 |
| `/api/scope/context` 消费方 | 无页面改动 | 新增 `grid_enabled` / `grid_warehouse_id` / `grid_owner_ids` 三个字段，可用于前端提示与排障 |

- **页面说明文案（原文，评审可引用）**：勾选＝该货主在这个仓有业务；某个仓只要有一个勾，
  就会启用「格子过滤」，即便账号有「全部货主」权限，在该仓也只能看到勾过的货主；
  把某个仓的勾全部取消＝关闭该仓格子过滤，行为退回纯粹账号授权；账号授权与格子取交集；
  未切换仓库的请求不启用格子。
- **权限**：矩阵页需登录且具备 `user:manage`；未登录由 `requireLogin()` 引导登录页。

#### 6.5.3 [实现] 端点与数据模型增量

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/owner-warehouses` | 矩阵数据：`owners`、`warehouses`、`cells`（active 格子）、`enabled_warehouse_ids` |
| PUT | `/api/owner-warehouses/<warehouse_id>` | 按仓全量替换 `owner_ids`；空数组＝关闭该仓格子；校验仓库 / 货主存在与数组类型 |

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

- 表在**中央库（控制面）权威**；业务库只是 `init_db()` 幂等带出的空壳，跨仓同步不包含此表。
- `scope.apply_owner_warehouse_grid()` 在 `before_request` 里执行：读当前仓 active 登记 →
  与账号 `owner_ids` 求交集 → `owner_all` 强制置 False → 原地写回 `g.scope`。
  13 个已接入 `S.scoped_where()` 的列表接口、写路径守门、按 ID 守门**全部自动生效，路由零改动**。
- `owner_warehouse` 属 `PUBLIC_TABLES` 白名单，本身不按货主注入（它是控制面配置表）。

#### 6.5.4 [实现] 验收要点与边界

- `tests/test_owner_warehouse.py` 10 例：DDL 表与索引、零配置保持旧行为、ALL 账号被格子收紧、
  业务列表过滤、写路径 403、按 ID 守门 403、授权与格子取交集、按仓独立、
  配置端点回读与三类校验、清空后关闭过滤。全量 **309 例绿**（299 → 309）。
- 真实 Chrome E2E（独立临时库 + 端口 5109 + admin token）**13/13**：矩阵渲染、状态标签、
  切仓器与导航入口、保存回读、该仓只看到登记货主、清空后恢复、无 JS 运行时错误。
- **兼容边界（PRD 权限章必须写明）**：
  ① 不带 `X-WMS-Warehouse` 的请求不启用格子（后端脚本 / 直接调 API 保持原行为）；
  ② 某仓零 active 登记＝功能未启用，**这是功能开关不是授权上下文**，
  `resolve_scope()` 的「授权缺失一律 403」铁律未被放宽，表缺失不会把账号放大成 ALL；
  ③ 有登记即严格白名单，清空立即关闭，无需重启（授权每次请求实时解析）；
  ④ 按仓独立，A 仓启用不影响 B 仓。
- **数据库选型口径**：用户 2026-11-05 确认继续方案 A（SQLite + 应用层隔离），
  「多 Tenant 3PL SaaS + PostgreSQL + RLS」写入 `docs/DOMAIN_BLUEPRINT.md` §9
  作为**未来演进路线、非当前实现**；PRD 中不得写成已实现能力。

