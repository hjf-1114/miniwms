# StockPilot 开发记录

每个里程碑记录：目标、改动清单、可验证结果。里程碑保持单目标内聚，按开发顺序演进。

## M1 商品档案（2026-10-30）

**目标**：建立项目骨架与商品档案完整 CRUD，作为后续采购/销售/盘点模块的参照数据。

**改动清单**：
- `main.py`：Flask 入口、`get_db()` 请求级连接、`init_db()` 建 `product` 表（SKU 唯一索引、分类/状态索引）、Blueprint 聚合
- `routes_product.py`：`/api/products` 增删改查 + 分页 + 关键词/分类/状态过滤 + SKU 去重 + 价格非负校验 + 库存非 0 禁删保护
- `tests/test_products.py`：12 个用例（创建校验/重复 SKU/分页搜索过滤/部分更新/删除保护）
- `README.md` / `requirements.txt`（仅 Flask）/ `.gitignore`

**可验证结果**：
- `python -m unittest discover -s tests` 全部通过
- SKU 重复创建返回 409；负价格返回 400；库存非 0 删除返回 409
- 库存字段建档默认 0，M1 不提供直接改库存入口（保证余额只能由单据驱动）

## 规划（Roadmap）

- M2 供应商档案
- M3 采购入库单（stock_qty 增加 + 采购流水）
- M4 销售出库单（stock_qty 扣减 + 销售流水 + 超卖拦截）
- M5 库存流水账（余额 = 流水累加可核对）
- M6 盘点调整（差异生成调整流水）
- M7 低库存预警（min_stock 对比查询）
- M8 报表统计（采购/销售汇总、毛利）
- M9 CSV 导入导出
- M10 简易鉴权 + API 文档收尾
