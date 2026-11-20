/* CrossBorderOps · Agent 控制台
 * 零依赖单页应用：只做三件事 —— 拉数据、渲染、轮询。
 * 所有模型产物文本都经过 esc() 转义，避免 HTML 注入。
 */
'use strict';

const state = {
  health: null,
  catalog: null,
  projects: [],
  projectId: null,
  project: null,
  run: null,
  artifacts: {},
  artifactMeta: [],
  trace: null,
  feedback: null,
  view: 'workbench',
  tab: 'market',
  busy: false,
  pollTimer: null,
};

const TABS = [
  { id: 'market', no: '01', title: '市场', kind: 'market_report' },
  { id: 'strategy', no: '02', title: '策略', kind: 'product_strategy' },
  { id: 'listing', no: '03', title: 'Listing', kind: 'listing_pack' },
  { id: 'image', no: '04', title: '图片', kind: 'image_pack' },
  { id: 'video', no: '05', title: '视频', kind: 'video_pack' },
  { id: 'ads', no: '06', title: '广告', kind: 'ad_plan' },
  { id: 'review', no: '07', title: '评论', kind: 'review_insight' },
  { id: 'publish', no: '08', title: '发布包', kind: 'publish_pack' },
  { id: 'trace', no: '09', title: 'Trace', kind: null },
];

const AGENT_TAB = {
  '01_market_research': 'market',
  '02_product_strategy': 'strategy',
  '03_listing_creative': 'listing',
  '04_product_image': 'image',
  '05_product_video': 'video',
  '06_advertising': 'ads',
  '07_review': 'review',
};

const AGENT_KIND = {
  '01_market_research': 'market_report',
  '02_product_strategy': 'product_strategy',
  '03_listing_creative': 'listing_pack',
  '04_product_image': 'image_pack',
  '05_product_video': 'video_pack',
  '06_advertising': 'ad_plan',
  '07_review': 'review_insight',
};

const TERMINAL = ['completed', 'failed', 'awaiting_approval', 'cancelled'];

/* ------------------------------------------------------------------ */
/* 工具函数                                                            */
/* ------------------------------------------------------------------ */
const $ = (sel) => document.querySelector(sel);

function esc(value) {
  return String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

const fmt = {
  num(v, d = 0) {
    if (v == null || Number.isNaN(Number(v))) return '—';
    return Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  },
  money(v, d = 2) {
    if (v == null) return '—';
    return 'R$ ' + fmt.num(v, d);
  },
  pct(v, d = 2) {
    if (v == null) return '—';
    return (Number(v) * 100).toFixed(d) + '%';
  },
  compact(v) {
    if (v == null) return '—';
    const n = Number(v);
    if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(1) + 'M';
    if (Math.abs(n) >= 1e3) return (n / 1e3).toFixed(1) + 'k';
    return fmt.num(n, 0);
  },
  time(s) {
    if (!s) return '—';
    const dt = new Date(s);
    return Number.isNaN(dt.getTime()) ? '—' : dt.toLocaleString('zh-CN', { hour12: false });
  },
  ms(v) {
    if (v == null) return '—';
    return v >= 1000 ? (v / 1000).toFixed(1) + 's' : v + 'ms';
  },
};

function toast(message, kind = 'info') {
  const stack = $('#toast-stack');
  if (!stack) return;
  const node = document.createElement('div');
  node.className = `toast ${kind}`;
  node.innerHTML = `<span class="toast-icon">${kind === 'error' ? '!' : kind === 'success' ? '✓' : 'i'}</span>
    <span class="toast-msg">${esc(message)}</span><button class="toast-close" type="button">×</button>`;
  node.querySelector('.toast-close').addEventListener('click', () => node.remove());
  stack.appendChild(node);
  setTimeout(() => node.remove(), 5200);
}

async function api(path, options = {}) {
  const init = { ...options, headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) } };
  if (init.body && typeof init.body !== 'string') init.body = JSON.stringify(init.body);
  const resp = await fetch(path, init);
  const text = await resp.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (_) { data = { raw: text }; }
  if (!resp.ok) {
    const detail = data && data.detail ? (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)) : resp.statusText;
    const error = new Error(detail || `HTTP ${resp.status}`);
    error.status = resp.status;
    throw error;
  }
  return data;
}

function etaClass(rate) {
  if (rate == null) return 'muted';
  if (rate >= 0.9) return 'success';
  if (rate >= 0.6) return 'warn';
  return 'danger';
}

/* ------------------------------------------------------------------ */
/* 数据加载                                                            */
/* ------------------------------------------------------------------ */
async function boot() {
  renderSkeleton();
  try {
    const [health, catalog] = await Promise.all([api('/api/health'), api('/api/meta/catalog')]);
    state.health = health;
    state.catalog = catalog;
    renderHealth();
    const projects = await api('/api/projects');
    state.projects = projects.items || [];
    renderProjectList();
    const withHistory = state.projects.filter((p) => (p.run_count || 0) > 0 && p.brief && p.brief.competitor_rows && p.brief.competitor_rows.length);
    const withData = state.projects.filter((p) => p.brief && p.brief.competitor_rows && p.brief.competitor_rows.length);
    const preferred = withHistory[0] || withData[0] || state.projects[0];
    if (preferred) await selectProject(preferred.id);
    else renderEmptyWorkbench();
  } catch (err) {
    renderError(err);
  }
}

async function selectProject(projectId) {
  state.projectId = projectId;
  state.run = null;
  state.trace = null;
  state.feedback = null;
  renderProjectList();
  const project = await api(`/api/projects/${projectId}`);
  state.project = project;
  const runs = project.runs || [];
  const latest = runs[0];
  if (latest) await loadRun(latest.id, { quiet: true });
  else {
    await loadArtifacts();
    renderAll();
  }
  renderTopbar();
}

async function loadRun(runId, { quiet = false } = {}) {
  const [run, artifacts, trace, feedback] = await Promise.all([
    api(`/api/runs/${runId}`),
    api(`/api/projects/${state.projectId}/artifacts?latest_only=true`),
    api(`/api/runs/${runId}/trace`).catch(() => null),
    api(`/api/runs/${runId}/feedback`).catch(() => null),
  ]);
  state.run = run;
  state.artifactMeta = artifacts.items || [];
  state.artifacts = Object.fromEntries((artifacts.items || []).map((a) => [a.kind, a.content]));
  state.trace = trace;
  state.feedback = feedback;
  if (!quiet) toast('已刷新', 'success');
  renderAll();
  watchRun();
}

function watchRun() {
  if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
  const run = state.run;
  if (!run || TERMINAL.includes(run.status)) return;
  state.pollTimer = setInterval(async () => {
    try {
      const fresh = await api(`/api/runs/${state.run.id}`);
      state.run = fresh;
      renderAll();
      if (TERMINAL.includes(fresh.status)) {
        clearInterval(state.pollTimer);
        state.pollTimer = null;
        const artifacts = await api(`/api/projects/${state.projectId}/artifacts?latest_only=true`);
        state.artifactMeta = artifacts.items || [];
        state.artifacts = Object.fromEntries((artifacts.items || []).map((a) => [a.kind, a.content]));
        state.trace = await api(`/api/runs/${fresh.id}/trace`).catch(() => null);
        state.feedback = await api(`/api/runs/${fresh.id}/feedback`).catch(() => null);
        renderAll();
        if (fresh.status === 'failed') toast(fresh.error || '运行失败', 'error');
        else if (fresh.status === 'awaiting_approval') toast('素材已就绪，等待人工审批', 'success');
        else toast('运行完成', 'success');
      }
    } catch (err) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
      toast(err.message, 'error');
    }
  }, 800);
}

async function loadArtifacts() {
  const artifacts = await api(`/api/projects/${state.projectId}/artifacts?latest_only=true`);
  state.artifactMeta = artifacts.items || [];
  state.artifacts = Object.fromEntries((artifacts.items || []).map((a) => [a.kind, a.content]));
}

/* ------------------------------------------------------------------ */
/* 操作                                                                */
/* ------------------------------------------------------------------ */
async function withBusy(fn) {
  if (state.busy) return;
  state.busy = true;
  renderActionBar();
  try { await fn(); } catch (err) { toast(err.message, 'error'); }
  finally {
    state.busy = false;
    renderActionBar();
  }
}

async function startRun(mode, body = {}) {
  return withBusy(async () => {
    const created = await api(`/api/projects/${state.projectId}/runs`, { method: 'POST', body: { mode, ...body } });
    state.run = { id: created.run_id, status: 'queued', stage: 'queued', steps: [], metrics: {} };
    state.tab = 'market';
    renderAll();
    watchRun();
    toast('已提交运行 ' + created.run_id, 'success');
  });
}

async function iterateRun() {
  if (!state.run || !state.project) return;
  const brief = state.project.brief || {};
  const adRows = brief.ad_rows || brief.ad_rows_latest;
  if (!adRows || !adRows.length) {
    toast('该项目没有广告数据，无法闭环诊断', 'error');
    return;
  }
  await withBusy(async () => {
    const created = await api(`/api/runs/${state.run.id}/iterate`, {
      method: 'POST',
      body: { ad_rows: adRows, reviews: brief.reviews || [], ad_rows_after: brief.ad_rows_after || [] },
    });
    state.run = { id: created.run_id, status: 'queued', stage: 'queued', steps: [], metrics: {} };
    renderAll();
    watchRun();
    toast('闭环已启动：诊断 → 回流 → v2 → 复测', 'success');
  });
}

async function approveRun() {
  await withBusy(async () => {
    await api(`/api/runs/${state.run.id}/approve`, { method: 'POST' });
    await loadRun(state.run.id, { quiet: true });
    toast('审批通过，发布包已生成', 'success');
  });
}

function rejectRun() {
  showModal({
    title: '驳回本次产出',
    desc: '驳回会终止本次运行，原因会写入运行记录。',
    input: true,
    confirmText: '确认驳回',
    danger: true,
    onConfirm: async (reason) => {
      await api(`/api/runs/${state.run.id}/reject`, { method: 'POST', body: { reason: reason || '人工驳回' } });
      await loadRun(state.run.id, { quiet: true });
      toast('已驳回', 'success');
    },
  });
}

async function runEval() {
  await withBusy(async () => {
    const result = await api('/api/eval/run?suite=golden', { method: 'POST' });
    state.evalResult = result;
    renderEval();
    toast(`评测完成：${result.passed}/${result.total}`, result.score >= 0.9 ? 'success' : 'error');
  });
}

/* ------------------------------------------------------------------ */
/* 通用渲染块                                                          */
/* ------------------------------------------------------------------ */
function pill(text, cls = 'pill-muted') {
  return `<span class="pill ${cls}">${esc(text)}</span>`;
}

function statusPill(status) {
  const map = {
    queued: 'pill-queue', running: 'pill-running', awaiting_approval: 'pill-approval',
    completed: 'pill-completed', failed: 'pill-failed', cancelled: 'pill-cancelled',
    succeeded: 'pill-success', pending: 'pill-muted',
  };
  const label = {
    queued: '排队中', running: '运行中', awaiting_approval: '待审批', completed: '已完成',
    failed: '失败', cancelled: '已取消', succeeded: '成功', pending: '待运行',
  }[status] || status;
  return `<span class="pill ${map[status] || 'pill-muted'}">${esc(label)}</span>`;
}

function emptyState(title, desc, icon = '○') {
  return `<div class="empty-state"><div class="empty-icon">${icon}</div>
    <div class="empty-title">${esc(title)}</div><div class="empty-desc">${esc(desc)}</div></div>`;
}

function card(title, sub, body, extra = '') {
  return `<div class="card"><div class="card-head"><div><div class="card-title">${esc(title)}</div>
    ${sub ? `<div class="card-sub">${esc(sub)}</div>` : ''}</div>${extra}</div>
    <div class="card-body">${body}</div></div>`;
}

function kpi(label, value, foot = '') {
  return `<div class="kpi-card"><div class="kpi-label">${esc(label)}</div>
    <div class="kpi-value tabular">${value}</div>${foot ? `<div class="kpi-foot">${foot}</div>` : ''}</div>`;
}

function table(headers, rows, cls = 'data-table') {
  if (!rows.length) return '<div class="table-empty">暂无数据</div>';
  return `<div class="table-wrap"><table class="${cls}"><thead><tr>${headers.map((h) => `<th>${esc(h)}</th>`).join('')}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

function tickList(items) {
  if (!items || !items.length) return '<div class="table-empty">暂无自检项</div>';
  return `<div class="check-grid">${items.map((c) => `
    <div class="check-row"><span class="ck ${c.passed ? 'pass' : 'fail'}">${c.passed ? '✓' : '✗'}</span>
    <div><div class="ck-label">${esc(c.name)}</div><div class="ck-detail">${esc(c.detail || '')}</div></div></div>`).join('')}</div>`;
}

/* ------------------------------------------------------------------ */
/* 侧边栏 / 顶栏 / 骨架                                                */
/* ------------------------------------------------------------------ */
function renderSkeleton() {
  $('#tab-panel').innerHTML = '<div class="loading-state"><span class="spinner"></span><div>正在加载控制台…</div></div>';
}

function renderHealth() {
  const h = state.health;
  const box = $('#health-badge');
  box.innerHTML = `<div class="health-row"><span class="dot succeeded"></span><strong>服务正常</strong></div>
    <div class="health-meta">${esc(h.provider)} · ${esc(h.model)} · v${esc(h.version)}</div>`;
}

function renderProjectList() {
  const list = $('#project-list');
  $('#project-count').textContent = String(state.projects.length);
  list.innerHTML = state.projects.map((p) => `
    <button class="project-item ${p.id === state.projectId ? 'active' : ''}" data-project="${esc(p.id)}" type="button">
      <span class="project-mark">${esc((p.name || '?').slice(0, 1))}</span>
      <span class="project-main"><span class="project-name">${esc(p.name)}</span>
      <span class="project-sub">${esc(p.platform)} · ${esc(p.market)} · ${esc(p.category || '未分类')}</span></span>
      <span class="project-chevron">›</span>
    </button>`).join('') || '<div class="empty-desc" style="padding:10px">还没有项目</div>';
  list.querySelectorAll('[data-project]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      if (state.busy) return;
      await selectProject(btn.dataset.project);
    });
  });
}

function renderTopbar() {
  const p = state.project;
  const run = state.run;
  if (!p) { $('#topbar').innerHTML = ''; return; }
  $('#topbar').innerHTML = `
    <div class="tb-left">
      <div class="tb-eyebrow">PROJECT / ${esc(p.id)}</div>
      <div class="tb-title-row"><h1 class="tb-title">${esc(p.name)}</h1>
        ${pill(p.platform, 'pill-platform')} ${pill(p.market, 'pill-outline')}</div>
      <div class="tb-sub">${esc(p.category || '未分类')} · ${esc(p.language)} · ${(p.brief && p.brief.competitor_rows ? p.brief.competitor_rows.length : 0)} 条竞品样本</div>
    </div>
    <div class="tb-right">
      ${run ? `<div class="tb-runbox"><div class="tb-runline">${statusPill(run.status)} ${pill(run.mode, 'pill-mode')}</div>
        <div class="tb-runid mono">${esc(run.id)}</div>
        <div class="tb-sub">阶段 ${esc(run.stage || '—')} ${run.error ? '· <span class="danger">' + esc(run.error) + '</span>' : ''}</div></div>` : ''}
      <div class="tb-actions">
        <button class="btn btn-ghost btn-sm" id="btn-refresh" type="button">刷新</button>
      </div>
    </div>`;
  const refresh = $('#btn-refresh');
  if (refresh) refresh.addEventListener('click', () => (run ? loadRun(run.id) : selectProject(state.projectId)));
}

function renderActionBar() {
  const run = state.run;
  const disabled = state.busy ? 'disabled' : '';
  $('#action-bar').innerHTML = `
    <div class="actionbar">
      <div class="actionbar-left">
        <div class="actionbar-title">流水线控制</div>
        <div class="actionbar-sub">01→02 串行，03/04/05 并行；素材发布前需要人工审批</div>
      </div>
      <div class="actionbar-actions">
        <button class="btn btn-primary" data-action="run-full" ${disabled} type="button">运行完整流水线</button>
        <button class="btn btn-ghost" data-action="run-diagnose" ${disabled} type="button">仅诊断</button>
        <button class="btn btn-dark" data-action="iterate" ${disabled || !run ? 'disabled' : ''} type="button">闭环迭代</button>
        <button class="btn btn-ghost" data-action="eval" ${disabled} type="button">评测</button>
      </div>
    </div>`;
  $('#action-bar').querySelectorAll('[data-action]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const action = btn.dataset.action;
      if (action === 'run-full') startRun('full_pipeline');
      else if (action === 'run-diagnose') startRun('diagnose');
      else if (action === 'iterate') iterateRun();
      else if (action === 'eval') { switchView('eval'); runEval(); }
    });
  });
  renderApproval();
}

function renderApproval() {
  const slot = $('#approval-slot');
  const run = state.run;
  if (!run || run.status !== 'awaiting_approval') { slot.innerHTML = ''; return; }
  slot.innerHTML = `
    <div class="approval-banner">
      <div class="approval-icon">!</div>
      <div class="approval-main">
        <div class="approval-title">创意簇已产出，等待人工审批</div>
        <div class="approval-sub">确认标题、主图规划与视频脚本无品牌/合规风险后，再生成发布包。</div>
      </div>
      <div class="approval-actions">
        <button class="btn btn-success" id="btn-approve" ${state.busy ? 'disabled' : ''} type="button">通过并发布</button>
        <button class="btn btn-danger" id="btn-reject" ${state.busy ? 'disabled' : ''} type="button">驳回</button>
      </div>
    </div>`;
  const approve = $('#btn-approve');
  const reject = $('#btn-reject');
  if (approve) approve.addEventListener('click', approveRun);
  if (reject) reject.addEventListener('click', rejectRun);
}

/* ------------------------------------------------------------------ */
/* 流水线 / tabs                                                       */
/* ------------------------------------------------------------------ */
function renderPipeline() {
  const agents = (state.catalog && state.catalog.agents) || [];
  const steps = (state.run && state.run.steps) || [];
  const byAgent = Object.fromEntries(steps.map((s) => [s.agent_id, s]));
  $('#pipeline').innerHTML = `
    <div class="pipeline-card">
      <div class="pipeline-head">
        <div class="pipeline-head-left">
          <div class="pipeline-title">Agent 流水线</div>
          <div class="pipeline-sub">点击卡片查看对应产物</div>
        </div>
        <div class="pipeline-head-right">
          ${state.run ? `<span class="metric-chip">checks ${state.run.metrics && state.run.metrics.checks_passed != null ? state.run.metrics.checks_passed : '—'}/${state.run.metrics && state.run.metrics.checks_total != null ? state.run.metrics.checks_total : '—'}</span>` : ''}
          ${state.run && !TERMINAL.includes(state.run.status) ? '<span class="live-tag"><span class="live-indicator"></span>LIVE</span>' : ''}
        </div>
      </div>
      <div class="pipeline-grid">
        ${agents.map((a) => {
          const step = byAgent[a.id];
          const hasArtifact = Boolean(state.artifacts[AGENT_KIND[a.id]]);
          const status = step ? step.status : (hasArtifact ? 'succeeded' : 'pending');
          const tab = AGENT_TAB[a.id];
          const checks = step ? `${step.checks_passed}/${step.checks_total}` : '';
          const meta = step
            ? (status === 'running' ? '执行中…' : checks ? `checks ${checks}` : status)
            : (hasArtifact ? '已有产物' : '待运行');
          return `<button class="agent-chip ${status} ${tab === state.tab ? 'active' : ''}" data-tab="${tab}" type="button">
            <div class="agent-top"><span class="agent-num">${esc(a.id.slice(0, 2))}</span>
              <span class="dot ${status}"></span></div>
            <div class="agent-name">${esc(a.cn_name)}</div>
            <div class="agent-meta">${meta}</div>
          </button>`;
        }).join('')}
      </div>
    </div>`;
  $('#pipeline').querySelectorAll('[data-tab]').forEach((el) => {
    el.addEventListener('click', () => { state.tab = el.dataset.tab; renderTabs(); renderTabContent(); });
  });
}

function renderTabs() {
  const hasArtifact = (kind) => Boolean(state.artifacts[kind]);
  $('#tabs').innerHTML = `<div class="tabs-wrap"><div class="tabs">${TABS.map((t) => `
    <button class="tab ${t.id === state.tab ? 'active' : ''} ${t.kind && !hasArtifact(t.kind) ? 'pending' : ''}"
      data-tab="${t.id}" type="button">
      <span class="tab-no">${t.no}</span><span class="tab-title">${t.title}</span>
      <span class="tab-dot ${t.kind && hasArtifact(t.kind) ? 'succeeded' : 'pending'}"></span>
    </button>`).join('')}</div></div>`;
  $('#tabs').querySelectorAll('[data-tab]').forEach((el) => {
    el.addEventListener('click', () => { state.tab = el.dataset.tab; renderTabs(); renderTabContent(); });
  });
}

function renderAll() {
  renderTopbar();
  renderActionBar();
  renderPipeline();
  renderTabs();
  renderTabContent();
  renderRail();
  if (state.view === 'eval') renderEval();
  if (state.view === 'metrics') renderMetrics();
}

/* ------------------------------------------------------------------ */
/* 各 tab 渲染                                                         */
/* ------------------------------------------------------------------ */
function renderTabContent() {
  const tab = TABS.find((t) => t.id === state.tab) || TABS[0];
  const panel = $('#tab-panel');
  if (tab.id === 'trace') { panel.innerHTML = renderTraceTab(); return; }
  const content = state.artifacts[tab.kind];
  if (!content) {
    panel.innerHTML = emptyState(`还没有 ${tab.title} 产物`, '运行流水线后，这里会显示该 Agent 的结构化产出。', tab.no);
    return;
  }
  const renderers = {
    market: renderMarket, strategy: renderStrategy, listing: renderListing,
    image: renderImage, video: renderVideo, ads: renderAds,
    review: renderReview, publish: renderPublish,
  };
  panel.innerHTML = renderers[tab.id](content) || emptyState('暂无内容', '产物为空', tab.no);
}

function tabHead(no, title, desc, side = '') {
  return `<div class="tab-head"><div class="tab-head-main"><span class="tab-no">${no}</span>
    <div><div class="tab-title">${esc(title)}</div><div class="tab-desc">${esc(desc)}</div></div></div>
    <div class="tab-head-side">${side}</div></div>`;
}

function renderMarket(c) {
  const verdictMap = { GO: ['v-go', '可以进入'], CAUTION: ['v-caution', '谨慎进入'], NO_GO: ['v-nogo', '不建议进入'] };
  const [vClass, vLabel] = verdictMap[c.precheck.verdict] || ['v-caution', c.precheck.verdict];
  const band = c.price_band || {};
  const buckets = (c.price_buckets && c.price_buckets.length ? c.price_buckets : band.buckets) || [];
  const maxSales = Math.max(1, ...buckets.map((b) => b.sales || 0));
  const mid = ((band.recommended_low || 0) + (band.recommended_high || 0)) / 2;

  const bucketBars = buckets.map((b) => {
    const isRec = mid >= b.low && mid < b.high;
    const width = Math.max(3, Math.round(((b.sales || 0) / maxSales) * 100));
    const barClass = isRec ? 'b-bar-rec' : (b.low < mid ? 'b-bar-low' : 'b-bar-high');
    return `<div class="bucket-row ${isRec ? 'is-rec' : ''}">
      <div class="bucket-label">R$ ${fmt.num(b.low, 0)}–${fmt.num(b.high, 0)}</div>
      <div class="bucket-track"><div class="bucket-bar ${barClass}" style="width:${width}%"></div></div>
      <div class="bucket-range">${fmt.num(b.sku)} SKU · ${fmt.num(b.sales)} 件</div></div>`;
  }).join('');

  return `${tabHead('01', '市场分析', '集中度、价格带波谷与配置机会', pill(c.precheck.verdict + ' · ' + c.precheck.score + ' 分', c.precheck.verdict === 'GO' ? 'pill-success' : c.precheck.verdict === 'NO_GO' ? 'pill-danger' : 'pill-warning'))}
    <div class="verdict-hero">
      <div class="verdict-badge ${vClass}">${esc(c.precheck.verdict)}</div>
      <div class="verdict-copy"><div class="verdict-copy-title">${esc(vLabel)}</div>
        <div class="verdict-copy-sub">综合分 ${c.precheck.score}/100 · 置信度 ${fmt.pct(c.confidence, 0)}</div></div>
      <div class="verdict-side"><div class="verdict-score tabular">${c.precheck.score}</div><div class="muted small">/ 100</div></div>
    </div>
    <div class="grid-4 mt-14">
      ${kpi('CR4 集中度', fmt.pct(c.metrics.cr4, 1), 'Top4 销量份额')}
      ${kpi('HHI', fmt.num(c.metrics.hhi, 0), '>2500 为高集中')}
      ${kpi('加权客单价', fmt.money(c.metrics.avg_price, 0), '月 GMV ' + fmt.money(c.metrics.gmv_estimate, 0))}
      ${kpi('需求指数', fmt.num(c.metrics.demand_index, 2), fmt.compact(c.metrics.competitor_count) + ' 个竞品')}
    </div>
    <div class="mt-16">${card('价格带分布', '柱长为该带月销量；高亮为推荐进入区间', `
      <div class="bucket-rows">${bucketBars}</div>
      <div class="price-rationale">${esc(band.rationale || '')}</div>
      <div class="price-axis-note">推荐区间 R$ ${fmt.num(band.recommended_low, 0)} – ${fmt.num(band.recommended_high, 0)} · 中位价 R$ ${fmt.num(band.median, 0)} · 波谷效率 ${fmt.num(band.valley_efficiency, 2)}</div>`)}</div>
    <div class="split-2 mt-14">
      ${card('机会点', '', `<div class="opp-list">${(c.opportunity_points || []).map((o, i) => `<div class="opp-item"><span class="opp-no">${i + 1}</span><span>${esc(o)}</span></div>`).join('')}</div>`)}
      ${card('风险', '会让结论失效的条件', `<div class="risk-list">${(c.risks || []).map((r) => `<div class="risk-item">${esc(r)}</div>`).join('')}</div>`)}
    </div>
    <div class="mt-14">${card('配置组合交叉表', '按销量份额排序', table(
      ['配置组合', '销量份额', '均价', '月销'],
      (c.config_matrix || []).map((row) => [esc(row.combo), fmt.pct(row.share, 1), fmt.money(row.avg_price, 0), fmt.num(row.sales)]),
    ))}</div>
    <div class="mt-14">${card('推荐配置', '', `<div class="callout"><div class="callout-label">RECOMMENDED</div><div class="callout-title">${esc(c.recommended_config)}</div></div>`)}</div>`;
}

function renderStrategy(c) {
  const p = c.pricing || {};
  return `${tabHead('02', '产品策略', '定位、唯一卖点、定价模型与 SKU 四角色', pill('净利率 ' + fmt.pct(p.net_margin_at_target, 0), 'pill-success'))}
    <div class="usp-card"><div class="usp-label">UNIQUE SELLING POINT</div>
      <div class="usp-text">${esc(c.main_selling_point)}</div>
      <div class="usp-foot">${esc(c.positioning || '')}</div></div>
    <div class="grid-4 mt-14">
      ${kpi('盈亏平衡价', fmt.money(p.breakeven_price), '低于此价即亏损')}
      ${kpi('目标价', fmt.money(p.target_price), '按目标净利反解')}
      ${kpi('券/折余量', fmt.money(p.coupon_headroom), '还能让 ' + fmt.pct(p.allowed_discount_rate, 1))}
      ${kpi('净利率', fmt.pct(p.net_margin_at_target, 1), '定价模型 ' + esc(p.formula_version || ''))}
    </div>
    <div class="mt-16">${card('SKU 四角色', '价格阶梯与定位分工', table(
      ['角色', 'SKU', '价格', '定价理由'],
      (c.sku_roles || []).map((s) => [`<span class="tag tag-role">${esc(s.role)}</span>`, esc(s.name), `<strong>${fmt.money(s.price)}</strong>`, esc(s.rationale)]),
    ))}</div>
    <div class="split-2 mt-14">
      ${card('配置阶梯', '价差需在详情页解释', `<div class="info-list">${(c.config_ladder || []).map((x) => `<div class="info-row"><span class="info-label">${esc(x.name)}</span><span class="mono">${esc(x.config || '')} · ${fmt.money(x.price)}</span></div>`).join('')}</div>`)}
      ${card('风险', '', `<div class="risk-list">${(c.risks || []).map((r) => `<div class="risk-item">${esc(r)}</div>`).join('')}</div>`)}
    </div>`;
}

function renderListing(c) {
  const t = c.title || {};
  const pctUsed = Math.min(100, Math.round(((t.chars || 0) / (t.limit || 1)) * 100));
  const km = c.keyword_map || {};
  const kw = (title, cls, list) => `<div class="keyword-col"><div class="keyword-col-head"><span class="tag ${cls}">${esc(title)}</span><span class="cnt">${(list || []).length}</span></div>
    <div class="keyword-col-body">${(list || []).map((k) => `<span class="spec-chip">${esc(k)}</span>`).join('') || '<span class="muted">—</span>'}</div></div>`;

  return `${tabHead('03', 'Listing 创意', '标题 / 五点卖点 / 详情页 / 参数溯源', pill(c.compliance && c.compliance.passed ? '合规通过' : '未通过', c.compliance && c.compliance.passed ? 'pill-success' : 'pill-danger'))}
    ${c.revision_note ? `<div class="revision-banner">${esc(c.revision_note)}</div>` : ''}
    <div class="title-box">
      <div class="title-meta"><span>标题</span><span class="tabular ${t.chars > t.limit ? 'danger' : ''}">${t.chars}/${t.limit} 字符</span></div>
      <div class="title-text">${esc(t.text)}</div>
      <div class="char-track"><div class="char-fill ${pctUsed > 100 ? 'over' : ''}" style="width:${Math.min(100, pctUsed)}%"></div></div>
      <div class="title-meta"><span>前 30 字符（权重最高）</span><span class="mono">${esc(t.head30 || '')}</span></div>
    </div>
    <div class="keyword-cols mt-14">${kw('核心词', 'tag-primary', km.primary)}${kw('长尾词', 'tag-long', km.long_tail)}${kw('否词', 'tag-negative', km.negative)}</div>
    <div class="split-2 mt-14">
      ${card('五点卖点', '参数 + 好处，不堆形容词', `<div class="bullet-list">${(c.bullets || []).map((b, i) => `<div class="bullet-item"><span class="bullet-no">${i + 1}</span><span>${esc(b)}</span></div>`).join('')}</div>`)}
      ${card('参数表', '每条标注来源', table(['参数', '值', '来源'], (c.specs || []).map((s) => [esc(s.name), esc(s.value), `<span class="tag ${s.source === 'fact' ? 'tag-fact' : 'tag-derived'}">${esc(s.source)}</span>`])) )}
    </div>
    <div class="mt-14">${card('详情页结构', '首屏解决决策问题', `
      <div class="desc-section"><div class="section-title">导语</div><div>${esc((c.description || {}).intro || '')}</div></div>
      ${((c.description || {}).sections || []).map((s) => `<div class="desc-section"><div class="section-title">${esc(s.title)}</div><div>${esc(s.body)}</div></div>`).join('')}
      <div class="desc-section"><div class="section-title">包装清单</div><div>${((c.description || {}).package_list || []).map((x) => `<span class="spec-chip">${esc(x)}</span>`).join('')}</div></div>`)}
    </div>
    <div class="split-2 mt-14">
      ${card('合规检查', '', `<div class="compliance-box ${c.compliance && c.compliance.passed ? 'ok' : 'block'}">
        <div class="compliance-head">${c.compliance && c.compliance.passed ? '✓ 合规通过' : '✗ 存在阻塞项'}</div>
        <div>${(c.compliance && c.compliance.blocked_terms || []).length ? esc(c.compliance.blocked_terms.join(', ')) : '未命中禁用词'}</div>
        <div class="muted small">${((c.compliance && c.compliance.notes) || []).map(esc).join(' · ')}</div></div>`)}
      ${card('自检清单', '', tickList(c.self_check))}
    </div>`;
}

function renderImage(c) {
  return `${tabHead('04', '主图与详情图', '9 图分工与出图 Prompt', pill('9 图', 'pill-success'))}
    <div class="spec-chips">${Object.entries(c.cover_spec || {}).map(([k, v]) => `<span class="spec-chip"><b>${esc(k)}</b> ${esc(v)}</span>`).join('')}</div>
    <div class="image-grid mt-14">${(c.nine_grid || []).map((g) => `
      <div class="image-card ${g.slot === 1 ? 'is-rec' : ''}">
        <div class="image-card-head"><span class="slot-no">${String(g.slot).padStart(2, '0')}</span>
          <span class="image-purpose">${esc(g.purpose)}</span></div>
        <div class="image-scene">${esc(g.scene)}</div>
        ${g.overlay_text ? `<span class="overlay-chip">文字：${esc(g.overlay_text)}</span>` : '<span class="overlay-chip muted">无叠加文字</span>'}
        <details class="prompt-details"><summary>出图 Prompt</summary><div class="prompt-text">${esc(g.prompt)}</div></details>
        ${g.risk ? `<div class="image-risk">${esc(g.risk)}</div>` : ''}
      </div>`).join('')}</div>
    <div class="mt-14">${card('自检清单', '', tickList(c.self_check))}</div>`;
}

function renderVideo(c) {
  const total = (c.timing_check || {}).total_duration || 1;
  return `${tabHead('05', '短视频', '钩子 / 分镜 / 口播 / 字幕时长', pill((c.timing_check || {}).within_target ? '时长达标' : '时长异常', (c.timing_check || {}).within_target ? 'pill-success' : 'pill-warning'))}
    <div class="hook-card"><div class="hook-label">3 秒钩子</div><div class="hook-text">${esc(c.hook)}</div></div>
    <div class="video-timeline mt-14">${(c.shots || []).map((s) => `<div class="tl-seg" style="flex:${Math.max(1, s.t_end - s.t_start)}"><span>${fmt.num(s.t_start, 1)}s</span></div>`).join('')}</div>
    <div class="shot-list mt-14">${(c.shots || []).map((s, i) => `
      <div class="shot-row">
        <div class="shot-main">
          <div class="shot-top"><span class="shot-no">SHOT ${i + 1}</span><span class="shot-time">${fmt.num(s.t_start, 1)}–${fmt.num(s.t_end, 1)}s</span>
            <span class="shot-dur">${fmt.num(s.t_end - s.t_start, 1)}s</span></div>
          <div class="shot-field"><span class="shot-label">画面</span>${esc(s.visual)}</div>
          <div class="shot-field"><span class="shot-label">口播</span>${esc(s.voiceover)}</div>
          <div class="shot-caption">字幕：${esc(s.caption)}</div>
        </div>
      </div>`).join('')}</div>
    <div class="timing-chips mt-14">
      <span class="timing-chip">总时长 ${fmt.num(total, 1)}s / 目标 ${fmt.num((c.timing_check || {}).target_duration, 0)}s</span>
      <span class="timing-chip">语速 ${fmt.num((c.timing_check || {}).avg_cps, 1)} 字符/秒</span>
      <span class="timing-chip">${(c.timing_check || {}).hook_ok ? '钩子在前 3 秒 ✓' : '钩子位置异常 ✗'}</span>
    </div>
    <div class="mt-14">${card('口播全文', '', `<div>${esc(c.narration)}</div>`)}</div>`;
}

function renderAds(c) {
  const f = c.funnel || {};
  const be = c.breakeven || {};
  const losing = (f.roas || 0) < (be.roas_min || 0);
  const retest = (state.run && state.run.metrics && state.run.metrics.retest)
    || ((state.project && state.project.runs || []).map((r) => r.metrics && r.metrics.retest).find(Boolean));
  const diag = (c.diagnosis || []).map((d) => `<div class="diag-card"><div class="diag-layer">${esc(d.layer)}</div>
    <div class="diag-symptom">${esc(d.symptom)}</div><div class="diag-root">${esc(d.root_cause)}</div>
    <div class="diag-evidence">${(d.evidence || []).map((e) => `<span class="spec-chip">${esc(e)}</span>`).join('')}</div></div>`).join('');

  return `${tabHead('06', '广告优化', '漏斗诊断 / 盈亏线 / P0-P1-P2 动作', pill(losing ? '低于盈亏线' : '盈利中', losing ? 'pill-danger' : 'pill-success'))}
    ${retest ? `<div class="retest-banner"><div class="retest-head"><span class="retest-title">闭环复测</span>
      <span class="pill ${retest.verdict === '达标' ? 'pill-success' : 'pill-warning'}">${esc(retest.verdict)}</span></div>
      ${table(['指标', '改前', '改后', '变化'], [
        ['ROAS', fmt.num(retest.before.roas, 2), fmt.num(retest.after.roas, 2), `<span class="${retest.roas_delta >= 0 ? 'delta-up' : 'delta-down'}">${retest.roas_delta >= 0 ? '+' : ''}${fmt.num(retest.roas_delta, 2)}</span>`],
        ['CPA', fmt.money(retest.before.cpa), fmt.money(retest.after.cpa), `<span class="${retest.cpa_delta <= 0 ? 'delta-up' : 'delta-down'}">${retest.cpa_delta <= 0 ? '−' : '+'}R$ ${fmt.num(Math.abs(retest.cpa_delta), 2)}</span>`],
        ['CVR', fmt.pct(retest.before.cvr), fmt.pct(retest.after.cvr), ''],
        ['CTR', fmt.pct(retest.before.ctr), fmt.pct(retest.after.ctr), ''],
      ], 'retest-table')}</div>` : ''}
    <div class="grid-4 mt-14">
      ${kpi('CTR', fmt.pct(f.ctr), fmt.compact(f.clicks) + ' / ' + fmt.compact(f.impressions))}
      ${kpi('CVR', fmt.pct(f.cvr), fmt.num(f.orders) + ' 单')}
      ${kpi('CPA', fmt.money(f.cpa), '上限 ' + fmt.money(be.cpa_max))}
      ${kpi('ROAS', fmt.num(f.roas, 2), '下限 ' + fmt.num(be.roas_min, 2))}
    </div>
    <div class="split-wide mt-14">
      ${card('漏斗与盈亏线', '', table(['层级', '数值', '口径'], [
        ['曝光', fmt.num(f.impressions), '—'],
        ['点击', fmt.num(f.clicks), 'CTR ' + fmt.pct(f.ctr)],
        ['订单', fmt.num(f.orders), 'CVR ' + fmt.pct(f.cvr)],
        ['花费', fmt.money(f.spend), 'CPC ' + fmt.money(f.cpc)],
        ['营收', fmt.money(f.revenue), 'AOV ' + fmt.money(f.aov)],
        ['贡献毛利/单', fmt.money(be.contribution_margin), '可承受 CPA ' + fmt.money(be.cpa_max)],
      ], 'funnel-table'))}
      ${card('诊断', '', diag || '<div class="table-empty">未触发阈值</div>')}
    </div>
    <div class="mt-14">${card('动作清单', 'P0 需 24h 内完成', table(
      ['优先级', '动作', '预期效果', '负责人', 'SLA'],
      (c.actions || []).map((a) => [`<span class="tag tag-${a.priority.toLowerCase()}">${esc(a.priority)}</span>`, esc(a.action), esc(a.expected_impact), esc(a.owner), a.sla_hours + 'h']),
      'action-table',
    ))}</div>`;
}

function renderReview(c) {
  const a = c.attribution || {};
  const counts = a.counts || {};
  const max = Math.max(1, ...Object.values(counts));
  const rows = Object.entries(counts).sort((x, y) => y[1] - x[1]).map(([k, v]) => `
    <div class="attr-row"><div class="attr-label">${esc(k)}</div>
      <div class="attr-track"><div class="attr-fill" style="width:${Math.round((v / max) * 100)}%"></div></div>
      <div class="attr-count">${fmt.num(v)}</div></div>`).join('');

  return `${tabHead('07', '评论分析', '八类归因 / 可修复率 / 页面修补', pill('样本 ' + fmt.num(a.total), 'pill-outline'))}
    <div class="grid-4 mt-14">
      ${kpi('差评率', fmt.pct(a.negative_rate, 1), '评分 ≤2 星占比')}
      ${kpi('可修复率', fmt.pct(a.fixable_rate, 1), '可由页面/包装修补')}
      ${kpi('误解率', fmt.pct(a.misunderstanding_rate, 1), '描述不符类')}
      ${kpi('样本量', fmt.num(a.total), '条评论')}
    </div>
    <div class="split-2 mt-14">
      ${card('归因分布', '多标签计数', `<div class="attr-chart">${rows}</div>`)}
      ${card('口碑与差评', '', `<div class="praise-complaint">
        <div class="praise"><div class="block-title">好评集中点</div>${(c.top_praise || []).map((p) => `<span class="spec-chip">${esc(p)}</span>`).join('')}</div>
        <div class="complaint"><div class="block-title">差评集中点</div>${(c.top_complaint || []).map((p) => `<span class="spec-chip">${esc(p)}</span>`).join('')}</div>
      </div>`)}
    </div>
    <div class="mt-14">${card('详情页修补建议', '', table(['目标', '改动', '理由', '证据数'], (c.listing_patches || []).map((p) => [`<span class="tag tag-layer">${esc(p.target)}</span>`, esc(p.change), esc(p.reason), fmt.num(p.evidence_count)])))}</div>
    <div class="mt-14">${card('图片修补建议', '', table(['目标', '改动', '理由', '证据数'], (c.image_patches || []).map((p) => [`<span class="tag tag-layer">${esc(p.target)}</span>`, esc(p.change), esc(p.reason), fmt.num(p.evidence_count)])))}</div>`;
}

function renderPublish(c) {
  const checklist = c.checklist || [];
  return `${tabHead('08', '发布包', '审批通过后生成，可交付上架', pill('APPROVED', 'pill-success'), )}
    <div class="publish-summary">
      <div class="pub-item"><div class="pub-label">Listing 标题</div><div class="pub-value">${esc(((c.listing || {}).title || {}).text || '')}</div></div>
      <div class="pub-item"><div class="pub-label">主卖点</div><div class="pub-value">${esc(((c.strategy || {}).main_selling_point) || '')}</div></div>
      <div class="pub-item"><div class="pub-label">目标价</div><div class="pub-value">${fmt.money((((c.strategy || {}).pricing) || {}).target_price)}</div></div>
      <div class="pub-item"><div class="pub-label">图片 / 视频</div><div class="pub-value">${((c.images || {}).grid || []).length} 张图 · ${((c.video || {}).shots || []).length} 个分镜</div></div>
    </div>
    <div class="mt-14">${card('发布自检', '全部通过才允许上架', `<div class="tick-list">${checklist.map((i) => `
      <div class="tick-item"><span class="tick-icon ${i.passed ? 'pass' : 'fail'}">${i.passed ? '✓' : '✗'}</span>
      <div><div>${esc(i.item)}</div><div class="tick-detail">${esc(i.detail || '')}</div></div></div>`).join('')}</div>`)}
    </div>
    <div class="mt-14">${card('交付物 JSON', '可直接对接上架脚本', `<button class="btn btn-ghost btn-sm" id="btn-json" type="button">查看 / 下载 JSON</button>`)}</div>`;
}

function renderTraceTab() {
  const spans = (state.trace && state.trace.spans) || [];
  const summary = (state.trace && state.trace.summary) || {};
  const feedback = (state.feedback && state.feedback.items) || [];
  const kindPill = { llm: 'pill-info', tool: 'pill-outline', guardrail: 'pill-warning' };
  return `${tabHead('09', 'Trace', 'LLM / 工具 / 护栏统一时间线', pill('span ' + fmt.num(summary.count), 'pill-outline'))}
    <div class="trace-summary">
      <span class="trace-chip">tokens ${fmt.num(summary.tokens)}</span>
      <span class="trace-chip">成本 $${fmt.num(summary.cost_usd, 4)}</span>
      <span class="trace-chip">耗时 ${fmt.ms(summary.latency_ms)}</span>
      <span class="trace-chip">错误 ${fmt.num(summary.errors)}</span>
    </div>
    <div class="trace-table-wrap mt-14">${table(['Span', '类型', '状态', '延迟', 'Tokens', '成本'], spans.map((s) => [
      `<span class="span-name mono">${esc(s.name)}</span>`, `<span class="pill ${kindPill[s.kind] || 'pill-muted'}">${esc(s.kind)}</span>`,
      `<span class="${s.status === 'ok' ? 'success' : s.status === 'retry' ? 'warn' : 'danger'}">${esc(s.status)}</span>`,
      fmt.ms(s.latency_ms), fmt.num(s.tokens), '$' + fmt.num(s.cost_usd, 5),
    ]), 'trace-table')}</div>
    <div class="mt-14">${card('回流事件', '诊断 → 创意 Agent 的可追溯链路', feedback.length ? `<div class="feedback-list">${feedback.map((f) => `
      <div class="feedback-item"><span class="feedback-sev tag tag-${(f.severity || 'p1').toLowerCase()}">${esc(f.severity)}</span>
        <div class="feedback-main"><div class="feedback-route">${esc(f.source_agent)} → ${esc(f.target_agent)}</div>
        <div class="feedback-issue">${esc(f.issue)}</div><div class="feedback-action">${esc(f.action)}</div></div></div>`).join('')}</div>` : '<div class="table-empty">本次运行没有回流事件</div>')}</div>`;
}

/* ------------------------------------------------------------------ */
/* 右栏                                                                */
/* ------------------------------------------------------------------ */
function renderRail() {
  const run = state.run;
  const m = (run && run.metrics) || {};
  const rate = m.checks_total ? m.checks_passed / m.checks_total : null;
  const guard = m.guardrails || { block: 0, repair: 0, warn: 0 };
  $('#rail').innerHTML = `
    <div class="rail-card">
      <div class="rail-head"><span class="rail-title">运行指标</span>${run ? statusPill(run.status) : ''}</div>
      <div class="rail-body">
        <div class="metric-chips">
          <span class="metric-chip">tokens <b>${fmt.num(m.tokens)}</b></span>
          <span class="metric-chip">成本 <b>$${fmt.num(m.cost_usd, 4)}</b></span>
          <span class="metric-chip">wall <b>${fmt.ms(m.wall_ms)}</b></span>
        </div>
        <div class="checks-panel mt-12">
          <div class="check-progress-head"><span>自检通过率</span><span class="tabular ${etaClass(rate)}">${rate == null ? '—' : fmt.pct(rate, 0)}</span></div>
          <div class="checks-track"><div class="checks-fill" style="width:${rate == null ? 0 : Math.round(rate * 100)}%"></div></div>
          <div class="metrics-sub">${m.checks_passed == null ? '尚无运行' : m.checks_passed + ' / ' + m.checks_total + ' 项通过'}</div>
        </div>
        <div class="guardrail-chips mt-12">
          <span class="guardrail-chip block">block ${guard.block || 0}</span>
          <span class="guardrail-chip repair">repair ${guard.repair || 0}</span>
          <span class="guardrail-chip warn">warn ${guard.warn || 0}</span>
        </div>
      </div>
    </div>
    <div class="rail-card mt-12">
      <div class="rail-head"><span class="rail-title">步骤</span></div>
      <div class="rail-body step-list">
        ${((run && run.steps) || []).length ? run.steps.map((s) => `
          <div class="step-row"><span class="step-seq">${String(s.seq).padStart(2, '0')}</span>
            <div class="step-main"><div class="step-name">${esc(s.agent_id)}</div>
            <div class="step-meta">${s.checks_passed}/${s.checks_total} checks · ${fmt.ms(s.latency_ms)} · ${fmt.num(s.tokens_in + s.tokens_out)} tok</div>
            <div class="step-checks"><div class="mini-bar-track"><div class="mini-bar-fill" style="width:${s.checks_total ? Math.round((s.checks_passed / s.checks_total) * 100) : 0}%"></div></div></div></div>
            <span class="dot ${s.status}"></span></div>`).join('') : '<div class="table-empty">还没有运行记录</div>'}
      </div>
    </div>
    <div class="rail-card mt-12">
      <div class="rail-head"><span class="rail-title">运行历史</span></div>
      <div class="rail-body step-list">
        ${((state.project && state.project.runs) || []).slice(0, 6).map((r) => `
          <button class="step-row" data-run="${esc(r.id)}" type="button">
            <span class="step-main"><span class="step-name mono">${esc(r.id.slice(0, 14))}</span>
            <span class="step-meta">${esc(r.mode)} · ${fmt.time(r.created_at)}</span></span>
            ${statusPill(r.status)}</button>`).join('') || '<div class="table-empty">暂无历史</div>'}
      </div>
    </div>`;
  $('#rail').querySelectorAll('[data-run]').forEach((btn) => {
    btn.addEventListener('click', () => loadRun(btn.dataset.run));
  });
}

/* ------------------------------------------------------------------ */
/* 视图切换 / 评测 / 指标                                              */
/* ------------------------------------------------------------------ */
function switchView(view) {
  state.view = view;
  document.querySelectorAll('.nav-item').forEach((el) => el.classList.toggle('active', el.dataset.view === view));
  $('#view-workbench').classList.toggle('hidden', view !== 'workbench');
  $('#view-eval').classList.toggle('hidden', view !== 'eval');
  $('#view-metrics').classList.toggle('hidden', view !== 'metrics');
  if (view === 'eval') renderEval();
  if (view === 'metrics') renderMetrics();
}

function renderEval() {
  const res = state.evalResult;
  const container = $('#view-eval');
  if (!res) {
    container.innerHTML = `<div class="view-head"><div><div class="view-title">评测门禁</div>
      <div class="view-desc">10 条 golden case，含 2 条负向用例：数据不足时必须失败。</div></div>
      <div class="view-head-actions"><button class="btn btn-primary" id="btn-eval-run" type="button">运行评测</button></div></div>
      ${emptyState('还没有评测结果', '点击「运行评测」执行 golden 套件。', 'EVAL')}`;
    const btn = $('#btn-eval-run');
    if (btn) btn.addEventListener('click', runEval);
    return;
  }
  container.innerHTML = `<div class="view-head"><div><div class="view-title">评测门禁</div>
    <div class="view-desc">改 Prompt / 换模型前先跑这套用例，通过率下降不允许上线。</div></div>
    <div class="view-head-actions"><button class="btn btn-primary" id="btn-eval-run" ${state.busy ? 'disabled' : ''} type="button">重新评测</button></div></div>
    <div class="eval-layout">
      <div class="eval-hero">
        <div class="ring" style="--score:${Math.round(res.score * 100)}"><div class="ring-inner"><strong>${Math.round(res.score * 100)}</strong><span>score</span></div></div>
        <div class="eval-hero-copy"><div class="eval-hero-title">${res.passed}/${res.total} 通过</div>
          <div class="eval-hero-sub">耗时 ${fmt.ms(res.duration_ms)} · 套件 ${esc(res.suite || 'golden')} · ${esc(res.id)}</div></div>
      </div>
      <div class="eval-panel">
        ${table(['用例', 'Agent', '结果', '说明'], (res.results || []).map((r) => [
          `<span class="mono">${esc(r.case)}</span>`, esc(r.agent),
          `<span class="case-status ${r.passed ? 'pass' : 'fail'}">${r.passed ? 'pass' : 'fail'}</span>`,
          `<span class="case-detail">${esc(r.detail)}</span>`,
        ]), 'case-table')}
      </div>
    </div>`;
  const btn = $('#btn-eval-run');
  if (btn) btn.addEventListener('click', runEval);
}

async function renderMetrics() {
  const container = $('#view-metrics');
  container.innerHTML = `<div class="view-head"><div><div class="view-title">平台指标</div>
    <div class="view-desc">跨运行聚合：成功率、成本、自检通过率、护栏分布。</div></div></div>
    <div class="loading-state"><span class="spinner"></span><div>加载指标…</div></div>`;
  let data;
  try { data = await api('/api/metrics/overview'); } catch (err) {
    container.innerHTML = emptyState('指标加载失败', err.message, '!');
    return;
  }
  const perAgent = Object.entries(data.per_agent || {});
  container.innerHTML = `<div class="view-head"><div><div class="view-title">平台指标</div>
    <div class="view-desc">跨运行聚合：成功率、成本、自检通过率、护栏分布。</div></div>
    <div class="view-head-actions"><button class="btn btn-ghost" id="btn-metrics-refresh" type="button">刷新</button></div></div>
    <div class="metrics-cards">
      ${kpi('运行总数', fmt.num(data.runs.total), '成功 ' + fmt.num(data.runs.completed) + ' · 失败 ' + fmt.num(data.runs.failed))}
      ${kpi('成功率', fmt.pct(data.runs.success_rate, 1), '按已结束运行统计')}
      ${kpi('累计 Token', fmt.compact(data.tokens), fmt.num(data.steps) + ' 个步骤')}
      ${kpi('累计成本', '$' + fmt.num(data.cost_usd, 4), '按配置单价估算')}
      ${kpi('自检通过', fmt.pct(data.checks.total ? data.checks.passed / data.checks.total : null, 1), fmt.num(data.checks.passed) + '/' + fmt.num(data.checks.total))}
      ${kpi('护栏', fmt.num((data.guardrails.block || 0) + (data.guardrails.repair || 0) + (data.guardrails.warn || 0)), 'block ' + fmt.num(data.guardrails.block) + ' · repair ' + fmt.num(data.guardrails.repair) + ' · warn ' + fmt.num(data.guardrails.warn))}
    </div>
    <div class="mt-16">${card('按 Agent 聚合', '', table(
      ['Agent', '运行次数', 'Token', '成本', '平均延迟', '自检通过率'],
      perAgent.map(([agent, v]) => [esc(agent), fmt.num(v.runs), fmt.num(v.tokens), '$' + fmt.num(v.cost_usd, 5), fmt.ms(v.latency_ms), v.check_pass_rate == null ? '—' : fmt.pct(v.check_pass_rate, 1)]),
    ))}</div>`;
  const btn = $('#btn-metrics-refresh');
  if (btn) btn.addEventListener('click', renderMetrics);
}

/* ------------------------------------------------------------------ */
/* 弹窗 / 错误                                                         */
/* ------------------------------------------------------------------ */
function showModal({ title, desc, input, confirmText, danger, onConfirm }) {
  const root = $('#modal-root');
  root.innerHTML = `<div class="modal-backdrop"><div class="modal" role="dialog" aria-modal="true">
    <div class="modal-head"><div class="modal-title">${esc(title)}</div><button class="modal-close" type="button">×</button></div>
    <div class="modal-body">${desc ? `<div class="modal-desc">${esc(desc)}</div>` : ''}
      ${input ? '<label class="modal-label">原因<input class="modal-input" id="modal-input" type="text" placeholder="例如：主图卖点与标题不一致"></label>' : ''}</div>
    <div class="modal-foot"><button class="btn btn-ghost" data-modal-cancel type="button">取消</button>
      <button class="btn ${danger ? 'btn-danger' : 'btn-primary'}" data-modal-ok type="button">${esc(confirmText || '确认')}</button></div>
  </div></div>`;
  const close = () => { root.innerHTML = ''; };
  root.querySelector('.modal-close').addEventListener('click', close);
  root.querySelector('[data-modal-cancel]').addEventListener('click', close);
  root.querySelector('[data-modal-ok]').addEventListener('click', async () => {
    const value = input ? (root.querySelector('#modal-input').value || '') : '';
    close();
    try { await onConfirm(value); } catch (err) { toast(err.message, 'error'); }
  });
}

function renderEmptyWorkbench() {
  $('#tab-panel').innerHTML = emptyState('还没有项目', '先用 POST /api/projects 创建一个项目，或运行 python scripts/demo.py。', 'CB');
}

function renderError(err) {
  $('#tab-panel').innerHTML = `<div class="error-state"><div class="error-icon">!</div>
    <div class="empty-title">控制台加载失败</div>
    <div class="empty-desc">${esc(err.message)}</div>
    <div class="empty-actions"><button class="btn btn-primary" id="btn-retry" type="button">重试</button></div></div>`;
  const retry = $('#btn-retry');
  if (retry) retry.addEventListener('click', boot);
}

/* ------------------------------------------------------------------ */
/* 事件绑定 / 启动                                                     */
/* ------------------------------------------------------------------ */
document.querySelectorAll('.nav-item').forEach((el) => {
  el.addEventListener('click', () => switchView(el.dataset.view));
});

document.addEventListener('click', (event) => {
  const jsonBtn = event.target.closest('#btn-json');
  if (jsonBtn) {
    const pack = state.artifacts.publish_pack;
    if (!pack) return;
    showModal({ title: '发布包 JSON', desc: '', confirmText: '关闭', onConfirm: () => {} });
    const body = document.querySelector('.modal-body');
    if (body) body.innerHTML = `<pre class="json-pre">${esc(JSON.stringify(pack, null, 2))}</pre>`;
  }
});

const runBtn = $('#sidebar-run');
if (runBtn) runBtn.addEventListener('click', () => startRun('full_pipeline'));

boot();
