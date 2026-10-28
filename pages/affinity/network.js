// Local SVG relationship diagrams: text nodes only, no avatar requests.
const element = (tag, attrs = {}, text = '') => {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  node.textContent = text; return node;
};

export function drawNetwork(container, candidates, members, {selected = '', onNode, onEdge} = {}) {
  container.replaceChildren();
  const degree = new Map();
  for (const edge of candidates) for (const id of [edge.source, edge.target]) degree.set(id, (degree.get(id) || 0) + 1);
  const ids = [...degree.keys()].sort((a, b) => degree.get(b) - degree.get(a)).slice(0, 40), included = new Set(ids);
  const edges = candidates.filter(edge => included.has(edge.source) && included.has(edge.target)).slice(0, 120);
  if (!ids.length) {
    const blank = document.createElement('p'); blank.className = 'blank'; blank.textContent = '暂无符合条件的关系，可切换群聊或成员查看。'; container.append(blank);
    return {nodes: 0, edges: 0};
  }
  const names = new Map(members.map(member => [member.id, member.name]));
  const svg = element('svg', {viewBox: '0 0 1000 550', role: 'img', 'aria-label': '群昵称与方向关系图'});
  const defs = element('defs'), colors = [...new Set(edges.map(edge => edge.color || '#1677ff'))];
  for (const [index, color] of colors.entries()) {
    const marker = element('marker', {id: `${container.id}-arrow-${index}`, viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: 'auto'});
    marker.append(element('path', {d: 'M 0 0 L 10 5 L 0 10 z', fill: color})); defs.append(marker);
  }
  svg.append(defs);
  const positions = new Map(ids.map((id, index) => {
    const angle = Math.PI * 2 * index / ids.length - Math.PI / 2;
    return [id, {x: 500 + Math.cos(angle) * 360, y: 275 + Math.sin(angle) * 210}];
  }));
  const paths = [], nodes = new Map();
  for (const edge of edges) {
    const color = edge.color || '#1677ff';
    const path = element('path', {class: 'edge', fill: 'none', stroke: color, 'stroke-width': 1.6,
      'marker-end': `url(#${container.id}-arrow-${colors.indexOf(color)})`});
    path.append(element('title', {}, `${edge.source_name} → ${edge.target_name}：${edge.label}`));
    if (onEdge) path.onclick = () => onEdge(edge);
    const label = element('text', {'text-anchor': 'middle', fill: color, 'font-size': 12, stroke: '#fff',
      'stroke-width': 5, 'stroke-linejoin': 'round', 'paint-order': 'stroke', 'pointer-events': 'none'}, edge.label);
    svg.append(path, label); paths.push({path, label, edge});
  }
  function redraw() {
    for (const {path, label, edge} of paths) {
      const a = positions.get(edge.source), b = positions.get(edge.target);
      const dx = b.x - a.x, dy = b.y - a.y, length = Math.hypot(dx, dy) || 1, ux = dx / length, uy = dy / length;
      const clearance = Math.min(68 / (Math.abs(ux) || .001), 23 / (Math.abs(uy) || .001));
      const cx = (a.x + b.x) / 2 - uy * 40, cy = (a.y + b.y) / 2 + ux * 40;
      path.setAttribute('d', `M ${a.x + ux * clearance} ${a.y + uy * clearance} Q ${cx} ${cy} ${b.x - ux * clearance} ${b.y - uy * clearance}`);
      label.setAttribute('x', (a.x + b.x) / 4 + cx / 2); label.setAttribute('y', (a.y + b.y) / 4 + cy / 2 - 5);
    }
    for (const [id, group] of nodes) { const p = positions.get(id); group.setAttribute('transform', `translate(${p.x} ${p.y})`); }
  }
  for (const id of ids) {
    const group = element('g', {class: 'node', 'data-user': id});
    group.append(element('rect', {x: -64, y: -19, width: 128, height: 38, rx: 5, fill: selected === id ? '#e6f4ff' : '#fff',
      stroke: selected === id ? '#1677ff' : '#bfbfbf'}));
    group.append(element('text', {'text-anchor': 'middle', dy: 4, fill: '#262626', 'font-size': 12}, Array.from(names.get(id) || id).slice(0, 10).join('')));
    group.append(element('title', {}, `${names.get(id) || id} · ${id}`));
    let start, moved;
    group.onpointerdown = event => { start = {x: event.clientX, y: event.clientY}; moved = false; group.setPointerCapture(event.pointerId); event.preventDefault(); };
    group.onpointermove = event => {
      if (!start || (!moved && Math.hypot(event.clientX - start.x, event.clientY - start.y) < 4)) return;
      moved = true;
      const p = new DOMPoint(event.clientX, event.clientY).matrixTransform(svg.getScreenCTM().inverse());
      positions.set(id, {x: Math.max(70, Math.min(930, p.x)), y: Math.max(25, Math.min(520, p.y))}); redraw();
    };
    group.onpointerup = () => { if (start && !moved && onNode) onNode(id); start = null; };
    group.onpointercancel = () => { start = null; }; nodes.set(id, group); svg.append(group);
  }
  redraw(); container.append(svg); return {nodes: ids.length, edges: edges.length};
}
