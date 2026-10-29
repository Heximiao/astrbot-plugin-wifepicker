// Same bundled engine as /关系图; no CDN or avatar requests.
const diagrams = new WeakMap();
let enginePromise;

export async function loadNetworkEngine(bridge) {
  if (window.vis?.Network) return;
  if (!enginePromise) enginePromise = (async () => {
    const {source} = await bridge.apiGet('affinity/network-script');
    if (typeof source !== 'string' || !source) throw new Error('关系图组件读取失败，请刷新重试。');
    // This is the plugin's fixed local bundle, delivered by its authenticated API.
    const script = document.createElement('script'); script.textContent = source;
    document.head.append(script); script.remove();
    if (!window.vis?.Network) throw new Error('关系图组件加载失败，请刷新重试。');
  })().catch(error => { enginePromise = null; throw error; });
  await enginePromise;
}

export function drawNetwork(container, candidates, members, {selected = '', onNode, onEdge} = {}) {
  diagrams.get(container)?.();
  diagrams.delete(container);
  container.replaceChildren();
  const degree = new Map(), names = new Map(members.map(member => [member.id, member.name]));
  for (const edge of candidates) {
    for (const id of [edge.source, edge.target]) degree.set(id, (degree.get(id) || 0) + 1);
    for (const [id, name] of [[edge.source, edge.source_name], [edge.target, edge.target_name]]) {
      if (name?.trim() && (!names.get(id)?.trim() || names.get(id) === id)) names.set(id, name);
    }
  }
  if (!degree.size) {
    const blank = document.createElement('p'); blank.className = 'blank';
    blank.textContent = '暂无符合条件的关系，可切换群聊或成员查看。'; container.append(blank);
    return {nodes: 0, edges: 0};
  }
  const shell = document.createElement('div'); shell.className = 'network-shell';
  const canvas = document.createElement('div'); canvas.className = 'network-canvas';
  canvas.setAttribute('aria-label', '群友关系图，可使用下方按钮缩放，拖动画布平移，双击昵称筛选成员');
  const controls = document.createElement('div'); controls.className = 'network-controls';
  controls.setAttribute('role', 'group'); controls.setAttribute('aria-label', '关系图操作');
  const status = document.createElement('span'); status.className = 'network-scale';
  status.setAttribute('aria-live', 'polite');
  shell.append(canvas, controls); container.append(shell);
  const colors = ['#2563eb', '#7c3aed', '#0891b2', '#db2777', '#0f766e'];
  const nodes = [...degree].map(([id, count], index) => ({
    id, label: names.get(id)?.trim() || id, shape: 'box',
    margin: {top: 9, bottom: 9, left: 13, right: 13}, borderWidth: selected === id ? 2 : 1,
    color: {background: selected === id ? '#dbeafe' : '#ffffff', border: selected === id ? '#2563eb' : '#dbe4f0',
      highlight: {background: '#eff6ff', border: '#2563eb'}, hover: {background: '#eff6ff', border: '#93b4f8'}},
    font: {face: 'Microsoft YaHei, sans-serif', size: 14 + Math.min(10, Math.log2(count + 1) * 2), color: colors[index % colors.length]},
    shadow: {enabled: true, color: 'rgba(30,64,175,0.06)', size: 8, x: 0, y: 2},
  }));
  const edges = candidates.map((edge, index) => ({
    id: index, from: edge.source, to: edge.target, label: edge.label,
    arrows: {to: {enabled: true, scaleFactor: .6}}, width: 1.3,
    color: {color: edge.color || '#8ba9e7', highlight: edge.color || '#2563eb', opacity: .75},
    font: {size: 11, color: edge.color || '#64748b', background: '#f8faff', strokeWidth: 0, align: 'middle'},
    smooth: {enabled: true, type: 'curvedCW', roundness: .12},
  }));
  const network = new window.vis.Network(canvas, {nodes, edges}, {
    layout: {randomSeed: 23, improvedLayout: false},
    nodes: {shapeProperties: {borderRadius: 10}},
    interaction: {hover: true, dragNodes: true, dragView: true, zoomView: true, hideEdgesOnDrag: candidates.length > 500},
    physics: {solver: 'barnesHut', barnesHut: {gravitationalConstant: -3200, centralGravity: .16,
      springLength: 155, springConstant: .035, damping: .4, avoidOverlap: 1},
      stabilization: {enabled: true, iterations: 250, updateInterval: 50}},
  });
  let disposed = false, fitted = false;
  const scale = () => { status.textContent = Math.round(network.getScale() * 100) + '%'; };
  const fit = () => { network.fit({animation: false}); scale(); };
  const button = (label, title, action) => {
    const control = document.createElement('button'); control.type = 'button'; control.textContent = label;
    control.title = title; control.setAttribute('aria-label', title); control.onclick = action;
    controls.append(control); return control;
  };
  const zoom = factor => { network.moveTo({scale: Math.max(.02, Math.min(8, network.getScale() * factor))}); scale(); };
  button('−', '缩小关系图', () => zoom(1 / 1.25)); controls.append(status);
  button('+', '放大关系图', () => zoom(1.25)); button('适应画布', '显示全部关系成员', fit);
  const expand = button('查看大图', '展开或收起关系图，Esc 退出大图', () => {
    shell.classList.toggle('expanded'); expand.textContent = shell.classList.contains('expanded') ? '收起大图' : '查看大图';
    network.redraw(); fit();
  });
  const escape = event => { if (event.key === 'Escape' && shell.classList.contains('expanded')) { expand.click(); expand.focus(); } };
  document.addEventListener('keydown', escape);
  network.on('zoom', scale);
  network.once('stabilizationIterationsDone', () => { network.setOptions({physics: false}); fit(); });
  network.on('doubleClick', event => { if (event.nodes.length && onNode) onNode(event.nodes[0]); });
  network.on('click', event => { if (!event.nodes.length && event.edges.length && onEdge) onEdge(candidates[event.edges[0]]); });
  // Hidden tabs have zero size. Fit once visible, then preserve user positions.
  const observer = new ResizeObserver(() => {
    if (disposed || !canvas.clientWidth || !canvas.clientHeight) return;
    network.redraw();
    if (!fitted) { fitted = true; fit(); }
  });
  observer.observe(canvas);
  diagrams.set(container, () => { disposed = true; observer.disconnect(); document.removeEventListener('keydown', escape); network.destroy(); });
  scale();
  return {nodes: nodes.length, edges: edges.length};
}
