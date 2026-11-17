# 工程交付验收清单

- 交付目标：内网 ai-server `192.168.2.203`，Filer `http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/`
- 交付时间：2026-11-17 15:32:03
- 数值指纹：`e819989972c994d1`
- 质量门：工程 可交付 / 策略结论 不放行 / 最终 不放行
- 验收入口：http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/index.html

## 验收项

- [ ] 连通性：内网 192.168.2.203 可访问，SSH :22 / nginx :80 / Filer :8888 / PostgreSQL :5432 在线
- [ ] 工程包可解包，目录结构与架构文档第 5 节一致
- [ ] python -m quantlab selftest → 25 项全通过
- [ ] python -m quantlab run → 退出码 0，产出 result.json / equity.csv / trades.csv
- [ ] python -m quantlab verify → ok=true，指纹 e819989972c994d1
- [ ] python scripts/validate_contracts.py → CONTRACT_VALIDATION: PASS
- [ ] 技能包静态自检 → 18/18 PASS
- [ ] 两份 HTML 报告可打开，metric-fingerprint 与 result.json 一致
- [ ] 质量门：工程 可交付 / 策略结论 不放行 / 最终 不放行
- [ ] 设计报告与审核报告的核心读数不与 result.json 冲突
- [ ] 接口契约 OpenAPI 3.1 可解析，20 条路径 / 23 个操作 / 54 处引用全部可解析
- [ ] 数据库 DDL：16 张表、pgvector(512)、现金/敞口 CHECK、HNSW 索引齐备
- [ ] 交付物 sha256 与 MANIFEST.json 逐项一致，且每个 URL 已回读校验
- [ ] 可运行系统：app/index.html 控制台 9 个面板可打开，离线快照读数与 result.json 一致
- [ ] 后端接口冒烟 41/41 通过（含真实执行 25 项单测、幂等键、冻结保护）
- [ ] 健康端点确定性校验 16/16 通过：http://127.0.0.1:8000/quant/api/v1/health → HTTP 200 + 响应 JSON 双证据；/api/health 反向对照 HTTP 404
- [ ] 内网既有 Agent 工作台 http://192.168.2.203:8093 只读探测 13/13 通过（仅 GET、不触发 LLM 任务）；已登记其对架构硬约束的影响，不擅自建表或改配置
- [ ] 前端在线 + 离线双路径渲染校验通过（9 面板 / 36 项，关键读数与指纹一致）
- [ ] 飞凯材料策略研究：确定性校验通过（等价性断言、逐笔账目闭合、指纹、反向对照）
- [ ] 飞凯材料研报与 result.json 绑定，结论不放行；研究不构成投资建议
- [ ] 飞凯材料 v2：月线死叉三口径 / 10 周线 / 震荡区间已形式化，35/35 项确定性校验通过（含无前视截断重算反证）
- [ ] v2 研报与 result_v2.json 绑定；15 个变体、全叠加信号 0 次，样本坍缩如实登记，结论不放行
- [ ] 本机完整版启动命令见 RUNNING.md：python apps/api/server.py，端口 8000
- [ ] 合规：research-only，无实盘下单与荐股内容

## 交付物

| 文件 | 字节 | SHA-256 | 回读校验 |
|---|---|---|---|
| [`astock-quant-lab-v3.tar.gz`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/astock-quant-lab-v3.tar.gz) | 2,687,558 | `35238ab86f9bee576ea4dad8df86b0199c64e6098a506ffd674d612d586a6bdf` | 通过 |
| [`astock-quant-design-report.html`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/astock-quant-design-report.html) | 52,918 | `e0b4360bc1fc53c94f4db6febf741959e69db84cffe66bb1edb75d79eaf7b243` | 通过 |
| [`astock-quant-audit-report.html`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/astock-quant-audit-report.html) | 29,589 | `ecc5be9a80c033bdc12c10e3a5f30a2d93af5660edb4ff797081d4f69abbd0b6` | 通过 |
| [`astock-quant-designer.zip`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/astock-quant-designer.zip) | 27,131 | `7ccd76c4c0d14fb8fa24a0b0374eb35c79dfa5acab7aad00d52a7ba993f2b29f` | 通过 |
| [`PLATFORM_ARCHITECTURE.md`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/PLATFORM_ARCHITECTURE.md) | 27,291 | `00d8397c287e75a80f4aa2d352fe745048e822475f0ab92558ab26f79438e478` | 通过 |
| [`openapi.yaml`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/openapi.yaml) | 12,836 | `215357d18b52e555f691b1594ead27ebdda36c56907bf2d90a5107ef53560fdd` | 通过 |
| [`schema.sql`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/schema.sql) | 12,740 | `1289f8a9922ec758e360923a700e1944e82930f8105c82bda990348882649db6` | 通过 |
| [`contracts_check.json`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/contracts_check.json) | 1,100 | `7b169ef68a7366cca1fa2ebf4828219ee6618877921731227cab3f8dd35d017e` | 通过 |
| [`verify.json`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/verify.json) | 354 | `3350604decd351edb4c7ba46d96800856ada895e00cd54b2ae3e15d187b5e10b` | 通过 |
| [`README.md`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/README.md) | 11,125 | `e124f743e0692fbb9500262817d4fa7e281629edf7382d21365d69890b88af27` | 通过 |
| [`RUNNING.md`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/RUNNING.md) | 8,605 | `38459fe69afa94569b20ec6f0ae76e47bbe5cb898285b655bcbb3829e9eb5a4b` | 通过 |
| [`health_check.json`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/health_check.json) | 4,043 | `3f04a6758569aa70b353212940faa8cdf8f3b351e3f201aad146860c589e744b` | 通过 |
| [`workbench_probe.json`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/workbench_probe.json) | 5,343 | `2a5cb39793af3af680b0420f14e2e769a34f5801ecf9040650b34e5b030eec8a` | 通过 |
| [`api_smoke.json`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/api_smoke.json) | 2,299 | `ad1b97f0f61cff14c2c12382a7232eca9f5643ae0ca09ada454bc35961f4092f` | 通过 |
| [`web_render_check.json`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/web_render_check.json) | 280 | `f06174ed492416910be246ec01607b6b16b674ece77b7e12934cf04c235af091` | 通过 |
| [`web_render_remote_check.json`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/web_render_remote_check.json) | 260 | `d4463fcc06167e52410f4cc5d23659bed6d0f7033abc9149ed4cdd4b1e0b95e1` | 通过 |
| [`feikai-kdj-report.html`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/feikai-kdj-report.html) | 33,606 | `d8b397eea44b602b38028a8c0e62a4c23491df059e224b569b4b36d32a232628` | 通过 |
| [`feikai-kdj-result.json`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/feikai-kdj-result.json) | 30,438 | `ca6d33e1a298a0d245359480c4e19f3bb70f225e66fde00c806714a4bbcf21e1` | 通过 |
| [`feikai-kdj-summary.md`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/feikai-kdj-summary.md) | 1,466 | `845eb23594d24ea72b63b0acc0f056a907b4ac3b6316f2d781f97f0fe82c01d0` | 通过 |
| [`feikai-kdj-validate.json`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/feikai-kdj-validate.json) | 3,442 | `914ff883a0b26443a8ef65f7b840a4ff090a0e402af2d748822c19571b6f1e14` | 通过 |
| [`feikai-kdj-v2-report.html`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/feikai-kdj-v2-report.html) | 27,133 | `eded27854d806c0c325a31b6c82c85c2fe938e8de3f8c74fe21bdc8d6078a1f6` | 通过 |
| [`feikai-kdj-v2-result.json`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/feikai-kdj-v2-result.json) | 40,078 | `18d2acfeec4eeb152eec2f5f8605def8cf46f007fa45c1280e6386e23e58bd64` | 通过 |
| [`feikai-kdj-v2-summary.md`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/feikai-kdj-v2-summary.md) | 2,137 | `b560312cca99480088a9b1d5dd76235e3e847771cca8bcdd5542c7fd1d2d2c96` | 通过 |
| [`feikai-kdj-v2-validate.json`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/feikai-kdj-v2-validate.json) | 3,697 | `57159c66c924542e37e544886cab59afe3d34346259060a8c98ea21de4272835` | 通过 |
| [`app/index.html`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/app/index.html) | 42,474 | `85d4eb836a9b328cdb687620a3f0270b2671479e8ec585d3296ad53b368eeb6b` | 通过 |
| [`app/snapshot.js`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/app/snapshot.js) | 145,559 | `f29cac3706c3541cc48bdb95f574ea5c3f931dce7a297896010c929f4664a80d` | 通过 |

## 复现命令

```bash
tar -xzf astock-quant-lab-v3.tar.gz && cd astock-quant-lab-v3/astock-quant-lab
pip install -r requirements.txt
python -m quantlab selftest
python -m quantlab run
python -m quantlab verify --render
python scripts/validate_contracts.py
python scripts/build_web_snapshot.py
python apps/api/server.py   # 浏览器打开 http://127.0.0.1:8000/
python scripts/validate_health.py   # http://127.0.0.1:8000/quant/api/v1/health
python scripts/validate_workbench.py   # 内网既有 Agent 工作台只读探测（仅 GET）
python scripts/smoke_api.py
node scripts/validate_web_render.js
```

**合规**：research-only，不含实盘下单、券商接口、自动交易循环；不荐股、不给买卖点与目标价、不承诺收益、不代操作。
