// Run: node --preserve-symlinks --preserve-symlinks-main --test tests/layout.test.cjs
"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");

const layout = require(path.join(process.cwd(), "viewer/layout.js"));

const edge = (from, to, kind = "import", weight = 1) => ({ from, to, kind, weight, examples: [`${from}->${to}`] });

test("focus columns put callers left and callees right, grouped per peer", () => {
  const edges = [
    edge("app/ui", "lib/core", "import", 2),
    edge("app/ui", "lib/core", "code", 3),
    edge("cli/main", "lib/core", "import", 1),
    edge("lib/core", "db/store", "sql", 4),
    edge("lib/core", "app/ui", "rpc", 1),
    edge("other/x", "other/y"),
  ];
  const { inputs, outputs } = layout.focusColumns("lib/core", edges);
  assert.deepEqual(inputs.map((p) => [p.id, p.repo, p.weight, p.kinds]), [
    ["app/ui", "app", 5, ["code", "import"]],
    ["cli/main", "cli", 1, ["import"]],
  ]);
  assert.deepEqual(outputs.map((p) => [p.id, p.weight, p.groups]), [
    ["app/ui", 1, ["protocol"]],
    ["db/store", 4, ["protocol"]],
  ]);
  assert.equal(inputs[0].edges.length, 2);
});

test("focus columns sort peers of one repo by weight and honour the visibility filter", () => {
  const edges = [edge("a/light", "t/x", "import", 1), edge("a/heavy", "t/x", "import", 9), edge("b/code", "t/x", "code", 5),
    edge("a/zed", "t/x", "import", 1), edge("a/alpha", "t/x", "import", 1)];
  const visible = layout.focusColumns("t/x", edges, (e) => e.kind !== "code");
  assert.deepEqual(visible.inputs.map((p) => p.id), ["a/heavy", "a/alpha", "a/light", "a/zed"]);
  assert.deepEqual(visible.outputs, []);
});

test("repo links sum cross-repo edges per repo pair and kind group", () => {
  const links = layout.repoLinks([
    edge("app/a", "lib/x", "import", 2),
    edge("app/b", "lib/y", "import", 3),
    edge("app/a", "lib/x", "rpc", 1),
    edge("app/a", "app/b", "code", 7),
    edge("lib/x", "app/a", "graphql", 1),
  ]);
  assert.deepEqual(links.map((l) => [l.from, l.to, l.group, l.weight, l.kinds]), [
    ["app", "lib", "import", 5, ["import"]],
    ["app", "lib", "protocol", 1, ["rpc"]],
    ["lib", "app", "protocol", 1, ["graphql"]],
  ]);
  assert.deepEqual(layout.repoLinks([edge("app/a", "lib/x")], () => false), []);
});

// --- wire routing ------------------------------------------------------------
const rects = {
  left0: { x: 0, y: 0, w: 100, h: 40 }, left1: { x: 0, y: 100, w: 100, h: 40 }, left2: { x: 0, y: 200, w: 100, h: 40 },
  centre: { x: 200, y: 80, w: 100, h: 100 }, right0: { x: 400, y: 0, w: 100, h: 40 },
};
const rectOf = (key) => rects[key];
const numbers = (d) => d.match(/-?\d+(\.\d+)?/g).map(Number);

test("wires leaving one side of a card get separate ports ordered by where they go", () => {
  const wires = layout.route([
    { from: "centre", to: "left2", fromSide: "left", toSide: "right" },
    { from: "centre", to: "left0", fromSide: "left", toSide: "right" },
  ], rectOf);
  const startY = (w) => numbers(w.d)[1];
  assert.ok(startY(wires[1]) < startY(wires[0]), "the wire to the upper card leaves higher");
  assert.equal(wires[0].d.startsWith("M200,"), true);
  assert.equal(wires[0].from, "centre");
});

test("unconstrained wires pick the sides that face each other", () => {
  const [wire] = layout.route([{ from: "left1", to: "right0" }], rectOf);
  assert.deepEqual(numbers(wire.d).slice(0, 2), [100, 120]);
  assert.deepEqual(numbers(wire.d).slice(-2), [400, 20]);
  const [down] = layout.route([{ from: "left0", to: "left2" }], rectOf);
  assert.deepEqual(numbers(down.d).slice(0, 2), [50, 40]);
  assert.deepEqual(numbers(down.d).slice(-2), [50, 200]);
});

function laneSegments(d) {
  // M x1,y1 H a Q ... V b Q ... H x2  ->  horizontal at y1, vertical at lane x, horizontal at y2
  const [x1, y1] = numbers(d.match(/^M[^H]*/)[0]);
  const laneX = numbers(d.match(/Q(-?[\d.]+),/)[0])[0];
  const end = numbers(d.match(/H[^H]*$/)[0])[0];
  const y2 = numbers(d.match(/Q[^Q]*Q(-?[\d.]+),(-?[\d.]+)/)[0]).slice(-1)[0];
  return { x1, y1, laneX, x2: end, y2 };
}

function orthogonalCrossings(paths) {
  const segs = paths.map(laneSegments);
  let count = 0;
  for (const a of segs) for (const b of segs) {
    if (a === b) continue;
    const between = (v, p, q) => v > Math.min(p, q) && v < Math.max(p, q);
    if (between(b.laneX, a.x1, a.laneX) && between(a.y1, b.y1, b.y2)) count += 1;
    if (between(b.laneX, a.laneX, a.x2) && between(a.y2, b.y1, b.y2)) count += 1;
  }
  return count;
}

test("wires through a gutter get their own lanes and never cross", () => {
  const wires = layout.route(["left0", "left1", "left2"].map((from) => ({ from, to: "centre", fromSide: "right", toSide: "left", gutter: "in" })), rectOf);
  const lanes = wires.map((w) => laneSegments(w.d).laneX);
  assert.equal(new Set(lanes).size, 3);
  lanes.forEach((x) => assert.ok(x > 100 && x < 200));
  assert.equal(orthogonalCrossings(wires.map((w) => w.d)), 0);
});

test("wires going up and down through one gutter do not cross either", () => {
  const tall = { ...rects, centre: { x: 200, y: 0, w: 100, h: 240 } };
  const keys = ["a", "b", "c", "d", "e"];
  keys.forEach((key, i) => { tall[key] = { x: 0, y: i * 60, w: 100, h: 40 }; });
  const wires = layout.route(keys.map((from) => ({ from, to: "centre", fromSide: "right", toSide: "left", gutter: "in" })), (key) => tall[key]);
  assert.equal(orthogonalCrossings(wires.map((w) => w.d)), 0);
});

test("a wire level with its port is a straight line", () => {
  assert.equal(layout.elbowPath({ x: 0, y: 50 }, { x: 80, y: 50.5 }, 40), "M0,50 H80");
  assert.equal(layout.elbowPath({ x: 0, y: 0 }, { x: 80, y: 100 }, 40), "M0,0 H32 Q40,0 40,8 V92 Q40,100 48,100 H80");
  assert.equal(layout.elbowPath({ x: 0, y: 100 }, { x: 80, y: 96 }, 40), "M0,100 H38 Q40,100 40,98 V98 Q40,96 42,96 H80");
  assert.equal(layout.elbowPath({ x: 0, y: 0 }, { x: 80, y: 1 }, 40), "M0,0 H39.5 Q40,0 40,0.5 V0.5 Q40,1 40.5,1 H80");
});

test("elbow corners shrink to fit a lane close to either card", () => {
  assert.equal(layout.elbowPath({ x: 10, y: 0 }, { x: 80, y: 100 }, 76), "M10,0 H72 Q76,0 76,4 V96 Q76,100 80,100 H80");
  assert.equal(layout.elbowPath({ x: 70, y: 0 }, { x: 200, y: 100 }, 74), "M70,0 H70 Q74,0 74,4 V96 Q74,100 78,100 H200");
  assert.equal(layout.elbowPath({ x: 40, y: 0 }, { x: 80, y: 100 }, 40), "M40,0 H40 Q40,0 40,0 V100 Q40,100 40,100 H80");
});

test("ports follow the centres of the cards the wires go to, on both axes", () => {
  const boxes = {
    hub: { x: 200, y: 0, w: 100, h: 400 }, t1: { x: 0, y: 0, w: 10, h: 100 }, t2: { x: 0, y: 10, w: 10, h: 140 }, t3: { x: 0, y: 55, w: 10, h: 10 },
    hub2: { x: 0, y: 200, w: 400, h: 100 }, u1: { x: 0, y: 0, w: 100, h: 10 }, u2: { x: 10, y: 0, w: 140, h: 10 }, u3: { x: 55, y: 0, w: 10, h: 10 },
  };
  const ys = layout.route(["t1", "t2", "t3"].map((to) => ({ from: "hub", to, fromSide: "left", toSide: "right" })), (k) => boxes[k])
    .map((w) => numbers(w.d)[1]);
  assert.ok(ys[0] < ys[2] && ys[2] < ys[1], `ports top to bottom go t1, t3, t2: ${ys}`);
  const xs = layout.route(["u1", "u2", "u3"].map((to) => ({ from: "hub2", to, fromSide: "top", toSide: "bottom" })), (k) => boxes[k])
    .map((w) => numbers(w.d)[0]);
  assert.ok(xs[0] < xs[2] && xs[2] < xs[1], `ports left to right go u1, u3, u2: ${xs}`);
});

test("curves bend 40% of their length, at least 28 and at most 180 pixels", () => {
  assert.equal(layout.curvePath({ x: 100, y: 5, nx: 1, ny: 0 }, { x: 130, y: 5, nx: -1, ny: 0 }), "M100,5 C128,5 102,5 130,5");
  assert.equal(layout.curvePath({ x: 100, y: 5, nx: 1, ny: 0 }, { x: 1100, y: 5, nx: -1, ny: 0 }), "M100,5 C280,5 920,5 1100,5");
  assert.equal(layout.curvePath({ x: 5, y: 100, nx: 0, ny: 1 }, { x: 5, y: 200, nx: 0, ny: -1 }), "M5,100 C5,140 5,160 5,200");
});

test("a card facing another exactly diagonally connects vertically, and half a pixel still picks a side", () => {
  const boxes = { a: { x: 0, y: 0, w: 100, h: 100 }, b: { x: 200, y: 200, w: 100, h: 100 },
    p: { x: 0, y: 0, w: 10, h: 10 }, right: { x: 0.5, y: 0, w: 10, h: 10 }, below: { x: 0, y: 0.5, w: 10, h: 10 } };
  const ends = (from, to) => { const n = numbers(layout.route([{ from, to }], (k) => boxes[k])[0].d); return [n.slice(0, 2), n.slice(-2)]; };
  assert.deepEqual(ends("a", "b"), [[50, 100], [250, 200]]);
  assert.deepEqual(ends("p", "right")[0], [10, 5]);
  assert.deepEqual(ends("p", "below")[0], [5, 10]);
});

test("a crowded gutter still has no crossings", () => {
  const boxes = { hub: { x: 300, y: 150, w: 100, h: 200 } };
  const keys = [4, 0, 7, 2, 8, 1, 5, 3, 6].map((i) => `s${i}`);
  keys.forEach((key) => { boxes[key] = { x: 0, y: Number(key.slice(1)) * 60, w: 100, h: 40 }; });
  const wires = layout.route(keys.map((from) => ({ from, to: "hub", fromSide: "right", toSide: "left", gutter: "g" })), (k) => boxes[k]);
  assert.equal(orthogonalCrossings(wires.map((w) => w.d)), 0);
  const outputs = layout.route(keys.map((to) => ({ from: "hub", to, fromSide: "right", toSide: "left", gutter: "g" })),
    (k) => ({ ...boxes[k], x: k === "hub" ? 0 : 300 }));
  assert.equal(orthogonalCrossings(outputs.map((w) => w.d)), 0);
});

// --- canvas layouts ----------------------------------------------------------
const SIZE = { peer: { w: 200, h: 70 }, centre: { w: 300, h: 200 }, label: 20, row: 10, group: 16, gap: 150,
  repo: { w: 180, h: 60 }, repoGap: 100, repoRow: 14, isolatedGap: 80 };

test("the primary link group prefers protocol, then import, then code", () => {
  assert.equal(layout.primaryGroup(["code", "protocol"]), "protocol");
  assert.equal(layout.primaryGroup(["code", "import"]), "import");
  assert.equal(layout.primaryGroup(["code"]), "code");
});

test("focus canvas stacks callers left and callees right under repo labels, centre in the middle", () => {
  const peer = (id, groups = ["import"], weight = 1) => ({ id, repo: layout.repoOf(id), weight, groups, kinds: groups });
  const canvas = layout.focusCanvas({ inputs: [peer("a/x"), peer("a/y"), peer("b/z", ["code"], 4)], outputs: [peer("c/w", ["protocol"])] }, SIZE);
  const at = Object.fromEntries(canvas.nodes.map((n) => [n.id, [n.type, n.x, n.y, n.w, n.h]]));
  assert.deepEqual(at["label:in:a"], ["label", 0, 0, 200, 20]);
  assert.deepEqual(at["in:a/x"], ["peer", 0, 20, 200, 70]);
  assert.deepEqual(at["in:a/y"], ["peer", 0, 100, 200, 70]);
  assert.deepEqual(at["label:in:b"], ["label", 0, 196, 200, 20]);
  assert.deepEqual(at["in:b/z"], ["peer", 0, 216, 200, 70]);
  // left column is 286 tall: the centre (200) and the right column (90) are centred on it
  assert.deepEqual(at.centre, ["centre", 350, 43, 300, 200]);
  assert.deepEqual(at["label:out:c"], ["label", 800, 98, 200, 20]);
  assert.deepEqual(at["out:c/w"], ["peer", 800, 118, 200, 70]);
  assert.deepEqual(canvas.nodes.find((n) => n.id === "in:b/z").data.peer.weight, 4);
  assert.deepEqual(canvas.wires.map((w) => [w.key, w.from, w.to, w.fromSide, w.toSide, w.gutter, w.group, w.weight]), [
    ["in:a/x", "in:a/x", "centre", "right", "left", "in", "import", 1],
    ["in:a/y", "in:a/y", "centre", "right", "left", "in", "import", 1],
    ["in:b/z", "in:b/z", "centre", "right", "left", "in", "code", 4],
    ["out:c/w", "centre", "out:c/w", "right", "left", "out", "protocol", 1],
  ]);
});

test("focus canvas with nothing on either side still places the centre", () => {
  const canvas = layout.focusCanvas({ inputs: [], outputs: [] }, SIZE);
  assert.deepEqual(canvas.nodes.map((n) => [n.id, n.x, n.y]), [["centre", 350, 0]]);
  assert.deepEqual(canvas.wires, []);
});

test("curved gutter wires are S-curves that keep their order, so they never cross", () => {
  const boxes = { hub: { x: 300, y: 150, w: 100, h: 200 } };
  const keys = [4, 0, 7, 2, 8, 1, 5, 3, 6].map((i) => `s${i}`);
  keys.forEach((key) => { boxes[key] = { x: 0, y: Number(key.slice(1)) * 60, w: 100, h: 40 }; });
  const wires = layout.route(keys.map((from) => ({ from, to: "hub", fromSide: "right", toSide: "left", gutter: "g" })), (k) => boxes[k], { shape: "curved" });
  for (const w of wires) {
    const [sx, sy, c1x, c1y, c2x, c2y, tx, ty] = numbers(w.d);
    assert.ok(w.d.startsWith("M") && w.d.includes(" C"), w.d);
    assert.deepEqual([c1x, c1y, c2x, c2y], [(sx + tx) / 2, sy, (sx + tx) / 2, ty]);
  }
  const ends = wires.map((w) => { const n = numbers(w.d); return [n[1], n[7]]; }).sort((a, b) => a[0] - b[0]);
  ends.forEach((pair, i) => { if (i) assert.ok(pair[1] > ends[i - 1][1], "port order follows source order"); });
});


// --- cycle cutting and the ELK repo layout --------------------------------------
test("acyclic keeps unlinked repos apart and ignores self links", () => {
  const result = layout.acyclic(["a", "b", "c", "lonely"], [{ from: "a", to: "b" }, { from: "b", to: "c" }, { from: "c", to: "c" }]);
  assert.deepEqual(result.connected, ["a", "b", "c"]);
  assert.deepEqual(result.isolated, ["lonely"]);
  assert.deepEqual([...result.reversed], []);
  assert.deepEqual(layout.acyclic([], []), { connected: [], isolated: [], reversed: new Set() });
});

test("cycles are cut at the lighter link so the heavy direction keeps callers left", () => {
  const result = layout.acyclic(["b", "a"], [{ from: "a", to: "b", weight: 9 }, { from: "b", to: "a", weight: 1 }]);
  assert.deepEqual([...result.reversed], ["b>a"]);
});

test("unweighted links count once each when deciding where to cut a cycle", () => {
  const result = layout.acyclic(["b", "a"], [{ from: "a", to: "b" }, { from: "a", to: "b" }, { from: "b", to: "a" }]);
  assert.deepEqual([...result.reversed], ["b>a"]);
});

test("sinks keep being peeled off before a cycle is cut", () => {
  const result = layout.acyclic(["m", "s", "x", "y"], [
    { from: "x", to: "y" }, { from: "y", to: "x" }, { from: "y", to: "m" }, { from: "m", to: "s" },
  ]);
  assert.deepEqual([...result.reversed], ["y>x"]);
});

const repoLink = (from, to, group = "import", weight = 1, kinds = [group]) => ({ from, to, group, weight, kinds });

test("the ELK graph hands back links to ELK pointing forward and leaves unlinked repos out", () => {
  const links = [repoLink("app", "lib", "import", 3), repoLink("lib", "app", "protocol", 1, ["rpc"]), repoLink("app", "lib", "protocol", 1, ["rpc"])];
  const { graph, isolated, reversed } = layout.elkRepoGraph(["app", "lib", "lone"], links, SIZE);
  assert.deepEqual(isolated, ["lone"]);
  assert.deepEqual([...reversed], ["lib>app"]);
  assert.deepEqual(graph.children, [{ id: "app", width: 180, height: 60 }, { id: "lib", width: 180, height: 60 }]);
  assert.deepEqual(graph.edges, [
    { id: "app>lib>import", sources: ["app"], targets: ["lib"] },
    { id: "lib>app>protocol", sources: ["app"], targets: ["lib"] },
    { id: "app>lib>protocol", sources: ["app"], targets: ["lib"] },
  ]);
  assert.equal(graph.layoutOptions["elk.algorithm"], "layered");
  assert.equal(graph.layoutOptions["elk.layered.layering.strategy"], "LONGEST_PATH");
  assert.equal(graph.layoutOptions["elk.edgeRouting"], "SPLINES");
  assert.equal(graph.layoutOptions["elk.layered.spacing.nodeNodeBetweenLayers"], "100");
  assert.equal(graph.layoutOptions["elk.spacing.nodeNode"], "14");
});

test("spline sections become cubic chains, straight sections lines, and back links run backwards", () => {
  const section = { startPoint: { x: 0, y: 0 }, bendPoints: [{ x: 10, y: 0 }, { x: 20, y: 5 }], endPoint: { x: 30, y: 5 } };
  assert.equal(layout.splinePath(section), "M0,0 C10,0 20,5 30,5");
  assert.equal(layout.splinePath(section, true), "M30,5 C20,5 10,0 0,0");
  assert.equal(layout.splinePath({ startPoint: { x: 0, y: 0 }, endPoint: { x: 9, y: 9 } }), "M0,0 L9,9");
  assert.equal(layout.splinePath({ startPoint: { x: 0, y: 0 }, bendPoints: [{ x: 4, y: 4 }], endPoint: { x: 9, y: 9 } }), "M0,0 L4,4 L9,9");
  const long = { startPoint: { x: 0, y: 0 }, bendPoints: [1, 2, 3, 4, 5].map((v) => ({ x: v, y: v })), endPoint: { x: 6, y: 6 } };
  assert.equal(layout.splinePath(long), "M0,0 C1,1 2,2 3,3 C4,4 5,5 6,6");
});

test("the repo canvas takes ELK positions and wires, with unlinked repos in a row below", () => {
  const links = [repoLink("app", "lib", "import", 3), repoLink("lib", "app", "protocol", 1, ["rpc"])];
  const info = { isolated: ["e", "f"], reversed: new Set(["lib>app"]) };
  const elk = {
    children: [{ id: "app", x: 12, y: 40, width: 180, height: 60 }, { id: "lib", x: 300, y: 12, width: 180, height: 60 }],
    edges: [
      { id: "app>lib>import", sections: [{ startPoint: { x: 192, y: 70 }, endPoint: { x: 300, y: 42 } }] },
      { id: "lib>app>protocol", sections: [{ startPoint: { x: 192, y: 80 }, endPoint: { x: 300, y: 52 } }] },
    ],
  };
  const canvas = layout.repoCanvas(elk, links, info, SIZE);
  assert.deepEqual(canvas.nodes.map((n) => [n.id, n.type, n.x, n.y, n.w, n.h]), [
    ["repo:app", "repo", 12, 40, 180, 60], ["repo:lib", "repo", 300, 12, 180, 60],
    ["label:isolated", "label", 12, 180, 180, 20], ["repo:e", "repo", 12, 200, 180, 60], ["repo:f", "repo", 206, 200, 180, 60],
  ]);
  assert.deepEqual(canvas.wires.map((w) => [w.key, w.from, w.to, w.group, w.weight, w.d, w.title]), [
    ["app>lib>import", "repo:app", "repo:lib", "import", 3, "M192,70 L300,42", "app → lib: import ×3"],
    ["lib>app>protocol", "repo:lib", "repo:app", "protocol", 1, "M300,52 L192,80", "lib → app: rpc ×1"],
  ]);
});

test("a repo canvas without ELK sections or linked repos still lays out", () => {
  const canvas = layout.repoCanvas({}, [repoLink("a", "b")], { isolated: ["e"], reversed: new Set() }, SIZE);
  assert.deepEqual(canvas.nodes.map((n) => [n.id, n.x, n.y]), [["label:isolated", 0, 80], ["repo:e", 0, 100]]);
  assert.equal(canvas.wires[0].d, undefined);
  assert.deepEqual(layout.repoCanvas({ children: [{ id: "a", x: 0, y: 0, width: 1, height: 1 }] }, [], { isolated: [], reversed: new Set() }, SIZE).nodes.map((n) => n.id), ["repo:a"]);
});

test("rerouting keeps precomputed wires until one of their cards moves", () => {
  const rects = { a: { x: 0, y: 0, w: 100, h: 40 }, b: { x: 300, y: 0, w: 100, h: 40 }, c: { x: 300, y: 200, w: 100, h: 40 } };
  const wires = [{ key: "ab", from: "a", to: "b", d: "M0,0 L1,1" }, { key: "ac", from: "a", to: "c", d: "M0,0 L2,2" }, { key: "bc", from: "b", to: "c" }];
  const still = layout.reroute(wires, (k) => rects[k], new Set());
  assert.deepEqual(still.map((w) => w.d.slice(0, 9)), ["M0,0 L1,1", "M0,0 L2,2", "M350,40 C"]);
  const moved = layout.reroute(wires, (k) => rects[k], new Set(["c"]));
  assert.equal(moved[0].d, "M0,0 L1,1");
  assert.ok(moved[1].d.startsWith("M50,40 C"), moved[1].d);
  assert.deepEqual(moved.map((w) => w.key), ["ab", "ac", "bc"]);
});

test("real ELK puts the foundations in the last column and routes a back link from its true source", async () => {
  const ELK = require(path.join(process.cwd(), "viewer/vendor/elk.bundled.js"));
  const links = [repoLink("app", "sdk", "import", 5), repoLink("sdk", "core", "import", 5), repoLink("app", "core", "import", 2),
    repoLink("tool", "core", "import", 1), repoLink("sdk", "app", "protocol", 1, ["rpc"])];
  const { graph, ...info } = layout.elkRepoGraph(["app", "sdk", "core", "tool"], links, SIZE);
  const canvas = layout.repoCanvas(await new ELK().layout(graph), links, info, SIZE);
  const x = Object.fromEntries(canvas.nodes.map((n) => [n.id, n.x]));
  assert.ok(x["repo:app"] < x["repo:sdk"] && x["repo:sdk"] < x["repo:core"]);
  assert.equal(x["repo:tool"], x["repo:sdk"], "tool uses only the foundation, so it sits one column before it");
  const back = canvas.wires.find((w) => w.key === "sdk>app>protocol");
  const [sx] = numbers(back.d), end = numbers(back.d).slice(-2);
  assert.ok(sx >= x["repo:sdk"] && end[0] <= x["repo:app"] + SIZE.repo.w, back.d);
});
