# 工程交付验收清单

- 交付目标：内网 ai-server `192.168.2.203`，Filer `http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/`
- 交付时间：2026-11-16 20:02:13
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
- [ ] 合规：research-only，无实盘下单与荐股内容

## 交付物

| 文件 | 字节 | SHA-256 | 回读校验 |
|---|---|---|---|
| [`astock-quant-lab-v3.tar.gz`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/astock-quant-lab-v3.tar.gz) | 2,312,249 | `d4e95ea0fb660a352669779fa0011af896056dff6daec79594b52436bd58949a` | 通过 |
| [`astock-quant-design-report.html`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/astock-quant-design-report.html) | 52,918 | `e0b4360bc1fc53c94f4db6febf741959e69db84cffe66bb1edb75d79eaf7b243` | 通过 |
| [`astock-quant-audit-report.html`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/astock-quant-audit-report.html) | 29,589 | `ecc5be9a80c033bdc12c10e3a5f30a2d93af5660edb4ff797081d4f69abbd0b6` | 通过 |
| [`astock-quant-designer.zip`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/astock-quant-designer.zip) | 27,131 | `7ccd76c4c0d14fb8fa24a0b0374eb35c79dfa5acab7aad00d52a7ba993f2b29f` | 通过 |
| [`PLATFORM_ARCHITECTURE.md`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/PLATFORM_ARCHITECTURE.md) | 24,161 | `265fbc27b3ebfe5692194054ff557d12d5714622e50d28bd9f590a6ff7c568ac` | 通过 |
| [`openapi.yaml`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/openapi.yaml) | 12,836 | `215357d18b52e555f691b1594ead27ebdda36c56907bf2d90a5107ef53560fdd` | 通过 |
| [`schema.sql`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/schema.sql) | 12,740 | `1289f8a9922ec758e360923a700e1944e82930f8105c82bda990348882649db6` | 通过 |
| [`contracts_check.json`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/contracts_check.json) | 1,100 | `b6bd1603c5f1b22e700bdc1cac4215a83a0be75594e56dd93769ac41808a1b44` | 通过 |
| [`verify.json`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/verify.json) | 354 | `8a90c27760ababb1619d2f59c8ad0c41373f170c7de9b0753f4a6bc31f3922e8` | 通过 |
| [`README.md`](http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/README.md) | 9,487 | `aa8f4e1ea337bb355b9528b78bb22bf89d405e8757bbc8111ea008e42d429c06` | 通过 |

## 复现命令

```bash
tar -xzf astock-quant-lab-v3.tar.gz && cd astock-quant-lab-v3/astock-quant-lab
pip install -r requirements.txt
python -m quantlab selftest
python -m quantlab run
python -m quantlab verify --render
python scripts/validate_contracts.py
```

**合规**：research-only，不含实盘下单、券商接口、自动交易循环；不荐股、不给买卖点与目标价、不承诺收益、不代操作。
