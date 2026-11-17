# 系统运行手册（可操作版本）

本工程的「可运行系统」与早期只含静态报告的交付不同：它有一个真实监听 HTTP 的后端服务、一组按 `contracts/openapi.yaml` 实现的接口、一个可交互的前端控制台，并能在页面上真实触发 `quantlab` 复现任务。

## 一、两种运行形态

| 形态 | 地址 | 能力 | 前置条件 |
|---|---|---|---|
| **本机完整版** | `http://127.0.0.1:8000/` | 全部 9 个面板 + 真实任务触发（单测 / 一致性校验 / 契约校验 / 完整回测）+ 报告在线读取 | Python 3.13 + Flask（已在 `requirements.txt`） |
| **内网静态版** | `http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/app/index.html` | 全部 9 个面板、真实读数、图表与筛选；**任务触发禁用** | 仅需浏览器；零后端、零 CDN、零数据库 |

内网服务器 `192.168.2.203` 上，本平台当前只使用 SeaweedFS Filer 匿名静态托管。**本机没有 SSH 免密凭据、也没有可直接使用的数据库账号**，所以没有部署常驻后端；静态版把 `output/result.json` 与净值序列固化为 `snapshot.js`，前端自动切换到「离线只读快照」模式并在界面上明确标注。这不是功能缺失的伪装：任务按钮在静态版被禁用且给出原因，完整交互在本机版可用。

> **内网执行通道的精确口径（2026-11-17 实测补充）**：服务器上另有既有服务 `http://192.168.2.203:8093/`（Agent 工作台），它的 `ops` 角色带 `run_shell`、`dataops` 角色带 `sql_query`，任务事件日志可见真实命令与 SQL 返回；PG 16.15 上 pgvector 已装。即「内网完全无法执行 Python / 无数据库账号」的说法不准确，应修正为「本机无直接凭据，但存在经既有工作台间接执行的通道」。该通道需 `POST /api/tasks` 触发 LLM 任务，属对既有系统的写操作。本平台对该服务的调用限定为只读 GET（`scripts/validate_workbench.py`）；`POST /api/tasks` 任务创建、DDL 建表、nginx 与 `/ai/*` 配置变更均列入 M1/M2 的运维侧实施项，本版本不执行。详见 `docs/PLATFORM_ARCHITECTURE.md` 第 0.1.1 节。

## 二、本机启动

```bash
cd astock-quant-lab
python -m pip install -r requirements.txt
python scripts/build_web_snapshot.py     # 从真实产物生成前端离线快照
python apps/api/server.py               # 监听 127.0.0.1:8000
```

浏览器打开 `http://127.0.0.1:8000/`。

自定义地址：`QUANT_HOST=0.0.0.0 QUANT_PORT=9000 python apps/api/server.py`。
仅建议绑定内网可信地址；本服务无鉴权，不要暴露到公网。

## 三、接口契约

前缀 `/quant/api/v1`，完整定义在 `contracts/openapi.yaml`。统一响应信封包含 `ok` / `data` / `request_id` / `server_time` / `metric_fingerprint`；触发类操作（`POST /runs`、`POST /deliveries`、审核与回调）必须带 `Idempotency-Key`（幂等键），重复提交返回同一任务。

**路径拼接口径**：契约 `servers[].url` 已含前缀，表中的路径需接在它后面。健康检查的完整 URL 是
`http://127.0.0.1:8000/quant/api/v1/health`（HTTP 200，返回上述信封）。
注意 `/api/health` **不是**本服务的端点，实测返回 404；不得把它当作运行态判据。该偏差已由 `scripts/validate_health.py` 用正向双证据（状态码 + 响应 JSON）与两条反向对照（`/api/health` 与不存在路径均为 404）固化。

主要操作：

| 方法 | 路径（接在前缀后） | 说明 |
|---|---|---|
| GET | `/health` → 完整 URL `http://127.0.0.1:8000/quant/api/v1/health` | 服务状态与三项真实产物可达性 |
| GET / POST | `/runs` | 列出运行 / 创建真实复现任务 |
| GET | `/runs/{id}/result` `/metrics` `/equity` `/trades` `/factors` `/overfit` `/quality-gate` | 逐项真实读数 |
| GET | `/verify/{id}` | 一致性校验与架构契约校验结果 |
| GET | `/audit/{id}/opinions` | 从 `docs/AUDIT_PANEL.md` 解析的 6 位委员意见 |
| GET / POST | `/preregistration` | 读取冻结状态；在线改写返回 409 CONFLICT |
| GET | `/reports/{id}/design` `/audit` | 报告元信息与在线读取地址 |
| GET | `/deliveries` | 内网交付报告与验收入口 |
| GET | `/agents/tasks` | 任务队列与编排说明 |

错误码：`INVALID_ARGUMENT`(400) / `NOT_FOUND`(404) / `CONFLICT`(409) / `IDEMPOTENCY_REQUIRED`(428) / `TASK_FAILED`(500) / `NOT_IMPLEMENTED`(501)。

## 四、验证命令

```bash
python -m quantlab selftest                 # 引擎 25 项单测
python -m quantlab verify --render           # 数值指纹与文档一致性
python scripts/validate_contracts.py        # OpenAPI 引用 / DDL 静态校验
python scripts/validate_health.py           # 服务已启动后：健康端点 16 项（含 2 条反向对照）
python scripts/validate_workbench.py        # 内网可达时：既有 Agent 工作台只读探测 13 项（不触发 LLM 任务）
python scripts/smoke_api.py                  # 服务已启动后：41 项接口冒烟（含真实任务执行）
node scripts/validate_web_render.js         # 前端在线 + 离线双路径渲染校验
```

`validate_health.py` 从契约解析 server base 与路径后实测健康检查，断言 HTTP 200、响应信封、三项产物可达与 `metric_fingerprint`，并要求 `/api/health` 与不存在路径返回 404。把 `--base` 指向未监听端口时应输出 `HEALTH_CHECK: FAIL`，证明校验器不是恒真。

`validate_web_render.js` 用最小 DOM 桩在 Node 里真实执行前端内联脚本，分别以「在线」和「离线」两种模式渲染 9 个面板，逐项断言关键真实读数出现、无装载失败、无 JS 异常。它带反向对照：把 base_url 指向未监听端口时应输出 `WEB_RENDER: FAIL`，证明校验器不是恒真。

## 五、目录落点

```
apps/api/server.py                 后端服务（Flask，实现契约）
apps/web/index.html               前端控制台（零 CDN，内联样式与脚本）
apps/web/snapshot.js              离线数据快照（由脚本生成，勿手工编辑）
scripts/build_web_snapshot.py     快照生成
scripts/smoke_api.py              接口冒烟测试
scripts/validate_web_render.js    前端渲染校验
scripts/validate_health.py        健康端点确定性校验（含反向对照）
scripts/validate_workbench.py    内网既有 Agent 工作台只读探测（仅 GET）
output/api_smoke.json             冒烟测试机读结果
output/web_render_check.json      渲染校验机读结果
output/health_check.json          健康端点双证据机读结果
output/workbench_probe.json       工作台只读探测机读结果
```

实现栈取舍：架构文档的目标栈是 FastAPI + Vue 3 + Vite。本机环境当前只有 Flask、没有 Node 构建链与 PostgreSQL 账号，因此 MVP 用 Flask + 原生 JS 实现**同一份接口契约**，保证现在就能跑通；M2 阶段替换实现栈时接口、目录职责与前端数据模型不变。数据库 `db/schema.sql` 已建模，但本版本不接线（本机无直接账号；内网虽经 `:8093` 工作台存在间接执行通道，其已知账号权限不足且需向既有服务提交 LLM 任务，未获授权不接线），所有读数走本地真实产物，不使用占位数据。

### 单标的研究复跑（v1 / v2）

```bash
cd astock-quant-lab
python research/feikai_kdj/fetch_data.py
python research/feikai_kdj/run.py && python research/feikai_kdj/build_report.py && python research/feikai_kdj/validate_research.py
python research/feikai_kdj/run_v2.py && python research/feikai_kdj/build_report_v2.py && python research/feikai_kdj/validate_v2.py
python scripts/build_web_snapshot.py     # 研究读数变更后必须重建前端快照
node scripts/validate_web_render.js      # 校验在线/离线双路径渲染
```

v1 形式化「上证新低买 + 反弹 T + KDJ J 负 + 2 负反弹」；v2 补齐对话里的
月线死叉 / 回踩 10 周线 / 震荡区间，并复用 v1 的回测引擎与成本模型（仅注入信号）。
周线与月线条件按「已完成周期 + 当日」映射，无前视由历史截点截断重算反证；
盘中未收盘 bar 一律排除。两份研究结论均为「工程可复跑 / 策略结论不放行」，
前端第 9 个面板展示两代读数；研究产物变更后必须重建快照，否则页面读数会陈旧。

## 六、合规

research-only（仅研究）；不含实盘下单、券商接口、自动交易循环。质量门结论不因平台化或运行形态改变：工程可交付、策略级结论不放行。不荐股、不给买卖点与目标价、不承诺收益、不代操作。
