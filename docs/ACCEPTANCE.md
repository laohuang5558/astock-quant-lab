# 工程交付验收清单

- 交付目标：内网 ai-server `192.168.2.203`，Filer `http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/`
- 交付时间：2026-11-17 12:59:38
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
- [ ] 前端在线 + 离线双路径渲染校验通过（9 面板 / 36 项，关键读数与指纹一致）
- [ ] 飞凯材料策略研究：确定性校验通过（等价性断言、逐笔账目闭合、指纹、反向对照）
- [ ] 飞凯材料研报与 result.json 绑定，结论不放行；研究不构成投资建议
- [ ] 本机完整版启动命令见 RUNNING.md：python apps/api/server.py，端口 8000
- [ ] 合规：research-only，无实盘下单与荐股内容

## 交付物

| 文件 | 字节 | SHA-256 | 回读校验 |
|---|---|---|---|
| [`astock-quant-lab-v3.tar.gz`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/astock-quant-lab-v3.tar.gz) | 2,623,207 | `501e8fe44f1bb248841254121402b885b3f1256d34cd0ad0fe1e706fc629adbd` | 通过 |
| [`astock-quant-design-report.html`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/astock-quant-design-report.html) | 52,918 | `e0b4360bc1fc53c94f4db6febf741959e69db84cffe66bb1edb75d79eaf7b243` | 通过 |
| [`astock-quant-audit-report.html`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/astock-quant-audit-report.html) | 29,589 | `ecc5be9a80c033bdc12c10e3a5f30a2d93af5660edb4ff797081d4f69abbd0b6` | 通过 |
| [`astock-quant-designer.zip`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/astock-quant-designer.zip) | 27,131 | `7ccd76c4c0d14fb8fa24a0b0374eb35c79dfa5acab7aad00d52a7ba993f2b29f` | 通过 |
| [`PLATFORM_ARCHITECTURE.md`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/PLATFORM_ARCHITECTURE.md) | 24,161 | `265fbc27b3ebfe5692194054ff557d12d5714622e50d28bd9f590a6ff7c568ac` | 通过 |
| [`openapi.yaml`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/openapi.yaml) | 12,836 | `215357d18b52e555f691b1594ead27ebdda36c56907bf2d90a5107ef53560fdd` | 通过 |
| [`schema.sql`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/schema.sql) | 12,740 | `1289f8a9922ec758e360923a700e1944e82930f8105c82bda990348882649db6` | 通过 |
| [`contracts_check.json`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/contracts_check.json) | 1,100 | `cc75dd7dfb1be9603d80e0ae1a5b66d5490259f7f9aa1e90057d822d1fec9d9d` | 通过 |
| [`verify.json`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/verify.json) | 354 | `fc3fcb055502d5f226d2622f6a0e2a3d965c43e65e2367fddc8589d394f85d33` | 通过 |
| [`README.md`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/README.md) | 11,125 | `e124f743e0692fbb9500262817d4fa7e281629edf7382d21365d69890b88af27` | 通过 |
| [`RUNNING.md`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/RUNNING.md) | 5,163 | `6534d39cc12af385cf01346227c3c690587b7118c726ec200038f8ad3b4e4cd5` | 通过 |
| [`api_smoke.json`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/api_smoke.json) | 2,299 | `ad1b97f0f61cff14c2c12382a7232eca9f5643ae0ca09ada454bc35961f4092f` | 通过 |
| [`web_render_check.json`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/web_render_check.json) | 280 | `438a8dfa3df6b0e38d4e4c39b8723d5d6de08a1ddaf13a559dcc51b77e469b42` | 通过 |
| [`web_render_remote_check.json`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/web_render_remote_check.json) | 260 | `88dfc7a960dd9d90e2eb044c4a3f0d4036917a05355194fa9e88b141c840cc73` | 通过 |
| [`feikai-kdj-report.html`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/feikai-kdj-report.html) | 33,606 | `d8b397eea44b602b38028a8c0e62a4c23491df059e224b569b4b36d32a232628` | 通过 |
| [`feikai-kdj-result.json`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/feikai-kdj-result.json) | 30,438 | `ca6d33e1a298a0d245359480c4e19f3bb70f225e66fde00c806714a4bbcf21e1` | 通过 |
| [`feikai-kdj-summary.md`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/feikai-kdj-summary.md) | 1,466 | `845eb23594d24ea72b63b0acc0f056a907b4ac3b6316f2d781f97f0fe82c01d0` | 通过 |
| [`feikai-kdj-validate.json`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/feikai-kdj-validate.json) | 3,442 | `914ff883a0b26443a8ef65f7b840a4ff090a0e402af2d748822c19571b6f1e14` | 通过 |
| [`app/index.html`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/app/index.html) | 38,688 | `0f6e71c0435aa5e00e68567c6bca209e2b00db6ed1c5e0322300e590a363fa12` | 通过 |
| [`app/snapshot.js`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/app/snapshot.js) | 130,175 | `9e680da48467681c6243b7a531e22fa9f74fca14885496e30302bca214f1a733` | 通过 |

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
python scripts/smoke_api.py
node scripts/validate_web_render.js
```

**合规**：research-only，不含实盘下单、券商接口、自动交易循环；不荐股、不给买卖点与目标价、不承诺收益、不代操作。
