const $ = id => document.getElementById(id);
const el = (tag, cls = '', text = '') => {const n = document.createElement(tag); n.className = cls; n.textContent = text; return n;};
const bytes = n => n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : n >= 1024 ? `${(n / 1024).toFixed(1)} KB` : `${n} B`;
const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const valueAt = (obj, path) => path.reduce((value, key) => value?.[key], obj);
const put = (obj, path, value) => {for (const key of path.slice(0, -1)) obj = obj[key] ??= {}; obj[path.at(-1)] = value;};
const labels = {database: ['SQLite 数据库', '求婚事件、互动证据、好感度账本及昵称缓存。'], json: ['JSON 业务记录', '今日老婆、活跃用户、冷却、群友资料和历史统计。'], model: ['小模型缓存', '已下载的模型权重与分词文件，可复用程序缓存。'], runtime: ['独立运行环境', '小模型使用的Python环境与CPU推理依赖。'], other: ['其他插件文件', '插件数据目录中的其他文件。']};

export function initAdmin(bridge, {run, notice, onSaved}) {
  let active = '', logCursor = 0, logRows = [], filterKey = '', configState, controls = [], storage;
  let storageTab = 'overview', source = '', pointer = '', dataPage = 1, loadVersion = 0;
  const api = (endpoint, params = {}) => bridge.apiGet(`affinity/admin/${endpoint}`, params);

  async function logs() {
    const level = $('logs-level').value, q = $('logs-search').value.trim();
    const key = JSON.stringify([level, q]);
    if (key !== filterKey) {logCursor = 0; logRows = []; filterKey = key;}
    const result = await api('logs', {level, q, after: logCursor});
    if (key !== JSON.stringify([$('logs-level').value, $('logs-search').value.trim()])) return;
    if (result.reset) logRows = [];
    logCursor = result.cursor;
    const merged = new Map(logRows.map(row => [row.id, row]));
    for (const row of result.rows) merged.set(row.id, row);
    logRows = [...merged.values()].slice(-500);
    $('logs-note').textContent = result.note;
    const list = $('log-list'), follow = list.scrollTop + list.clientHeight >= list.scrollHeight - 35;
    list.replaceChildren(...logRows.map(row => {
      const line = el('div', `log-row log-${row.level.toLowerCase()}`);
      line.append(el('time', '', new Date(row.at * 1000).toLocaleString('zh-CN', {hour12: false})), el('span', 'log-level', row.level), el('span', 'log-source', row.source), el('pre', '', row.message)); return line;
    }));
    if (!logRows.length) list.append(el('p', 'blank', '当前筛选条件下暂无本插件日志。'));
    if (follow) list.scrollTop = list.scrollHeight;
  }

  function field(key, schema, path, current) {
    const name = [...path, key], original = valueAt(current, name) ?? schema.default;
    const box = el('div', 'config-field'), label = el('label', '', schema.description || key);
    const id = 'setting-' + name.join('-'); label.htmlFor = id;
    let input;
    if (schema.type === 'bool') {input = el('input'); input.type = 'checkbox'; input.checked = Boolean(original);}
    else if (schema.options) {
      input = el('select'); schema.options.forEach((option, index) => {const n = el('option', '', schema.labels?.[index] || option); n.value = option; input.append(n);}); input.value = original ?? schema.options[0];
    } else if (schema.type === 'list') {input = el('textarea'); input.rows = 3; input.value = (original || []).join('\n'); input.placeholder = '每行一个账号或群号';}
    else {input = el('input'); input.type = ['int', 'float'].includes(schema.type) ? 'number' : 'text'; input.value = original ?? ''; if (input.type === 'number') {input.step = schema.type === 'int' ? '1' : 'any'; input.min = schema.slider?.min ?? 0; if (schema.slider?.max != null) input.max = schema.slider.max; input.required = true;}}
    input.id = id;
    const read = () => schema.type === 'bool' ? input.checked : schema.type === 'list' ? input.value.split(/[\n,，]/).map(s => s.trim()).filter(Boolean) : ['int', 'float'].includes(schema.type) ? Number(input.value) : input.value;
    controls.push({path: name, original: schema.type === 'list' ? (original || []).map(String) : original, input, read});
    box.append(label, input, el('p', 'field-hint', schema.hint || key)); return box;
  }

  async function config() {
    configState = await api('config'); controls = [];
    const root = $('config-fields'), basic = el('div', 'config-grid'); root.replaceChildren(basic);
    for (const [key, schema] of Object.entries(configState.schema)) {
      if (schema.type !== 'object') basic.append(field(key, schema, [], configState.config));
      else {
        const section = el('section', 'config-section'), grid = el('div', 'config-grid');
        section.append(el('h3', '', schema.description || key), el('p', 'field-hint', schema.hint || ''));
        for (const [child, childSchema] of Object.entries(schema.items || {})) grid.append(field(child, childSchema, [key], configState.config));
        section.append(grid); root.append(section);
      }
    }
    $('config-status').textContent = '已读取当前配置，修改后点击保存。';
  }

  $('config-form').onsubmit = event => {event.preventDefault(); run(async () => {
    if (!configState) return;
    const changes = {};
    for (const control of controls) {if (!control.input.reportValidity()) return; const value = control.read(); if (!equal(value, control.original)) put(changes, control.path, value);}
    if (!Object.keys(changes).length) {$('config-status').textContent = '配置没有变化。'; return;}
    $('config-save').disabled = true; controls.forEach(c => {c.input.disabled = true;});
    try {
      const result = await bridge.apiPost('affinity/admin/config/save', {revision: configState.revision, changes});
      await config(); if (onSaved) await onSaved(); $('config-status').textContent = result.message; notice(result.message);
    } finally {$('config-save').disabled = false; controls.forEach(c => {c.input.disabled = false;});}
  });};

  async function loadStorage() {
    storage = await api('storage');
    const cards = [['数据总占用', storage.bytes, storage.count], ...storage.partitions.map(p => [labels[p.key][0], p.bytes, p.files])];
    $('storage-metrics').replaceChildren(...cards.map(([name, size, count]) => {const card = el('div', 'metric'); card.append(el('p', 'metric-label', name), el('div', 'metric-value', bytes(size)), el('p', 'metric-detail', `${count} 个文件`)); return card;}));
    $('storage-note').textContent = storage.partial ? '最多扫描20000个文件，当前占用为已扫描部分；数据查看不会删除文件。' : '显示插件数据目录磁盘占用；数据查看不会删除记录或文件。';
    $('storage-partitions').replaceChildren(...storage.partitions.map(part => {
      const row = el('tr'), percentage = storage.bytes ? part.bytes / storage.bytes * 100 : 0;
      const share = el('td'), bar = el('span', 'space-bar'), fill = el('i'); fill.style.width = `${percentage}%`; bar.append(fill); share.append(bar, document.createTextNode(` ${percentage.toFixed(1)}%`));
      const action = el('td'), button = el('button', 'button secondary', ['database', 'json'].includes(part.key) ? '查看数据' : '查看说明');
      button.onclick = () => run(async () => {if (['database', 'json'].includes(part.key)) await selectStorage(part.key); else notice(`${labels[part.key][0]}：${labels[part.key][1]} 当前${part.files}个文件，${bytes(part.bytes)}。`);}); action.append(button);
      row.append(el('td', '', labels[part.key][0]), el('td', '', part.files), el('td', '', bytes(part.bytes)), share, el('td', '', labels[part.key][1]), action); return row;
    }));
    if (storageTab !== 'overview') await selectStorage(storageTab);
  }

  async function selectStorage(tab) {
    storageTab = tab;
    document.querySelectorAll('[data-storage]').forEach(b => b.classList.toggle('active', b.dataset.storage === tab));
    $('storage-overview').classList.toggle('hidden', tab !== 'overview'); $('storage-browser').classList.toggle('hidden', tab === 'overview');
    if (tab === 'overview') return;
    if (!storage) await loadStorage();
    const sources = tab === 'database' ? storage.tables : storage.files.filter(file => file.exists);
    if (!sources.some(item => item.name === source)) source = sources[0]?.name || '';
    $('data-sources').replaceChildren(...sources.map(item => {
      const button = el('button', `source-button${item.name === source ? ' active' : ''}`);
      button.append(el('strong', '', item.label), el('span', '', item.name), el('small', '', tab === 'database' ? `${item.rows} 行` : bytes(item.bytes)));
      button.onclick = () => run(async () => {source = item.name; pointer = ''; dataPage = 1; await selectStorage(tab);}); return button;
    }));
    if (!source) {$('data-content').replaceChildren(el('p', 'blank', '暂无数据源。')); $('data-pagination').classList.add('hidden'); return;}
    pointer = ''; dataPage = 1; await loadData();
  }

  async function loadData() {
    const version = ++loadVersion, tab = storageTab, current = source;
    const result = await api(tab === 'database' ? 'table' : 'json', tab === 'database' ? {table: source, page: dataPage} : {file: source, pointer, page: dataPage});
    if (version !== loadVersion || current !== source || tab !== storageTab) return;
    $('data-title').textContent = result.name;
    $('data-description').textContent = tab === 'database' ? `数据库表格 · ${result.total} 行 · 每页50行，可滚动查看所有字段。` : `JSON磁盘快照 · ${result.total} 项 · 点击对象或数组继续展开，内存中尚未写盘的变化可能暂未显示。`;
    $('json-breadcrumb').replaceChildren();
    const wrap = el('div', 'table-scroll'), table = el('table', 'records-table data-table'), head = el('thead'), header = el('tr'), body = el('tbody');
    const columns = tab === 'database' ? result.columns : ['字段 / 下标', '类型', '内容', '操作'];
    columns.forEach(column => header.append(el('th', '', column))); head.append(header); table.append(head, body);
    if (tab === 'json') {
      const root = el('button', 'button secondary', '根节点'), parent = el('button', 'button secondary', '返回上一级');
      root.onclick = () => run(async () => {pointer = ''; dataPage = 1; await loadData();});
      parent.disabled = !pointer; parent.onclick = () => run(async () => {pointer = pointer.slice(0, pointer.lastIndexOf('/')); dataPage = 1; await loadData();});
      $('json-breadcrumb').append(root, parent, el('code', 'json-path', pointer || '/'));
    }
    for (const item of result.rows) {
      const row = el('tr');
      if (tab === 'database') for (const key of columns) {
        const cell = el('td'), text = item[key] == null ? 'NULL' : typeof item[key] === 'object' ? JSON.stringify(item[key]) : String(item[key]);
        if (text.length > 100) {const details = el('details'); details.append(el('summary', '', text.slice(0, 80) + '…'), el('pre', '', text)); cell.append(details);} else cell.textContent = text;
        row.append(cell);
      } else {
        const action = el('td');
        if (item.branch) {const button = el('button', 'button secondary', '展开'); button.onclick = () => run(async () => {pointer = item.pointer; dataPage = 1; await loadData();}); action.append(button);}
        row.append(el('td', '', item.key), el('td', '', item.type), el('td', 'json-preview', item.preview), action);
      }
      body.append(row);
    }
    wrap.append(table); $('data-content').replaceChildren(wrap);
    if (!result.rows.length) $('data-content').append(el('p', 'blank', '这个数据源暂时为空。'));
    $('data-pagination').classList.remove('hidden'); $('data-page').textContent = `${result.page} / ${Math.max(1, Math.ceil(result.total / result.page_size))}`;
    $('data-prev').disabled = result.page <= 1; $('data-next').disabled = result.page * result.page_size >= result.total;
  }

  $('logs-refresh').onclick = () => run(logs); $('logs-level').onchange = () => run(logs);
  $('logs-search').onchange = () => run(logs); $('config-reload').onclick = () => run(config);
  $('storage-refresh').onclick = () => run(loadStorage);
  document.querySelectorAll('[data-storage]').forEach(b => b.onclick = () => run(() => selectStorage(b.dataset.storage)));
  $('data-prev').onclick = () => run(async () => {dataPage = Math.max(1, dataPage - 1); await loadData();});
  $('data-next').onclick = () => run(async () => {dataPage++; await loadData();});
  const timer = setInterval(() => {if (active === 'logs' && $('logs-live').checked && !document.hidden && bridge) run(logs);}, 3000);
  window.addEventListener('beforeunload', () => clearInterval(timer));
  return {async activate(tab) {active = tab; if (!bridge) return; if (tab === 'logs') await logs(); if (tab === 'config' && !configState) await config(); if (tab === 'data' && !storage) await loadStorage();},
    async refresh() {if (active === 'logs') await logs(); else if (active === 'config') notice('配置修改后请使用保存或重新读取按钮。'); else if (active === 'data') await loadStorage();}};
}
