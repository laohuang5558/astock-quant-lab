# :8094 改造评估 · Phase 1 专家团产出原文与主理人裁决

> 团队：`terminal-8094-review`（TeamCreate 建立）· Workflow B（架构与技术选型评审）+ 实施
> Phase 0 由主理人实测完成（用户明确「不在询问，先给我开发出来」，跳过提问）
> Phase 1 并行调度：`scenario-product-manager`（许可研）+ `system-architect`（梁承宇）
> 事实基线：`docs/TERMINAL_8094_EVIDENCE.md` + `output/terminal_8094_probe.json`（32/32，反向对照 0/32）
> 记录原则：成员产出原文存档，主理人只做编排、裁决与汇编；成员结论冲突时显式裁决并记入「决策与权衡」

---

## 一、Phase 1 成员产出原文（存档）

### 1.1 `scenario-product-manager` · 场景产品策略 v1.2（许可研）

> 基线 v1.1；v1.2 = v1.1 + 架构拍板。§5 为全量最终版，供脚本直接消费。
> 8 处变更：①部署形态落定（:8000 Blueprint 常驻 + 离线 fixture 双通道），验收表拆 L1 离线／L2 在线两层；②`/health` 回归「继承能力、不计本期 Must」；③错误体改读既有 `error.code`、放弃顶层别名，`data` 接受 None；④纠正「无 code」指 data 中无个股代码，不是禁止 `error.code` 字段；⑤上游失败兜底码并入复用断言；⑥F4 `truncation_rule` 枚举化、五字段入指纹；⑦F8 升级为「全量响应 disclaimer 完整率=100%」（含错误／降级）；⑧新增可复跑判据，排除 `{server_time, request_id}`。

**1. 项目定义与成功标准**：一句话定义＝旁路规整层／不改写上游选股结果。成功标准②：规整层验收脚本 **L1 离线 + L2 在线双通道**均 N/N PASS、退出码 0；三种负例（Blueprint 未挂载／fixture 缺失或篡改／上游不可达）反向对照判 FAIL。其余：上游取证保持 PASS、Must 100% 脚本判定、research-only 覆盖 100%、0 写入且 0 条判据写成「8094 返回结果变正确」。

**2. 目标用户与用户故事**：U1 终端使用者／U2 本平台验收方／U3 集成方；US1-US5（F1 仅 U3 受害，US5 为 Could）。

**3. MVP 范围（MoSCoW）**
| 档 | F | 理由 |
|---|---|---|
| Must | F6 | 统一信封／错误码／参数边界(days≤2000)；`/health`、幂等键、指标指纹、安全响应头＝**继承能力**，零成本继承，不新增代码／端点、不计本期验收 |
| Must | F8 | 合规红线；disclaimer 在 envelope 层统一注入，**含错误／降级响应的全量覆盖**；含自建验收页置顶声明与零 CDN |
| Could | F1 | 契约失真、仅 U3 受害；6 键映射 Won't（待裁决） |
| Won't | F7 | 条件性风险，零 CDN 列前瞻加固、不计 PASS/FAIL |

F2／F4／F3／F5 行同 v1.1。

**4. RICE 与实施序**：F1 主受害方仅 U3、R=1、RICE 0.4。Top5 **F6 → F8 → F2 → F4 → F3**；F8 前置于 F2（disclaimer 在 envelope 层注入，是含 F2 错误响应在内所有响应的前置依赖）；M3 内部 F4(stats_scope) → F3(normalize 过滤)。F2 五 payload 仍 P0 阻断，F2／F4 二选一取 F2。

**5. 可度量验收（v1.2 全量最终版）**

*L1 离线确定性层*（`validate_adapter.py` import + fixture 回放，不依赖内网，必跑）：断言模块**输出对象**字段，不断言 HTTP 状态码。
1. **F2（P0 阻断）**：5 个已知无效 conds 与有效 `cci_w>99` 回放 → 无效集 `ok=false`、`error.code=INVALID_CONDS`、`data ∈ {None, []}` 且 **data 中不含任何个股代码**、`disclaimer` 完整；有效集 `ok=true`、回显 `applied_conds` 且首元素≠`sz000851`。
2. **F8**：全量输出（含成功／错误／降级）→ `disclaimer.research_only=true`、命中「不构成投资建议」、**完整率=100%**；验收页置顶声明数≥1、禁词数=0、自建页外部 CDN 引用数=0。
3. **F4**：同一 signals fixture → **双口径并置**（default `sample_n=60`／full `total=515`），两对象各带 `sample_n/window/limit/truncation_rule`（枚举化）、caveat 命中常量文案、`strategy_verdict` 恒 `not_approved`；**不对上游胜率数值做相等判定**；五字段纳入 `metric_fingerprint` 规范化 payload。
4. **F3**：含 2 只 null、10 只 ST 的 300 条 → null 数=0、ST 数=0、`filtered_count`=剔除数、`filter_rules` ⊆ {metric_null, st, unnamed, suspended}。
5. **降级**（复用 F2 断言）：上游超时／超限 fixture → `ok=false`、`degraded=true`、错误码 ∈ {INVALID_CONDS, 上游兜底码}、`data` 为空或显式标注样例、仍带 disclaimer 与「非实时·缓存/样例」；半包次数=0、**降级响应被当作真实选股结果消费次数=0**。
6. **可复跑**：同一 fixture 连续两次 → 排除 `{server_time, request_id}` 后 `data`／`metric_fingerprint`／`disclaimer`／`strategy_verdict` 逐字段差异数=0；本期**不校验**指纹具体取值或生成规范。

*L2 在线契约层*（内网可达时跑，Blueprint 挂载 :8000）：
7. **挂载与 F6**：`GET /quant/api/v1/terminal8094/<任一只读端点>` → 200 且信封最小集 `ok/data/request_id/server_time` 齐全（**以此证明挂载，不新增 /health 端点**）；`days=-1/999999`、`limit=999999`、非法代码 → 400、`{detail}` 原生体次数=0、`error.code` 存在、`request_id` 非空。
8. **边界与全局**：`days=999999` → 400 且上游未被调用、响应体 <1MB；上游超时 → 504/5xx + `degraded=true`，不返回半包。两套脚本与三种负例 → 上游取证保持 PASS、规整层全 PASS／退出码 0、三种反向对照均 FAIL；**0 条判据断言「8094 返回结果变正确」**；`metric_fingerprint` 写「不校验」而非「禁止出现」；F7 零 CDN 仅前瞻加固、不计 PASS/FAIL。

**6. 风险与合规边界**：disclaimer 覆盖含错误／降级响应的全量输出；降级响应不得以真实选股结果形态被消费。人机边界：0 环节交智能体。

**7. 否决的 2 个范围方案**：A 直改 8094 服务端（无源码、无写权限、越权）；B 升级为完整研究引擎（超出「验证改造点」范围，触发范围膨胀）。架构「继承能力不计 Must」不触发 B（零新增代码／端点／工作量）。

**8. 待确认**：①F1 的 6 键 PRESETS 映射是否纳入本期（产品侧维持 Could／映射 Won't）；②ST「一律排除」vs「保留打标」：默认排除＋开关；③F4 是否强制全样本展示：建议否；④部署形态已由架构关闭；⑤顶层别名已回架构：直接用既有 `error.code`。

### 1.2 `system-architect` · 铁律回执 + 附录 A（梁承宇）

**铁律执行**：收到 `scenario-product-manager` 两条**直连**消息（v1.1 对齐／索取 v1.2 两结论），按主理人「停止与 PM 直连、跨成员信息经我中转」裁决，**未直连回复 PM**，全部上报主理人。Phase 1 架构侧已收口，`v1.2 FINAL` 为唯一基线，本条为附录 A 字段补强 + 中转／裁决请求。**F2 优先于 F4 裁决不变**。

**部署形态（可原样转发 PM）**：**常驻，但不是「新增常驻 HTTP 服务」**——规整层 = 挂进 :8000 既有 Flask app 的 Blueprint，路由前缀 `/quant/api/v1/terminal8094`，**不新增进程、不新增 health／生命周期**，复用平台既有 `/quant/api/v1/health`。明确否决挂 :8093（只读 GET 硬边界 + 需 `POST /api/tasks` 触发 LLM 写操作）。:8888 验收页仅展示、不承载逻辑。双通道单一实现：同一 `apps/api/terminal8094/*` 模块，在线由 Blueprint 调、离线由 `scripts/validate_adapter.py` import + fixture 回放。验收表「**继承能力自检**」行措辞标注「**零新增功能，仅验证复用**」；不探 `/quant/api/v1/terminal8094/health`（不存在，探了会误 FAIL）。

**F6 最终裁剪**：本期 Must = 统一信封最小集（`ok/data/request_id/server_time`）、`error.code=INVALID_CONDS`、参数边界（days≤2000、limit 上限）。`/health`、幂等键、指标指纹、安全响应头（含 CSP）统一归入「**继承能力（不计入本期验收）**」，由既有 `envelope()` / `_IDEMPOTENCY` / `/quant/api/v1/health` 零边际成本继承；验收表对 `metric_fingerprint` 写「不校验」而非「禁止出现」。复用硬约束已落地：规整层 `from server import envelope, fail, API_PREFIX`，禁止自建第二套信封；`validate_adapter.py` 每条响应断言 `request_id` 非空、`metric_fingerprint == 'e819989972c994d1'`。

**附录 A 字段补强**（字段细化，不新增功能、不违反裁剪）：
- 统一字段名 **`applied_conds`**（收敛 `provenance.preset_applied`／「实际 conds」表述）；F2 有效集也回显于此。
- **降级响应**：`degraded=true` + `data=[]` 或显式标注样例 + `disclaimer.research_only` + `provenance.stale` +「非实时·缓存/样例」文案；硬约束「降级响应不得以真实选股结果形态被消费」（否则 F2 静默错误在超时路径原样复现）、「半包次数=0」（与 1MB 流式上限一致）。
- **双口径对象**：各带 `sample_n/window/limit/truncation_rule` + `caveat`（「未计成本·无样本外·无多重检验校正·有条件存活样本」）+ `strategy_verdict` 恒 `not_approved`；**不对上游胜率数值做相等判定**。双口径需两次只读上游调用（`limit=60` / `limit=600`），须并入端到端 8s 预算，建议并发或短 TTL 缓存，且全样本调用同样受 1MB 上限。
- 其余 `filtered_count/filter_rules`、`error_code=INVALID_CONDS`、每响应 `request_id` 已对齐。

**确认对齐、无需改动**：F4 数值相等判定修正、F7 拆两层（自建验收页零 CDN＝Must 可判定；8094 `/docs` `/redoc` 零 CDN＝前瞻加固、不计 PASS/FAIL，理由「服务端静态页规整层改不到」）、F2 五 payload P0、research-only 共同边界、days≤2000 / 1MB / 超时降级断言、0 条断言「8094 返回结果变正确」。

**风险提示**：PM v1.1 若按原稿冻结，会因 `/health` 判据探不存在端点而**误 FAIL**。

**F1 真分歧（请求主理人裁决）**：PM 把 F1 定为 Could／映射 Won't，折中「对外不复制失真声明 + 回显 `applied_conds`」；但主理人上条裁决「F1 并入 M2」，架构 v1.2 FINAL 据此把 6 键冻结常量映射写进 M2（+0.5 人日）——两者口径不一致。架构意见：PM 理由②（6 键是上游前端内联 JS 私有字典，复制成冻结常量后上游一改我方契约先失真，等于用新契约失真修旧的）成立，与 v1.1 风险表「PRESETS 映射漂移」同源。建议**分阶段**：本期只做零额外实现的契约卫生，6 键冻结常量映射延后到触发条件「本期开放第三方按 OpenAPI 接入」满足时补（Reach 重估至 6 升 Should）。请裁决：维持「F1 并入 M2」或调整为分阶段。

---

## 二、主理人裁决（高屋建）

### 裁决 1 · F1 分歧 → **采纳「分阶段」，本期不实现 6 键映射**

- **生效条文**：本期只做**零额外实现的契约卫生**——我方契约**不声明** `preset` 参数（避免复制失真声明），所有响应统一回显 **`applied_conds`**。6 键 PRESETS 冻结常量映射**从本期 M2 移出**，改为「条件触发的后续项」，不写验收用例、不写代码。
- **触发条件**：本期开放第三方按 OpenAPI 接入时，Reach 重估至 6，F1 升 Should 并补映射；在此之前保持 Could。
- **理由**：①6 键是上游前端内联 JS 私有字典，复制成冻结常量后上游一改我方契约先失真，等于用新的契约失真去修旧的；②F1 主受害方 U3 不在本期 Reach 内（RICE 0.4）；③本期已有 5 项 Must 与 P0 判据，不为零 Reach 项增加验收面；④采纳架构师自己给出的分阶段建议，两位成员的专业判断一致。
- **决策变更记录**：我此前发给架构师的「F1 并入 M2」是编排预判，现予撤回；以本裁决为准。被否决方案 = 「本期直接实现 6 键冻结常量映射」，否决理由如上。

### 裁决 2 · 继承能力 → **确认调和方案，保留复用硬约束**

- `/health`、幂等键、指标指纹、安全响应头归入「**继承能力（不计入本期验收）**」：零新增代码／端点／进程／生命周期，由既有 `envelope()` / `_IDEMPOTENCY` / `/quant/api/v1/health` 继承。
- 规整层**必须** `from server import envelope, fail, API_PREFIX`，禁止自建第二套信封。
- `validate_adapter.py` 对每条响应断言 `request_id` 非空、`metric_fingerprint == 'e819989972c994d1'`，验收表写在「**继承能力自检**」行，措辞标注「**零新增功能，仅验证复用生效**」。
- **与 PM §5-6 的口径统一**（两位成员此处表述有张力）：PM「本期不校验指纹具体取值或生成规范」＝不要求规整层**实现**指纹算法；我的硬约束＝验证规整层**复用了**既有 `envelope()` 而产出该字段。两者不矛盾：断言的是「复用事实」，不是「新增功能」。验收表按「继承能力自检」措辞，不写成「F6 Must 校验指纹」，也不写「禁止出现」。
- **明确禁止**：探 `/quant/api/v1/terminal8094/health`（该端点按裁决不存在，探了会误 FAIL）。挂载证明改用 `GET /quant/api/v1/terminal8094/<任一只读端点>` → 200 + 信封最小集齐全。

### 裁决 3 · 错误体字段 → **统一 `error.code`，取消顶层别名**

- 我此前给架构师的「顶层 `error_code` + `error.code` 别名」裁决**撤回**；采纳 PM v1.2 与架构附录 A 的收敛结论：单一事实来源 `error.code=INVALID_CONDS`，不加顶层别名，`data` 接受 `None` 或 `[]`。
- **理由**：双字段存在不一致风险；复用既有错误体是零成本继承；单一字段即可满足 L1/L2 全部判据。
- PM 措辞澄清生效：「无 code」指 **data 中无个股代码**，不是禁止 `error.code` 字段。

### 裁决 4 · 事实基线更正（主理人实测，覆盖任何成员文本中的旧表述）

1. **F5 不是「不可复算」**：全样本反推确认 `fwd_h = close[t+h] / open[t+1] - 1`（信号次日开盘买入）。5/20/60 日三周期 n=413/412/407，T+1 开盘口径最大绝对误差 4.996e-05（仅 4 位小数舍入），收盘到收盘口径平均误差约 0.007。算法自洽、可独立复算；真实缺口是「契约未披露该口径」+「日线硬截断 2000 根（首根 2018-08-16）与 `meta.universe_start=2015-06-01` 不符」。因此改造建议为**补披露**，不是改算法、也不是标 UNVERIFIABLE。
2. **F2 精确机制**：上游按分号逐条解析 `conds`，无效项**静默丢弃**；全部无效时退化为默认选股清单。`conds` 通道本身有效，前端 6 个预设正是把字符串填进 `#cond-input` 再以 `conds` 提交；注入型字符串未被求值，不构成安全漏洞。缺陷是「失败时无反馈 + 无声降级」。
3. **上游取证计数已更新为 32/32**（反向对照 0/32），成员文本中的「27/27」为旧稿，一律以 `output/terminal_8094_probe.json` 为准。
4. **F7 是条件性风险**：`cdn.jsdelivr.net` / `fonts.googleapis.com` 本次实测**可达**（187,956 B / 1,103,471 B / 209 B），故为「内网隔离或离线时会白屏」，不是现状已坏。
5. **上游延迟实测 P95**（12 次采样，供端到端预算校准）：`/api/meta` 24ms、`/api/stats` 9ms、`/api/pick` 37ms、`/api/scan` 15ms、`/api/stock?days=420` 61ms、`/api/stock?days=999999` 131ms、`/api/signals?limit=600` 27ms。8s 端到端预算充裕；`days` 边界拦截放在上游调用之前，避免 449KB 大响应。

### 裁决 5 · 其余待确认项

- ST 处理：**默认排除 + 开关**（枚举 `metric_null / st / unnamed / suspended`），采纳 PM。
- F4：**不强制**全样本展示，双口径并置即可，采纳 PM。
- 实施序 **F6 → F8 → F2 → F4 → F3** 不变；**F2 优先于 F4** 的架构裁决不变。
- 部署形态：**挂 :8000 既有 Flask app 的 Blueprint**，否决 :8093 与新增进程，采纳架构。

---

## 三、决策与权衡汇总（供 Phase 4 汇编）

| # | 冲突／方案 | 裁决 | 被否决方案及理由 |
|---|---|---|---|
| 1 | F1 6 键映射：M2 实现 vs Could/Won't | 分阶段；本期只做契约卫生 | 本期实现 6 键映射——上游私有字典复制后必然漂移，等于用新失真修旧失真；Reach 仅 U3 |
| 2 | `/health` 等是否列 Must | 继承能力，不计验收 | 新增 health／进程／生命周期——零新增代码原则，且探不存在端点会误 FAIL |
| 3 | 错误体顶层别名 | 统一 `error.code` | 双字段别名——存在不一致风险，复用既有错误体即可 |
| 4 | 规整层形态 | :8000 Blueprint + 离线 fixture 双通道 | ①反向代理常驻新服务（新增进程／生命周期）；②挂 :8093（需 POST 触发 LLM 写操作，越只读边界）；③纯离线快照（无法验证在线契约） |
| 5 | F2 vs F4 只能修一个 | 先修 F2 | 先修 F4——F2 是使用者直接踩到的静默错误，F4 是披露问题 |
| 6 | 是否直改 8094 服务端 | 否 | 无源码、无写权限、越权；改造落点只能是本平台侧 |
| 7 | 是否升级完整研究引擎 | 否 | 超出「验证改造点」范围，触发范围膨胀 |
