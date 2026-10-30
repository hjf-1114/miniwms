# StockPilot — 轻量进销存系统

一个面向小微商户的进销存 REST API 服务：管理商品档案、供应商、采购入库、销售出库、
库存流水与盘点，库存余额始终可由流水推导核对。

## 技术栈

- Python 3.8+（仅标准库）
- Flask（唯一第三方依赖，见 `requirements.txt`）
- SQLite（单文件数据库，零配置）

## 快速开始

```bash
pip install -r requirements.txt
python main.py            # 默认监听 0.0.0.0:5100，数据库文件 ./stockpilot.db
```

运行测试：

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

## 功能范围

### 已实现

- **M1 商品档案**（`routes_product.py`）
  - 商品增删改查：SKU 唯一约束、名称/分类/单位/进价/售价/最低库存
  - 列表：关键词模糊搜索（SKU/名称）、分类与状态过滤、分页（默认 20 条，上限 100）
  - 保护规则：库存不为 0 的商品禁止物理删除，只能停用（`status=inactive`）

### 规划中（见 DEVELOPMENT.md）

- 供应商档案、采购入库、销售出库、库存流水账、盘点调整、低库存预警、报表统计、CSV 导入导出

## API 概览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/products` | 创建商品（SKU 必填唯一，价格非负） |
| GET | `/api/products` | 列表，支持 `keyword`/`category`/`status`/`page`/`page_size` |
| GET | `/api/products/<id>` | 商品详情 |
| PUT | `/api/products/<id>` | 部分更新 |
| DELETE | `/api/products/<id>` | 删除（库存非 0 时拒绝） |

## 项目结构

```
main.py               Flask 入口、数据库初始化与连接管理
routes_product.py     商品档案 Blueprint
tests/                单元测试（unittest，临时库隔离）
DEVELOPMENT.md        里程碑开发记录
```

## 设计约定

- 所有接口返回 JSON：成功 `{"ok": true, ...}`，失败 `{"ok": false, "errors": [...]}` + 对应 4xx 状态码
- 库存数量 `stock_qty` 只允许由业务单据（采购/销售/盘点）驱动变化，M1 阶段仅随建档初始化为 0
- 时间戳由 SQLite `datetime('now','localtime')` 生成
