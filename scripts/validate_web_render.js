/**
 * 前端渲染确定性校验（Node，无第三方依赖）
 *
 * 用最小 DOM 桩真实执行 apps/web/index.html 的内联 JS，分别验证：
 *   online  —— location.port=8000，fetch 指向真实后端服务，走 API 数据链路
 *   offline —— location.port=8888，走内联快照（内网静态托管形态）
 * 断言：10 个面板均产出内容、关键真实读数出现、无 JS 运行时异常、无装载失败；
 * 第 10 面板（8094 改造评估）另做真实读数绑定与「抹掉 t8094 → 降级」反向对照。
 *
 * 用法：node scripts/validate_web_render.js [base_url]
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const BASE = process.argv[2] || 'http://127.0.0.1:8000';
// 可选：从远端静态托管地址读取 index.html / snapshot.js，用于验证已发布页面本身
const SOURCE = process.argv[3] || null;
let html = fs.readFileSync(path.join(ROOT, 'apps/web/index.html'), 'utf8');
let snapshotJs = fs.readFileSync(path.join(ROOT, 'apps/web/snapshot.js'), 'utf8');

const blocks = [...html.matchAll(/<script(?![^>]*src=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
if (blocks.length !== 1) throw new Error(`预期 1 段内联脚本，实际 ${blocks.length}（防假阴性）`);
const inline = blocks[0];

const CHECKS = [];
function check(name, cond) { CHECKS.push([name, !!cond]); }

function makeEl(id) {
  return {
    id, innerHTML: '', textContent: '', style: {}, _attrs: {},
    classList: { add() {}, remove() {} },
    setAttribute(k, v) { this._attrs[k] = v; },
    getAttribute(k) { return this._attrs[k]; },
    addEventListener() {},
    closest() { return null; },
    querySelector() { return null; },
  };
}

async function runMode(mode) {
  const els = {};
  const metaEl = makeEl('meta-fingerprint');
  const documentStub = {
    getElementById(id) { return els[id] || (els[id] = makeEl(id)); },
    querySelector(sel) { return sel.indexOf('metric-fingerprint') >= 0 ? metaEl : null; },
    querySelectorAll() { return []; },
  };
  const runtimeErrors = [];
  const realFetch = globalThis.fetch;
  const ctx = {
    console,
    document: documentStub,
    location: { port: mode === 'online' ? '8000' : '8888' },
    window: {},
    fetch: (url, opt) => realFetch(new URL(url, BASE), opt),
    setTimeout, clearTimeout, setInterval, clearInterval,
    Promise, JSON, Math, Number, String, Array, Object, Date, Error, isNaN,
    AbortSignal,
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  try {
    vm.runInContext(snapshotJs, ctx, { filename: 'snapshot.js' });
    // snapshot.js 写的是 window.__QUANT_SNAPSHOT__
    if (!ctx.window.__QUANT_SNAPSHOT__) throw new Error('快照未挂载到 window');
    vm.runInContext(inline, ctx, { filename: 'index.inline.js' });
  } catch (e) {
    runtimeErrors.push(`同步执行异常: ${e.message}`);
  }
  await new Promise(r => setTimeout(r, mode === 'online' ? 3000 : 400));

  const panels = ['p-overview', 'p-equity', 'p-factors', 'p-overfit', 'p-gate', 'p-risk', 'p-tasks', 'p-delivery', 'p-feikai', 'p-t8094'];
  const allHtml = panels.map(id => (els[id] ? els[id].innerHTML : '')).join('\n');
  if (runtimeErrors.length) console.log('  SYNC ERROR:', runtimeErrors.join(' | '));
  check(`[${mode}] 10 个面板全部渲染出内容`, panels.every(id => els[id] && els[id].innerHTML.length > 200));
  const fk = (els['p-feikai'] || {}).innerHTML || '';
  check(`[${mode}] 飞凯材料策略面板含真实读数`, fk.includes('飞凯材料') && fk.includes('不放行')
        && fk.includes('等价随机对照') && fk.length > 400);
  check(`[${mode}] 飞凯材料策略面板含逐笔交易与变体表`,
        (fk.match(/<tr>/g) || []).length >= 7 && fk.includes('主规则'));
  // v2：对话新增条件面板的数值必须来自同一份快照，且检查本身不能恒真
  const V = (ctx.window.__QUANT_SNAPSHOT__ || {}).feikai_v2 || {};
  const ma5 = Number(V.current && V.current.ma5w_qfq).toFixed(3);
  const ma10 = Number(V.current && V.current.ma10w_qfq).toFixed(3);
  const fp2 = String(V.fingerprint || '');
  check(`[${mode}] v2 面板含新增条件核对与合规边界`,
        fk.includes('v2 · 对话新增条件核对') && fk.includes('月线 MACD 死叉')
        && fk.includes('不构成买卖点或操作建议'));
  check(`[${mode}] v2 面板绑定 5 / 10 周线真实读数`,
        !!V.current && fk.includes(ma5) && fk.includes(ma10));
  check(`[${mode}] v2 面板绑定变体数与样本坍缩读数`,
        !!V.variant_summary && fk.includes(String(V.variant_summary.n_variants))
        && fk.includes('全叠加信号数') && fk.includes('样本坍缩'));
  check(`[${mode}] v2 面板绑定研究指纹与报告链接`,
        fp2.length === 16 && fk.includes(fp2) && fk.includes('feikai-kdj-v2-report.html'));
  check(`[${mode}] 反向对照：抹掉 v2 指纹后绑定失效`,
        fp2.length === 16 && !fk.replace(fp2, '0000000000000000').includes(fp2));
  check(`[${mode}] 无数据装载失败`, !allHtml.includes('数据装载失败') && !allHtml.includes('class="err"'));
  check(`[${mode}] 质量门不放行结论出现`, allHtml.includes('不放行'));
  check(`[${mode}] 总收益 52.04% 出现`, allHtml.includes('52.04%'));
  check(`[${mode}] 夏普 0.3477 出现`, allHtml.includes('0.3477'));
  check(`[${mode}] 最大回撤 -60.19% 出现`, allHtml.includes('-60.19%'));
  check(`[${mode}] 净值 SVG 曲线生成`, allHtml.includes('<svg') && allHtml.includes('viewBox="0 0 1100 300"'));
  check(`[${mode}] 因子表 6 行`, ['mom_60', 'mom_120', 'rev_5', 'low_vol_20', 'liq_20', 'illiq_20'].every(k => allHtml.includes(k)));
  check(`[${mode}] 随机对照 200 次与 31.5 分位`, allHtml.includes('200') && allHtml.includes('31.5'));
  check(`[${mode}] 质量门 23 项裁决`, (els['p-gate'].innerHTML.match(/<tr>/g) || []).length >= 23);
  check(`[${mode}] 风险定级 13 项`, (els['p-risk'].innerHTML.match(/<tr>/g) || []).length >= 13);
  check(`[${mode}] 审核意见 6 位委员`, ['量化工作流主席', '回测工程师', '现实检验官'].every(k => allHtml.includes(k)));
  check(`[${mode}] 合规 research-only 声明`, els['p-delivery'].innerHTML.includes('research-only') || (els['footer'] || {}).innerHTML?.includes('research-only'));
  check(`[${mode}] 指纹写入 meta`, metaEl._attrs['content'] === 'e819989972c994d1');
  check(`[${mode}] 模式徽标正确`, (els['badges'].innerHTML || '').includes(mode === 'online' ? '在线服务' : '离线只读快照'));
  // ── 第 10 面板：8094 改造评估，全部数值必须来自 t8094 快照，禁止恒真 ──
  const T8 = (ctx.window.__QUANT_SNAPSHOT__ || {}).t8094 || {};
  const t8 = (els['p-t8094'] || {}).innerHTML || '';
  const ads = T8.agg_dual_scope || {};
  const dw = ads.default && ads.default.fwd60 && ads.default.fwd60.win;
  const fw = ads.full && ads.full.fwd60 && ads.full.fwd60.win;
  check(`[${mode}] 第 10 面板渲染 8094 改造评估`,
        t8.length > 600 && t8.includes('8094 改造评估') && t8.includes('F2') && t8.includes('八类改造点'));
  check(`[${mode}] 第 10 面板绑定上游取证与反向对照计数`,
        !!T8.probe && t8.includes(String(T8.probe.passed)) && t8.includes(T8.probe.negative_control || '0/32 FAIL')
        && (T8.findings || []).length >= 32);
  check(`[${mode}] 第 10 面板绑定 F4 双口径真实胜率（防恒真：两读数必须不同）`,
        dw != null && fw != null && dw !== fw && t8.includes(dw.toFixed(3)) && t8.includes(fw.toFixed(3)));
  // 统计增强：历史表现分布画像须真实绑定区间均值，默认/全样本两口径读数必须不同（防恒真）
  const dAvg = ads.default && ads.default.fwd60 && ads.default.fwd60.avg;
  const fAvg = ads.full && ads.full.fwd60 && ads.full.fwd60.avg;
  check(`[${mode}] 第 10 面板含历史表现画像且区间均值双口径绑定（防恒真）`,
        t8.includes('历史表现分布画像') && t8.includes('口径敏感性') && t8.includes('正向样本占比')
        && dAvg != null && fAvg != null && dAvg !== fAvg
        && t8.includes((dAvg*100).toFixed(2)+'%') && t8.includes((fAvg*100).toFixed(2)+'%'));
  check(`[${mode}] 第 10 面板绑定 F5 口径与规整层验收计数`,
        !!T8.fwd_formula && t8.includes(T8.fwd_formula) && !!T8.adapter
        && t8.includes(String(T8.adapter.passed)) && t8.includes('关键实测读数'));
  check(`[${mode}] 第 10 面板为只读验收、无装载失败、含 research-only`,
        !t8.includes('class="err"') && t8.includes('research-only'));
  check(`[${mode}] 同步执行无异常`, runtimeErrors.length === 0);
  return { mode, panels: panels.length, htmlChars: allHtml.length };
}

// 反向对照：把 t8094 从快照中抹掉后重跑内联 JS，第 10 面板必须降级且不含任何胜率读数
async function runT8094Negative() {
  const els2 = {};
  const doc2 = {
    getElementById(id) { return els2[id] || (els2[id] = makeEl(id)); },
    querySelector() { return makeEl('meta-fingerprint'); },
    querySelectorAll() { return []; },
  };
  const ctx2 = {
    console, document: doc2, location: { port: '8888' }, window: {},
    fetch: () => Promise.reject(new Error('offline negative control')),
    setTimeout, clearTimeout, setInterval, clearInterval,
    Promise, JSON, Math, Number, String, Array, Object, Date, Error, isNaN, AbortSignal,
  };
  ctx2.globalThis = ctx2;
  vm.createContext(ctx2);
  let err = null;
  try {
    vm.runInContext(snapshotJs, ctx2, { filename: 'snapshot.negative.js' });
    if (ctx2.window.__QUANT_SNAPSHOT__ && 't8094' in ctx2.window.__QUANT_SNAPSHOT__) {
      delete ctx2.window.__QUANT_SNAPSHOT__.t8094;
    }
    vm.runInContext(inline, ctx2, { filename: 'index.inline.negative.js' });
  } catch (e) { err = e.message; }
  await new Promise(r => setTimeout(r, 400));
  const html2 = (els2['p-t8094'] || {}).innerHTML || '';
  check('[对照] 抹掉 t8094 → 第 10 面板显示降级提示而非空白',
        !err && html2.includes('降级') && html2.includes('缺少 t8094 快照字段'));
  check('[对照] 降级态不展示任何推断胜率读数',
        !html2.includes('0.906') && !html2.includes('0.596') && !html2.includes('90.6%'));
  check('[对照] 降级态不残留历史画像区间均值',
        !html2.includes('历史表现分布画像') && !html2.includes('21.32%') && !html2.includes('5.51%'));
}

(async () => {
  if (SOURCE) {
    const pageUrl = SOURCE.replace(/\/$/, '') + '/index.html';
    const snapUrl = SOURCE.replace(/\/$/, '') + '/snapshot.js';
    const [p, s] = await Promise.all([fetch(pageUrl), fetch(snapUrl)]);
    if (!p.ok || !s.ok) throw new Error(`远端页面读取失败: index.html=${p.status} snapshot.js=${s.status}`);
    html = await p.text();
    snapshotJs = await s.text();
    console.log(`SOURCE(remote): ${pageUrl}  html=${html.length} chars  snapshot=${snapshotJs.length} chars`);
    // 远端静态托管只存在离线形态；在线形态对已发布页面不适用
    globalThis.__ONLY_OFFLINE__ = true;
  }
  const results = [];
  for (const mode of (SOURCE ? ['offline'] : ['online', 'offline'])) {
    results.push(await runMode(mode));
  }
  await runT8094Negative();
  let bad = 0;
  for (const [name, ok] of CHECKS) {
    if (!ok) { bad++; console.log('  FAIL', name); }
  }
  console.log(`\nRENDER_CHECKS ${CHECKS.length - bad}/${CHECKS.length}`);
  results.forEach(r => console.log(`  ${r.mode}: ${r.panels} panels, ${r.htmlChars.toLocaleString()} chars HTML`));
  if (bad) { console.log('WEB_RENDER: FAIL'); process.exit(1); }
  console.log('WEB_RENDER: PASS');
  const outName = SOURCE ? 'web_render_remote_check.json' : 'web_render_check.json';
  fs.writeFileSync(path.join(ROOT, 'output', outName),
    JSON.stringify({ ok: true, base: BASE, source: SOURCE, checks: CHECKS.length,
                     failed: 0, modes: results }, null, 2));
})().catch(e => { console.error('FATAL', e); process.exit(1); });
