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

### 1.1 双价格文件（v3）

| 文件 | 抓取命令 | 复权口径 | 用途 |
|---|---|---|---|
| `data/sample_real/prices.csv` | `python scripts/fetch_tencent.py --adjust qfq --out data/sample_real/prices.csv` | 前复权（`qfqday`） | 收益计算、盯市、成交价、成本记账 |
| `data/sample_real/prices_raw.csv` | `python scripts/fetch_tencent.py --adjust none --out data/sample_real/prices_raw.csv` | 未复权（`day`） | **仅**用于涨跌停价格基准（`prev_close_raw` / `open_raw`） |

两份文件由 `config.data.prices_csv` 与 `config.data.raw_prices_csv` 指向；列名与类型完全一致，便于同一 `load_prices()` 解析。`--adjust` 只切腾讯接口节点，不改动标准长表结构。未复权文件不会被用于收益或盯市，**禁止把两套价格混算**，否则除权日会产生虚假收益。

## 2. 数据源与层级

| 用途 | 来源 | 层级 | 时点 |
|---|---|---|---|
| 主行情（收益/盯市/成交） | 腾讯证券 `web.ifzq.gtimg.cn` 前复权日线 `qfqday` | L3 第三方 | 2018-01-02 ~ 2026-11-13 |
| 涨跌停基准 | 腾讯证券 `web.ifzq.gtimg.cn` 未复权日线 `day` | L3 第三方 | 同期、同 20 只标的 |
| 交叉校验 | 通达信 MCP `tdx_kline`（`period=4, tqFlag=1`） | L3 第三方 | 600519 于 2026-11-13 |

**跨源校验记录**：600519 在 2026-11-13，腾讯返回 `open=1404.000, close=1435.260, high=1437.690, low=1394.000, volume=41780(手)`；通达信返回 `open=1404.000000, close=1435.260010, high=1437.689941, low=1394.000000, volume=41779.84`，逐字段一致。已固化为 `tests/test_core.py::test_cross_source_ohlc_600519`。

演示样本规模：原始 CSV **42,093 行**，面板化清洗后入模 **42,091 根 bar** / 20 只 / 2,150 个交易日 / 覆盖率 97.89%（部分标的上市较晚，如 688981 仅 1,529 根）。两个数字口径不同，报告与质量门统一以 `result.json` 的 `meta.sample.bars = 42091` 为准。

## 3. 适配器

```python
from quantlab.data import load_prices, MarketData, from_tdx_kline_json

md = MarketData(load_prices("data/sample_real/prices.csv"), cfg)
# cfg.data.raw_prices_csv 存在且覆盖率达标时，MarketData 自动载入未复权面板
print(md.price_basis, md.raw_coverage, md.wiring)

# 通达信 MCP 导出的 tdx_kline JSON → 标准长表
df = from_tdx_kline_json("data/raw/600519.json", code="600519", name="贵州茅台")
```

`from_tdx_kline_json()` 直接取 `Rows.Volume` / `Rows.Amount`（工具已换算完毕），**禁止再按 Unit / setcode 二次换算**。

`MarketData.wiring` 是配置接线审计段，逐项声明 `universe.exclude_st`、`exclude_suspended`、`min_listed_days`、`min_avg_amount_20d` 是否真正生效，以及 `raw_prices_loaded`、`price_basis`、`raw_coverage`，供 `audit.config_wiring` 消费。任何配置开关只写在 JSON 里却没进代码，都会被质量门判为不成立。

## 4. 交易约束的派生口径

- 涨跌停：`round(base_prev × (1 ± limit), 2)`；主板 10%、创业板/科创板 20%、北交所 30%。
- **v3 双价格**：启用未复权面板时 `base_prev = prev_close_raw`、`base_open = open_raw`；未启用时回退到前复权 `prev_close` / `open`。收益、盯市、成交价始终用前复权面板，二者不混算。
- **覆盖率门槛**：未复权面板对当前样本日期的覆盖率 <50% 时不启用，并把 `price_basis` 标为 `qfq_only__raw_coverage_too_low`。这条回退是为了让合成面板单测仍能检验交易逻辑，而不是用「测试绕过」掩盖缺陷。
- 可买：`base_open < 涨停价`；可卖：`base_open > 跌停价`；无开盘价视为停牌。
- 可入池 `eligible`：`tradable AND not ST AND age_days >= 120 AND prev_close 非空`，并接线 `universe.min_avg_amount_20d`（>0 时按 20 日均额过滤）。
- 上市不足 120 日的判定使用「首个观测日」代理，**不是真实 IPO 日**，属口径折损，已登记。
- 被拒绝的订单登记 `unfilled_history` 并在下一个 pending 日重试，不静默丢弃。

## 5. 已登记的数据缺口（不得静默忽略）

1. **行业分类缺失** → `industry=UNKNOWN`，行业中性化与行业权重上限在本次样本中不生效。
2. **历史 ST 标记缺失** → ST 历史剔除无法回溯验证。
3. **退市股 / 成分股历史变动缺失** → 全 A 回测存在幸存者偏差；演示样本是「当前存续的 20 只大中盘」，天然规避小盘流动性与退市风险，会系统性高估表现。
4. **PIT 财务数据缺失** → EP / BP / ROE 因子插件未启用；接入时必须同时提供 `report_date` 与 `publish_date`，按可得时点滞后，否则构成前视偏差。
5. **成交额为近似值** → 仅用于横截面排序，不用于真实参与率或容量测算；容量模块因此只在给出资金规模时给出**基于该近似值的模型估计**，不得当作事实。
6. **除权除息 / 分红送转流水缺失** → 成交价无法切到未复权口径，否则现金与分红不自洽；双价格只完成「涨跌停基准」这半步，质量门按「存疑」定级。
7. **真实宽基指数与指数成分历史缺失** → 择时与 beta 用等权样本指数代理，系统性风险读数带此口径折损。

## 6. 升级到生产级全 A 面板的清单

- [ ] 含退市股、历史 ST、历史行业分类的**点时间（PIT）**成分面板
- [ ] 财务三表 + `publish_date`，按公告可得时点对齐
- [ ] 交易所真实 `amount`（而非近似）与日均成交额分档
- [ ] 指数成分历史变动（避免用当前成分回溯）
- [ ] 停复牌、分红送转、配股事件表（含除权除息因子，才能把成交价切到未复权口径）
- [ ] 交易规则历史变更（注册制涨跌幅调整）
- [ ] 真实宽基指数日线（替换等权样本指数代理）
- [ ] 资金规模输入（`step0.capital`）以启用容量结论

补齐后须重跑 `python -m quantlab run`，重建两份 HTML 报告，执行 `python -m quantlab verify --render`，并重新过质量门。任何数值变动都会让旧 `metric-fingerprint` 失效，从而强制文档同步。

## 7. v3 数据侧验收

- `python -m quantlab selftest`：`TestDualPriceBasis` 验证合成面板覆盖率回退与真实样本使用未复权涨跌停基准；`TestConfigWiring` 验证 `min_avg_amount_20d` 等开关确实接线。
- `python -m quantlab verify --render`：校验 `docs/metrics.md` 中 `meta.sample.bars` 等标记与 `result.json` 一致，并阻止历史作废读数回流到文档。
- 原始 CSV 行数与入模 bar 数是**两个不同口径**（前者为原始行，后者为面板化清洗后），文档不得混用；当前读数见 `docs/metrics.md`。
