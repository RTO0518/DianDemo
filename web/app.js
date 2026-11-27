/* ==========================================================================
   配电网本体推理 —— 前端逻辑
   纯原生 JS，无构建步骤；所有数据来自后端推理接口
   ========================================================================== */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const API = {
  async get(path) {
    const res = await fetch(`/api${path}`);
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `请求失败 ${res.status}`);
    return res.json();
  },
  async post(path, body) {
    const res = await fetch(`/api${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `请求失败 ${res.status}`);
    return res.json();
  },
  async put(path, body) {
    const res = await fetch(`/api${path}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `请求失败 ${res.status}`);
    return res.json();
  },
};

const PAGE_META = {
  ontology: ['本体模型', '领域概念、属性与公理：28 个类、46 个属性、16 条公理'],
  topology: ['电网拓扑', '单线图仿真：点击开关即可模拟分合闸，失电范围实时推演'],
  sparql: ['图谱查询', 'SPARQL 只读查询：在 OWL 闭包后的推理图上检索，换一种问法不用改代码'],
  ingest: ['数据接入', '台账 CSV → 字段映射 → 逐行校验 → 三元组预览，甲方数据能不能进来的第一道关'],
  chat: ['智能问答', '自然语言提问，大模型只做翻译，数字全部来自推理引擎'],
  verify: ['规则验证', '8 道推理题 + 12 条规则 + 阈值配置 + OWL 一致性自检'],
  audit: ['运行审计', '每一次问答、推理、查询都留痕，结论可追溯'],
};

const state = {
  view: 'ontology',
  schema: null,
  graph: null,
  chart: null,
  categories: [],
  classLabels: {},
  topo: null,
  switchStates: {},
  scenario: null,
  history: [],
  rules: [],
  sparqlExamples: [],
  ingest: { ledger: null, topology: null, ledgerName: '', topologyName: '' },
  thresholds: null,
};

/* ------------------------------------------------------------------ 导航 */
function switchView(name) {
  state.view = name;
  $$('.nav-item').forEach((b) => b.classList.toggle('is-active', b.dataset.view === name));
  $$('.view').forEach((v) => v.classList.toggle('is-active', v.id === `view-${name}`));
  const [title, sub] = PAGE_META[name];
  $('#pageTitle').textContent = title;
  $('#pageSub').textContent = sub;
  if (name === 'ontology' && state.chart) setTimeout(() => state.chart.resize(), 30);
  if (name === 'topology' && state.topo) renderTopology();
  if (name === 'sparql' && !state.sparqlLoaded) initSparql();
  if (name === 'audit') initAudit();
}

$$('.nav-item').forEach((btn) => btn.addEventListener('click', () => switchView(btn.dataset.view)));

/* ------------------------------------------------------------------ 通用 */
const CAT_COLOR = {
  设备: '#185FA5', 区域: '#3B6D11', 用户: '#854F0B',
  事件: '#993C1D', 量测: '#534AB7', 状态: '#5F5E5A', 其他: '#888780',
};
const CAT_BG = {
  设备: '#E6F1FB', 区域: '#EAF3DE', 用户: '#FAEEDA',
  事件: '#FAECE7', 量测: '#EEEDFE', 状态: '#F1EFE8', 其他: '#F1EFE8',
};

function pct(v) {
  if (v === null || v === undefined) return '—';
  return `${(v * 100).toFixed(1)}%`;
}

/* ------------------------------------------------------------------ 本体 */
async function initOntology() {
  const [schema, graph, rules, version] = await Promise.all([
    API.get('/ontology/schema'),
    API.get('/ontology/graph'),
    API.get('/reason/rules'),
    API.get('/ontology/version').catch(() => null),
  ]);
  state.schema = schema;
  state.graph = graph;
  state.rules = rules.rules;

  $('#statClasses').textContent = schema.class_count;
  $('#statObjProps').textContent = schema.object_property_count;
  $('#statDataProps').textContent = schema.data_property_count;
  $('#statAxioms').textContent = schema.axiom_count;
  $('#statRules').textContent = rules.count;

  if (version) renderVersion(version);

  schema.classes.forEach((c) => { state.classLabels[c.name] = c.label; });
  renderOntoChart(graph);
  loadClass('PowerEquipment');
}

function renderVersion(v) {
  const ttl = v.files.ontology_ttl || {};
  const inst = v.files.instances_ttl || {};
  const th = v.files.thresholds || {};
  $('#ontoVersion').innerHTML = `
    <span class="vb-item">本体 v${v.ontology_version}</span>
    <span class="vb-item"><i>本体</i><code>${ttl.short || '—'}</code></span>
    <span class="vb-item"><i>实例</i><code>${inst.short || '—'}</code></span>
    <span class="vb-item"><i>阈值</i><code>${th.short || '—'}</code></span>
    <span class="vb-item">${v.counts.triples} 条三元组 · ${v.counts.instances} 个实例 · ${v.counts.axioms} 条公理</span>
    <span class="vb-note">内容哈希即版本凭证，任何改动都会变</span>`;
}

function treeLayout(graph, width, height) {
  const children = new Map();
  graph.nodes.forEach((n) => children.set(n.id, []));
  const childSet = new Set();
  graph.edges.filter((e) => e.kind === '继承').forEach((e) => {
    if (children.has(e.source) && children.has(e.target)) {
      children.get(e.source).push(e.target);
      childSet.add(e.target);
    }
  });
  const roots = graph.nodes.filter((n) => !childSet.has(n.id));
  let leafIndex = 0;
  const pos = new Map();
  const walk = (id, depth) => {
    const kids = children.get(id) || [];
    if (!kids.length) {
      pos.set(id, { x: depth, y: leafIndex });
      leafIndex += 1;
      return;
    }
    kids.forEach((k) => {
      if (!pos.has(k)) walk(k, depth + 1);
    });
    const ys = kids.map((k) => (pos.get(k) || {}).y).filter((v) => v !== undefined);
    if (ys.length) pos.set(id, { x: depth, y: (Math.min(...ys) + Math.max(...ys)) / 2 });
  };
  roots.forEach((r) => { if (!pos.has(r.id)) walk(r.id, 0); });

  const maxDepth = Math.max(...Array.from(pos.values(), (p) => p.x), 1);
  const maxLeaf = Math.max(leafIndex - 1, 1);
  const padL = 26, padR = 132, padT = 26, padB = 34;
  const stepX = (width - padL - padR) / maxDepth;
  const stepY = (height - padT - padB) / maxLeaf;
  const out = {};
  pos.forEach((p, id) => {
    out[id] = { x: padL + p.x * stepX, y: padT + p.y * stepY };
  });
  return out;
}

function renderOntoChart(graph) {
  const cats = [...new Set(graph.nodes.map((n) => n.category))];
  state.categories = cats;
  const el = $('#ontoChart');
  if (!state.chart) state.chart = echarts.init(el);
  const showProps = $('#toggleProps').checked;

  const box = el.getBoundingClientRect();
  const pos = treeLayout(graph, Math.max(box.width - 8, 520), Math.max(box.height - 8, 360));
  const depthOf = {};
  graph.edges.filter((e) => e.kind === '继承').forEach((e) => { depthOf[e.target] = (depthOf[e.source] || 0) + 1; });

  const nodes = graph.nodes.map((n) => ({
    name: n.id,
    value: n.label,
    x: pos[n.id] ? pos[n.id].x : 0,
    y: pos[n.id] ? pos[n.id].y : 0,
    symbolSize: n.abstract || n.inferred ? 20 : 14,
    category: cats.indexOf(n.category),
    itemStyle: {
      color: CAT_BG[n.category] || '#F1EFE8',
      borderColor: CAT_COLOR[n.category] || '#888780',
      borderWidth: n.abstract ? 1.6 : 1,
      borderType: n.inferred ? 'dashed' : 'solid',
    },
  }));

  const links = graph.edges
    .filter((e) => showProps || e.kind === '继承')
    .map((e) => ({
      source: e.source,
      target: e.target,
      value: e.label,
      lineStyle: {
        color: e.kind === '继承' ? '#c3c7cf' : '#dfe3ea',
        width: e.kind === '继承' ? 1.5 : 1,
        type: e.kind === '继承' ? 'solid' : 'dashed',
        curveness: e.kind === '继承' ? 0 : 0.12,
        opacity: 0.95,
      },
    }));

  state.chart.setOption({
    tooltip: {
      formatter: (p) => {
        if (p.dataType === 'edge') return `${p.data.source} → ${p.data.target}<br/>${p.data.value || ''}`;
        const n = graph.nodes.find((x) => x.id === p.name);
        return n ? `<b>${n.label}</b>（${n.id}）<br/>${n.comment || ''}` : p.name;
      },
      textStyle: { fontSize: 12 },
    },
    legend: [{
      data: cats,
      bottom: 4,
      icon: 'circle',
      itemWidth: 8,
      itemHeight: 8,
      itemGap: 14,
      textStyle: { fontSize: 11, color: '#5b6270' },
    }],
    series: [{
      type: 'graph',
      layout: 'none',
      roam: true,
      draggable: true,
      zoom: 1,
      categories: cats.map((c) => ({ name: c, itemStyle: { color: CAT_COLOR[c] } })),
      data: nodes,
      links,
      label: {
        show: true,
        position: 'right',
        distance: 5,
        fontSize: 10.5,
        color: '#1d2129',
        formatter: (p) => state.classLabels[p.name] || p.name,
      },
      edgeSymbol: ['none', 'arrow'],
      edgeSymbolSize: 5,
      emphasis: { focus: 'adjacency', lineStyle: { width: 2 }, label: { fontSize: 12 } },
      lineStyle: { color: '#c3c7cf' },
    }],
  }, true);

  if (!state._ontoResizeBound) {
    state._ontoResizeBound = true;
    const el2 = $('#ontoChart');
    new ResizeObserver(() => {
      if (state.view === 'ontology' && state.chart && state.graph) renderOntoChart(state.graph);
    }).observe(el2);
  }

  state.chart.off('click');
  state.chart.on('click', (p) => {
    if (p.dataType === 'node') loadClass(p.name);
  });
}

async function loadClass(name) {
  const box = $('#classDetail');
  try {
    const c = await API.get(`/ontology/class/${name}`);
    const parents = c.parents.map((p) => `<span class="tag accent">${state.classLabels[p] || p}</span>`).join('');
    const children = c.children.map((p) => `<span class="tag">${state.classLabels[p] || p}</span>`).join('');
    const props = c.object_properties.map((p) => `
      <div class="prop-row">
        <span class="prop-name">${p.label}</span>
        <span class="prop-type">${p.direction === 'out' ? '→' : '←'} ${state.classLabels[p.other] || p.other}</span>
      </div>`).join('') || '<div class="list-empty">无</div>';
    const dprops = c.data_properties.map((p) => `
      <div class="prop-row">
        <span class="prop-name">${p.label}</span>
        <span class="prop-type">${p.range}${p.unit ? ' · ' + p.unit : ''}</span>
      </div>`).join('') || '<div class="list-empty">无</div>';
    const axioms = c.axioms.map((a) => `<span class="tag warn">${a.id} ${a.label}</span>`).join('') || '<div class="list-empty">无直接约束</div>';
    const instances = c.instances.slice(0, 12).map((i) => `<span class="tag">${i}</span>`).join('');

    box.innerHTML = `
      <div class="detail-title"><h3>${c.label}</h3><code>${c.name}</code></div>
      <div class="detail-comment">${c.comment || '（无说明）'}</div>
      <div class="detail-section"><h4>父类 / 子类</h4>
        <div class="tag-row">${parents || '<span class="tag">owl:Thing</span>'}${children ? '<span class="muted" style="padding:0 4px">· 子类</span>' + children : ''}</div>
      </div>
      <div class="detail-section"><h4>对象属性</h4>${props}</div>
      <div class="detail-section"><h4>数据属性</h4>${dprops}</div>
      <div class="detail-section"><h4>相关公理</h4><div class="tag-row">${axioms}</div></div>
      <div class="detail-section"><h4>实例（${c.instance_count}，含子类）</h4><div class="tag-row">${instances || '<span class="muted">无实例</span>'}</div></div>`;
  } catch (err) {
    box.innerHTML = `<div class="detail-empty">加载失败：${err.message}</div>`;
  }
}

$('#toggleProps').addEventListener('change', () => {
  if (state.graph) renderOntoChart(state.graph);
});

/* ------------------------------------------------------------------ 拓扑 */
const LAYOUT = {
  ST1: { x: 70, y: 270, w: 104, h: 58 },
  FX: { x: 210, y: 110, w: 88, h: 44 },
  FY: { x: 310, y: 430, w: 88, h: 44 },
  BR1: { x: 310, y: 110, w: 76, h: 38 },
  BR2: { x: 420, y: 430, w: 76, h: 38 },
  LS1: { x: 420, y: 110, w: 88, h: 44 },
  S1: { x: 530, y: 110, w: 76, h: 38 },
  LS2: { x: 640, y: 110, w: 88, h: 44 },
  S2: { x: 750, y: 110, w: 76, h: 38 },
  LS3: { x: 870, y: 110, w: 88, h: 44 },
  TR_D: { x: 350, y: 235, w: 96, h: 46 },
  TR_E: { x: 455, y: 235, w: 96, h: 46 },
  TR_A: { x: 560, y: 235, w: 96, h: 46 },
  TR_B: { x: 710, y: 235, w: 96, h: 46 },
  TR_C: { x: 870, y: 235, w: 96, h: 46 },
  L1: { x: 640, y: 330, w: 92, h: 40 },
  LS4: { x: 530, y: 430, w: 88, h: 44 },
  TR_F: { x: 410, y: 545, w: 96, h: 46 },
  TR_G: { x: 520, y: 545, w: 96, h: 46 },
  TR_H: { x: 630, y: 545, w: 96, h: 46 },
};

function nodeMeta(n) {
  return LAYOUT[n.id.replace('SW_', '')] || { x: 500, y: 300, w: 90, h: 44 };
}

function orthPath(a, b) {
  const A = nodeMeta(a), B = nodeMeta(b);
  const dx = B.x - A.x, dy = B.y - A.y;
  if (Math.abs(dy) < 6) {
    const x1 = A.x + (dx > 0 ? A.w / 2 : -A.w / 2);
    const x2 = B.x + (dx > 0 ? -B.w / 2 : B.w / 2);
    return `M${x1} ${A.y} H${x2}`;
  }
  if (Math.abs(dx) < 6) {
    const y1 = A.y + (dy > 0 ? A.h / 2 : -A.h / 2);
    const y2 = B.y + (dy > 0 ? -B.h / 2 : B.h / 2);
    return `M${A.x} ${y1} V${y2}`;
  }
  if (Math.abs(dx) > Math.abs(dy)) {
    const x1 = A.x + (dx > 0 ? A.w / 2 : -A.w / 2);
    const x2 = B.x + (dx > 0 ? -B.w / 2 : B.w / 2);
    const mx = (x1 + x2) / 2;
    return `M${x1} ${A.y} H${mx} V${B.y} H${x2}`;
  }
  const y1 = A.y + (dy > 0 ? A.h / 2 : -A.h / 2);
  const y2 = B.y + (dy > 0 ? -B.h / 2 : B.h / 2);
  const my = (y1 + y2) / 2;
  return `M${A.x} ${y1} V${my} H${B.x} V${y2}`;
}

function fillFor(n, dead) {
  if (dead) return { fill: '#FCEBEB', stroke: '#A32D2D', text: '#501313' };
  if (n.type === 'substation') return { fill: '#EEEDFE', stroke: '#534AB7', text: '#26215C' };
  if (n.type === 'feeder') return { fill: '#EAF3DE', stroke: '#3B6D11', text: '#173404' };
  if (n.type === 'switch') {
    return n.state === 'closed'
      ? { fill: '#FFFFFF', stroke: '#185FA5', text: '#0C447C' }
      : { fill: '#FFFFFF', stroke: '#A32D2D', text: '#501313' };
  }
  if (n.type === 'transformer') {
    if (n.status === 'overloaded') return { fill: '#FAEEDA', stroke: '#854F0B', text: '#412402' };
    if (n.status === 'heavy') return { fill: '#FDF4E3', stroke: '#BA7517', text: '#412402' };
    return { fill: '#E6F1FB', stroke: '#185FA5', text: '#0C447C' };
  }
  return { fill: '#E6F1FB', stroke: '#185FA5', text: '#0C447C' };
}

function subtitleFor(n) {
  if (n.type === 'switch') return n.state === 'closed' ? '合位' : '分位';
  if (n.type === 'transformer') {
    const parts = [];
    if (n.households) parts.push(`${n.households}户`);
    parts.push(pct(n.rate));
    return parts.join(' · ');
  }
  if (n.type === 'feeder') return `${Math.round(n.load_kw)} / ${Math.round(n.rated_kw)} kW`;
  if (n.type === 'substation') return '110kV';
  return '';
}

let topoDead = new Set();

function renderTopology() {
  if (!state.topo) return;
  const svg = $('#topoSvg');
  const { nodes, edges } = state.topo;
  const drawn = nodes.filter((n) => n.type !== 'district');
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));

  const edgeHtml = edges.map((e) => {
    const a = byId[e.source], b = byId[e.target];
    if (!a || !b || a.type === 'district' || b.type === 'district') return '';
    const sw = a.type === 'switch' ? a : (b.type === 'switch' ? b : null);
    const open = sw && sw.state === 'open';
    const dead = topoDead.has(a.id) && topoDead.has(b.id);
    const cls = ['topo-edge', open ? 'is-open' : '', dead ? 'is-dead' : ''].filter(Boolean).join(' ');
    return `<path class="${cls}" d="${orthPath(a, b)}" marker-end="url(#arrowLine)"/>`;
  }).join('');

  const nodeHtml = drawn.map((n) => {
    const m = nodeMeta(n);
    const dead = topoDead.has(n.id);
    const color = fillFor(n, dead);
    const sub = subtitleFor(n);
    const isSwitch = n.type === 'switch';
    const dash = isSwitch && n.state === 'open' ? 'stroke-dasharray="5 3"' : '';
    return `
      <g class="topo-node ${isSwitch ? 'is-switch' : ''}" data-id="${n.id}" data-code="${n.code || ''}">
        <rect x="${m.x - m.w / 2}" y="${m.y - m.h / 2}" width="${m.w}" height="${m.h}" rx="7"
              fill="${color.fill}" stroke="${color.stroke}" stroke-width="1" ${dash}/>
        <text x="${m.x}" y="${sub ? m.y - 7 : m.y}" text-anchor="middle" dominant-baseline="central"
              font-size="12" font-weight="500" fill="${color.text}">${isSwitch ? n.code : n.label}</text>
        ${sub ? `<text x="${m.x}" y="${m.y + 11}" text-anchor="middle" dominant-baseline="central"
              font-size="10.5" fill="${color.stroke}">${isSwitch ? sub : sub}</text>` : ''}
      </g>`;
  }).join('');

  svg.innerHTML = `
    <defs>
      <marker id="arrowLine" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
        <path d="M2 1L8 5L2 9" fill="none" stroke="#b9bdc5" stroke-width="1.6" stroke-linecap="round"/>
      </marker>
    </defs>
    ${edgeHtml}
    ${nodeHtml}`;

  $$('.topo-node.is-switch', svg).forEach((g) => {
    g.addEventListener('click', () => toggleSwitch(g.dataset.code));
  });
}

async function toggleSwitch(code) {
  if (!code) return;
  const current = state.switchStates[code] || (state.topo.states[code]);
  const next = current === 'closed' ? 'open' : 'closed';
  const payload = { ...state.switchStates, [code]: next };
  // 手动操作开关后就不再属于任何预设工况，按钮高亮必须撤掉，
  // 否则界面上会出现"按钮亮着但实际是自定义工况"的错位
  state.scenario = null;
  await applyStates(payload);
}

/* 工况按钮的选中态：显式记录当前预设，而不是靠比对开关状态反推
   ——「S1 断开」和「F02 高峰转供」的开关组合完全相同，只有负荷假设不同。 */
function syncScenarioBar() {
  const current = state.scenario || '';
  $$('#scenarioBar .btn').forEach((btn) => {
    const on = !!current && btn.dataset.scenario === current;
    btn.classList.toggle('is-active', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
}

async function applyStates(states, extra = {}) {
  const res = await API.post('/grid/workstate', { states });
  state.switchStates = states;
  state.topo.states = { ...state.topo.states, ...states };
  state.topo.nodes.forEach((n) => {
    if (n.type === 'switch') n.state = states[n.code] || n.state;
  });
  topoDead = new Set(res.de_energized.map((d) => d.id));
  state.lastOutage = res;
  renderTopology();
  renderImpact(res, extra);
  loadAdvice(res, extra);
  syncScenarioBar();
}

function renderImpact(res, extra) {
  $('#impactHouseholds').textContent = res.households;
  $('#impactRatio').textContent =
    `全网 ${res.total_households} 户 · 占比 ${pct(res.ratio)} · 失电设备 ${res.de_energized.length} 台`;

  const bars = [];
  const feeders = state.topo.nodes.filter((n) => n.type === 'feeder');
  feeders.forEach((f) => {
    const cls = f.rate >= 1 ? 'danger' : (f.rate >= 0.8 ? 'warn' : '');
    bars.push(`
      <div class="bar-row">${f.label} 负载率 ${pct(f.rate)}
        <div class="bar-track"><div class="bar-fill ${cls}" style="width:${Math.min(100, f.rate * 100)}%"></div></div>
      </div>`);
  });
  const max = state.topo.nodes.filter((n) => n.type === 'transformer')
    .sort((a, b) => b.rate - a.rate)[0];
  if (max) {
    bars.push(`
      <div class="bar-row">最高配变 ${max.label} ${pct(max.rate)}
        <div class="bar-track"><div class="bar-fill ${max.rate >= 1 ? 'danger' : (max.rate >= 0.8 ? 'warn' : '')}" style="width:${Math.min(100, max.rate * 100)}%"></div></div>
      </div>`);
  }
  $('#impactBars').innerHTML = bars.join('');

  const list = $('#districtList');
  if (!res.affected_districts.length) {
    list.innerHTML = '<div class="list-empty">当前无失电台区</div>';
  } else {
    list.innerHTML = res.affected_districts.map((d) => `
      <div class="list-item"><span class="dot"></span>
        <span class="name">${d.label}</span>
        <span class="num">${d.households} 户</span>
      </div>`).join('');
  }
}

async function loadAdvice(res, extra) {
  const box = $('#advice');
  if (!res.households) {
    box.innerHTML = '全网红电，无失电台区，运行方式正常。';
    return;
  }
  try {
    const t = await API.post('/reason/transfer', {
      states: state.switchStates,
      target_feeder_load_kw: extra.peak || null,
    });
    const plan = t.plans && t.plans[0];
    if (!plan) {
      box.innerHTML = `失电区无可用联络通道，不具备转供条件。<br>建议：发电车保电，或调整检修工期。`;
      return;
    }
    if (plan.feasible) {
      const after = plan.spare_after_kw !== undefined ? plan.spare_after_kw : null;
      box.innerHTML = `可转供：合上 <b>${plan.via_switch_label}</b>，由 <b>${plan.to_feeder_label}</b> 供电，
        转移 <b>${plan.transferred_kw.toFixed(0)} kW</b>，恢复 <b>${plan.restored_households} 户</b>；
        对侧负载率 ${pct(plan.target_rate_before)} → <b>${pct(plan.target_rate_after)}</b>`
        + (after !== null ? `，转供后余量 <b>${after.toFixed(0)} kW</b>。` : '。');
    } else {
      box.innerHTML = `不具备转供条件：对侧备用仅 <b>${plan.spare_kw.toFixed(0)} kW</b>，
        需转移 ${plan.transferred_kw.toFixed(0)} kW，缺口 <b>${plan.gap_kw.toFixed(0)} kW</b>。
        建议先压减负荷 ${plan.gap_kw.toFixed(0)} kW，或采用发电车保电。`;
    }
  } catch (err) {
    box.innerHTML = `转供评估失败：${err.message}`;
  }
}

async function initTopology() {
  state.topo = await API.get('/grid/topology');
  state.switchStates = {};
  // 初始工况：S1 断开（与需求文档示例一致），按钮同步高亮
  state.scenario = 's1';
  await applyStates({ S1: 'open' }, { switchId: 'S1' });
}

const SCENARIOS = {
  s1:    { states: { S1: 'open' }, extra: { switchId: 'S1' } },
  br1:   { states: { BR1: 'open' }, extra: { switchId: 'BR1' } },
  peak:  { states: { S1: 'open' }, extra: { switchId: 'S1', peak: 3400 } },
  reset: { states: { S1: 'closed', S2: 'closed', BR1: 'closed', BR2: 'closed', L1: 'open' }, extra: {} },
};

$$('[data-scenario]').forEach((btn) => {
  btn.addEventListener('click', async () => {
    const key = btn.dataset.scenario;
    const cfg = SCENARIOS[key];
    if (!cfg) return;
    if (state.scenario === key && key !== 'reset') return;   // 已在该工况，避免重复请求
    state.scenario = key;
    syncScenarioBar();          // 先亮起来，不等网络回来
    // 转供结论由 applyStates → loadAdvice 一并算，这里不再重复调用，
    // 否则高峰工况会先渲染出"可行"再被"不可行"覆盖，出现一闪而过的错结论
    await applyStates(cfg.states, cfg.extra);
  });
});

/* ------------------------------------------------------------------ 对话 */
function addMessage(role, text, meta) {
  const wrap = document.createElement('div');
  wrap.className = `msg ${role === 'user' ? 'msg-user' : 'msg-bot'}`;
  wrap.innerHTML = `
    <div class="msg-role">${role === 'user' ? '我' : '推理助手'}</div>
    <div class="msg-body"></div>
    ${meta ? `<div class="msg-meta">${meta}</div>` : ''}`;
  wrap.querySelector('.msg-body').textContent = text;
  $('#messages').appendChild(wrap);
  $('#messages').scrollTop = $('#messages').scrollHeight;
  return wrap;
}

function renderTrace(data) {
  const box = $('#trace');
  const parts = [];
  if (data.tool_calls && data.tool_calls.length) {
    data.tool_calls.forEach((t) => {
      parts.push(`<div class="tool-row"><code>${t.name}</code>${t.summary}
        <span class="ms">${t.elapsed_ms} ms</span></div>`);
    });
  }
  const groups = [];
  (data.trace || []).forEach((s) => {
    const last = groups[groups.length - 1];
    if (last && last.rule === s.rule) last.items.push(s);
    else groups.push({ rule: s.rule, name: s.rule_name, items: [s] });
  });
  groups.forEach((g) => {
    parts.push(`<div class="trace-group">
      <div class="trace-rule">${g.rule} · ${g.name}</div>
      ${g.items.map((i) => `<div class="trace-action">${i.action}</div>
        <div class="trace-detail">${i.detail}</div>`).join('')}
    </div>`);
  });
  box.innerHTML = parts.join('') || '<div class="list-empty">本次回答未调用推理工具</div>';
  $('#traceMeta').textContent = data.elapsed_ms ? `${data.elapsed_ms} ms` : '—';
}

async function ask(text) {
  if (!text.trim()) return;
  addMessage('user', text);
  const history = state.history.slice(-8);
  state.history.push({ role: 'user', content: text });
  const btn = $('#sendBtn');
  btn.disabled = true;
  btn.textContent = '推理中';
  try {
    const res = await API.post('/chat', { message: text, history });
    const rules = res.rules_cited && res.rules_cited.length ? `依据 ${res.rules_cited.join(' / ')}` : '';
    addMessage('assistant', res.answer, `${res.provider.model} · ${res.elapsed_ms} ms${rules ? ' · ' + rules : ''}`);
    state.history.push({ role: 'assistant', content: res.answer });
    renderTrace(res);
  } catch (err) {
    addMessage('assistant', `请求失败：${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = '发送';
  }
}

$('#chatForm').addEventListener('submit', (e) => {
  e.preventDefault();
  const input = $('#chatInput');
  const text = input.value;
  input.value = '';
  ask(text);
});
$$('.chip').forEach((c) => c.addEventListener('click', () => ask(c.textContent.trim())));

/* ------------------------------------------------------------------ 验证 */
async function initVerify() {
  const [verify, rules] = await Promise.all([API.get('/reason/verify'), API.get('/reason/rules')]);
  renderVerify(verify);
  renderRules(rules.rules);
  await initThresholds();
}

function renderVerify(data) {
  $('#verifySummary').innerHTML = `
    <b>${data.summary}</b>　其中 5 题为需求文档指定题目，3 题为评审追问场景（含不可行与无解情形）。
    预期答案写死在用例里，实际答案全部由推理引擎现算。`;
  $('#verifyList').innerHTML = data.results.map((r) => `
    <div class="verify-item">
      <div class="verify-head">
        <span class="verify-id">${r.id}</span>
        <span class="verify-q">${r.question}</span>
        <span class="pill rule">${r.rule}</span>
        <span class="pill ${r.passed ? 'ok' : 'bad'}">${r.passed ? '通过' : '不一致'}</span>
      </div>
      <div class="verify-body">
        <div><div class="verify-label">预期答案</div><div class="verify-text">${r.expected}</div></div>
        <div><div class="verify-label">引擎实际输出</div><div class="verify-text">${r.actual}</div></div>
      </div>
    </div>`).join('');
}

function renderRules(rules) {
  $('#rulesTable').innerHTML = `
    <thead><tr><th style="width:56px">编号</th><th style="width:150px">规则</th><th>说明</th>
    <th style="width:110px">依赖公理</th><th style="width:170px">输出</th></tr></thead>
    <tbody>${rules.map((r) => `
      <tr>
        <td class="code">${r.id}</td>
        <td>${r.name}</td>
        <td class="wrap">${r.description}</td>
        <td class="code">${r.axioms.join(' ')}</td>
        <td class="wrap">${r.output}</td>
      </tr>`).join('')}</tbody>`;
}

$('#runVerify').addEventListener('click', async () => {
  const btn = $('#runVerify');
  btn.disabled = true;
  btn.textContent = '验证中';
  try {
    renderVerify(await API.get('/reason/verify'));
  } finally {
    btn.disabled = false;
    btn.textContent = '重新验证';
  }
});

$('#runOwl').addEventListener('click', async () => {
  const btn = $('#runOwl');
  btn.disabled = true;
  btn.textContent = '自检中';
  try {
    const r = await API.post('/reason/owl-check', { switch_id: 'S1', state: 'open' });
    const o = r.owl;
    $('#verifySummary').innerHTML = `
      OWL 一致性自检：${o.passed ? '<b>通过</b>' : '<b>发现冲突</b>'}　
      断言前 ${o.triples_before} 条三元组，闭包后 ${o.triples_after} 条（新增 ${o.inferred_triples} 条推理结果）；
      冲突 ${o.conflicts.length} 处。抽查个体 ${o.spot_check.device} 被 OWL 归入设备大类：
      ${o.spot_check.inherited_power_equipment ? '是' : '否'}。
      <br><span class="muted">局限：${o.limits.join('；')}</span>`;
  } catch (err) {
    $('#verifySummary').textContent = `自检失败：${err.message}`;
  } finally {
    btn.disabled = false;
    btn.textContent = 'OWL 一致性自检';
  }
});

/* ------------------------------------------------------------------ SPARQL */
async function initSparql() {
  state.sparqlLoaded = true;
  try {
    const [ex, st] = await Promise.all([API.get('/sparql/examples'), API.get('/sparql/stats')]);
    state.sparqlExamples = ex.examples;
    $('#sparqlStats').textContent =
      `${st.inferred_triples} 条三元组（显式 ${st.raw_triples} + 推导 ${st.delta}）`;
    renderExamples(ex.examples);
    if (!$('#sparqlInput').value) {
      $('#sparqlInput').value = ex.examples[0].query;
      runSparql();   // 进来就给一条能出结果，避免空白页让人以为没通
    }
  } catch (err) {
    $('#sparqlExamples').innerHTML = `<div class="list-empty">示例加载失败：${err.message}</div>`;
  }
}

function renderExamples(examples) {
  $('#sparqlExamples').innerHTML = examples.map((e) => `
    <button class="example-item" data-id="${e.id}">
      <div class="example-title"><span class="example-id">${e.id}</span>${e.title}</div>
      <div class="example-desc">${e.description}</div>
    </button>`).join('');
  $$('#sparqlExamples .example-item').forEach((btn) => {
    btn.addEventListener('click', () => {
      const ex = state.sparqlExamples.find((x) => x.id === btn.dataset.id);
      if (ex) {
        $('#sparqlInput').value = ex.query;
        $('#sparqlInput').scrollIntoView({ block: 'nearest' });
        runSparql();
      }
    });
  });
}

function termText(t) {
  if (!t) return '—';
  if (t.type === 'uri') return t.local || t.value;
  return t.value;
}

async function runSparql() {
  const query = $('#sparqlInput').value.trim();
  const box = $('#sparqlResult');
  if (!query) return;
  const btn = $('#runSparql');
  btn.disabled = true;
  btn.textContent = '执行中';
  box.innerHTML = '<div class="list-empty">执行中…</div>';
  try {
    const data = await API.post('/sparql/query', { query, limit: 200 });
    if (data.kind === 'ask') {
      box.innerHTML = `<div class="ask-result">ASK 结果：<b>${data.boolean}</b></div>`;
      $('#sparqlMeta').textContent = `${data.elapsed_ms} ms`;
      return;
    }
    if (!data.rows.length) {
      box.innerHTML = '<div class="list-empty">查询执行成功，但没有匹配结果</div>';
      $('#sparqlMeta').textContent = `${data.elapsed_ms} ms · 0 行`;
      return;
    }
    const cols = data.variables;
    const head = cols.map((c) => `<th>${c}</th>`).join('');
    const body = data.rows.map((row) => `<tr>${cols.map((c) => {
      const t = row[c];
      const cls = t && t.type === 'uri' ? 'cell-uri' : 'cell-val';
      const title = t && t.type === 'uri' ? ` title="${t.value}"` : '';
      return `<td class="${cls}"${title}>${termText(t)}</td>`;
    }).join('')}</tr>`).join('');
    box.innerHTML = `<table class="table sparql-table">
      <thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
    $('#sparqlMeta').textContent =
      `${data.elapsed_ms} ms · ${data.row_count} 行${data.truncated ? ' · 已截断' : ''}`;
  } catch (err) {
    box.innerHTML = `<div class="error-box">${err.message}</div>`;
    $('#sparqlMeta').textContent = '';
  } finally {
    btn.disabled = false;
    btn.textContent = '执行查询';
  }
}

$('#runSparql').addEventListener('click', runSparql);
$('#sparqlInput').addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    e.preventDefault();
    runSparql();
  }
});

/* ------------------------------------------------------------------ 数据接入 */
function toBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let bin = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(bin);
}

function bindUpload(inputId, hintId, key) {
  const input = $(inputId);
  input.addEventListener('change', async () => {
    const file = input.files && input.files[0];
    if (!file) return;
    const buf = await file.arrayBuffer();
    state.ingest[key] = { name: file.name, content_base64: toBase64(buf) };
    $(hintId).innerHTML = `<b>${file.name}</b> · ${(file.size / 1024).toFixed(1)} KB`;
    $(hintId).classList.add('is-loaded');
  });
}

bindUpload('#fileLedger', '#hintLedger', 'ledger');
bindUpload('#fileTopology', '#hintTopology', 'topology');

$('#loadSample').addEventListener('click', async () => {
  const btn = $('#loadSample');
  btn.disabled = true;
  btn.textContent = '载入中';
  try {
    const [ledger, topo] = await Promise.all([
      API.get('/ingest/templates/' + encodeURIComponent('设备台账模板')),
      API.get('/ingest/templates/' + encodeURIComponent('拓扑关系模板')),
    ]);
    state.ingest.ledger = { name: ledger.file, content_text: ledger.content, encoding: 'utf-8' };
    state.ingest.topology = { name: topo.file, content_text: topo.content, encoding: 'utf-8' };
    $('#hintLedger').innerHTML = `<b>${ledger.file}</b> · 示例数据`;
    $('#hintLedger').classList.add('is-loaded');
    $('#hintTopology').innerHTML = `<b>${topo.file}</b> · 示例数据`;
    $('#hintTopology').classList.add('is-loaded');
    runIngest();
  } catch (err) {
    alert(`示例载入失败：${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = '载入示例台账';
  }
});

$('#runIngest').addEventListener('click', runIngest);

async function runIngest() {
  if (!state.ingest.ledger) {
    alert('请先选择设备台账 CSV，或点「载入示例台账」');
    return;
  }
  const btn = $('#runIngest');
  btn.disabled = true;
  btn.textContent = '校验中';
  try {
    const payload = { ledger: state.ingest.ledger };
    if (state.ingest.topology) payload.topology = state.ingest.topology;
    const data = await API.post('/ingest/preview', payload);
    renderIngest(data);
  } catch (err) {
    $('#ingestReportCard').hidden = false;
    $('#ingestSummary').innerHTML = `<span class="bad-text">请求失败：${err.message}</span>`;
  } finally {
    btn.disabled = false;
    btn.textContent = '开始校验';
  }
}

function renderIngest(data) {
  const L = data.ledger;
  const T = data.topology;
  $('#ingestReportCard').hidden = false;
  $('#ingestReady').innerHTML = data.ready
    ? '<span class="ok-text">校验通过</span>'
    : '<span class="bad-text">存在阻断性问题</span>';

  const parts = [`<b>${L.filename || '台账'}</b>：${L.summary}`];
  if (T) parts.push(`<b>${T.filename || '拓扑表'}</b>：${T.summary}`);
  if (data.next_step) parts.push(`<span class="muted">${data.next_step}</span>`);
  $('#ingestSummary').innerHTML = parts.join('<br>');

  const mp = L.columns.mapping.map((m) =>
    `<span class="map-item"><i>${m.column}</i><span class="arrow">→</span><code>${m.label}</code></span>`).join('');
  const extra = [];
  if (L.columns.unmapped.length) {
    extra.push(`<div class="map-warn">未识别列（会被忽略）：${L.columns.unmapped.join('、')}</div>`);
  }
  if (L.columns.missing_required.length) {
    extra.push(`<div class="map-bad">缺少必填列：${L.columns.missing_required.join('、')}</div>`);
  }
  $('#ingestMapping').innerHTML = mp + extra.join('');

  $('#ingestTypes').innerHTML = Object.entries(L.type_stat)
    .map(([k, v]) => `<span class="tag">${k} <b>${v}</b></span>`).join('');

  const issues = [...(L.issues || []), ...((T && T.issues) || [])];
  if (!issues.length) {
    $('#ingestIssues').innerHTML = '<div class="list-empty ok-text">没有发现数据质量问题</div>';
  } else {
    $('#ingestIssues').innerHTML = issues.map((i) => `
      <div class="issue ${i.level === 'error' ? 'issue-error' : 'issue-warn'}">
        <span class="issue-code">${i.code}</span>
        <span class="issue-row">${i.row ? '第 ' + i.row + ' 行' : '全表'}</span>
        <span class="issue-msg">${i.message}</span>
      </div>`).join('');
  }

  $('#ingestTtlCard').hidden = false;
  $('#ingestTtlMeta').textContent =
    `约 ${data.stats.device_count} 台设备 / ${data.stats.switch_edges} 条开关边 / 估算 ${data.stats.estimated_triples} 条三元组`;
  $('#ingestTtl').textContent = data.turtle;
}

$('#copyTtl').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText($('#ingestTtl').textContent);
    $('#copyTtl').textContent = '已复制';
    setTimeout(() => { $('#copyTtl').textContent = '复制'; }, 1500);
  } catch (e) {
    $('#copyTtl').textContent = '复制失败';
  }
});

/* ------------------------------------------------------------------ 审计 */
async function initAudit() {
  try {
    const [sum, rec] = await Promise.all([
      API.get('/audit/summary'),
      API.get('/audit/records?limit=60'),
    ]);
    $('#auditTotal').textContent = sum.total;
    $('#auditFailed').textContent = sum.failed;
    $('#auditKinds').textContent = Object.keys(sum.by_kind).length;
    $('#auditRules').textContent = Object.keys(sum.by_rule).length;
    $('#auditNote').innerHTML = sum.total
      ? `记录文件 <code>${sum.file}</code>　首条 ${sum.first_at}　末条 ${sum.last_at}
         <br><span class="muted">${sum.note}</span>`
      : '还没有记录。去「智能问答」提一个问，或到「电网拓扑」点一次开关，这里就会有痕迹。';
    renderAudit(rec.records);
  } catch (err) {
    $('#auditNote').textContent = `审计读取失败：${err.message}`;
  }
}

const KIND_CN = { chat: '问答', reasoning: '推理', sparql: '查询', ingest: '接入', system: '系统' };

function renderAudit(records) {
  if (!records.length) {
    $('#auditTable').innerHTML = '';
    return;
  }
  $('#auditTable').innerHTML = `
    <thead><tr>
      <th style="width:64px">序号</th><th style="width:150px">时间</th>
      <th style="width:70px">类型</th><th style="width:110px">动作</th>
      <th>摘要</th><th style="width:110px">命中规则</th><th style="width:80px">耗时</th>
    </tr></thead>
    <tbody>${records.map((r) => `
      <tr>
        <td class="code">${r.seq}</td>
        <td class="nowrap">${(r.ts || '').replace('T', ' ')}</td>
        <td><span class="pill rule">${KIND_CN[r.kind] || r.kind}</span></td>
        <td>${r.action || '—'}</td>
        <td class="wrap">${r.summary || ''}${r.ok ? '' : ' <span class="bad-text">（失败）</span>'}</td>
        <td class="code">${(r.rules || []).join(' ') || '—'}</td>
        <td class="num">${r.elapsed_ms || 0} ms</td>
      </tr>`).join('')}</tbody>`;
}

$('#refreshAudit').addEventListener('click', initAudit);
$('#clearAudit').addEventListener('click', async () => {
  if (!confirm('清空全部审计记录？此操作不可恢复，仅用于演示重置。')) return;
  await API.post('/audit/clear', {});
  initAudit();
});

/* ------------------------------------------------------------------ 阈值 */
async function initThresholds() {
  try {
    const data = await API.get('/reason/thresholds');
    state.thresholds = data;
    renderThresholds(data);
  } catch (err) {
    $('#thresholdList').innerHTML = `<div class="list-empty">阈值加载失败：${err.message}</div>`;
  }
}

function renderThresholds(data) {
  $('#thresholdSource').textContent =
    `来源：${data.source}${data.values.overload_rate !== data.baseline.overload_rate ? '（已改动）' : ''}`;
  $('#thresholdNote').innerHTML = `${data.note}
    <br><span class="muted">拖动滑块立即生效，所有过载判定与转供结论会随之变化；8 道验证题始终按基线阈值计算。</span>`;
  $('#thresholdList').innerHTML = data.items.map((it) => {
    const pctVal = Math.round(it.value * 1000) / 10;
    const pctBase = Math.round(it.baseline * 1000) / 10;
    const changed = Math.abs(it.value - it.baseline) > 1e-9;
    return `<div class="threshold-row" data-key="${it.key}">
      <div class="threshold-head">
        <span class="threshold-label">${it.label}</span>
        <span class="pill rule">${it.rules.join(' ')}</span>
        <span class="threshold-value ${changed ? 'is-changed' : ''}">${pctVal}%</span>
        <span class="muted">基线 ${pctBase}%</span>
      </div>
      <input type="range" class="slider"
             min="${it.min * 100}" max="${it.max * 100}" step="1" value="${pctVal}">
      <div class="threshold-desc">${it.description}</div>
    </div>`;
  }).join('');

  $$('#thresholdList .slider').forEach((slider) => {
    slider.addEventListener('change', async () => {
      const row = slider.closest('.threshold-row');
      const key = row.dataset.key;
      const val = Number(slider.value) / 100;
      try {
        const res = await API.put('/reason/thresholds', { values: { [key]: val } });
        renderThresholds(res.thresholds);
        loadOverloadHints();
      } catch (err) {
        alert(`阈值修改失败：${err.message}`);
        initThresholds();
      }
    });
  });
}

$('#resetThresholds').addEventListener('click', async () => {
  const data = await API.post('/reason/thresholds/reset', {});
  renderThresholds(data);
  loadOverloadHints();
});

async function loadOverloadHints() {
  try {
    const r = await API.get('/reason/overload');
    const el = $('#thresholdSource');
    if (el) el.title = `当前工况：过载 ${r.overloaded_count} 台，重载 ${r.heavy_count} 台（悬停可见）`;
  } catch (e) { /* 忽略 */ }
}

/* ------------------------------------------------------------------ 启动 */
async function boot() {
  try {
    const [health, status] = await Promise.all([API.get('/health'), API.get('/chat/status')]);
    const c = health.census;
    $('#censusBadge').textContent =
      `${c.feeders} 条馈线 · ${c.transformers} 台配变 · ${c.households} 户 · ${c.total_load_kw.toFixed(0)} kW`;
    const p = status.provider;
    $('#modelText').textContent = p.mode === 'online' ? `在线 · ${p.model}` : '离线规则引擎';
    $('#modelText').title = `${p.provider} / ${p.model}`;
    $('#modelChip').classList.add(p.mode === 'online' ? 'is-online' : 'is-offline');
  } catch (err) {
    $('#modelText').textContent = '服务未连接';
  }

  await initOntology();
  await initTopology();
  await initVerify();

  window.addEventListener('resize', () => {
    if (state.chart) state.chart.resize();
  });
}

const params = new URLSearchParams(location.search);
const initialView = params.get('view');
const presetQuestion = params.get('q');
if (presetQuestion) setTimeout(() => { switchView('chat'); ask(presetQuestion); }, 900);
if (initialView && PAGE_META[initialView]) setTimeout(() => switchView(initialView), 400);

boot();
