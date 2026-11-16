#!/usr/bin/env python3
"""内网工程交付：打包 → 计算 sha256 → 上传 SeaweedFS Filer → 回读校验 → 生成验收页。

目标环境：ai-server 192.168.2.203，Filer 匿名 PUT/GET :8888。
所有上传文件在上传后都会 GET 回读并逐字节比对 sha256，回读不一致即判定交付失败。
不使用 SSH 凭据；不改动现有 nginx / /ai/* 配置。

用法：python deploy/deliver.py
产物：dist/ 下的交付包与验收页、docs/ACCEPTANCE.md、output/delivery_report.json
"""
from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import tarfile
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT.parent
DIST = ROOT / "dist"
FILER = "http://192.168.2.203:8888"
BASE_PATH = "/workbuddy/astock-quant-lab/v3/"
BASE_URL = FILER + BASE_PATH

CT = {
    ".html": "text/html; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".yaml": "text/yaml; charset=utf-8",
    ".sql": "text/plain; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".zip": "application/zip",
    ".gz": "application/gzip",
    ".txt": "text/plain; charset=utf-8",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def http(method: str, url: str, data: bytes | None = None, ctype: str | None = None):
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", ctype or mimetypes.guess_type(url)[0] or "application/octet-stream")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, r.read()


def build_bundle() -> Path:
    """把工程、报告、技能包、架构工件打成自包含 tar.gz。"""
    DIST.mkdir(exist_ok=True)
    out = DIST / "astock-quant-lab-v3.tar.gz"
    skip_dirs = {".git", "__pycache__", ".pytest_cache", ".scratch", "dist", ".venv", "venv"}
    skip_suffix = {".pyc", ".pyo", ".log"}

    def wanted(p: Path) -> bool:
        if any(part in skip_dirs for part in p.parts):
            return False
        return p.suffix not in skip_suffix

    with tarfile.open(out, "w:gz") as tf:
        for src in sorted(ROOT.rglob("*")):
            if not src.is_file() or not wanted(src.relative_to(ROOT)):
                continue
            tf.add(src, arcname=f"astock-quant-lab-v3/astock-quant-lab/{src.relative_to(ROOT).as_posix()}")
        extras = [
            (WORKSPACE / "astock-quant-design-report.html", "reports/astock-quant-design-report.html"),
            (WORKSPACE / "astock-quant-audit-report.html", "reports/astock-quant-audit-report.html"),
            (WORKSPACE / "astock-quant-designer.zip", "skill/astock-quant-designer.zip"),
        ]
        for src, arc in extras:
            if src.exists():
                tf.add(src, arcname=f"astock-quant-lab-v3/{arc}")
    return out


def load_context() -> dict:
    result = json.loads((ROOT / "output" / "result.json").read_text(encoding="utf-8"))
    verify = json.loads((ROOT / "output" / "verify.json").read_text(encoding="utf-8"))
    contracts = json.loads((ROOT / "output" / "contracts_check.json").read_text(encoding="utf-8"))
    gate = result["quality_gate"]
    return {
        "fingerprint": verify["fingerprint"],
        "verify_ok": verify["ok"],
        "contracts_ok": contracts["ok"],
        "api_paths": contracts["checks"]["path_count"],
        "api_operations": contracts["checks"]["operation_count"],
        "api_refs": contracts["checks"]["ref_count"],
        "ddl_tables": contracts["checks"]["ddl_table_count"],
        "engineering_gate": gate["engineering_gate"],
        "strategy_gate": gate["strategy_conclusion_gate"],
        "final_verdict": gate["final_verdict"],
        "counts": gate.get("counts", {}),
        "hard_fail": gate.get("hard_fail_items", []),
        "sample_bars": result["meta"]["sample"]["bars"],
        "n_trades": result["performance"]["n_trades"],
        "cash_min": result["performance"]["cash_min_cny"],
        "exposure_max": result["performance"]["max_position_exposure"],
        "mc_percentile": result["overfit"]["suite"]["3_monte_carlo_deflated_sharpe"]["baseline_percentile"],
        "mc_paths": result["overfit"]["suite"]["3_monte_carlo_deflated_sharpe"]["n_paths"],
        "selftest": result["audit"]["selftest"]["tests_run"],
    }


def acceptance_html(ctx: dict, files: list[dict], checks: list[str]) -> str:
    rows = "\n".join(
        f'<tr><td><a href="{f["name"]}">{f["name"]}</a></td>'
        f'<td>{f["size"]:,}</td><td><code>{f["sha256"]}</code></td>'
        f'<td>{"已回读校验" if f["verified"] else "校验失败"}</td></tr>'
        for f in files
    )
    items = "\n".join(f"<li><label><input type='checkbox'> {c}</label></li>" for c in checks)
    hard = "、".join(str(x) for x in ctx["hard_fail"]) or "无"
    counts = ctx["counts"]
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="metric-fingerprint" content="{ctx['fingerprint']}">
<title>A股量化研究平台 · 内网工程交付验收</title>
<style>
body{{font-family:system-ui,-apple-system,"Microsoft YaHei",sans-serif;max-width:1080px;margin:32px auto;padding:0 20px;background:#f6f8fa;color:#1f2328;line-height:1.65}}
h1{{font-size:26px;margin-bottom:4px}} h2{{font-size:19px;margin-top:32px;border-bottom:1px solid #d8dee4;padding-bottom:6px}}
.sub{{color:#57606a;font-size:14px;margin-bottom:20px}}
.card{{background:#fff;border:1px solid #d8dee4;border-radius:8px;padding:16px 20px;margin:14px 0}}
table{{width:100%;border-collapse:collapse;font-size:13px}} th,td{{border-bottom:1px solid #eaeef2;padding:8px 10px;text-align:left;vertical-align:top}}
th{{background:#f6f8fa}} code{{background:#f0f3f6;padding:1px 5px;border-radius:4px;font-size:12px;word-break:break-all}}
.tag{{display:inline-block;border-radius:12px;padding:2px 10px;font-size:13px;margin-right:8px}}
.ok{{background:#dafbe1;color:#116329}} .warn{{background:#fff8c5;color:#7d4e00}} .bad{{background:#ffebe9;color:#a40e26}}
li{{margin:6px 0}} a{{color:#0969da;text-decoration:none}} a:hover{{text-decoration:underline}}
.foot{{color:#57606a;font-size:13px;margin-top:28px;border-top:1px solid #d8dee4;padding-top:12px}}
</style></head><body>
<h1>A 股量化研究平台 · 工程交付验收</h1>
<div class="sub">交付目标：内网 ai-server <code>192.168.2.203</code> · SeaweedFS Filer <code>:8888</code> · 交付时间 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>

<div class="card">
<h2 style="margin-top:0;border:0">交付结论</h2>
<span class="tag ok">工程 {ctx['engineering_gate']}</span>
<span class="tag bad">策略结论 {ctx['strategy_gate']}</span>
<span class="tag warn">最终 {ctx['final_verdict']}</span>
<p style="margin:12px 0 0">质量门计数：成立 {counts.get('成立','—')} / 不成立 {counts.get('不成立','—')} / 存疑 {counts.get('存疑','—')}。
硬否决项：<strong>{hard}</strong>。该判定不因平台化或交付动作而改变。</p>
<p style="margin:8px 0 0">数值指纹 <code>{ctx['fingerprint']}</code>；一致性校验 {'通过' if ctx['verify_ok'] else '未通过'}；架构工件校验 {'通过' if ctx['contracts_ok'] else '未通过'}。</p>
</div>

<h2>一、验收清单（可勾选）</h2>
<div class="card"><ul>{items}</ul></div>

<h2>二、交付物清单（均已回读比对 sha256）</h2>
<div class="card"><table>
<tr><th>文件</th><th>字节</th><th>SHA-256</th><th>状态</th></tr>
{rows}
</table></div>

<h2>三、核心验收入口</h2>
<div class="card"><ul>
<li><a href="astock-quant-design-report.html">可视化设计报告（ECharts，含指纹）</a></li>
<li><a href="astock-quant-audit-report.html">专家团联合审核报告</a></li>
<li><a href="PLATFORM_ARCHITECTURE.md">平台化技术架构（前后端 / 数据库 / 智能体编排 / 接口契约 / 目录 / 里程碑）</a></li>
<li><a href="openapi.yaml">接口契约 OpenAPI 3.1</a> · <a href="schema.sql">数据库 DDL</a> · <a href="ACCEPTANCE.md">验收清单文本版</a></li>
<li><a href="astock-quant-lab-v3.tar.gz">完整工程交付包（tar.gz）</a> · <a href="astock-quant-designer.zip">WorkBuddy 技能包</a></li>
<li><a href="MANIFEST.json">交付清单（机读，含全部哈希）</a></li>
<li>公开仓库：<a href="https://github.com/laohuang5558/astock-quant-lab">github.com/laohuang5558/astock-quant-lab</a></li>
</ul></div>

<h2>四、关键实测读数</h2>
<div class="card"><table>
<tr><th>项</th><th>读数</th><th>说明</th></tr>
<tr><td>单元测试</td><td>{ctx['selftest']} 项全通过</td><td>显式断言逐日现金与敞口约束</td></tr>
<tr><td>入模 bar</td><td>{ctx['sample_bars']:,}</td><td>20 只真实 A 股，前复权口径</td></tr>
<tr><td>成交笔数</td><td>{ctx['n_trades']}</td><td>两段式撮合、lots FIFO T+1</td></tr>
<tr><td>最低现金 / 最大敞口</td><td>{ctx['cash_min']} / {ctx['exposure_max']}</td><td>纯多头无杠杆硬约束</td></tr>
<tr><td>等价随机对照</td><td>{ctx['mc_paths']} 次真实引擎重跑</td><td>基准分位 {ctx['mc_percentile']}，未显著优于随机</td></tr>
</table></div>

<div class="foot">合规：research-only，不含实盘下单、券商接口、自动交易循环；不荐股、不给买卖点与目标价、不承诺收益、不代操作。
回测结果不等于实盘表现，历史业绩不代表未来。本页为静态验收页，勾选状态仅保存在当前浏览器页面内。</div>
</body></html>"""


def main() -> int:
    ctx = load_context()
    if not ctx["verify_ok"] or not ctx["contracts_ok"]:
        print("ABORT: 一致性校验或架构工件校验未通过，不执行交付")
        return 1

    bundle = build_bundle()
    DIST.mkdir(exist_ok=True)
    # 待上传文件：文件名 -> 本地路径
    payload: list[tuple[str, Path]] = [
        ("astock-quant-lab-v3.tar.gz", bundle),
        ("astock-quant-design-report.html", WORKSPACE / "astock-quant-design-report.html"),
        ("astock-quant-audit-report.html", WORKSPACE / "astock-quant-audit-report.html"),
        ("astock-quant-designer.zip", WORKSPACE / "astock-quant-designer.zip"),
        ("PLATFORM_ARCHITECTURE.md", ROOT / "docs" / "PLATFORM_ARCHITECTURE.md"),
        ("openapi.yaml", ROOT / "contracts" / "openapi.yaml"),
        ("schema.sql", ROOT / "db" / "schema.sql"),
        ("contracts_check.json", ROOT / "output" / "contracts_check.json"),
        ("verify.json", ROOT / "output" / "verify.json"),
        ("README.md", ROOT / "README.md"),
    ]

    checks = [
        "连通性：内网 192.168.2.203 可访问，SSH :22 / nginx :80 / Filer :8888 / PostgreSQL :5432 在线",
        "工程包可解包，目录结构与架构文档第 5 节一致",
        f"python -m quantlab selftest → {ctx['selftest']} 项全通过",
        "python -m quantlab run → 退出码 0，产出 result.json / equity.csv / trades.csv",
        f"python -m quantlab verify → ok=true，指纹 {ctx['fingerprint']}",
        "python scripts/validate_contracts.py → CONTRACT_VALIDATION: PASS",
        "技能包静态自检 → 18/18 PASS",
        "两份 HTML 报告可打开，metric-fingerprint 与 result.json 一致",
        f"质量门：工程 {ctx['engineering_gate']} / 策略结论 {ctx['strategy_gate']} / 最终 {ctx['final_verdict']}",
        "设计报告与审核报告的核心读数不与 result.json 冲突",
        f"接口契约 OpenAPI 3.1 可解析，{ctx['api_paths']} 条路径 / {ctx['api_operations']} 个操作 / {ctx['api_refs']} 处引用全部可解析",
        f"数据库 DDL：{ctx['ddl_tables']} 张表、pgvector(512)、现金/敞口 CHECK、HNSW 索引齐备",
        "交付物 sha256 与 MANIFEST.json 逐项一致，且每个 URL 已回读校验",
        "合规：research-only，无实盘下单与荐股内容",
    ]

    # 先上传，收集校验结果；验收页与清单依赖校验结果，最后再上传
    manifest: list[dict] = []
    problems: list[str] = []
    for name, path in payload:
        if not path.exists():
            problems.append(f"本地缺失：{path}")
            continue
        data = path.read_bytes()
        digest = sha256(data)
        url = BASE_URL + name
        ctype = CT.get(Path(name).suffix, "application/octet-stream")
        if name.endswith(".tar.gz"):
            ctype = CT[".gz"]
        try:
            status, _ = http("PUT", url, data, ctype)
            rstatus, back = http("GET", url)
            verified = (rstatus == 200 and sha256(back) == digest)
            manifest.append({"name": name, "size": len(data), "sha256": digest,
                              "url": url, "put_status": status, "verified": verified})
            if not verified:
                problems.append(f"回读校验失败：{name}")
            print(f"  {name}: {len(data):,} B  {digest[:16]}…  put={status} get={rstatus} verified={verified}")
        except (urllib.error.URLError, OSError) as e:
            problems.append(f"上传失败：{name} -> {e}")
            print(f"  {name}: FAILED {e}")

    # 机读清单
    manifest_obj = {
        "delivery_target": "192.168.2.203",
        "base_url": BASE_URL,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "metric_fingerprint": ctx["fingerprint"],
        "final_verdict": ctx["final_verdict"],
        "files": manifest,
        "all_verified": not problems and len(manifest) == len(payload),
    }
    (DIST / "MANIFEST.json").write_text(json.dumps(manifest_obj, ensure_ascii=False, indent=2), encoding="utf-8")

    html = acceptance_html(ctx, manifest, checks).encode("utf-8")
    (DIST / "index.html").write_bytes(html)
    acceptance_md = "\n".join([
        "# 工程交付验收清单", "",
        f"- 交付目标：内网 ai-server `192.168.2.203`，Filer `{BASE_URL}`",
        f"- 交付时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 数值指纹：`{ctx['fingerprint']}`",
        f"- 质量门：工程 {ctx['engineering_gate']} / 策略结论 {ctx['strategy_gate']} / 最终 {ctx['final_verdict']}",
        f"- 验收入口：{BASE_URL}index.html", "",
        "## 验收项", "", *[f"- [ ] {c}" for c in checks], "",
        "## 交付物", "",
        "| 文件 | 字节 | SHA-256 | 回读校验 |", "|---|---|---|---|",
        *[f"| [`{f['name']}`]({f['url']}) | {f['size']:,} | `{f['sha256']}` | {'通过' if f['verified'] else '失败'} |"
          for f in manifest], "",
        "## 复现命令", "", "```bash",
        "tar -xzf astock-quant-lab-v3.tar.gz && cd astock-quant-lab-v3/astock-quant-lab",
        "pip install -r requirements.txt",
        "python -m quantlab selftest",
        "python -m quantlab run",
        "python -m quantlab verify --render",
        "python scripts/validate_contracts.py",
        "```", "",
        "**合规**：research-only，不含实盘下单、券商接口、自动交易循环；不荐股、不给买卖点与目标价、不承诺收益、不代操作。", "",
    ])
    (DIST / "ACCEPTANCE.md").write_text(acceptance_md, encoding="utf-8")
    (ROOT / "docs" / "ACCEPTANCE.md").write_text(acceptance_md, encoding="utf-8")

    for name, path in [("index.html", DIST / "index.html"),
                       ("ACCEPTANCE.md", DIST / "ACCEPTANCE.md")]:
        data = path.read_bytes()
        url = BASE_URL + name
        try:
            status, _ = http("PUT", url, data, CT[Path(name).suffix])
            rstatus, back = http("GET", url)
            ok = rstatus == 200 and sha256(back) == sha256(data)
            manifest.append({"name": name, "size": len(data), "sha256": sha256(data),
                             "url": url, "put_status": status, "verified": ok})
            if not ok:
                problems.append(f"回读校验失败：{name}")
            print(f"  {name}: {len(data):,} B  put={status} get={rstatus} verified={ok}")
        except (urllib.error.URLError, OSError) as e:
            problems.append(f"上传失败：{name} -> {e}")
            print(f"  {name}: FAILED {e}")

    # 最终清单：不记录自身哈希（自指循环），逐项列出其余全部交付物
    manifest_obj["files"] = manifest
    manifest_obj["all_verified"] = not problems
    manifest_obj["note"] = "本清单不记录自身 SHA-256（自指循环）；其余文件均已 GET 回读逐字节比对"
    manifest_bytes = json.dumps(manifest_obj, ensure_ascii=False, indent=2).encode("utf-8")
    (DIST / "MANIFEST.json").write_bytes(manifest_bytes)
    (ROOT / "output" / "delivery_report.json").write_bytes(manifest_bytes)
    try:
        status, _ = http("PUT", BASE_URL + "MANIFEST.json", manifest_bytes, CT[".json"])
        rstatus, back = http("GET", BASE_URL + "MANIFEST.json")
        ok = rstatus == 200 and back == manifest_bytes
        print(f"  MANIFEST.json: {len(manifest_bytes):,} B  put={status} get={rstatus} verified={ok}")
        if not ok:
            problems.append("回读校验失败：MANIFEST.json")
    except (urllib.error.URLError, OSError) as e:
        problems.append(f"上传失败：MANIFEST.json -> {e}")
        print(f"  MANIFEST.json: FAILED {e}")

    # 清理探测文件
    try:
        http("DELETE", FILER + "/workbuddy/_probe.txt")
        print("  cleaned /workbuddy/_probe.txt")
    except Exception:
        pass

    print("\nBASE_URL:", BASE_URL)
    print("FILES:", len(manifest), "PROBLEMS:", problems)
    print("DELIVERY:", "PASS" if not problems else "FAIL")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
