// Pure layout logic for the code map viewer. Inlined into code-map.html by
// build.py and tested by tests/layout.test.cjs.
"use strict";

const repoOf = (id) => id.slice(0, id.indexOf("/"));
const groupOf = (kind) => (kind === "code" || kind === "import" ? kind : "protocol");
const always = () => true;
const sorted = (values) => [...values].sort();

// Neighbours of one capability: who uses it (inputs) and what it uses (outputs).
function focusColumns(id, edges, isVisible = always) {
  const visible = edges.filter(isVisible);
  return {
    inputs: peers(visible.filter((e) => e.to === id), "from"),
    outputs: peers(visible.filter((e) => e.from === id), "to"),
  };
}

function peers(edges, end) {
  const byPeer = new Map();
  for (const edge of edges) {
    const id = edge[end];
    if (!byPeer.has(id)) byPeer.set(id, { id, repo: repoOf(id), weight: 0, edges: [] });
    const peer = byPeer.get(id);
    peer.weight += edge.weight;
    peer.edges.push(edge);
  }
  const list = [...byPeer.values()].map((peer) => ({
    ...peer,
    kinds: sorted(new Set(peer.edges.map((e) => e.kind))),
    groups: sorted(new Set(peer.edges.map((e) => groupOf(e.kind)))),
  }));
  return list.sort((a, b) => a.repo.localeCompare(b.repo) || b.weight - a.weight || a.id.localeCompare(b.id));
}

// Cross-repo edges summed per repo pair and kind group.
function repoLinks(edges, isVisible = always) {
  const byKey = new Map();
  for (const edge of edges.filter(isVisible)) {
    const from = repoOf(edge.from), to = repoOf(edge.to), group = groupOf(edge.kind);
    if (from === to) continue;
    const key = `${from}>${to}>${group}`;
    if (!byKey.has(key)) byKey.set(key, { from, to, group, weight: 0, kinds: new Set() });
    const link = byKey.get(key);
    link.weight += edge.weight;
    link.kinds.add(edge.kind);
  }
  return [...byKey.keys()].sort().map((key) => ({ ...byKey.get(key), kinds: sorted(byKey.get(key).kinds) }));
}

// Which repos are linked at all, and which links to reverse so the rest form no
// cycle. ELK does the layering itself, but its own cycle breaking may cut the
// heavy direction of a cycle; ours cuts the light one.
function acyclic(nodes, links) {
  const successors = adjacency(nodes, links);
  const connected = nodes.filter((id) => successors.get(id).size || [...successors.values()].some((s) => s.has(id)));
  return { connected, isolated: nodes.filter((id) => !connected.includes(id)), reversed: backLinks(connected, successors) };
}

// from -> Map(to -> summed weight)
function adjacency(nodes, links) {
  const successors = new Map(nodes.map((id) => [id, new Map()]));
  for (const { from, to, weight = 1 } of links) {
    if (from !== to) successors.get(from).set(to, (successors.get(from).get(to) || 0) + weight);
  }
  return successors;
}

// Greedy feedback-arc-set order (Eades, Lin & Smyth): peel off sinks and
// sources; inside a cycle, place the node that uses most relative to how much
// it is used first. Links pointing backwards in this order get reversed.
function cycleFreeOrder(nodes, successors) {
  const graph = shrinkingGraph(nodes, successors);
  const left = [], right = [];
  while (graph.alive.size) {
    peelEnds(graph, left, right);
    if (!graph.alive.size) break;
    const pick = mostOutgoing(graph);
    left.push(pick);
    graph.remove(pick);
  }
  return [...left, ...right];
}

function shrinkingGraph(nodes, successors) {
  const out = new Map(nodes.map((id) => [id, new Map()]));
  const inc = new Map(nodes.map((id) => [id, new Map()]));
  for (const id of nodes) for (const [to, weight] of successors.get(id)) {
    out.get(id).set(to, weight);
    inc.get(to).set(id, weight);
  }
  const alive = new Set(nodes);
  const remove = (id) => {
    alive.delete(id);
    for (const to of out.get(id).keys()) inc.get(to).delete(id);
    for (const from of inc.get(id).keys()) out.get(from).delete(id);
  };
  return { out, inc, alive, remove };
}

// Sinks go to the right end, sources to the left, until neither is left.
function peelEnds(graph, left, right) {
  const isSink = (id) => !graph.out.get(id).size;
  const isSource = (id) => !graph.inc.get(id).size;
  while (peel(graph, isSink, (id) => right.unshift(id)) || peel(graph, isSource, (id) => left.push(id)));
}

function peel(graph, isEnd, place) {
  let peeled = false;
  for (const id of [...graph.alive]) {
    if (graph.alive.has(id) && isEnd(id)) {
      place(id);
      graph.remove(id);
      peeled = true;
    }
  }
  return peeled;
}

function mostOutgoing(graph) {
  const total = (weights) => [...weights.values()].reduce((sum, w) => sum + w, 0);
  const balance = (id) => total(graph.out.get(id)) - total(graph.inc.get(id));
  return [...graph.alive].reduce((best, id) => (balance(id) > balance(best) ? id : best));
}

function backLinks(nodes, successors) {
  const position = new Map(cycleFreeOrder(nodes, successors).map((id, index) => [id, index]));
  const reversed = new Set();
  for (const from of nodes) for (const to of successors.get(from).keys()) {
    if (position.get(from) > position.get(to)) reversed.add(`${from}>${to}`);
  }
  return reversed;
}

// --- wire routing ---------------------------------------------------------

const pushTo = (map, key, value) => (map.get(key) || map.set(key, []).get(key)).push(value);
const NORMALS = { left: [-1, 0], right: [1, 0], top: [0, -1], bottom: [0, 1] };
const centreOf = (r) => ({ x: r.x + r.w / 2, y: r.y + r.h / 2 });

function facingSide(rect, toward) {
  const c = centreOf(rect), dx = toward.x - c.x, dy = toward.y - c.y;
  if (Math.abs(dx) * rect.h > Math.abs(dy) * rect.w) return dx > 0 ? "right" : "left";
  return dy > 0 ? "bottom" : "top";
}

function portPoint(rect, side, t) {
  const [nx, ny] = NORMALS[side];
  return {
    x: nx ? rect.x + (nx > 0 ? rect.w : 0) : rect.x + rect.w * t,
    y: ny ? rect.y + (ny > 0 ? rect.h : 0) : rect.y + rect.h * t,
    nx, ny,
  };
}

// Route wires between cards. `rectOf(key)` gives each card's box. A wire may
// fix its sides and may share a `gutter` with other wires: gutter wires become
// S-curves (shape "curved") or orthogonal lanes (shape "orthogonal"). All other
// wires become smooth curves along the side normals.
function route(wires, rectOf, { shape = "orthogonal" } = {}) {
  const placed = wires.map((w) => {
    const a = rectOf(w.from), b = rectOf(w.to);
    return { w, a, b, fromSide: w.fromSide || facingSide(a, centreOf(b)), toSide: w.toSide || facingSide(b, centreOf(a)) };
  });
  allocatePorts(placed);
  const gutters = new Map();
  const routed = placed.map((p) => {
    const s = portPoint(p.a, p.fromSide, p.fromT), t = portPoint(p.b, p.toSide, p.toT);
    if (!p.w.gutter) return { ...p.w, d: curvePath(s, t) };
    if (shape === "curved") return { ...p.w, d: sCurvePath(s, t) };
    const wire = { ...p.w, s, t };
    pushTo(gutters, p.w.gutter, wire);
    return wire;
  });
  for (const list of gutters.values()) assignLanes(list);
  return routed;
}

// Wires that leave the same side of a card get their own port, ordered by
// where their other end is, so bundles fan out instead of piling up.
function allocatePorts(placed) {
  const ports = new Map();
  for (const p of placed) {
    pushTo(ports, `${p.w.from}|${p.fromSide}`, { p, end: "fromT", other: centreOf(p.b), side: p.fromSide });
    pushTo(ports, `${p.w.to}|${p.toSide}`, { p, end: "toT", other: centreOf(p.a), side: p.toSide });
  }
  for (const list of ports.values()) {
    const horizontal = list[0].side === "left" || list[0].side === "right";
    list.sort((m, n) => (horizontal ? m.other.y - n.other.y : m.other.x - n.other.x));
    list.forEach((port, index) => { port.p[port.end] = 0.15 + (0.7 * (index + 1)) / (list.length + 1); });
  }
}

// Leaves and enters along the side normals; the bend is 40% of the distance,
// kept between 28 and 180 pixels.
function curvePath(s, t) {
  const bend = Math.min(180, Math.max(28, Math.hypot(t.x - s.x, t.y - s.y) * 0.4));
  return `M${s.x},${s.y} C${s.x + s.nx * bend},${s.y + s.ny * bend} ${t.x + t.nx * bend},${t.y + t.ny * bend} ${t.x},${t.y}`;
}

// Leaves and enters horizontally with both control points halfway across the
// gutter. Wires sharing a gutter keep their vertical order all the way, so
// they fan out without crossing.
function sCurvePath(s, t) {
  const mid = (s.x + t.x) / 2;
  return `M${s.x},${s.y} C${mid},${s.y} ${mid},${t.y} ${t.x},${t.y}`;
}

// Orthogonal routing through one gutter: every wire gets its own vertical
// lane. Wires going down take lanes right-to-left from the top, wires going up
// left-to-right from the top, which keeps every pair of wires from crossing.
function assignLanes(wires) {
  const down = wires.filter((w) => w.t.y >= w.s.y).sort((a, b) => b.s.y - a.s.y);
  const up = wires.filter((w) => w.t.y < w.s.y).sort((a, b) => a.s.y - b.s.y);
  const ordered = [...down, ...up];
  const left = Math.max(...wires.map((w) => w.s.x)), right = Math.min(...wires.map((w) => w.t.x));
  ordered.forEach((w, index) => { w.d = elbowPath(w.s, w.t, left + ((right - left) * (index + 1)) / (ordered.length + 1)); });
}

function elbowPath(s, t, x) {
  const dy = t.y - s.y;
  if (Math.abs(dy) < 1) return `M${s.x},${s.y} H${t.x}`;
  const dir = Math.sign(dy), r = Math.max(0, Math.min(8, Math.abs(dy) / 2, x - s.x, t.x - x));
  return `M${s.x},${s.y} H${x - r} Q${x},${s.y} ${x},${s.y + dir * r} V${t.y - dir * r} Q${x},${t.y} ${x + r},${t.y} H${t.x}`;
}

// --- canvas layouts ------------------------------------------------------------

const primaryGroup = (groups) => ["protocol", "import", "code"].find((g) => groups.includes(g));

// Positions for the focus canvas: callers left, the capability in the middle,
// callees right, each side grouped under repo labels and centred vertically.
function focusCanvas({ inputs, outputs }, size) {
  const centreX = size.peer.w + size.gap;
  const left = stackPeers(inputs, "in", 0, size), right = stackPeers(outputs, "out", centreX + size.centre.w + size.gap, size);
  const tallest = Math.max(left.height, right.height);
  const shift = (column) => column.nodes.map((n) => ({ ...n, y: n.y + (tallest - column.height) / 2 }));
  const centre = { id: "centre", type: "centre", x: centreX, y: Math.max(0, (tallest - size.centre.h) / 2), w: size.centre.w, h: size.centre.h, data: {} };
  const wire = (peer, from, to, gutter) => ({ key: `${gutter}:${peer.id}`, from, to, fromSide: "right", toSide: "left", gutter,
    group: primaryGroup(peer.groups), weight: peer.weight, title: `${peer.id}: ${peer.kinds.join(", ")} ×${peer.weight}` });
  return {
    nodes: [...shift(left), centre, ...shift(right)],
    wires: [...inputs.map((p) => wire(p, `in:${p.id}`, "centre", "in")), ...outputs.map((p) => wire(p, "centre", `out:${p.id}`, "out"))],
  };
}

function stackPeers(peers, side, x, size) {
  const nodes = [];
  let y = 0;
  const byRepo = new Map();
  for (const peer of peers) pushTo(byRepo, peer.repo, peer);
  for (const [repo, list] of byRepo) {
    nodes.push({ id: `label:${side}:${repo}`, type: "label", x, y, w: size.peer.w, h: size.label, data: { text: repo } });
    y += size.label;
    for (const peer of list) {
      nodes.push({ id: `${side}:${peer.id}`, type: "peer", x, y, w: size.peer.w, h: size.peer.h, data: { peer, side } });
      y += size.peer.h + size.row;
    }
    y += size.group;
  }
  return { nodes, height: nodes.length ? y - size.row - size.group : 0 };
}

// --- ELK repo layout ----------------------------------------------------------

const ELK_OPTIONS = {
  "elk.algorithm": "layered",
  "elk.direction": "RIGHT",
  "elk.layered.layering.strategy": "LONGEST_PATH", // repos that use nothing share the last column
  "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF", // line cards up with their neighbours
  "elk.edgeRouting": "SPLINES", // route links around cards, not behind them
};
const repoLinkKey = (link) => `${link.from}>${link.to}>${link.group}`;

// The ELK input for the repo canvas. Back links go to ELK pointing forward so it
// sees our cycle cut; `repoCanvas` draws them the right way round again.
function elkRepoGraph(names, links, size) {
  const { connected, isolated, reversed } = acyclic(names, links);
  const graph = {
    id: "repos",
    layoutOptions: { ...ELK_OPTIONS, "elk.layered.spacing.nodeNodeBetweenLayers": String(size.repoGap), "elk.spacing.nodeNode": String(size.repoRow) },
    children: connected.map((id) => ({ id, width: size.repo.w, height: size.repo.h })),
    edges: links.map((l) => {
      const back = reversed.has(`${l.from}>${l.to}`);
      return { id: repoLinkKey(l), sources: [back ? l.to : l.from], targets: [back ? l.from : l.to] };
    }),
  };
  return { graph, isolated, reversed };
}

// Canvas nodes and wires from ELK's result; unlinked repos go in a row below.
function repoCanvas(elk, links, { isolated, reversed }, size) {
  const { w, h } = size.repo;
  const nodes = (elk.children || []).map((n) => ({ id: `repo:${n.id}`, type: "repo", x: n.x, y: n.y, w: n.width, h: n.height, data: { name: n.id } }));
  if (isolated.length) {
    const left = Math.min(...nodes.map((n) => n.x), Infinity);
    const x0 = left === Infinity ? 0 : left;
    const top = Math.max(0, ...nodes.map((n) => n.y + n.h)) + size.isolatedGap;
    nodes.push({ id: "label:isolated", type: "label", x: x0, y: top, w, h: size.label, data: { text: "No cross-repo links" } });
    isolated.forEach((name, k) => nodes.push({ id: `repo:${name}`, type: "repo", x: x0 + k * (w + size.repoRow), y: top + size.label, w, h, data: { name } }));
  }
  const sections = new Map((elk.edges || []).map((e) => [e.id, e.sections && e.sections[0]]));
  const wires = links.map((l) => {
    const section = sections.get(repoLinkKey(l));
    return { key: repoLinkKey(l), from: `repo:${l.from}`, to: `repo:${l.to}`, group: l.group, weight: l.weight,
      d: section ? splinePath(section, reversed.has(`${l.from}>${l.to}`)) : undefined,
      title: `${l.from} → ${l.to}: ${l.kinds.join(", ")} ×${l.weight}` };
  });
  return { nodes, wires };
}

// ELK spline sections are chains of cubic Béziers (start, then three points per
// segment); anything else is drawn as straight lines.
function splinePath({ startPoint, bendPoints = [], endPoint }, reverse = false) {
  const points = [startPoint, ...bendPoints, endPoint];
  if (reverse) points.reverse();
  const [first, ...rest] = points;
  const at = (p) => `${p.x},${p.y}`;
  if (rest.length % 3) return `M${at(first)} ${rest.map((p) => `L${at(p)}`).join(" ")}`;
  const segments = [];
  for (let i = 0; i < rest.length; i += 3) segments.push(`C${rest.slice(i, i + 3).map(at).join(" ")}`);
  return `M${at(first)} ${segments.join(" ")}`;
}

// Keep each precomputed wire (`d`) while both its cards stay put; route the rest.
function reroute(wires, rectOf, moved, options) {
  const keep = (w) => w.d && !moved.has(w.from) && !moved.has(w.to);
  const routed = new Map(route(wires.filter((w) => !keep(w)), rectOf, options).map((w) => [w.key, w]));
  return wires.map((w) => (keep(w) ? w : routed.get(w.key)));
}

const CodeMapLayout = {
  focusColumns, repoLinks, acyclic, route, reroute, curvePath, elbowPath, splinePath, focusCanvas, elkRepoGraph, repoCanvas,
  primaryGroup, repoOf, groupOf,
};
if (typeof module === "object" && module.exports) module.exports = CodeMapLayout;
else window.CodeMapLayout = CodeMapLayout;
