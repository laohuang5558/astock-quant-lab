# 工程交付验收清单

- 交付目标：内网 ai-server `192.168.2.203`，Filer `http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/`
- 交付时间：2026-11-16 20:27:53
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
- [ ] 可运行系统：app/index.html 控制台 8 个面板可打开，离线快照读数与 result.json 一致
- [ ] 后端接口冒烟 41/41 通过（含真实执行 25 项单测、幂等键、冻结保护）
- [ ] 前端在线 + 离线双路径渲染校验通过（8 面板，关键读数与指纹一致）
- [ ] 本机完整版启动命令见 RUNNING.md：python apps/api/server.py，端口 8000
- [ ] 合规：research-only，无实盘下单与荐股内容

## 交付物

| 文件 | 字节 | SHA-256 | 回读校验 |
|---|---|---|---|
| [`astock-quant-lab-v3.tar.gz`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/astock-quant-lab-v3.tar.gz) | 2,370,538 | `0a94053761994d93aaf16fad67293e897920ec31685e405505b5f2a86daff2d8` | 通过 |
| [`astock-quant-design-report.html`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/astock-quant-design-report.html) | 52,918 | `e0b4360bc1fc53c94f4db6febf741959e69db84cffe66bb1edb75d79eaf7b243` | 通过 |
| [`astock-quant-audit-report.html`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/astock-quant-audit-report.html) | 29,589 | `ecc5be9a80c033bdc12c10e3a5f30a2d93af5660edb4ff797081d4f69abbd0b6` | 通过 |
| [`astock-quant-designer.zip`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/astock-quant-designer.zip) | 27,131 | `7ccd76c4c0d14fb8fa24a0b0374eb35c79dfa5acab7aad00d52a7ba993f2b29f` | 通过 |
| [`PLATFORM_ARCHITECTURE.md`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/PLATFORM_ARCHITECTURE.md) | 24,161 | `265fbc27b3ebfe5692194054ff557d12d5714622e50d28bd9f590a6ff7c568ac` | 通过 |
| [`openapi.yaml`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/openapi.yaml) | 12,836 | `215357d18b52e555f691b1594ead27ebdda36c56907bf2d90a5107ef53560fdd` | 通过 |
| [`schema.sql`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/schema.sql) | 12,740 | `1289f8a9922ec758e360923a700e1944e82930f8105c82bda990348882649db6` | 通过 |
| [`contracts_check.json`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/contracts_check.json) | 1,100 | `4b51b8a4387db108fb8093df8851d4e8e425f4fa2f7c52cedc37b644e0160b28` | 通过 |
| [`verify.json`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/verify.json) | 354 | `8dca6f1ca6e28ec2fc838b14a3132dcd4edf258a24c91d79ff5ba0d2b5258c74` | 通过 |
| [`README.md`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/README.md) | 10,068 | `36534930fc196416bcb4b026206ebd2b586dbbb7777c246d9c2bcdbc9e82d78a` | 通过 |
| [`RUNNING.md`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/RUNNING.md) | 5,163 | `6534d39cc12af385cf01346227c3c690587b7118c726ec200038f8ad3b4e4cd5` | 通过 |
| [`api_smoke.json`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/api_smoke.json) | 2,299 | `ad1b97f0f61cff14c2c12382a7232eca9f5643ae0ca09ada454bc35961f4092f` | 通过 |
| [`web_render_check.json`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/web_render_check.json) | 262 | `1617dc34baea2902975b26caf4a6341af6dacd855dbb93ad012e31b1fda2aaa8` | 通过 |
| [`web_render_remote_check.json`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/web_render_remote_check.json) | 260 | `77668c845b3d79ff2f1737a293bc077a2914571f550d92e791f2efd4cf63b4a9` | 通过 |
| [`app/index.html`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/app/index.html) | 32,425 | `3d1f160a97c4e44edd52fa6ab21dc4d30a4419c59528fda7c49a520dd2388c02` | 通过 |
| [`app/snapshot.js`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/app/snapshot.js) | 111,478 | `587da0aacc5cac6896a9ec4f12d11c683b7d6c44c89b5342730234ddba863c52` | 通过 |

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
