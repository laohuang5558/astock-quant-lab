# :8094 规整层 · 部署 / 交付 / 回滚方案

> 团队：`terminal-8094-review` · Phase 3 · 部署运维工程师：陆上线
> 事实基线：`docs/TERMINAL_8094_EVIDENCE.md`（32/32，反向对照 0/32）、`docs/TERMINAL_8094_PHASE1.md`（主理人裁决 1–5）、`docs/TERMINAL_8094_ARCH_FINAL.md`（架构定稿 v1.2 FINAL）、`deploy/deliver.py`、`docs/RUNNING.md`、`apps/api/server.py`
> 机读证据：`output/terminal_8094_probe.json`、`output/terminal8094_adapter_check.json`（注意两个文件名不同，前者有下划线、后者没有）
> 纪律：**没有回滚不上线**；任何发布动作对上游 `192.168.2.203:8094` 的请求方法集合为 `{GET}`，不产生写入。
> 合规：research-only。不荐股、不给买卖点与目标价、不承诺收益、不代操作。

---

## 需求覆盖索引（供主理人逐项核对）

| # | 主理人必覆盖点 | 本文档落点 |
|---|---|---|
| 1 | 部署形态（已裁决）+ 明确否决三方案 + :8888 只放验收页 | §0、§3.1、§3.3 |
| 2 | 环境分层与前置条件（含启动命令 / 端口 8000）+ 既有继承 vs 本次新增 | §2.1、§2.2、§2.3 |
| 3 | 可复制发布命令序列 + 每步通过判据与失败处理 | §4.1、§4.2、§4.3、§4.4 |
| 4 | 四类上游告警判据 + request_id 串联 + 降级不得被消费监控项 | §7.2、§7.1、§7.3 |
| 5 | 灰度 + 最小回滚单元（单文件注册行）+ 验收页标注 + 不改上游边界 | §5、§6 |
| 6 | 性能与容量（P95 / 8s / days≤2000 + 1MB / 双口径两次调用） | §3.4 |
| 7 | 风险清单（风险/概率/影响/应对，≥6 行） | §9（10 行） |
| 8 | 合规边界（0 写入上游 / 不触发 LLM / 不改 nginx 与 /ai/* / research-only） | §8.3 |

同时完整覆盖部署运维九项规范：环境规划(§2) / 容器化与编排(§3) / CI·CD(§4) / 发布策略(§5) / 回滚预案(§6) / 可观测性与应急(§7) / 安全与成本(§8) / 上线检查单(§10) / 待确认(§11)。

---

## 0. 部署形态（已裁决，唯一有效形态，不另提新形态）

### 0.1 形态定义

**规整层 = 挂进本机 `:8000` 既有 Flask app 的 Blueprint**，路由前缀 `/quant/api/v1/terminal8094`。

- **不新增进程**：复用 `python apps/api/server.py` 这一个既有 Flask 进程（`threaded=True`、`use_reloader=False`）。
- **不新增 `/health` 端点**：复用平台既有 `http://127.0.0.1:8000/quant/api/v1/health` 作为「继承能力自检」。`/api/health` 与 `/quant/api/v1/terminal8094/health` 都不是本服务健康端点，禁止探测（探了会误 FAIL）。
- **不新增生命周期**：Blueprint 无独立启停、无独立探针、无独立端口、无独立任务池；其存活完全依附于 `:8000` 进程。
- **复用硬约束**（裁决 2）：规整层必须 `from server import envelope, fail, API_PREFIX, ERROR_CODES`，禁止自建第二套响应信封；`metric_fingerprint` 为继承恒量 `e819989972c994d1`，由 `envelope()` 产出，规整层不实现指纹算法。
- **挂载可验证性**：`GET /quant/api/v1/terminal8094/meta` → HTTP 200 + 信封最小集 `ok/data/request_id/server_time` 齐全，即证明 Blueprint 已挂载。`/quant/api/v1/health` 只证明 Flask 活着，证明不了挂载，两条判据并存、不可互相替代。
- **双通道单一实现**：同一套 `apps/api/terminal8094.py` 规整层模块（架构稿中为 `apps/api/terminal8094/` 包，以实现 PR 为准），在线由 Blueprint 调用、离线由 `scripts/validate_adapter.py` import + fixture 回放，不产生两套逻辑漂移。

### 0.2 明确否决的三种形态（不得复活）

| # | 被否决形态 | 否决理由 |
|---|---|---|
| ① | 反向代理常驻新服务（nginx / 独立 HTTP 进程 / 容器） | 新增进程与生命周期，违反「不新增进程」裁决；且只在 header 层动作，做不了 F2 静默回退识别、F4 双口径、统一信封归一；1 人全栈无运维必要 |
| ② | 挂 `:8093` Agent 工作台 | 本平台对 :8093 的请求方法集合为 `{GET}`（只读硬边界）；该服务的任务执行需 `POST /api/tasks` 触发 LLM 写操作，挂载即越过只读边界并引入不可控外部依赖 |
| ③ | 纯离线快照作为主形态 | 无法验证在线契约，`/quant/api/v1/terminal8094/*` 的 200 / 4xx / 5xx 与响应头判据全部落空；仅保留为 L1 验收夹具与离线回放通道 |

### 0.3 `:8888` Filer 的边界

`http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/` **只承载验收页与静态报告**（`index.html`、`app/index.html`、`app/snapshot.js`、`terminal8094-report.html`、各类 `*.json` 机读证据、`astock-quant-lab-v3.tar.gz`），零后端、零 Python 逻辑、零 CDN、零数据库。规整层的 Python 逻辑只存在于本机 `:8000`，不向 Filer 部署任何可执行服务。

### 0.4 请求链路

```
验收方 → :8888 零 CDN 验收页（仅展示）
       → :8000 既有 Flask app（apps/api/server.py）
       → Blueprint /quant/api/v1/terminal8094/*（本期唯一插入点）
       → client.py 只读 GET（connect 2s / read 5s / 端到端 8s / 1MB 流式上限 / host 白名单）
       → :8094 nginx + FastAPI 0.1.0（内网既有，他人活服务，无源码、无写权限）
```

---

## 1. 本次交付的「改动点」清单（只描述，不改 `server.py`）

> **本节只描述，不在本次交付中修改 `apps/api/server.py`。** 注册行由实施 PR 完成；本文档定义它必须长什么样、放在哪里、以及回滚时删哪一行。

### 1.1 `apps/api/server.py` 的三处新增（不改动既有任何函数体）

1. **导入位置红线**：必须在 `API_PREFIX`(L29) / `ERROR_CODES`(L37) / `envelope()`(L72) / `fail()`(L86) 定义**之后**才导入并注册规整层；**禁止在文件顶部 import**。规整层模块顶部有 `from server import envelope, fail, API_PREFIX, ERROR_CODES`，若在 `server.py` 顶部导入，会拿到未完成初始化的模块 → `ImportError`，`:8000` 整体起不来（架构定稿 §1.3、附录 B.2）。推荐落点：紧邻 `errorhandler` 注册之前、`main()` 之后的代码段，或 `fail()` 定义结束之后。
2. **错误码注册**（架构定稿 §3.2 陷阱）：`server.envelope()` 用 `ERROR_CODES.get(code, 500)` 决定状态码。规整层模块加载时须向既有字典注册本期 4 个码，否则 `fail("INVALID_CONDS")` 默认落 **500**，与 L2「非法参数 → 4xx」判据冲突，且 504 / 502 告警静态码全部失真：
   ```python
   from server import ERROR_CODES
   ERROR_CODES.update({
       "INVALID_CONDS":              400,
       "UPSTREAM_UNAVAILABLE":      502,
       "UPSTREAM_TIMEOUT":          504,
       "UPSTREAM_CONTRACT_MISMATCH": 502,
   })
   ```
   仅新增键，既有 6 类取值不变，不修改 `server.py` 源码、不自建信封。
3. **注册行（= 最小回滚单元）**：
   ```python
   from apps.api.terminal8094 import create_blueprint  # 以实现 PR 的模块路径为准
   app.register_blueprint(create_blueprint(envelope, fail, API_PREFIX))
   ```
   Blueprint 内部以前缀 `API_PREFIX + "/terminal8094"` 注册只读 GET 路由，`bp.after_request` 统一注入 `disclaimer`（成功 / 错误 / 降级全量覆盖）。

### 1.2 明确不做的改动

不新增 `/health`、不新增 `app.run`、不改 `main()`、不改 `envelope()` 的指纹算法、不改 `_IDEMPOTENCY`、不改 nginx、不改 `/ai/*`、不向 :8093 发 POST、不向 :8094 发非 GET 请求。

---

## 2. 环境规划

### 2.1 环境分层

| 环境 | 用途 | 数据来源 | 配置来源 | 访问权限 |
|---|---|---|---|---|
| **本地开发 / 离线评审（L1）** | `validate_adapter.py` import + fixture 回放；内网不可达时也能评审 | `fixtures/terminal8094/*.json` + `MANIFEST.json`（含 hash、采集时间、`upstream_built_at`）+ `output/*.json` 真实产物 | 代码内确定性常量、fixture MANIFEST；无外部配置、无密钥 | 仅本机，无网络出口要求；评审方只读 |
| **本机在线联调（L2，`:8000`）** | Blueprint 挂载证明、在线契约判据、smoke、web_render online 模式 | 真实上游 `:8094` 只读 GET + 本地 `output/result.json` 等真实产物 | `QUANT_HOST` / `QUANT_PORT` 环境变量 + `apps/api/server.py` 既有常量；上游地址/超时/白名单在规整层 `client` 常量中，配置与代码分离，无密钥 | 默认 `127.0.0.1:8000` 回环，不对外；对 `:8094` 仅 GET；`:8000` 无鉴权，绑定 `0.0.0.0` 仅限内网可信地址 |
| **内网验收 / 静态交付（`:8888` Filer）** | 验收页、静态报告、机读证据、完整工程包 | `dist/` 交付包 + `apps/web/snapshot.js` + 各 `output/*.json` | `deploy/deliver.py` 常量 `FILER` / `BASE_PATH = /workbuddy/astock-quant-lab/v3/` | 内网匿名 PUT（仅交付方）/ GET（验收方）；零后端、零 CDN、零数据库 |
| **上游生产（`:8094` `192.168.2.203`）** | 内网既有「A股条件分析终端 v0.1.0」，**非本平台部署** | 该服务自身数据（`5,850` 只 / `11,647,813` 条日线 / `built_at=2026-11-17 17:44:47`） | 不由本平台配置；本平台无源码、无服务端写权限、无 SSH 免密、无直接 DB 凭据 | 本平台对其请求方法集合 = `{GET}`；不做健康检查写入、不触发任务、不改配置 |

### 2.2 前置条件（本机实测，发布前逐条复核）

| 项 | 实测 / 要求 | 复核命令 |
|---|---|---|
| Python | **3.13.14**（已实测可用） | `python --version` |
| Flask | **3.1.3**（`requirements.txt` 为 `Flask>=3.0`，已实测可用） | `python -c "import flask,importlib.metadata;print(importlib.metadata.version('flask'))"` |
| 依赖 | `pandas` / `numpy` / `PyYAML` / `Flask`；规整层 client 用 `urllib`、conds 校验用 `ast`，**不新增第三方依赖** | `python -m pip install -r requirements.txt` |
| Node | 用于 `validate_web_render.js`（CI 用 Node 20） | `node --version` |
| 上游连通 | `http://192.168.2.203:8094/` 可达，`/api/meta` 200 且 `n_stocks>0` | `python scripts/validate_terminal_8094.py` |
| `:8000` 端口 | **本机实测空闲**（`netstat -ano \| findstr :8000` 无 LISTENING） | 同上，发布窗口开始前再确认一次 |
| **启动命令** | **`python apps/api/server.py`，端口 8000**（`QUANT_PORT` 默认 8000、`QUANT_HOST` 默认 127.0.0.1；`use_reloader=False`，改注册行后必须重启进程） | 另开终端常驻运行 |
| Filer 可写 | `:8888` 匿名 PUT/GET 可用 | `python deploy/deliver.py` 内含逐文件 GET 回读校验 |
| git | 分支 `master`，remote `https://github.com/laohuang5558/astock-quant-lab.git`；push master 触发 `.github/workflows/ci.yml` | `git status --short` / `git remote -v` |

### 2.3 「既有能力继承」vs「本次新增」

| 类别 | 内容 |
|---|---|
| **既有能力继承（零新增代码 / 端点 / 进程 / 生命周期）** | `apps/api/server.py` 的 `envelope()` / `fail()` / `ERROR_CODES` / `API_PREFIX` / `_IDEMPOTENCY` / `metric_fingerprint()`；`/quant/api/v1/health`；`ThreadPoolExecutor(max_workers=2)`；`deploy/deliver.py` 交付管线（打包 → sha256 → Filer PUT → GET 回读逐字节比对 → 验收页 / `ACCEPTANCE.md` / `MANIFEST.json`）；`scripts/validate_contracts.py`、`smoke_api.py`、`build_web_snapshot.py`、`validate_web_render.js`；`.github/workflows/ci.yml`；`python -m quantlab verify --render`；`output/result.json` 指纹 `e819989972c994d1` |
| **本次新增（Phase 2/3，队友并行落盘中）** | `apps/api/server.py` 的 3 处注册改动（§1.1）；规整层模块 `apps/api/terminal8094.py`（Blueprint / client / conds_guard / normalize / stats_scope）；`contracts/terminal8094.openapi.yaml`；`fixtures/terminal8094/`；`scripts/validate_terminal_8094.py`（32/32 上游体检，已落盘）；`scripts/validate_adapter.py`（L1/L2 + 反向对照，已落盘）；`scripts/build_web_snapshot.py` 的 `terminal_8094()` 增量（已改，工作区 `M`）；`apps/web/terminal8094-report.html`、`terminal8094_panel_snippet.html`（已落盘）；`docs/TERMINAL_8094_*.md`（EVIDENCE / PHASE1 / ARCH_FINAL / DATA_SPEC）；`output/terminal_8094_probe.json`、`output/terminal8094_adapter_check.json`；**本文件** `docs/TERMINAL_8094_DEPLOY.md` |

> **发布门禁**：截至本次评估，规整层模块 `apps/api/terminal8094/`（或 `.py`）、`contracts/terminal8094.openapi.yaml`、`fixtures/terminal8094/` 尚未落盘，`output/terminal8094_adapter_check.json` 当前为 `ok=false / L1=FAIL / L2=SKIPPED`。**这是开发进行中的正常状态，不是可发布状态。** §4 的命令序列是「目标态」门禁，任一前置产物缺失即在该步 FAIL 并停止，禁止带着 `ok=false` 继续。

---

## 3. 容器化与编排（含性能与容量预算）

### 3.1 镜像与编排策略：明确不容器化

本期**不构建镜像、不引入 Dockerfile、不引入 Docker Compose、不引入 K8s、不新增任何常驻进程或 sidecar**。这不是遗漏，而是 §0 部署形态裁决的直接结果：新增进程/生命周期被否决，规整层只是 `:8000` 既有 Flask 进程内的一段路由。因此：

- 镜像分层优化：**不适用**（无镜像）。
- 编排方案：**进程内 Blueprint 注册**（§1.1），`app.register_blueprint()` 单点挂载 / 单点摘除。
- 伸缩策略：**无水平伸缩**。`:8000` 单进程 `threaded=True`；`ThreadPoolExecutor(max_workers=2)` 是既有 `/runs` 任务池，规整层为同步只读 GET，不占用该池。容量护城河是 1 MB 响应上限与 8 s 超时，而非加实例。若上游 P95 显著劣化，按架构定稿 §6.1 重校准超时预算（0.5 人日演进项），不靠扩容解决。
- 无状态：规整层纯函数式转换、不落库、不建业务表、不写远端状态；`fixtures/` 仅为静态验收夹具，不参与在线链路。

### 3.2 资源配额与关键运行时配置

| 项 | 值 | 约束来源 |
|---|---|---|
| connect 超时 | 2 s | 架构定稿 §4 |
| read 超时 | 5 s | 架构定稿 §4 |
| 端到端硬上限 | 8 s | 裁决 4 / 架构定稿 §4 |
| 上游响应流式上限 | 1 MB（超限 → 502，不 OOM、不返回半包） | 架构定稿 §4 |
| `days` 边界 | 1..2000，**在路由层、上游调用之前拦截**（越界 → 400 且上游未被调用） | 裁决 4 / §3.4 |
| `limit` / `code` / `conds` | `limit` 有上界；`code` 正则 `^(sh\|sz)\d{6}$`；`conds` 白名单 AST | 架构定稿 §3.4 |
| 上游 host | 固定白名单 `192.168.2.203:8094`，路径/端口不可由用户参数拼接（防 SSRF） | 架构定稿 §4 |
| 单请求上游调用数 | ≤ 2（仅 F4 双口径需要 2 次） | 架构定稿 §4 成本行 |
| 上游方法 | `{GET}` | 合规边界 §8.3 |

### 3.3 健康检查与优雅停机

- **健康检查**：复用既有 `/quant/api/v1/health`（正向双证据：HTTP 200 + 响应 JSON；反向对照 `/api/health` 与不存在路径均 404，由 `scripts/validate_health.py` 固化）。规整层**不新增** `/health`、不新增探针、不新增就绪/存活回调。
- **优雅停机**：Blueprint 无独立生命周期；停机 = 对 `:8000` 进程发 SIGINT/SIGTERM。因 `threaded=True` 且上游 read 上限 5 s，在途只读 GET 收敛 ≤ 5 s；无写事务、无连接泄漏、无需 drain。
- **配置生效**：`use_reloader=False`，注册行改动必须**重启 `python apps/api/server.py`** 才生效——这也是 §6 回滚必须包含「重启」一步的原因。
- 平台既有 41 项 `smoke_api.py` 冒烟与 `/quant/api/v1/*` 其他接口在挂载前后必须保持全绿；挂载 Blueprint 不得影响静态兜底路由 `/<path:filename>` 与既有 404 handler。

### 3.4 性能与容量预算

**上游延迟实测 P95（12 次采样，裁决 4）**

| 上游端点 | P95 |
|---|---|
| `/api/meta` | 24 ms |
| `/api/stats` | 9 ms |
| `/api/pick` | 37 ms |
| `/api/scan` | 15 ms |
| `/api/stock?days=420` | 61 ms |
| `/api/stock?days=999999` | 131 ms（449,253 B 大响应） |
| `/api/signals?limit=600` | 27 ms |

**预算结论（8 s 端到端）**

1. **`days≤2000` 与 1 MB 响应上限的前置拦截放在上游调用之前**：因此 `days=999999` 的 131 ms / 449,253 B 大响应在规整链路**不可复现**；常规最慢上游仅 61 ms（`days=420`）。L2 判据「`days=999999` → 400 且上游未被调用、响应体 <1 MB」即验证这一点。
2. **F4 双口径需两次只读调用**（`signals?limit=60` / `limit=600`）：实测约 `2 × 27 ms ≈ 54 ms`，**串行即可**（架构定稿 §4.1 明确「无需并发或缓存，避免过度设计」），占 8 s 预算 < 1%，预算充裕。全样本调用同样受 1 MB 上限约束。
3. 规整层自身目标：端到端 P95 ≤ 上游 P95 + 150 ms 且 ≤ 8 s；单次 parse、禁止深拷贝、信封只放规范化子集。
4. 容量结论：单进程同步只读、无状态、无落库；1 MB 上限护住内存，8 s 超时护住线程堆积；零新增基础设施、零模型调用、零新增存储。

---

## 4. CI/CD 流水线

### 4.1 发布命令序列（可复制执行，本机 Windows / Git Bash / Python 3.13.14）

> 定位：**本机人工流水线 + push 触发 CI 回归**的混合模式。现有 `.github/workflows/ci.yml`（ubuntu / Python 3.11）跑平台既有门禁（contracts / selftest / run / verify / build_snapshot / web_render / smoke），**不含** :8094 上游体检（CI 环境内网不可达）、不含 adapter L2、不含 Filer 交付。故以下步骤必须在本机（内网可达、Python 3.13.14）人工执行，且带 §4.4 人工审批卡点，不能全托管 CI。

```bash
# ---------- 前置：环境与端口 ----------
cd astock-quant-lab
python --version                                  # 期望 3.13.14
python -m pip install -r requirements.txt
git status --short                                # 逐项核对本次纳入的文件，禁止 git add -A

# ---------- S0（实施 PR，非本次文档动作）：注册 Blueprint ----------
# 按 §1.1 在 apps/api/server.py 增 3 处：导入位置、ERROR_CODES.update(4 码)、注册行。
# 本步只描述，不在本次交付改 server.py。

# ---------- S1 启动 :8000（S3/S5/S8 的硬前置） ----------
# 另开一个终端常驻运行；启动命令固定为：python apps/api/server.py，端口 8000
python apps/api/server.py
# 期望 stdout: START quant-platform  http://127.0.0.1:8000/   (API /quant/api/v1)
curl -s http://127.0.0.1:8000/quant/api/v1/health        # 期望 200 + ok=true（继承能力自检）
curl -s http://127.0.0.1:8000/quant/api/v1/terminal8094/meta   # 期望 200 + 信封最小集齐全（挂载证明）
# 禁止探测 /quant/api/v1/terminal8094/health 与 /api/health

# ---------- 主序列（主理人给定顺序，逐步判据见 §4.2） ----------
python scripts/validate_terminal_8094.py                          # S2 上游体检 32/32
python scripts/validate_adapter.py                                # S3 L1/L2 双层 + 反向对照
python scripts/build_web_snapshot.py                              # S4 重建前端快照
node scripts/validate_web_render.js                               # S5 前端在线+离线双路径渲染
python scripts/validate_contracts.py                              # S6 架构工件/契约校验
python -m quantlab verify --render                                # S7 一致性 + 指纹
python scripts/smoke_api.py                                       # S8 接口冒烟 41 项
python deploy/deliver.py                                          # S9 打包+sha256+Filer PUT/GET 回读+验收页
node scripts/validate_web_render.js \
  http://192.168.2.203:8888 \
  http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/app       # S10 远端渲染校验
git add <§4.3 清单> && git commit -m "<§4.3 消息>" && git push origin master   # S11

# ---------- 反向对照（校验器不是恒真，跑完必须重跑主校验恢复 OUT） ----------
python scripts/validate_terminal_8094.py --base http://127.0.0.1:9              # 期望 FAIL(0/32), 退出码 1
python scripts/validate_adapter.py --upstream-base http://127.0.0.1:9           # 期望 L2 FAIL, 退出码 1
python scripts/validate_adapter.py --self-test-negative                         # 期望 F2 断言 FAIL, 退出码 1
python scripts/validate_adapter.py                                              # ← 必须重跑：反向对照会覆写 adapter_check.json
```

### 4.2 阶段 / 步骤 / 质量门禁 / 失败处理

| 阶段 | 步骤 | 质量门禁（可比对的字面量） | 失败处理 |
|---|---|---|---|
| 校验·上游 | **S2** `validate_terminal_8094.py` | `TERMINAL_8094_PROBE: PASS (32/32)`；退出码 0；`output/terminal_8094_probe.json` 的 `ok=true`、`passed=32`、`total=32`、`method="read-only GET"` | `FAIL(n/32)` 或 `FAIL (0/32) [连通性预检失败]` → 停止发布，判上游契约漂移/不可达；记录 `probe_time` 与 `built_at`；禁止改断言让它恒真；重跑仍 FAIL 则走 §6 回滚到「仅离线 L1」状态，不进入 S3 |
| 校验·规整层 | **S3** `validate_adapter.py` | `TERMINAL8094_ADAPTER_CHECK: PASS`；退出码 0；**且 `output/terminal8094_adapter_check.json` 中 `layers.L1.status=="PASS"`、`layers.L2.status=="PASS"`（不是 SKIPPED）**、`failed=0`、`skipped=0`、`negative_controls_passed==negative_controls_total`；3 条反向对照均判 FAIL | **最关键门禁**：若 `L2=SKIPPED` 但退出码 0（§4.2 注），一律判发布失败，回到 S1 确认 :8000 已启动并挂载，重跑；`L1=FAIL` 且 `missing=[...]` → 规整层模块未落盘/导出不符，停止；反向对照跑完务必重跑主校验恢复 `adapter_check.json`，否则 S4 会读到 FAIL 版产物 |
| 构建·快照 | **S4** `build_web_snapshot.py` | `WROTE ... snapshot.js ... fingerprint=e819989972c994d1`；断言不触发：`t8094.probe.ok` 全通过、`t8094.adapter.ok==true`、F4 双口径 `fwd60.win` 不相等（防恒真）；`apps/web/index.html` 的 metric-fingerprint 被固化为当前指纹 | `RuntimeError: 8094 体检产物缺失` → 回 S2；`规整层验收产物缺失` → 回 S3；`8094 上游体检未全通过 / 规整层验收未通过，快照不得固化` → 说明 S3 的 OK 是旧文件或被反向对照覆写，重跑 S2/S3；`F4 双口径读数相同，疑为绑定失效` → 快照绑定失效，禁止固化 |
| 构建·前端 | **S5** `validate_web_render.js` | `WEB_RENDER: PASS` + `RENDER_CHECKS N/N`（online + offline 双模式、9 面板）；`output/web_render_check.json` 的 `ok=true`、`failed=0` | online 模式 FAIL / `数据装载失败` → :8000 未启动（回 S1）；offline FAIL → `snapshot.js` 未重建（回 S4）；反向对照：把 `base_url` 指向未监听端口应输出 `WEB_RENDER: FAIL` |
| 校验·契约 | **S6** `validate_contracts.py` | `CONTRACT_VALIDATION: PASS`；`output/contracts_check.json` 的 `ok=true` | `FAIL` / 退出码 1 → OpenAPI `$ref` 或 DDL 静态校验不过；**S9 `deliver.py` 的 `load_context()` 会读 `contracts_check.json`，`contracts_ok=false` 直接 `ABORT`**，必须先修复 |
| 校验·一致性 | **S7** `python -m quantlab verify --render` | 退出码 0；`output/verify.json` 的 `ok=true`；指纹 `e819989972c994d1`；两份 HTML 报告 metric-fingerprint 与 `result.json` 一致 | 指纹/文档陈旧值不一致 → 停止；`verify_ok=false` 同样触发 S9 `ABORT` |
| 校验·接口 | **S8** `smoke_api.py` | `SMOKE: PASS` + `PASS 41/41`；`output/api_smoke.json` 的 `ok=true`；既有 `/quant/api/v1/*` 全绿（证明挂载 Blueprint 未破坏既有路由、错误码、幂等键） | 任一 FAIL → 停止；重点检查 404 handler 与静态兜底路由是否被 Blueprint 抢匹配；`IDEMPOTENCY_REQUIRED`/`CONFLICT`/`INVALID_ARGUMENT` 状态码回归 |
| 交付·内网 | **S9** `deploy/deliver.py` | `DELIVERY: PASS`、`PROBLEMS: []`；每个文件打印 `put=200 get=200 verified=True`；`dist/MANIFEST.json` 与 `output/delivery_report.json` 的 `all_verified=true` | `ABORT: 一致性校验或架构工件校验未通过，不执行交付` → 回 S6/S7；`verified=False` / `上传失败` / `本地缺失` → 判交付失败，检查 Filer 可达与 payload 完整性，重跑（PUT/GET 回读幂等）；禁止在 `PROBLEMS` 非空时进入 S11 |
| 交付·远端 | **S10** 远端渲染校验 | `SOURCE(remote): ...` 成功读取远端 `index.html` / `snapshot.js`；`WEB_RENDER: PASS`（仅 offline 模式）；`output/web_render_remote_check.json` 的 `ok=true`、`modes=[{"mode":"offline","panels":9}]` | `远端页面读取失败 ...=404` → S9 交付不完整，重跑 S9；`WEB_RENDER: FAIL` → 远端页面 CDN/快照陈旧，核对零 CDN 与 `app/snapshot.js` |
| 版本 | **S11** `git add / commit / push origin master` | 提交成功、push 成功；CI `quantlab-consistency-ci` 被触发且绿灯；`git status --short` 干净 | push 被拒 → 先 `git pull --rebase` 再推，禁止 force push；CI 红 → 停止，本步不影响已交付的 Filer 静态页，但须在 `ACCEPTANCE.md` 标注 CI 状态 |

> **§4.2 注（本次代码核查实锤）**：`validate_adapter.py` 的 `overall = (p.total>0) and (p.failed==0) and nc_all_ok and (l1_stat["ok"] is True)` —— **只校验 L1**。因此 `:8000` 未启动时 `L2=SKIPPED`、`failed=0`、反向对照通过，脚本仍打印 `PASS` 并返回退出码 0。**只看退出码会把「在线契约零验证」误读为全绿**。发布脚本必须显式断言 `layers.L2.status == "PASS"` 且 `skipped == 0`（§4.4 卡点 G1）。

### 4.3 制品版本与追溯策略

- **版本锚点**：`git commit SHA`（`master`）+ 交付时间 + 继承指纹 `e819989972c994d1` + `output/terminal_8094_probe.json` 的 `probe_time` / 上游 `built_at`。
- **Filer 无内建版本**：`BASE_PATH = /workbuddy/astock-quant-lab/v3/` 是**覆盖式匿名 PUT**，无版本目录。追溯依赖 `dist/MANIFEST.json` 逐文件 `sha256` + PUT/GET 回读比对 + commit SHA 三者对齐；`MANIFEST.json` 不记录自身哈希（自指循环，脚本已注明）。
- **机读证据链**：`output/terminal_8094_probe.json`（上游体检）→ `output/terminal8094_adapter_check.json`（L1/L2 + 反向对照）→ `output/web_render_check.json` / `web_render_remote_check.json`（前端双路径 / 远端）→ `output/contracts_check.json` / `verify.json` / `api_smoke.json` → `output/delivery_report.json`（`all_verified`）。
- **上游漂移锚点**：fixture `MANIFEST.json` 记录响应 hash、采集时间、`upstream_built_at`；`provenance` 回显 hash 与元数据，不回传上游原文；`upstream_fingerprint` 用于触发 `UPSTREAM_CONTRACT_MISMATCH`。
- **请求级串联**：`envelope()` 生成的 `request_id`（`uuid4().hex[:16]`）贯穿响应体、`X-Request-Id` 响应头与结构化日志（§7.1）。
- **可复跑**：同一 fixture 连续两次，排除 `{server_time, request_id}` 后 `data` / `disclaimer` / `strategy_verdict` / `metric_fingerprint` 逐字段差异数 = 0。

**S11 建议纳入的文件清单**（提交前用 `git status --short` 逐项核对，**禁止 `git add -A`**，避免把 `.scratch` / 临时产物带入）：

```bash
git add docs/TERMINAL_8094_DEPLOY.md docs/TERMINAL_8094_EVIDENCE.md \
        docs/TERMINAL_8094_PHASE1.md docs/TERMINAL_8094_ARCH_FINAL.md \
        docs/TERMINAL_8094_DATA_SPEC.md \
        scripts/validate_terminal_8094.py scripts/validate_adapter.py \
        scripts/build_web_snapshot.py \
        apps/api/terminal8094.py apps/api/server.py \
        apps/web/terminal8094-report.html apps/web/terminal8094_panel_snippet.html \
        apps/web/index.html apps/web/snapshot.js \
        output/terminal_8094_probe.json output/terminal8094_adapter_check.json \
        output/web_render_check.json output/web_render_remote_check.json \
        docs/ACCEPTANCE.md dist/MANIFEST.json output/delivery_report.json
git commit -m "feat(terminal8094): 旁路规整层挂 :8000 Blueprint + L1/L2 双通道验收 + 交付/回滚方案 (research-only)"
git push origin master
```

> 提交前核对：规整层模块、`contracts/terminal8094.openapi.yaml`、`fixtures/terminal8094/` 若尚未落盘，**本次不得提交、不得上线**；`apps/api/server.py` 的 diff 必须只含 §1.1 的三处新增，不含任何既有函数体改动。

### 4.4 人工审批卡点

| 卡点 | 位置 | 审批人 | 核对内容 |
|---|---|---|---|
| **G0 注册行 review** | S0 之后、S1 之前 | 架构师 + 主理人 | `server.py` diff 仅含 3 处新增；导入在 `envelope/fail/ERROR_CODES/API_PREFIX` 定义之后；`ERROR_CODES` 已注册 4 个码；未新增 `/health` / 进程 / 生命周期；未自建信封 |
| **G1 在线契约确认** | S3 之后、S4 之前 | 主理人 | **显式读取 `adapter_check.json`：`layers.L2.status == "PASS"` 且 `skipped == 0`**（不看退出码）；3 条反向对照均判 FAIL；`probe.json` 仍 32/32 |
| **G2 交付前放行** | S8 之后、S9 之前 | 主理人 | smoke 41/41 全绿、`verify_ok`/`contracts_ok` 为 true、上游 32/32 复跑仍成立、Filer PUT/GET 可用、合规 disclaimer 完整率 100% |
| **G3 交付后放行** | S9/S10 之后、S11 之前 | 主理人 | `delivery_report.json` 的 `all_verified=true`、`PROBLEMS=[]`、远端验收页可打开且零 CDN、Filer 证据版本与本次 `probe_time` 一致 |

---

## 5. 发布策略

### 5.1 发布方式与选择理由

**采用「单文件注册行的开关式灰度发布」（等价特性开关 / 单实例金丝雀），不采用蓝绿、滚动或按流量切分。**

理由：① 裁决否决新增进程与实例，无第二套环境可做蓝绿切换、无多副本可做滚动；② 变更单元收敛到 `apps/api/server.py` 的**一行 `app.register_blueprint(...)`**，挂载即生效、摘除即全退，回滚半径最小；③ 前缀 `/quant/api/v1/terminal8094` 是全新命名空间，与平台既有 `/quant/api/v1/*` 天然隔离，新增路由对存量接口零流量影响；④ 内网单机、research-only、无用户体系、无多租户，按用户/租户/流量百分比灰度无意义。

### 5.2 灰度维度与放量节奏

| 阶段 | 放量对象 | 放行条件 | 回退动作 |
|---|---|---|---|
| P0 离线 | 仅 L1 fixture 回放，无网络出口 | `adapter_check.json` 的 `L1=PASS`、3 条反向对照 FAIL | 不进入 P1 |
| P1 挂载（本机回环） | 仅 `127.0.0.1:8000` 本机验收脚本 | `GET /terminal8094/meta` → 200 + 信封最小集；`L2=PASS` | 摘注册行 + 重启（§6.2） |
| P2 验收页（内网只读） | `:8888` 静态验收页与报告，内网验收方 | `DELIVERY: PASS` + 远端渲染 PASS，页面带「旁路规整、不改动既有服务」标注 | 撤下验收页入口 / 加回滚角标（§6.3） |
| P3 版本固化 | 公开仓库 `master` + CI | G3 通过、CI 绿灯 | `git revert`（不改 Filer 既有证据） |
| **不开放** | 第三方按 OpenAPI 接入、:8093 任务通道、公网绑定 | 触发条件「本期开放第三方按 OpenAPI 接入」按裁决 1 默认**未满足**；满足时才升 F1 Should 并补 6 键映射 | — |

放量节奏：每个阶段必须停稳并复跑上游 32/32 与平台 41 项冒烟，再进入下一阶段；任一门禁 FAIL 停在当前阶段，不跳跃、不以「先上线再补」放行。

### 5.3 数据库变更与发布顺序

**本期零数据库变更**：规整层为纯函数式转换 + 统一信封，不落库、不建业务表、不接 PostgreSQL（`db/schema.sql` 不改动）；`fixtures/` 是静态 JSON 文件，不是数据库迁移。因此不存在 expand → migrate → contract 的编排问题，也不存在「DB 变更与代码发布耦合」的回滚风险。

若后续演进引入持久化，必须遵守：**数据库变更与应用发布解耦，先兼容后清理** —— ① 先做向后兼容的 expand（加列/加表，旧代码不受影响）并独立验证；② 再发布应用（双写/双读）；③ 观察期结束后才做 contract（删旧列/旧表）；每一阶段可独立回滚，且 DDL 脚本与代码发布分属不同制品、不同审批卡点。

---

## 6. 回滚预案

> 原则：**没有回滚不上线**。回滚目标是在 1 分钟内让 `/quant/api/v1/terminal8094/*` 全部消失、平台既有 `/quant/api/v1/*` 保持全绿、上游 :8094 状态完全不变。

### 6.1 触发条件 / 判定方式 / 执行步骤 / 预计耗时 / 验证方式

| 触发条件 | 判定方式 | 执行步骤 | 预计耗时 | 验证方式 |
|---|---|---|---|---|
| L2 在线契约判据 FAIL 且 5 分钟内无法修复（`meta` 非 200 / 信封最小集缺失 / 非法参数未返回 4xx / `days=999999` 打到上游） | `validate_adapter.py` 的 `L2=FAIL`，或 `curl /terminal8094/meta` 异常 | ① 注释/删除 `apps/api/server.py` 的 `app.register_blueprint(...)` 行（连同其 import 与 `ERROR_CODES.update`）；② 重启 `python apps/api/server.py`；③ 保留规整层模块文件与证据，不删除 | **≤ 1 min** | `GET /terminal8094/meta` → 404；`GET /quant/api/v1/health` → 200；`smoke_api.py` 41/41 PASS |
| 「降级响应被当作真实选股结果消费」监控项 > 0 | §7.3 计数器 `degraded_as_real_result > 0`，或 `degraded=true` 响应的 `data` 出现个股代码 / 首元素 `sz000851` | 立即按上一行回滚；同时冻结 `fixtures/`，标记该 `request_id` 供复盘 | **≤ 1 min** | 降级响应 `data ∈ {None, []}`、`disclaimer.research_only=true`、`provenance.stale=true`；消费计数器归零 |
| 上游契约漂移导致错误码风暴（`UPSTREAM_CONTRACT_MISMATCH` 5 分钟内 ≥ 3 次） | §7.2 告警计数 + `upstream_fingerprint` 与 fixture MANIFEST 不一致 | ① 摘注册行回滚；② 重跑 `validate_terminal_8094.py` 取证 32/32；③ 刷新 fixture 后再评估重新放量 | ≤ 3 min | 上游 probe 仍 PASS（或如实记录漂移）；规整层路由 404 |
| 挂载 Blueprint 影响既有接口（静态兜底路由 / 404 handler / 既有错误码被抢匹配） | `smoke_api.py` 出现 FAIL；`INVALID_CONDS` 落 500（说明 `ERROR_CODES` 未注册） | 摘注册行 + 重启；不修改既有 `server.py` 函数体 | ≤ 2 min | 41/41 全绿；`/api/health` 与不存在路径仍 404 |
| 合规：disclaimer 完整率 < 100%，或验收页出现荐股 / 买卖点 / 目标价 / 收益承诺措辞 | 全量响应扫描 `disclaimer`；验收页禁词扫描 | ① 摘注册行；② 撤下 :8888 验收页入口或加「已回滚」角标（§6.3）；③ 重新 `deliver.py` | ≤ 3 min | 页面禁词数 = 0、外部 CDN 引用数 = 0、置顶 research-only 声明 ≥ 1 |
| 上游 :8094 整体不可达 / 下线（他人活服务变更） | probe 连通性预检短路 `FAIL (0/32)` | 不回滚注册行也可降级到「严格模式」；若错误率超阈值则摘注册行，仅保留 L1 离线评审 | ≤ 1 min | `UPSTREAM_UNAVAILABLE` 502 + `degraded=true` + `data=None`，不返回半包 |

### 6.2 最小回滚单元：单文件 Blueprint 注册行

- **唯一变更点**：`apps/api/server.py` 中的 `app.register_blueprint(create_blueprint(envelope, fail, API_PREFIX))` 一行（连同紧邻的 import 与 `ERROR_CODES.update` 共 3 行）。
- **回滚动作集合**：注释/删除这 3 行 → 重启 `python apps/api/server.py`（因 `use_reloader=False`，不重启不生效）→ 验证（§6.1）。
- **不删除规整层模块文件、fixtures、验收脚本、机读证据**：它们未被 import 即为死代码，不产生任何运行时影响；保留以便复盘与重新放量。回滚因此是「摘开关」而非「删功能」，半径最小、可逆。
- **不需要**：杀额外进程、摘流量、切 nginx、回滚负载均衡、回滚数据库（本期零 DB 变更）、通知上游方。

### 6.3 回滚后验收页如何标注

`apps/web/terminal8094-report.html`、`dist/index.html` 与 Filer 上的验收页须同步标注，禁止保留「全绿 / 在线 PASS」字样：

- 顶部横幅：**「规整层已回滚」+ 回滚时间（GMT+8）+ 回滚 commit SHA**；
- L2 徽标由 `PASS` 改为 `ROLLED BACK`，并写明「`/quant/api/v1/terminal8094/*` 当前返回 404，在线契约未验证」；
- 页面读数统一加「**离线只读快照 / 历史验收证据，非实时在线结果**」角标；`output/terminal8094_adapter_check.json`、`output/web_render_remote_check.json` 保留为证据但附 `rolled_back: true`、时间戳与回滚 commit；
- 零 CDN、置顶 research-only 声明、禁词数 = 0 三项保持不变；
- 若需让 Filer 与本地证据一致，重跑一次 `python deploy/deliver.py`（PUT/GET 回读幂等，`sha256` 对齐），不要手工改 Filer 文件。

### 6.4 「回滚不改动上游 :8094」边界

- 回滚动作集合完全落在本平台可控域：`:8000` 的注册行、本机进程重启、`:8888` 的静态页与证据、git 版本。
- 对 `192.168.2.203:8094`：**零请求、零写入、零配置变更、零数据改动**；回滚前后该服务状态应完全一致。
- **反向验证**：回滚后重跑 `python scripts/validate_terminal_8094.py`，仍应为 `PASS (32/32)` —— 这同时证明「上游没被动过」和「取证基线未失真」。若 probe 不再 32/32，说明是上游自身漂移（他人活服务），须单独记录，不得归因于本次回滚。
- 同理，回滚不涉及 `:8093`、不涉及 `POST /api/tasks`、不涉及 nginx 与 `/ai/*`。

### 6.5 回滚演练安排

- **上线前必做一次**：在 `:8000` 按「摘注册行 → 重启 → `GET /terminal8094/meta` 应 404 → `GET /quant/api/v1/health` 应 200 → `smoke_api.py` 41/41 → 恢复注册行 → 重启 → `meta` 应 200」跑完整闭环，实测耗时并记录到 `output/delivery_report.json` 附注定；目标 ≤ 1 min。
- **反向对照即演练**：`validate_adapter.py` 的三种负例（Blueprint 未挂载 / fixture 缺失或篡改 / 上游不可达）与 §4.1 的三条反向对照命令，本身就是回滚触发判据的定期演练。
- **周期**：灰度期每周一次；每次上游 `built_at` 变更、每次规整层模块改动、每次 `server.py` 注册行改动后各加演一次。演练结果计入 §10 上线检查单。

---

## 7. 可观测性与应急

### 7.1 日志 / 指标 / 追踪 + `request_id` 串联

- **结构化日志**（单条一行 JSON，本机 stdout + 文件留存，抽 100 条字段完整率 100%）：
  `request_id` / `server_time` / `route` / `upstream_status` / `upstream_latency_ms` / `upstream_bytes` / `upstream_call_count` / `error.code` / `degraded` / `warnings_count` / `fingerprint` / `applied_conds` / `provenance.stale`。
- **`request_id` 串联**：上游 :8094 的 `/api/meta` 响应**无 `X-Request-Id`**（证据 F6e 实测），因此由规整层在入口用 `envelope()` 生成 `uuid4().hex[:16]`，写入响应体 `request_id`、回写 `X-Request-Id` 响应头（架构定稿 §3.6），并透传到每一次上游 GET 的日志记录。串联链路：**验收页请求 → :8000 access log → 规整层结构化日志 → 上游调用记录 → 错误/降级响应**，一个 ID 贯穿全链路，便于按 ID 捞取一次调用的全部证据。
- **指标**：请求量 / P95 & P99 延迟 / 4 类错误码计数 / 降级率 / `upstream_call_count` 分布（应 ≤ 2）/ 响应体字节分布（应 < 1 MB）/ `degraded_as_real_result` 计数 / `half_packet_count`。基线对照 §3.4 的 P95 表。
- **追踪**：本期不引入分布式链路追踪组件（零新增基础设施裁决），以 `request_id` 作为唯一贯穿标识即可满足单机单进程的可追溯性。
- **仪表盘**：复用 `:8888` 零 CDN 验收页的 KPI 卡片与 `output/*.json` 机读证据，不新增监控平台、不新增 Agent、不新增外部 SaaS。

### 7.2 四类上游告警判据（4 个错误码全覆盖）

| 告警 | 错误码 / HTTP | 判据（触发条件） | 响应形态 | 级别 |
|---|---|---|---|---|
| **上游不可达** | `UPSTREAM_UNAVAILABLE` / **502** | connect 2 s 超时、连接拒绝、DNS 失败、上游返回非 2xx、`upstream_status` 为空；**且** 1 MB 流式上限被触发（超限同归此锚点） | 降级严格模式：`ok=false`、`degraded=true`、`data ∈ {None, []}`、`provenance.stale=true`、disclaimer 含「非实时·缓存/样例」；不返回样例选股清单、不返回半包 | P1 |
| **上游超时** | `UPSTREAM_TIMEOUT` / **504** | read 5 s 超时或端到端 8 s 预算耗尽（`upstream_latency_ms > 8000`），且降级/fixture 路径不可用或禁用 | 非降级路径的明确错误码，不返回半包 | P2 |
| **上游超限（前置拦截）** | `INVALID_CONDS` / **400**（`INVALID_ARGUMENT` 同族） | 路由层在**上游调用之前**拦截：`days<1` 或 `days>2000`、`limit` 越上界、`code` 不匹配 `^(sh\|sz)\d{6}$`、`conds` 白名单 AST 拒绝、F2 静默回退识别（首元素 `sz000851` 且 conds 非默认） | `ok=false`、`error.code=INVALID_CONDS`、`data` 中不含任何个股代码、`applied_conds` 回显；**`upstream_call_count` 必须为 0** | P2 |
| **契约漂移** | `UPSTREAM_CONTRACT_MISMATCH` / **502** | `upstream_fingerprint` / 响应 hash 与 fixture `MANIFEST.json` 不一致、`schema` 字段缺失或类型变化、`group` 枚举（G1/G2/G3/G4/G5/G7/G11）变化、上游 `built_at` / `latest_date` 刷新 | 明确错误码 + 冻结 fixture，不静默透传漂移数据；触发 §6.1 回滚评估 | P1 |

> **口径说明（避免与架构定稿 §4.2 冲突）**：降级严格模式的**唯一锚点是 `UPSTREAM_UNAVAILABLE`(502)**，超时/超限走到降级时都归此码并置 `degraded=true`；`UPSTREAM_TIMEOUT`(504) 与 `UPSTREAM_CONTRACT_MISMATCH`(502) 保留为**非降级路径**结构性错误码，不进降级断言集合以免判据膨胀。4 个码均在 §1.1 的 `ERROR_CODES.update` 中注册，否则 `envelope()` 会统一落 500，告警判据失效。
> **禁用项**：禁止让任何告警路径返回 HTTP 200 + 上游默认选股清单（这正是 F2 静默错误），禁止半包响应，禁止把 `ok=false` 的降级体包装成 `ok=true`。

### 7.3 「降级响应不得被当作真实选股结果消费」监控项（硬闸门）

这是 F2 静默错误在超时路径原样复现的唯一防线，设 3 个必须为 0 的计数器，任一 > 0 立即触发 §6.1 回滚：

| 计数器 | 判定方式 | 阈值 |
|---|---|---|
| `degraded_as_real_result` | 任何 `degraded=true` 的响应，若 `data` 中出现个股代码（正则 `^(sh\|sz)\d{6}$`）、首元素为 `sz000851 *ST高鸿`，或被调用方以「200 + 选股清单」形态消费（写入验收页选股表 / 进入下游策略） | **= 0** |
| `half_packet_count` | 响应体被 1 MB 上限截断后仍返回部分 JSON / 部分列表 | **= 0** |
| `disclaimer_missing_count` | 全量输出（含成功 / 错误 / 降级）中 `disclaimer.research_only=true` 且命中「不构成投资建议」的缺失数 | **= 0**（完整率 100%） |

配套：降级响应强制 `data=None`、`provenance.stale=true`、「非实时·缓存/样例」文案；验收页对 `degraded=true` 的响应只展示提示条，不渲染选股清单。

### 7.4 告警分级与通知渠道

| 级别 | 触发 | 通知渠道 | 响应时限 |
|---|---|---|---|
| **P0 立即回滚** | §7.3 任一计数器 > 0；合规禁词出现；:8094 出现非 GET 请求；:8093 出现 `POST /api/tasks` | 主理人 + 部署值班（本机终端 + 团队即时消息），同时写入 `output/delivery_report.json` 附注 | 5 min 内摘注册行 |
| **P1** | `UPSTREAM_UNAVAILABLE` / `UPSTREAM_CONTRACT_MISMATCH` 5 min 内 ≥ 3 次；上游 probe 跌出 32/32；`L2` 由 PASS 变 FAIL/SKIPPED | 同上 + 冻结放量阶段 | 15 min |
| **P2** | 单次 `UPSTREAM_TIMEOUT`；`INVALID_CONDS` 频次异常升高（多为调用方误用）；上游 `built_at` 刷新 | 当日汇总，进入复盘清单 | 当日 |

不引入外部告警 SaaS / webhook（内网隔离、零新增基础设施）；通知依赖本机终端输出 + 机读证据文件 + 团队即时消息人工播报。

### 7.5 值班响应与故障复盘

- **值班**：发布窗口内 1 名部署值班 + 1 名架构审批人在岗；P0 由值班直接执行 §6.2 回滚，事后补报，不等待审批。
- **响应流程**：告警 → 按 `request_id` 捞取全链路日志 → 判定是否命中 §6.1 触发条件 → 执行回滚 → 复跑 `/quant/api/v1/health` + `smoke_api.py` 41 项 + `validate_terminal_8094.py` 32/32 → 更新验收页角标 → 记录耗时。
- **复盘**：24 小时内产出复盘记录，必须回答四问：① 哪条门禁应提前捕获却没捕获（尤其 G1 的 `L2=SKIPPED`）；② `upstream_built_at` 是否变化；③ 降级三计数器是否为 0；④ 回滚实测耗时是否 ≤ 1 min。结论回写 §9 风险清单与 §10 检查单，形成闭环。
- **故障不追责、但必须留痕**：所有 `request_id`、证据 JSON、回滚 commit SHA 保留，不清理。

---

## 8. 安全与成本 + 合规边界

### 8.1 密钥、证书与漏洞扫描

- **本期不引入密钥、不引入 TLS 证书、不引入数据库凭据、不引入大模型 API Key**：`:8000` 为 `127.0.0.1` 回环无鉴权服务，`:8888` 为内网匿名 Filer，`:8094` 为只读 GET，明确否决 `:8093` LLM 通道（0 次模型调用）。因此不存在「密钥进代码库」的入口。
- **若后续演进**（把 `:8000` 绑到 `0.0.0.0`、给 Filer 加鉴权、引入云端 KV）：密钥与证书一律走环境变量 + 密钥托管，禁止进代码库与 git；证书设到期前 30 天告警；按最小权限分配，仅规整层 `client` 持有上游只读凭据（本期无凭据）。
- **涉及大模型 API Key 的轮换与配额告警（前瞻，本期不触发）**：本期明确不涉及（裁决否决 :8093、0 次 LLM 调用）；若未来触发展开 LLM 通道，须满足：① Key 存环境变量/密钥托管、禁止入代码库与日志；② 轮换周期 ≤ 90 天，支持双 Key 并行灰度，旧 Key 轮换后 24 h 内失效；③ 按 Key 维度设配额告警（日调用量 / 费用达阈值 80% 预警、95% 熔断），告警绑定 `request_id` 与调用方；④ 禁止无限重试与静默降级到付费通道。
- **依赖漏洞扫描**：`requirements.txt` 仅 `pandas` / `numpy` / `PyYAML` / `Flask`，本期规整层用标准库 `urllib` + `ast`，**不新增第三方依赖**，攻击面不扩大；发布前 `python -m pip install -r requirements.txt` 后执行依赖漏洞扫描（`pip-audit` 或等效），有高危 CVE 时停在 S1 之前处理。
- **输入面**：`conds` 白名单 AST（出现调用/属性/`__import__` 节点 → 拒绝）；上游 host 固定白名单、路径与端口不可由用户参数拼接（防 SSRF）；`name` 等字符串转义输出（防 XSS）；注入型字符串上游未求值（证据 F2，不构成 RCE），规整层的安全判据是「显式失败」而非「拦注入」。
- **权限**：规整层对 :8094 仅 GET、单请求 ≤ 2 次、1 MB 上限；对 :8093 仅 GET；不改 nginx 与 `/ai/*`。

### 8.2 成本优化与预算告警

- **零新增基础设施**：挂既有 `:8000` 进程、用既有 `:8888` Filer、无新端口、无镜像、无容器、无新存储、无模型调用、无外部 API 计费。
- **成本上界**：单请求最多 2 次上游只读 GET + 1 MB 响应上限 + 8 s 超时；`days≤2000` 前置拦截使 449 KB 大响应不进入链路。
- **预算/配额告警**：① `upstream_call_count > 2` → P2（疑似双口径逻辑失控）；② 单响应 `upstream_bytes ≥ 1 MB` → P1（前置拦截可能失效）；③ 上游 GET 日调用量较基线偏离 > 3 倍 → P2；④ `:8093` LLM 任务调用数 > 0 → **P0**（越过只读边界）；⑤ 端到端 P95 > 8 s → P1（按架构 §6.1 重校准预算）。

### 8.3 合规边界（措辞锚定行为本身）

| 边界 | 行为陈述 |
|---|---|
| **0 写入上游** | 本部署对 `192.168.2.203:8094` 发出的 HTTP 请求方法集合为 `{GET}`；不含 POST / PUT / PATCH / DELETE。`scripts/validate_terminal_8094.py`、规整层 `client`、`scripts/validate_workbench.py` 均只发 GET；不触发该服务的任务，不写远端状态，不改其数据与配置。 |
| **不触发 LLM 任务** | 本部署不向 `192.168.2.203:8093` 发送 `POST /api/tasks`；对 :8093 的请求方法集合为 `{GET}`，由 `scripts/validate_workbench.py` 执行只读探测。本部署的模型调用次数为 0。 |
| **不改 nginx 与 `/ai/*`** | 本部署不写 nginx 配置文件、不修改 `/ai/*` 路由表、不使用 SSH 凭据、不接数据库 DDL。交付写操作仅由 `deploy/deliver.py` 完成：对 Filer `:8888` 的匿名 PUT（交付物）、逐文件 GET 回读，以及对本平台自建探测文件 `/workbuddy/_probe.txt` 的一次 DELETE 清理。以上均在既有匿名静态托管通道内，对象不是 :8094。 |
| **research-only** | 验收页与全量响应（含错误 / 降级）带 `disclaimer.research_only=true` 与「不构成投资建议」声明，置顶展示；不输出买卖点、目标价、收益承诺、代操作入口；`strategy_verdict` 恒 `not_approved`；F4 双口径带 caveat「未计成本·无样本外·无多重检验校正·有条件存活样本」；验收表 0 条判据断言「:8094 返回结果变正确」。 |
| **旁路边界** | 交付物显著标注「旁路规整、不改动既有服务」；不改写上游选股结果；0 环节交智能体；上游 :8094 是内网既有服务，非本平台部署。 |

---

## 9. 风险清单

| 风险 | 概率 | 影响 | 应对 |
|---|---|---|---|
| **上游为他人活服务会变更**（字段/口径漂移、`built_at` 刷新、分组枚举变化、服务下线） | 中 | 高 | `upstream_fingerprint` + fixture `MANIFEST.json` 的 hash / 采集时间 / `upstream_built_at` 比对 → `UPSTREAM_CONTRACT_MISMATCH` 502；发布前与回滚后各复跑 `validate_terminal_8094.py` 32/32；漂移即冻结 fixture、暂停放量、走 §6.1 回滚；不静默透传 |
| **F1 私有字典漂移**（6 键 `PRESETS` 是上游前端内联 JS 私有字典） | 中 | 中 | 按裁决 1 本期**不复制**、不写映射、不写验收用例，只做契约卫生：我方契约不声明 `preset`，全量响应回显 `applied_conds`；触发条件「本期开放第三方按 OpenAPI 接入」满足时才升 Should 并补映射，避免「用新契约失真修旧失真」 |
| **F7 内网隔离 CDN 白屏**（:8094 `/docs` 依赖 `cdn.jsdelivr.net`、`/redoc` 依赖 `fonts.googleapis.com`；本次实测可达，属条件性风险） | 条件触发 | 中 | 不代理、不改写 :8094 文档；本平台验收页与契约文档零 CDN 自托管（外部 CDN 引用数 = 0 为 Must 可判定）；:8094 侧零 CDN 列前瞻加固、**不计 PASS/FAIL**（服务端静态页规整层改不到） |
| **`:8000` 未启动导致 L2 `SKIPPED` 被误读为 `PASS`** | **高** | **高** | 代码实锤：`validate_adapter.py` 的 `overall` 只校验 L1，`L2=SKIPPED` 时仍退出码 0。强制门禁 G1：显式断言 `layers.L2.status=="PASS"` 且 `skipped==0`，**不看退出码**；S1 启动 `python apps/api/server.py`（端口 8000）是 S3/S5/S8 的硬前置；`GET /terminal8094/meta` → 200 作为挂载证明；CI 环境内网不可达，L2 必须在本地跑，不得把 CI 绿灯当 L2 通过 |
| **fixture 与真实上游漂移**（离线夹具采集时的 hash / `built_at` 与上线时上游不一致） | 中 | 中 | fixture `MANIFEST.json` 记录响应 hash、采集时间、`upstream_built_at`；每次发布先跑 32/32 取证再跑 L1；漂移即刷新 fixture 并重跑 L1/L2；`provenance` 只回显 hash 与元数据，不回传上游原文；`metric_fingerprint` 为继承恒量，可复跑用「排除 `{server_time, request_id}` 后 `data` 逐字段相等」验出漂移 |
| **合规措辞被误读为荐股**（首页「选股 / 运行 / 胜率 / 操盘线」+ 输出个股清单与后 5/20/60 日胜率） | 中 | 高 | 全量响应（含错误 / 降级）disclaimer 100% 覆盖 + 验收页置顶「不构成投资建议」+ 禁词扫描数 = 0 + `strategy_verdict` 恒 `not_approved` + 双口径 caveat；不新增买卖点 / 目标价 / 收益承诺；措辞锚定行为本身，不写目的条件式表述 |
| **`ERROR_CODES` 未注册 4 个新码 → `fail()` 统一落 500** | 中 | 中 | §1.1 第 2 处注册 `INVALID_CONDS:400` / `UPSTREAM_UNAVAILABLE:502` / `UPSTREAM_TIMEOUT:504` / `UPSTREAM_CONTRACT_MISMATCH:502`；L2 判据断言 4xx/5xx 正确；G0 review 核对该行存在 |
| **Blueprint 顶部 import 引发循环依赖 → `:8000` 整体起不来** | 中 | 高 | 遵守 §1.1 导入位置红线（在 `envelope/fail/ERROR_CODES/API_PREFIX` 定义之后）；G0 review diff；S1 启动后立即验证既有 `/quant/api/v1/health` 与 41 项冒烟全绿；命中即按 §6.2 回滚 |
| **`build_web_snapshot.py` 强依赖 `terminal8094_adapter_check.json` 的 `ok=true`**（当前为 `false / L1 FAIL / L2 SKIPPED`），且反向对照命令会覆写该文件 | 中 | 中 | 发布序列严禁跳过 S2/S3；三条 `--base` / `--upstream-base` / `--self-test-negative` 反向对照跑完**必须重跑主校验**恢复 `adapter_check.json` 为 PASS 版，再进 S4；S4 的三条断言（probe 全通过 / adapter.ok / F4 双口径不相同）即为该风险的兜底 |
| **`deliver.py` 与 S10 远端渲染校验的顺序耦合**：`payload` 含 `web_render_remote_check.json`，但该文件由交付后的远端校验刷新，Filer 上可能是旧版证据 | 中 | 中 | S9 上传的是工作区已有的 remote_check（上一次交付产物）；S10 刷新本地文件后，若要求 Filer 证据与本次 commit 一致，**再跑一次 `python deploy/deliver.py`**（PUT/GET 回读幂等、sha256 对齐）或单独 PUT 该文件；G3 核对 `probe_time` 与 `check_time` 一致性；禁止手工编辑 Filer 文件 |
| **git 工作区混入未提交 / 临时产物**（当前有 `M` 与多项 `??`，规整层模块、`contracts/terminal8094.openapi.yaml`、`fixtures/terminal8094/` 尚未落盘） | 中 | 中 | S11 前 `git status --short` 逐项核对 §4.3 清单，禁止 `git add -A`；前置产物未落盘则本次不得提交、不得上线；`server.py` diff 须只含 §1.1 三处新增 |

---

## 10. 上线检查单（可直接勾选）

**上线前（Pre）**

- [ ] Python 3.13.14 / Flask 3.1.3 / Node / `pip install -r requirements.txt` 通过；依赖漏洞扫描无高危项
- [ ] `:8000` 本机实测空闲；`netstat -ano | findstr :8000` 无 LISTENING
- [ ] 上游 `http://192.168.2.203:8094/` 可达，`validate_terminal_8094.py` → `PASS (32/32)`、`method="read-only GET"`；`--base` 指向未监听端口 → `FAIL (0/32)` 退出码 1
- [ ] **G0**：`server.py` diff 仅含 §1.1 三处新增；导入位置在 `envelope/fail/ERROR_CODES/API_PREFIX` 之后；4 个错误码已注册；未新增 `/health` / 进程 / 生命周期；`from server import envelope, fail, API_PREFIX` 复用成立
- [ ] 规整层模块、`contracts/terminal8094.openapi.yaml`、`fixtures/terminal8094/` 均已落盘并纳入版本控制
- [ ] 启动 **`python apps/api/server.py`，端口 8000**；`/quant/api/v1/health` → 200；`GET /quant/api/v1/terminal8094/meta` → 200 + 信封最小集齐全
- [ ] **禁止**探测 `/quant/api/v1/terminal8094/health` 与 `/api/health` 作为挂载 / 运行态判据
- [ ] `days=999999` → 400 且 `upstream_call_count=0`、响应体 < 1 MB；`days=-1` / `limit=999999` / 非法代码 → 4xx + `error.code` 存在 + `request_id` 非空；`{detail}` 原生体次数 = 0
- [ ] `ERROR_CODES` 4 码生效：`INVALID_CONDS` 落 400（不是 500）
- [ ] 全量响应 disclaimer 完整率 100%（含错误 / 降级）；验收页置顶声明 ≥ 1、禁词数 = 0、外部 CDN 引用数 = 0
- [ ] 降级严格模式：`degraded=true` + `data=None` + `provenance.stale=true` + 「非实时·缓存/样例」；三计数器（§7.3）均为 0
- [ ] **回滚演练**完成并计时 ≤ 1 min：摘注册行 → 重启 → `meta` 404 / `health` 200 / 41 项冒烟 PASS → 恢复

**上线中（During）**

- [ ] S3 **G1**：`adapter_check.json` 的 `L1=PASS` 且 **`L2=PASS`（不是 SKIPPED）**、`skipped=0`、`failed=0`、反向对照全 FAIL —— **不依据退出码判定**
- [ ] 三条反向对照跑完后已**重跑主校验**，恢复 `adapter_check.json` 为 PASS 版
- [ ] S4 快照三条断言通过、指纹 `e819989972c994d1` 已固化进 `index.html` meta
- [ ] S5 `WEB_RENDER: PASS`（online + offline 双模式 9 面板）；S6 `CONTRACT_VALIDATION: PASS`；S7 `verify.json ok=true`；S8 `SMOKE: PASS 41/41`
- [ ] **G2**：上游 32/32 复跑仍成立、平台既有 `/quant/api/v1/*` 全绿、Filer PUT/GET 可用
- [ ] S9 `DELIVERY: PASS`、`PROBLEMS: []`、每个文件 `verified=True`、`all_verified=true`
- [ ] S10 远端渲染 `WEB_RENDER: PASS`、仅 offline 模式、9 面板；若本地 `web_render_remote_check.json` 被刷新，已重跑 S9 使 Filer 证据对齐
- [ ] **G3**：`delivery_report.json` / `MANIFEST.json` 与 Filer 实际内容 sha256 一致、无 `本地缺失` / `回读校验失败`
- [ ] S11 `git status --short` 逐项核对、禁止 `git add -A`；push `origin master` 成功、CI 触发

**上线后（Post）**

- [ ] 四类告警（§7.2）与三计数器（§7.3）接入日常巡检；P0/P1/P2 通知渠道与响应人已确认
- [ ] 抽 100 条结构化日志，字段完整率 100%；`request_id` 可从验收页一路串到上游调用记录
- [ ] 灰度按 P0→P1→P2→P3 停稳，每阶段复跑 32/32 与 41 项冒烟；未开放第三方按 OpenAPI 接入与 :8093 任务通道
- [ ] 上游 `built_at` / `latest_date` 变更监测生效；`upstream_fingerprint` 漂移告警可触发
- [ ] 每周回滚演练排期已登记；24 h 复盘模板（四问）就绪
- [ ] 验收页「旁路规整、不改动既有服务」标注可见；research-only 声明置顶
- [ ] 回滚预案（§6）与本文档随 commit SHA 一同归档，最小回滚单元明确为「`server.py` 单行注册 + 重启」

---

## 11. 待确认

1. **规整层模块路径与形态**：架构定稿写 `apps/api/terminal8094/` 包（blueprint/client/conds_guard/normalize/stats_scope），`validate_adapter.py` 的 `target` 写 `apps/api/terminal8094.py` 单文件。§1.1 的注册行须以实现 PR 的实际导出 `create_blueprint(envelope, fail, api_prefix)` 为准，请架构师与后端工程师确认最终路径与导出名。
2. **前置产物落盘时点**：截至本次评估，`contracts/terminal8094.openapi.yaml`、`fixtures/terminal8094/`、规整层模块尚未落盘，`adapter_check.json` 为 `ok=false / L1 FAIL / L2 SKIPPED`。请主理人确认这些由 Phase 2/3 哪个里程碑交付、以及 G1 放行的最晚时点。
3. **`build_web_snapshot.py` 增量与 `deliver.py` payload 的覆盖关系**：`terminal8094-report.html`、`terminal8094_adapter_check.json` 目前**不在** `deploy/deliver.py` 的 `payload` 列表（只随 `astock-quant-lab-v3.tar.gz` 整包上传）。若验收方需要这两个文件的独立 Filer URL，需确认是否增补 payload —— 本文档不修改 `deliver.py`。
4. **S9 / S10 顺序**：主理人给定序列为 `deliver.py` → 远端渲染校验；但 `payload` 含 `web_render_remote_check.json`，远端校验会刷新它。请确认是否固定采用「S10 后重跑一次 S9」的收口动作（§9 风险第 10 行）。
5. **CI 覆盖范围**：`.github/workflows/ci.yml` 为 ubuntu / Python 3.11、内网不可达，不跑 :8094 上游体检与 adapter L2。请确认是否保持「本机人工流水线 + push 触发 CI 回归」的混合模式，不加内网 runner。
6. **F1 六键映射触发前提**（架构定稿 §7.2-1）：按裁决 1 默认未满足，保持 Could；若「本期开放第三方按 OpenAPI 接入」满足，需主理人一句话升 Should 并补 0.5 人日。
7. **`output/result.json` 重跑导致 `metric_fingerprint == 'e819989972c994d1'` 继承自检假失败**：建议在断言旁加「与 `quantlab.fingerprint(result.json)` 实时一致」兜底（不替换裁决 2 给的两条断言）。
8. **上游 `group` 枚举与 `market` 取值**是否需在子契约枚举化：本期按透传 + 白名单正则处理，不展开；请数据工程组确认。
9. **回滚后 Filer 证据的保留策略**：§6.3 主张保留历史证据并加 `rolled_back: true` 角标，请主理人确认是否需要改为撤下（下架）验收页。

---

> 定稿：陆上线（部署运维工程师） · Phase 3 · research-only
> 本方案不修改 `apps/api/server.py`、`deploy/deliver.py` 或任何既有服务配置；仅新增本文档。所有发布动作对上游 :8094 的请求方法集合为 `{GET}`。
