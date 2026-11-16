# 数据合约（DATA_CONTRACT）

## 1. 标准长表 CSV

一行 = 一个标的的一个交易日。列名与类型：

| 列 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `date` | `YYYY-MM-DD` | ✅ | 交易日 |
| `code` | 6 位字符串 | ✅ | 保留前导零，如 `000001` |
| `open` / `high` / `low` / `close` | float | ✅ | **前复权**价格 |
| `volume` | float | ✅ | 成交量（手） |
| `amount` | float | ➖ | 成交额（元）；缺省时按 `volume×100×(H+L+C)/3` 近似 |
| `name` | str | ➖ | 名称 |
| `industry` | str | ➖ | 行业分类；缺失填 `UNKNOWN`，**不得猜测** |
| `is_st` | 0/1 | ➖ | ST 标记；缺失时按 0 处理并登记缺口 |
| `listed_date` | `YYYY-MM-DD` | ➖ | 上市日；缺失时以首个观测日作**下界代理** |

`load_prices()` 会做：日期规范化、代码补零、数值强制转换、非正价格剔除、`(date, code)` 去重、按日期排序。

## 2. 数据源与层级

| 用途 | 来源 | 层级 | 时点 |
|---|---|---|---|
| 主行情 | 腾讯证券 `web.ifzq.gtimg.cn` 前复权日线 | L3 第三方 | 2018-01-02 ~ 2026-11-13 |
| 交叉校验 | 通达信 MCP `tdx_kline`（`period=4, tqFlag=1`） | L3 第三方 | 600519 于 2026-11-13 |

**跨源校验记录**：600519 在 2026-11-13，腾讯返回 `open=1404.000, close=1435.260, high=1437.690, low=1394.000, volume=41780(手)`；通达信返回 `open=1404.000000, close=1435.260010, high=1437.689941, low=1394.000000, volume=41779.84`，逐字段一致。已固化为 `tests/test_core.py::test_cross_source_ohlc_600519`。

演示样本规模：原始 CSV **42,093 行**，面板化清洗后入模 **42,091 根 bar** / 20 只 / 2,150 个交易日 / 覆盖率 97.89%（部分标的上市较晚，如 688981 仅 1,529 根）。两个数字口径不同，报告与质量门统一以 `result.json` 的 `meta.sample.bars = 42091` 为准。

## 3. 适配器

```python
from quantlab.data import load_prices, MarketData, from_tdx_kline_json

md = MarketData(load_prices("data/sample_real/prices.csv"), cfg)

# 通达信 MCP 导出的 tdx_kline JSON → 标准长表
df = from_tdx_kline_json("data/raw/600519.json", code="600519", name="贵州茅台")
```

`from_tdx_kline_json()` 直接取 `Rows.Volume` / `Rows.Amount`（工具已换算完毕），**禁止再按 Unit / setcode 二次换算**。

## 4. 交易约束的派生口径

- 涨跌停：`round(prev_close × (1 ± limit), 2)`；主板 10%、创业板/科创板 20%、北交所 30%。
- 可买：`open < 涨停价`；可卖：`open > 跌停价`；无开盘价视为停牌。
- 可入池 `eligible`：`tradable AND not ST AND age_days >= 120 AND prev_close 非空`。
- 上市不足 120 日的判定使用「首个观测日」代理，**不是真实 IPO 日**，属口径折损，已登记。

## 5. 已登记的数据缺口（不得静默忽略）

1. **行业分类缺失** → `industry=UNKNOWN`，行业中性化与行业权重上限在本次样本中不生效。
2. **历史 ST 标记缺失** → ST 历史剔除无法回溯验证。
3. **退市股 / 成分股历史变动缺失** → 全 A 回测存在幸存者偏差；演示样本是「当前存续的 20 只大中盘」，天然规避小盘流动性与退市风险，会系统性高估表现。
4. **PIT 财务数据缺失** → EP / BP / ROE 因子插件未启用；接入时必须同时提供 `report_date` 与 `publish_date`，按可得时点滞后，否则构成前视偏差。
5. **成交额为近似值** → 仅用于横截面排序，不用于真实参与率或容量测算。

## 6. 升级到生产级全 A 面板的清单

- [ ] 含退市股、历史 ST、历史行业分类的**点时间（PIT）**成分面板
- [ ] 财务三表 + `publish_date`，按公告可得时点对齐
- [ ] 交易所真实 `amount`（而非近似）与日均成交额分档
- [ ] 指数成分历史变动（避免用当前成分回溯）
- [ ] 停复牌、分红送转、配股事件表
- [ ] 交易规则历史变更（注册制涨跌幅调整）

补齐后须重跑 `python -m quantlab run`，并重新过质量门。
