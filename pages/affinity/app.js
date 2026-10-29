import {drawNetwork, loadNetworkEngine} from './network.js';
import {initAdmin} from './admin.js';
const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;
const $ = id => document.getElementById(id);
const state = {groups: [], group: null, marriage: null, pair: null, scope: null, person: '', source: '', target: '', page: 1, tab: 'overview'};
let groupRequest = 0, pairRequest = 0;
let admin;
const fmt = value => value == null ? '—' : Number(value).toFixed(1);
const date = at => new Date(at * 1000).toLocaleString('zh-CN', {hour12: false});
const shortDate = at => new Date(at * 1000).toLocaleDateString('zh-CN', {month: 'numeric', day: 'numeric'});
const number = value => Number(value || 0).toLocaleString('zh-CN');
const sign = value => Math.abs(value) < .000001 ? '0' : Math.abs(value) < .001 ? (value > 0 ? '+<0.001' : '−<0.001') : `${value > 0 ? '+' : ''}${value.toFixed(3)}`;
const node = (tag, className = '', text = '') => { const result = document.createElement(tag); result.className = className; result.textContent = text; return result; };
const svgNode = (tag, attrs = {}, text = '') => { const result = document.createElementNS('http://www.w3.org/2000/svg', tag); for (const [key, value] of Object.entries(attrs)) result.setAttribute(key, value); result.textContent = text; return result; };
function notice(text, error = false) { $('notice').textContent = text; $('notice').classList.toggle('error', error); }
async function run(action) { try { await action(); } catch (error) { notice(error.message || '读取数据失败，请稍后再试。', true); } }
function params(extra = {}) { return {platform: state.scope[0], bot: state.scope[1], group: state.scope[2], ...extra}; }
function theme() { document.documentElement.dataset.theme = 'light'; }

async function loadMeta() {
  $('refresh').disabled = true;
  try {
    const result = await bridge.apiGet('affinity/meta');
    state.groups = result.groups;
    const selected = state.scope && JSON.stringify(state.scope);
    $('group-select').replaceChildren();
    for (const [index, group] of result.groups.entries()) {
      const option = node('option', '', `${group.name} · ${group.scope[0] || '今日记录'}`); option.value = index;
      option.selected = JSON.stringify(group.scope) === selected; $('group-select').append(option);
    }
    $('model-status').textContent = `情绪模型：${result.model} · ${result.aggressive ? '激进模式' : '普通模式'}`;
    if (!result.groups.length) {
      $('empty').classList.remove('hidden'); $('workspace').classList.add('hidden');
      state.group = state.pair = state.scope = null; ++groupRequest; ++pairRequest;
      $('member-list').replaceChildren(); $('member-count').textContent = '0 人'; $('scope-label').textContent = '';
      $('group-select').append(node('option', '', '暂无允许查看的群聊'));
      notice('暂时没有可查看的群聊记录。仍可查看本插件的运行日志、配置中心和数据管理。'); setTab(state.tab); return;
    }
    $('empty').classList.add('hidden'); $('workspace').classList.remove('hidden');
    const index = Math.max(0, result.groups.findIndex(group => JSON.stringify(group.scope) === selected));
    $('group-select').value = index;
    if (!selected || selected !== JSON.stringify(result.groups[index].scope)) {
      state.person = state.source = state.target = ''; state.pair = null; state.page = 1;
    }
    state.scope = result.groups[index].scope;
    notice(result.enabled ? '记录的是日常互动，不是读心术。点击关系即可追溯每一分的来处。' : '当前好感度采集已关闭，页面只展示留存期内的历史。');
    await loadGroup();
  } finally { $('refresh').disabled = false; }
}

async function loadGroup() {
  const request = ++groupRequest;
  ++pairRequest;
  const selectedGroup = state.groups.find(group => JSON.stringify(group.scope) === JSON.stringify(state.scope));
  const affinityAllowed = selectedGroup?.affinity_allowed !== false && Boolean(state.scope[0] && state.scope[1]);
  const [affinity, marriage] = await Promise.all([
    affinityAllowed ? bridge.apiGet('affinity/group', params({user: state.person})) : Promise.resolve(null),
    bridge.apiGet('affinity/marriage', params({user: state.person})),
  ]);
  if (request !== groupRequest) return;
  const result = affinity || {scope: state.scope, name: selectedGroup.name, at: Date.now() / 1000,
    members: [], totals: {members: 0, chat: 0, poke: 0, accepted: 0, rejected: 0, forced: 0, messages: 0},
    relations: [], activity: [], truncated: false};
  result.name = marriage.name || result.name;
  result.affinityAllowed = affinityAllowed;
  state.marriage = marriage;
  const members = new Map(marriage.members.map(member => [member.id, member]));
  for (const member of result.members) if (!members.has(member.id) || (members.get(member.id).name === member.id && member.name !== member.id)) members.set(member.id, member);
  result.members = [...members.values()];
  state.group = result;
  for (const tab of ['detail', 'graph']) document.querySelector(`[data-tab=${tab}]`).disabled = !affinityAllowed;
  if (!affinityAllowed && ['detail', 'graph'].includes(state.tab)) setTab('overview');
  if (selectedGroup && result.name) {
    selectedGroup.name = result.name;
    const index = state.groups.indexOf(selectedGroup);
    if ($('group-select').options[index]) $('group-select').options[index].textContent = `${result.name} · ${state.scope[0] || '今日记录'}`;
  }
  $('group-title').textContent = result.name || selectedGroup?.name || `群 ${state.scope[2]}`;
  $('scope-label').textContent = affinityAllowed ? `${state.scope[0]} / Bot ${state.scope[1]} / 群 ${state.scope[2]}` : `群 ${state.scope[2]} · 今日老婆记录`;
  $('updated-at').textContent = `更新于 ${date(result.at)}`;
  renderMetrics(); renderMembers(); renderRelations(); renderSelectors(); renderActivity(); renderGraph(); renderMarriage();
  notice(affinityAllowed ? '今日老婆关系与群内关系图同步更新；好感度按方向单独计算。' : '当前群展示今日老婆关系；暂无可查看的好感度记录，或受好感度独立名单限制。');
  if (!state.source || !state.target) {
    const first = result.relations.find(row => row.target === state.person) || result.relations[0];
    if (first) { state.source = first.source; state.target = first.target; renderSelectors(); }
  }
  if (affinityAllowed && state.source && state.target) await loadPair();
  else { $('pair-content').classList.add('hidden'); $('pair-empty').classList.remove('hidden'); $('curve-panel').classList.add('hidden'); $('ledger-panel').classList.add('hidden'); }
  setTab(state.tab);
}

function renderMetrics() {
  const totals = state.group.totals;
  const data = [['今日关系方向', state.marriage.totals.relations, `${number(state.marriage.totals.members)} 位群友参与`], ['有效聊天', totals.chat, '近60天，提及 + 引用'],
    ['戳一戳', totals.poke, '冷却与限额后的次数'], ['求婚同意', totals.accepted, '每次同意只统计一次'],
    ['求婚被拒', totals.rejected, '超时与取消不算拒绝'], ['强娶', totals.forced, `另记录 ${number(totals.messages)} 条消息`]];
  const icons = ['marriage', 'detail', 'overview', 'graph', 'logs', 'marriage'];
  $('metrics').replaceChildren(...data.map(([label, value, detail], index) => {
    const available = index === 0 || state.group.affinityAllowed, card = node('div', 'metric');
    const line = node('div', 'metric-number'), icon = svgNode('svg', {viewBox: '0 0 24 24', width: 21, height: 21, 'aria-hidden': true});
    icon.append(svgNode('path', {d: tabIcons[icons[index]], fill: 'none', stroke: 'currentColor', 'stroke-width': 1.7, 'stroke-linecap': 'round', 'stroke-linejoin': 'round'}));
    line.append(icon, node('strong', 'metric-value', available ? number(value) : '—'));
    card.append(node('p', 'metric-label', label), line, node('p', 'metric-detail', available ? detail : '暂无可查看的好感度数据')); return card;
  }));
}

function renderMembers() {
  if (!state.group) return;
  const search = $('member-search').value.trim().toLowerCase();
  const members = state.group.members.filter(member => `${member.name} ${member.id}`.toLowerCase().includes(search));
  $('member-count').textContent = `${number(state.group.members.length)} 人`;
  $('all-members').classList.toggle('active', !state.person);
  $('member-list').replaceChildren(...members.slice(0, 200).map(member => {
    const button = node('button', `member-button${member.id === state.person ? ' active' : ''}`);
    const label = node('div', 'member-name'); label.append(node('div', '', member.name), node('div', 'member-id', member.id));
    button.append(label);
    button.title = `${member.name} · ${member.id}`; button.onclick = () => run(() => choosePerson(member.id)); return button;
  }));
  if (!members.length) $('member-list').append(node('p', 'blank', '没有找到这位群友'));
}

async function choosePerson(id) { state.person = id; state.source = state.target = ''; state.page = 1; await loadGroup(); }
function renderSelectors() {
  for (const [id, selected] of [['source-select', state.source], ['target-select', state.target]]) {
    $(id).replaceChildren(...state.group.members.map(member => { const option = node('option', '', `${member.name} · ${member.id}`); option.value = member.id; return option; }));
    if (selected) $(id).value = selected;
  }
}
function renderRelations() {
  const rows = state.group.relations;
  $('relations-title').textContent = state.person ? '与 TA 有关的关系' : '群友之间的来往';
  $('relation-list').replaceChildren(...rows.map(row => {
    const button = node('button', `relation-row${row.source === state.source && row.target === state.target ? ' active' : ''}`);
    const label = node('span', 'relation-direction'); label.append(document.createTextNode(row.source_name), node('span', 'arrow', '→'), document.createTextNode(row.target_name));
    button.append(label, node('strong', '', fmt(row.score))); button.onclick = () => run(() => choosePair(row.source, row.target)); return button;
  }));
  if (!rows.length) $('relation-list').append(node('p', 'blank', '暂时没有形成定向好感度记录。普通群消息本身不会给任何人加分。'));
  $('relation-note').textContent = state.group.truncated ? '优先展示互动较多的120个方向，可在左侧选择群友缩小范围。' : '双方方向分别计算；只戳对方，不会自动让对方喜欢自己。';
}
async function choosePair(source, target) { state.source = source; state.target = target; state.page = 1; setTab('detail'); renderSelectors(); renderRelations(); await loadPair(); }
async function loadPair() {
  const request = ++pairRequest;
  const result = await bridge.apiGet('affinity/pair', params({source: state.source, target: state.target, days: $('days-select').value, page: state.page}));
  if (request !== pairRequest) return;
  state.pair = result; renderPair(); renderCurve(); renderLedger(); renderRelations();
}
function renderPair() {
  const pair = state.pair;
  $('pair-content').classList.remove('hidden'); $('pair-empty').classList.add('hidden');
  $('curve-panel').classList.remove('hidden'); $('ledger-panel').classList.remove('hidden');
  $('pair-title').textContent = `${pair.source_name} 对 ${pair.target_name}`;
  $('score-value').textContent = pair.current ? fmt(pair.current.score) : '无数据';
  $('reverse-score').textContent = `反方向 ${pair.reverse ? fmt(pair.reverse.score) : '无数据'}`;
  $('mode-label').textContent = pair.current?.multiplier > 1 ? '激进 × 1.6' : '普通模式';
  $('components').replaceChildren(...pair.components.map(component => {
    const card = node('div', 'component'); card.append(node('p', '', component.label), node('strong', component.value < 0 ? 'negative' : component.value > 0 ? 'positive' : '', component.key === 'base' ? fmt(component.value) : sign(component.value)));
    card.title = `${component.label}：${component.value.toFixed(6)}`; return card;
  }));
  $('history-note').textContent = pair.history_note;
}

function drawLine(container, points, {fullScale = false, height = 230, color = 'var(--rose)', tooltip = false, scoreRange = true} = {}) {
  container.replaceChildren();
  const available = points.filter(point => point.score != null);
  if (!available.length) { container.append(node('div', 'blank', '这段时间没有可计算的数据，未建立关系时不会画成0分。')); return; }
  const width = Math.max(280, container.clientWidth || 900), left = 42, right = 18, top = 15, bottom = 29;
  let minimum = Math.min(...available.map(point => point.score)), maximum = Math.max(...available.map(point => point.score));
  const margin = Math.max(.25, (maximum - minimum) * .18);
  minimum = fullScale ? 0 : Math.max(0, minimum - margin); maximum = fullScale ? 100 : scoreRange ? Math.min(100, maximum + margin) : maximum + margin;
  if (maximum <= minimum) maximum = minimum + 1;
  const first = points[0].at, last = Math.max(first + 1, points[points.length - 1].at);
  const x = at => left + (at - first) / (last - first) * (width - left - right);
  const y = score => top + (maximum - score) / (maximum - minimum) * (height - top - bottom);
  const svg = svgNode('svg', {viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: 'none', role: 'img', 'aria-label': '分数随时间变化曲线'});
  for (let i = 0; i < 4; i++) {
    const score = minimum + (maximum - minimum) * i / 3, py = y(score);
    svg.append(svgNode('line', {x1: left, y1: py, x2: width - right, y2: py, stroke: 'var(--line)', 'stroke-dasharray': '3 5'}));
    svg.append(svgNode('text', {x: left - 9, y: py + 3, fill: 'var(--muted)', 'font-size': 10, 'text-anchor': 'end'}, score.toFixed(1)));
  }
  let path = '', connected = false;
  for (const point of points) {
    if (point.score == null) { connected = false; continue; }
    path += `${connected ? 'L' : 'M'}${x(point.at)},${y(point.score)} `; connected = true;
  }
  svg.append(svgNode('path', {d: path, fill: 'none', stroke: color, 'stroke-width': 2, 'vector-effect': 'non-scaling-stroke', 'stroke-linejoin': 'round'}));
  for (const point of available) svg.append(svgNode('circle', {cx: x(point.at), cy: y(point.score), r: point.basis === 'recorded' ? 3 : 1.7, fill: point.basis === 'recorded' ? color : 'var(--muted)'}));
  for (let i = 0; i < 5; i++) { const at = first + (last - first) * i / 4; svg.append(svgNode('text', {x: x(at), y: height - 6, fill: 'var(--muted)', 'font-size': 10, 'text-anchor': i === 0 ? 'start' : i === 4 ? 'end' : 'middle'}, shortDate(at))); }
  container.append(svg);
  if (tooltip) {
    const tip = node('div', 'chart-tooltip hidden'); container.append(tip);
    const focus = svgNode('line', {y1: top, y2: height - bottom, stroke: 'var(--muted)', 'stroke-dasharray': '3 3', visibility: 'hidden'}); svg.append(focus);
    svg.onpointermove = event => {
      const box = svg.getBoundingClientRect(), px = (event.clientX - box.left) * width / box.width;
      const nearest = available.reduce((best, point) => Math.abs(x(point.at) - px) < Math.abs(x(best.at) - px) ? point : best);
      focus.setAttribute('x1', x(nearest.at)); focus.setAttribute('x2', x(nearest.at)); focus.setAttribute('visibility', 'visible');
      tip.textContent = `${date(nearest.at)}\n好感度 ${nearest.score.toFixed(4)}\n${nearest.basis === 'recorded' ? '实测：当时计算结果' : '回算：使用当前规则'}`;
      tip.classList.remove('hidden'); tip.style.left = `${Math.max(0, Math.min(box.width - 220, event.clientX - box.left + 12))}px`; tip.style.top = '10px';
    };
    svg.onpointerleave = () => { tip.classList.add('hidden'); focus.setAttribute('visibility', 'hidden'); };
  }
}
function renderActivity() {
  if (!state.group.affinityAllowed) { $('activity-chart').replaceChildren(node('p', 'blank', '暂无可查看的好感度互动记录，可查看今日老婆关系。')); return; }
  const activity = new Map(state.group.activity.map(point => [Math.floor(point.at / 86400), (point.interactions || 0) + (point.games || 0)]));
  const lastDay = Math.floor(state.group.at / 86400), points = [];
  for (let day = lastDay - 59; day <= lastDay; day++) points.push({at: day * 86400, score: activity.get(day) || 0});
  drawLine($('activity-chart'), points, {height: 160, color: 'var(--rose)', scoreRange: false});
}
function renderCurve() { drawLine($('score-chart'), state.pair.curve, {fullScale: $('full-scale').checked, tooltip: true}); }

function renderLedger() {
  const pair = state.pair;
  $('ledger-count').textContent = `${number(pair.total)} 条变化 / 证据`;
  $('ledger').replaceChildren(...pair.ledger.map(entry => {
    const item = node('article', 'ledger-entry'), top = node('div', 'ledger-top');
    const delta = !entry.before && entry.after ? '建立记录' : !entry.after ? (entry.before ? '证据到期' : '未形成分数') : sign(entry.delta);
    top.append(node('span', 'ledger-kind', entry.label), node('time', 'ledger-time', date(entry.at)), node('span', 'ledger-basis', entry.basis === 'recorded' ? '实测' : '回算'), node('span', `ledger-delta ${entry.delta > 0 ? 'positive' : entry.delta < 0 ? 'negative' : 'muted'}`, delta));
    item.append(top, node('p', '', entry.explanation));
    const before = entry.before?.score, after = entry.after?.score;
    item.append(node('p', '', `${before == null ? '未建立记录' : before.toFixed(4)} → ${after == null ? '无数据' : after.toFixed(4)}`));
    const details = node('details'), summary = node('summary', '', '展开计算前后的分项'); details.append(summary);
    const table = node('table', 'detail-table');
    const head = node('tr'); for (const label of ['计分部分', '变化前', '变化后', '变化']) head.append(node('th', '', label)); table.append(head);
    const columns = [['base', '基础值'], ['familiarity', '熟悉积累'], ['friendliness', '情绪倾向'], ['game', '婚姻互动'], ['poke', '戳一戳'], ['manual_adjustment', '手动修正'], ['clamp', '分数上限修正']];
    const value = (snapshot, key) => {
      if (!snapshot) return 0;
      if (key === 'clamp') return snapshot.score - snapshot.base - snapshot.manual_adjustment -
        ['familiarity', 'friendliness', 'game', 'poke'].reduce((sum, part) => sum + (snapshot[part] || 0), 0) * snapshot.multiplier;
      return (snapshot[key] || 0) * (['base', 'manual_adjustment'].includes(key) ? 1 : (snapshot.multiplier || 1));
    };
    for (const [key, label] of columns) {
      const a = value(entry.before, key), b = value(entry.after, key);
      const row = node('tr'); for (const text of [label, a.toFixed(4), b.toFixed(4), sign(b - a)]) row.append(node('td', '', text)); table.append(row);
    }
    details.append(table, node('p', '', `倍率：${entry.before?.multiplier || '—'} → ${entry.after?.multiplier || '—'}；超过分数上限的部分不会继续增加。`)); item.append(details); return item;
  }));
  if (!pair.ledger.length) $('ledger').append(node('p', 'blank', '这个时间范围还没有变动记录。旧版未保存的逐次变化无法还原为实测账本。'));
  $('page-label').textContent = `${pair.page} / ${Math.max(1, Math.ceil(pair.total / pair.page_size))}`;
  $('previous').disabled = pair.page <= 1; $('next').disabled = pair.page * pair.page_size >= pair.total;
}

function renderGraph() {
  if (!state.group) return;
  const threshold = Number($('graph-min').value); $('graph-min-value').textContent = threshold;
  const edges = state.group.relations.filter(row => row.score >= threshold).map(row => ({...row, label: fmt(row.score)}));
  const shown = drawNetwork($('network'), edges, state.group.members, {
    selected: state.person, onNode: id => run(() => choosePerson(id)), onEdge: edge => run(() => choosePair(edge.source, edge.target)),
  });
  $('graph-note').textContent = `当前显示 ${shown.nodes} 人、${shown.edges} 个方向。滚轮或按钮缩放，拖动画布平移，双击昵称筛选成员。${state.group.truncated ? '好感度数据优先展示120个方向，可在左侧选择成员查看其关系。' : ''}`;
}

function renderMarriage() {
  if (!state.marriage) return;
  const report = state.marriage, totals = report.totals;
  const data = [['参与群友', totals.members], ['抽中方向', totals.drawn || 0], ['挑中方向', totals.picked || 0], ['强娶方向', totals.forced || 0], ['求婚成功方向', totals.proposed || 0]];
  $('marriage-summary').replaceChildren(...data.map(([label, count]) => {
    const card = node('div', 'summary-item'); card.append(node('p', '', label), node('strong', '', number(count))); return card;
  }));
  const colors = {drawn: '#1677ff', picked: '#d48806', forced: '#f5222d', proposed: '#722ed1', linked: '#595959'};
  const grouped = new Map();
  for (const edge of report.edges) {
    const key = JSON.stringify([edge.source, edge.target, edge.kind]);
    if (grouped.has(key)) grouped.get(key).count++;
    else grouped.set(key, {...edge, count: 1, color: colors[edge.kind]});
  }
  const edges = [...grouped.values()].map(edge => ({...edge, label: edge.label + (edge.count > 1 ? ` ×${edge.count}` : '')}));
  const shown = drawNetwork($('marriage-network'), edges, state.group.members, {selected: state.person, onNode: id => run(() => choosePerson(id))});
  $('marriage-graph-note').textContent = `已展示${state.person ? '该成员相关的' : '全部今日'} ${shown.nodes} 人、${shown.edges} 条连线。滚轮或按钮缩放，拖动画布平移，拖动昵称整理位置；双击昵称筛选成员，左侧“看全群的关系”恢复全图。`;
  $('marriage-note').textContent = `${report.date} · ${report.note}`;
  if (report.missing_names) $('marriage-note').textContent += ` 仍有${report.missing_names}位群友未获取昵称，可能已退群或接口未返回资料；补查最多每24小时一次。`;
  if (report.group_name_missing) $('marriage-note').textContent += ' 群名暂未获取，接口可能不可用或未开放群资料权限；24小时内不会重复补查。';
  $('marriage-count').textContent = `${number(report.edges.length)} 个关系方向`;
  $('marriage-records').replaceChildren(...report.edges.map(edge => {
    const row = node('tr'), kind = node('td'), tag = node('span', 'kind-tag', edge.label); tag.style.color = colors[edge.kind]; kind.append(tag);
    row.append(node('td', '', edge.source_name), kind, node('td', '', edge.target_name), node('td', '', edge.timestamp.replace('T', ' ').slice(0, 19))); return row;
  }));
  if (!report.edges.length) { const row = node('tr'), cell = node('td', 'blank', '今天还没有符合筛选条件的老婆关系，去群里抽取或求婚吧。'); cell.colSpan = 4; row.append(cell); $('marriage-records').append(row); }
}
function setTab(tab) {
  state.tab = tab;
  const administration = ['logs', 'config', 'data'].includes(tab);
  document.querySelector('.layout').classList.toggle('administration', administration);
  $('workspace').classList.toggle('hidden', administration || !state.scope);
  $('empty').classList.toggle('hidden', administration || Boolean(state.scope));
  if (administration) notice({logs: '仅显示今日老婆插件的运行日志，可按级别和内容筛选。', config: '修改当前插件配置，完成编辑后点击保存。', data: '查看本插件存储占用、SQLite数据库表和JSON业务文件。'}[tab]);
  document.querySelectorAll('[data-tab]').forEach(button => button.classList.toggle('active', button.dataset.tab === tab));
  for (const name of ['overview', 'marriage', 'detail', 'graph']) $(`${name}-panel`).classList.toggle('hidden', tab !== name);
  for (const name of ['logs', 'config', 'data']) $(`${name}-panel`).classList.toggle('hidden', tab !== name);
  if (tab === 'overview' && state.group) renderActivity();
  if (tab === 'detail' && state.pair) renderCurve();
  if (admin) run(() => admin.activate(tab));
}
$('refresh').onclick = () => run(() => ['logs', 'config', 'data'].includes(state.tab) ? admin.refresh() : loadMeta());
$('open-marriage').onclick = () => setTab('marriage');
$('group-select').onchange = () => run(async () => { state.scope = state.groups[Number($('group-select').value)].scope; state.person = state.source = state.target = ''; state.page = 1; state.pair = null; await loadGroup(); });
$('member-search').oninput = renderMembers;
$('all-members').onclick = () => run(() => choosePerson(''));
$('query-pair').onclick = () => run(async () => { const source = $('source-select').value, target = $('target-select').value; if (source === target) throw new Error('选两位不同的群友，才能查看彼此的关系哦。'); await choosePair(source, target); });
$('swap').onclick = () => run(() => choosePair($('target-select').value, $('source-select').value));
$('days-select').onchange = () => run(async () => { state.page = 1; if (state.source && state.target) await loadPair(); });
$('full-scale').onchange = () => { if (state.pair) renderCurve(); };
$('previous').onclick = () => run(async () => { state.page = Math.max(1, state.page - 1); await loadPair(); });
$('next').onclick = () => run(async () => { state.page++; await loadPair(); });
$('graph-min').oninput = renderGraph;
document.querySelectorAll('[data-tab]').forEach(button => button.onclick = () => setTab(button.dataset.tab));
const tabIcons = {
  overview: 'M3 12a9 9 0 1 1 18 0v7H3z M12 12l4-4 M6 15h1 M17 15h1 M11 5h2',
  marriage: 'M4 4h5v5H4z M15 15h5v5h-5z M4 15h5v5H4z M7 9v6 M9 7h8v8',
  detail: 'M5 3h14v18H5z M8 8h8 M8 12h8 M8 16h5',
  graph: 'M5 5h4v4H5z M15 4h4v4h-4z M10 16h4v4h-4z M9 7h6 M7 9l4 7 M17 8l-4 8',
  logs: 'M5 3h10l4 4v14H5z M15 3v5h4 M8 12h8 M8 16h6',
  config: 'M4 6h16 M4 12h16 M4 18h16 M8 3v6 M16 9v6 M10 15v6',
  data: 'M4 3h16v18H4z M4 9h16 M4 15h16 M8 6h1 M8 12h1 M8 18h1',
};
document.querySelectorAll('[data-tab]').forEach(button => {
  const icon = svgNode('svg', {viewBox: '0 0 24 24', width: 16, height: 16, 'aria-hidden': true});
  icon.append(svgNode('path', {d: tabIcons[button.dataset.tab], fill: 'none', stroke: 'currentColor', 'stroke-width': 1.6, 'stroke-linecap': 'round', 'stroke-linejoin': 'round'})); button.prepend(icon);
});
admin = initAdmin(bridge, {run, notice, onSaved: () => run(loadMeta)});
if (!bridge) notice('请从 AstrBot 管理面板 → 今日老婆插件 打开此页面。', true);
else run(async () => { const context = await bridge.ready(); theme(context); bridge.onContext(theme); await loadNetworkEngine(bridge); await loadMeta(); });
const timer = setInterval(() => { if ($('auto-refresh').checked && !document.hidden && !$('refresh').disabled && !['logs', 'config', 'data'].includes(state.tab)) run(loadMeta); }, 30000);
let resizeFrame;
window.addEventListener('resize', () => {
  cancelAnimationFrame(resizeFrame);
  resizeFrame = requestAnimationFrame(() => { if (state.group) renderActivity(); if (state.pair && state.tab === 'detail') renderCurve(); });
});
window.addEventListener('beforeunload', () => clearInterval(timer));
