# v3 开发闭环说明

本文件记录「专家团联合审核 → 整改路线图 → 工程侧执行」的第三轮开发。所有**数值读数**一律以
`output/result.json` 与 `docs/metrics.md` 为准，本文件不转录绩效数字，避免口径漂移。

## 范围界定

审核路线图共 10 条未决项。按依赖类型拆分：

| 类型 | 条目 | 本轮处理 |
|---|---|---|
| 工程侧可闭环 | #3 等价随机对照、#5 样本外计数持久化、#6 风险定级与容量、#7 参数网格、#8 配置项接线、#9 未复权价格、#10 文档一致性 CI | **已实现并跑通** |
| 需外部数据 | #2 全 A 点时间面板（退市股 / 历史 ST / 行业 / PIT 财务 / 真实成交额） | 未决，登记在 `quality_gate.followups` |
| 需用户输入 | #4 资金规模 → 容量结论 | 计算路径已实现，`capital=null` 时显式 `not_evaluated`；给值即出结论 |
| 研究纪律 | #1 冻结权重组合、禁止据样本反号 | 已固化为预注册哈希 + 策略指纹 + 递增计数机制 |

不通过填占位数值把「未决」伪装成「已解决」。

## 改动清单

| 模块 | 文件 | 改动 | 验收 |
|---|---|---|---|
| 等价随机对照 | `quantlab/pipeline.py`、`quantlab/analytics.py` | `run_backtest(..., score_override=)` 跳过因子计算但完整保留权重函数、择时、熔断、两段式撮合；`monte_carlo` 真跑 `mc_paths` 次完整引擎 | `test_score_override_reproduces_baseline` 断言真实 score 重跑与基准逐日净值/成本一致；`control_equivalence` 由该断言推导 |
| 预注册与状态 | `quantlab/state.py`（新增）、`quantlab/cli.py` | 策略参数 SHA-256 指纹、冻结假设、回改自动递增 `oos_use_count`、超上限降级；CLI `preregister` / `state` | `test_sync_and_force` 覆盖初始化、不变、变更、强制改写、超上限 |
| 参数网格 | `quantlab/analytics.py`、`config/default.json` | 7 轴单轴扰动、邻域共识夏普、点估计嫌疑；`trials_param_grid` 由真实网格推导 | `result.json` 中 `n_combos`、`consensus_sharpe`、`n_attempted == dsr.n_trials` |
| 配置项接线 | `quantlab/factors.py`、`strategy.py`、`data.py` | winsor / standardize / nan_policy / `FACTOR_META.sign` / 行业权重上限 / `universe` 开关 / `market_timing.proxy` / `impact_model` 全部由配置驱动，非法值报错 | `TestConfigWiring` 4 项；`audit.config_wiring` 逐项声明 |
| 双价格口径 | `scripts/fetch_tencent.py`、`quantlab/data.py` | 新增 `--adjust none` 抓未复权真实日线；涨跌停基准改用未复权前收与开盘；覆盖率不足时回退并登记 | `TestDualPriceBasis` 2 项；`data/sample_real/prices_raw.csv` |
| 风险与容量 | `quantlab/risk.py`、`quantlab/report.py` | 系统性 beta OLS、停牌/跌停无法止损、流动性三条独立定级；`capacity_report` 双路径 | `TestRiskExtensions`；`risk.beta` / `risk.capacity` 字段 |
| 质量门 | `quantlab/report.py` | 新增样本外次数、随机对照显著性、参数邻域共识、配置接线、双价格口径 5 条判据；硬否决项由「等价性」升级为「显著性」；两票由读数推导 | `quality_gate.items` 逐条可追溯 |
| 一致性 CI | `quantlab/fingerprint.py`、`quantlab/verify.py`（新增）、`scripts/check_inline_js.py`（新增）、`.github/workflows/ci.yml`（新增） | 结果关键读数指纹绑定 HTML 报告与 `docs/metrics.md`；`[[m:path]]` 标记核对；历史作废值扫描；CI 跑 selftest → run → 报告重建 → `node --check` → verify | `python -m quantlab verify --render` |

## 关键设计取舍

1. **等价性用断言，不用自称**：`score_override` 路径与基准必须逐值一致，否则分位数与 DSR 直接置空。这比在报告里写「对照组等价」可证伪得多。
2. **整改不放水**：修好等价对照后，原来的「随机对照等价性」硬否决没有被删除，而是替换为更严格的「基准是否显著优于等价随机（≥P95）」。工程能力增强不等于策略结论变好。
3. **双价格只做半步并明说**：涨跌停基准已切到未复权价（这是可实现的正确部分）；成交价仍用前复权价，因为缺除权除息流水，强行切换会让现金与分红不自洽。质量门把该项判为「存疑」而非「成立」。
4. **约束不可行时留现金**：行业权重上限在行业数不足以支撑满仓时，输出总权重 <1（剩余现金），而不是归一化后把权重推回超限额。
5. **指纹不含 validation / data 段**：加一个检验维度不应被误记为「策略调参」，否则 `oos_use_count` 会被无意义地推高。

## 运行与验证

```bash
pip install -r requirements.txt
python -m quantlab selftest          # 25 项
python -m quantlab run               # 完整研究流水线（含 200 次等价重跑）
python scripts/build_design_report.py --out ../astock-quant-design-report.html
python scripts/build_design_report.py --out docs/DESIGN_REPORT.html
python scripts/build_audit_report.py --out-dir docs --top-html ../astock-quant-audit-report.html
python scripts/check_inline_js.py ../astock-quant-design-report.html ../astock-quant-audit-report.html
python -m quantlab verify --render   # 指纹 / 标记 / 陈旧值
python -m quantlab state             # 预注册与样本外使用次数
```

CI 在 `.github/workflows/ci.yml` 中自动执行同一套流程。

**合规**：本工程为 research-only，不含实盘下单与券商接口；回测结果不等于实盘表现，历史业绩不代表未来；不荐股、不给买卖点与目标价、不承诺收益、不代操作。
