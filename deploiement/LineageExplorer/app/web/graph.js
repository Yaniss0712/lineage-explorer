/* Lineage Explorer — rendu du graphe (SVG, sans dépendance externe).
 *
 * GraphView(svg, callbacks, minimapSvg)
 *   .setData({nodes, edges, center, lanes})   vue « lineage » : une colonne par tag, rangées par ordre
 *                                             (ce qui alimente à gauche, ce qui est alimenté à droite)
 *   .setColumn({name, tables, links, lanes})  vue « colonne » : les tables qui portent la colonne + sa chaîne
 *   .setOverview({groups, edges})             vue d'ensemble : un bloc par tag (ou par base)
 *   .merge(data) / .hide(id) / .highlight(nodeIds, edgeIds) / .select(id) / .selectEdge(key) / .fit()
 *   .zoomBy(f) / .focus(id) / .find(text) / .snapshot() / .restore(snap)
 *
 * Arêtes : kind FLOW (flèche) | JOIN (sans flèche) ; status VALIDE (plein) | A_VALIDER (tirets)
 *          | SUGGESTION (pointillés). Lien lu dans le SQL : petite pastille « SQL ».
 */
(function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const COL_GAP = 250, ROW_GAP = 105, DEFAULT_COLOR = "#a1a1aa";
  const TYPE_INNER = { OBJET_METIER: "métier", VIZ: "viz", SOURCE: "src", AUTRE: "…" };

  function el(name, attrs, parent) {
    const node = document.createElementNS(NS, name);
    for (const k in attrs || {}) node.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(node);
    return node;
  }
  function radius(n) { return n.isGroup ? 0 : 16 + Math.min(24, Math.sqrt(n.nb_columns || 1) * 3.2); }
  function short(text, max) { text = String(text || ""); return text.length > max ? text.slice(0, max - 1) + "…" : text; }
  const edgeKey = (e) => String(e.edge_id || e.id);
  const GROUP_W = 190, GROUP_H = 74;

  class GraphView {
    constructor(svg, callbacks, minimap) {
      this.svg = svg; this.cb = callbacks || {}; this.minimap = minimap || null;
      this.nodes = new Map(); this.edges = new Map(); this.lanes = [];
      this.center = null; this.selected = null; this.selectedEdge = null; this.mode = "lineage";
      this.view = { x: 0, y: 0, k: 1 };
      this._initSvg(); this._initPanZoom(); this._initMinimap();
    }

    _initSvg() {
      this.svg.innerHTML = "";
      const defs = el("defs", {}, this.svg);
      for (const [id, color] of Object.entries({ flow: "var(--flow)", pending: "var(--pending)", sugg: "var(--sugg)", hl: "var(--accent)" })) {
        const m = el("marker", { id: "arrow-" + id, viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse" }, defs);
        el("path", { d: "M0,0 L10,5 L0,10 z", style: "fill:" + color }, m);
      }
      this.root = el("g", {}, this.svg);
      this.laneLayer = el("g", { class: "lanes" }, this.root);
      this.edgeLayer = el("g", {}, this.root);
      this.nodeLayer = el("g", {}, this.root);
    }

    _initPanZoom() {
      let drag = null;
      this.svg.addEventListener("mousedown", (e) => {
        if (e.button !== 0) return;
        const nodeEl = e.target.closest(".node");
        if (nodeEl) { const n = this.nodes.get(nodeEl.dataset.id); if (n) drag = { type: "node", n, sx: e.clientX, sy: e.clientY, x: n.x, y: n.y, moved: false }; }
        else if (!e.target.closest(".edge")) { drag = { type: "pan", sx: e.clientX, sy: e.clientY, x: this.view.x, y: this.view.y, moved: false }; this.svg.classList.add("panning"); }
      });
      window.addEventListener("mousemove", (e) => {
        if (!drag) return;
        const dx = e.clientX - drag.sx, dy = e.clientY - drag.sy;
        if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
        if (drag.type === "pan") { this.view.x = drag.x + dx; this.view.y = drag.y + dy; this._applyView(); }
        else if (drag.moved) { drag.n.x = drag.x + dx / this.view.k; drag.n.y = drag.y + dy / this.view.k; drag.n.pinned = true; this._render(); }
      });
      window.addEventListener("mouseup", (e) => {
        if (drag && drag.type === "node" && drag.moved) this._suppressClick = true;
        // Clic dans le vide (sans glisser) : on désélectionne seulement, rien ne se referme.
        if (drag && drag.type === "pan" && !drag.moved && e.target.closest && e.target.closest("svg") === this.svg) this.cb.onBackground && this.cb.onBackground();
        drag = null; this.svg.classList.remove("panning");
      });
      this.svg.addEventListener("wheel", (e) => {
        e.preventDefault();
        const rect = this.svg.getBoundingClientRect();
        this._zoomAt(e.clientX - rect.left, e.clientY - rect.top, Math.exp(-e.deltaY * 0.0015));
      }, { passive: false });
    }
    _zoomAt(mx, my, factor) {
      const k = Math.max(0.1, Math.min(3, this.view.k * factor));
      this.view.x = mx - (mx - this.view.x) * (k / this.view.k); this.view.y = my - (my - this.view.y) * (k / this.view.k);
      this.view.k = k; this._applyView();
    }
    zoomBy(factor) { const r = this.svg.getBoundingClientRect(); this._zoomAt(r.width / 2, r.height / 2, factor); }
    zoomLevel() { return this.view.k; }
    _applyView() {
      this.root.setAttribute("transform", `translate(${this.view.x},${this.view.y}) scale(${this.view.k})`);
      this._renderMinimap();
      this.cb.onViewChange && this.cb.onViewChange(this.view);
    }

    // ------------------------------------------------------------------ données
    setData(data) {
      this.mode = "lineage"; this.nodes.clear(); this.edges.clear(); this.lanes = data.lanes || [];
      this.center = data.center; this.selected = data.center; this.selectedEdge = null; this._hlNodes = this._hlEdges = null;
      this._add(data); this._layout(); this._render(); this.fit();
    }
    merge(data) {
      const known = new Map(this.lanes.map((l) => [l.id, l]));
      for (const l of data.lanes || []) known.set(l.id, l);
      this.lanes = [...known.values()].sort((a, b) => ((a.order ?? 9999) - (b.order ?? 9999)) || String(a.name).localeCompare(b.name));
      this._add(data); this._layout(true); this._render();
    }
    _add(data) {
      for (const n of data.nodes) { const old = this.nodes.get(n.id); this.nodes.set(n.id, old ? { ...old, ...n, x: old.x, y: old.y, pinned: old.pinned } : { ...n }); }
      for (const e of data.edges) this.edges.set(edgeKey(e), { ...e });
    }
    isEmpty() { return this.nodes.size === 0; }
    hide(id) {
      if (id === this.center) return;
      this.nodes.delete(id);
      for (const [k, e] of this.edges) if (e.source_object === id || e.target_object === id) this.edges.delete(k);
      this._render();
    }

    setColumn(data) {
      this.mode = "column"; this.nodes.clear(); this.edges.clear(); this._hlNodes = this._hlEdges = null;
      this.lanes = data.lanes || []; this.center = null; this.selected = null; this.selectedEdge = null;
      for (const t of data.tables) this.nodes.set(t.id, { ...t });
      for (const l of data.links) this.edges.set(edgeKey(l), { ...l });
      this._layout(); this._render(); this.fit();
    }

    setOverview(data) {
      this.mode = "overview"; this.nodes.clear(); this.edges.clear(); this._hlNodes = this._hlEdges = null;
      this.lanes = []; this.center = null; this.selected = null; this.selectedEdge = null;
      const orders = [...new Set(data.groups.map((g) => (typeof g.order === "number" ? g.order : 1e9)))].sort((a, b) => a - b);
      const stacks = new Map();
      for (const g of data.groups) {
        const col = orders.indexOf(typeof g.order === "number" ? g.order : 1e9);
        const row = stacks.get(col) || 0; stacks.set(col, row + 1);
        this.nodes.set(g.id, { ...g, isGroup: true, x: col * (GROUP_W + 120), y: row * (GROUP_H + 46) });
      }
      for (const [col, count] of stacks) for (const n of this.nodes.values()) if (Math.round(n.x / (GROUP_W + 120)) === col) n.y -= ((count - 1) * (GROUP_H + 46)) / 2;
      for (const e of data.edges) this.edges.set(edgeKey(e), { ...e });
      this._render(); this.fit();
    }

    // ------------------------------------------------------------------ disposition : colonnes par tag
    _layout(keepPinned) {
      const ids = [...this.nodes.keys()];
      const laneIndex = new Map(this.lanes.map((l, i) => [l.id, i]));
      const laneOf = (id) => { const n = this.nodes.get(id); return n && n.tag_id != null && laneIndex.has(n.tag_id) ? laneIndex.get(n.tag_id) : null; };
      const flows = [...this.edges.values()].filter((e) => e.kind === "FLOW" && this.nodes.has(e.source_object) && this.nodes.has(e.target_object))
        .map((e) => (e.status === "SUGGESTION" && e.proposed_source && e.proposed_source !== e.source_object) ? [e.target_object, e.source_object] : [e.source_object, e.target_object])
        .filter(([s, t]) => s !== t);
      const cap = ids.length + 2;
      // 1. rang à l'intérieur d'un tag (un BV qui alimente un autre BV se place à sa droite)
      const sub = new Map(ids.map((id) => [id, 0]));
      for (let it = 0; it < cap; it++) {
        let changed = false;
        for (const [s, t] of flows) { const ls = laneOf(s); if (ls === null || ls !== laneOf(t)) continue; const r = sub.get(s) + 1; if (r > sub.get(t) && r < cap) { sub.set(t, r); changed = true; } }
        if (!changed) break;
      }
      const width = this.lanes.map(() => 0);
      for (const id of ids) { const l = laneOf(id); if (l !== null) width[l] = Math.max(width[l], sub.get(id) + 1); }
      const offset = []; let acc = 0;
      for (let i = 0; i < this.lanes.length; i++) { offset.push(acc); acc += width[i] ? width[i] : 0; }
      const col = new Map();
      for (const id of ids) { const l = laneOf(id); if (l !== null) col.set(id, offset[l] + sub.get(id)); }
      const tagged = new Set(col.keys());
      // 2. objets sans tag : placés d'après leurs voisins (après leur source, avant leur cible)
      for (let it = 0; it < cap; it++) {
        let changed = false;
        for (const [s, t] of flows) {
          if (col.has(s) && !tagged.has(t)) { const want = col.get(s) + 1; if ((!col.has(t) || col.get(t) < want) && want < acc + cap) { col.set(t, want); changed = true; } }
          if (col.has(t) && !tagged.has(s) && !col.has(s)) { col.set(s, col.get(t) - 1); changed = true; }
        }
        if (!changed) break;
      }
      // 3. groupes sans tag ni voisin placé : ordre topologique, à la suite
      const inFlow = new Set(flows.flat());
      if (this.center) inFlow.add(this.center);
      const rest = ids.filter((id) => !col.has(id) && inFlow.has(id));
      if (rest.length) {
        const base = col.size ? Math.max(...col.values()) + 1 : 0;
        const rank = new Map(rest.map((id) => [id, 0])); const restSet = new Set(rest);
        for (let it = 0; it < cap; it++) {
          let changed = false;
          for (const [s, t] of flows) { if (!restSet.has(s) || !restSet.has(t)) continue; const r = rank.get(s) + 1; if (r > rank.get(t) && r < cap) { rank.set(t, r); changed = true; } }
          if (!changed) break;
        }
        for (const id of rest) col.set(id, base + rank.get(id));
      }
      const loose = ids.filter((id) => !col.has(id)).sort();
      const columns = new Map();
      for (const id of ids) { if (!col.has(id)) continue; const c = col.get(id); if (!columns.has(c)) columns.set(c, []); columns.get(c).push(id); }
      const keys = [...columns.keys()].sort((a, b) => a - b);
      const neighbours = new Map(ids.map((id) => [id, []]));
      for (const e of this.edges.values()) if (neighbours.has(e.source_object) && neighbours.has(e.target_object)) { neighbours.get(e.source_object).push(e.target_object); neighbours.get(e.target_object).push(e.source_object); }
      const pos = new Map();
      for (const c of keys) columns.get(c).sort().forEach((id, i) => pos.set(id, i));
      for (let sweep = 0; sweep < 6; sweep++) {
        for (const c of (sweep % 2 ? [...keys].reverse() : keys)) {
          const list = columns.get(c);
          const bary = (id) => { const ns = neighbours.get(id).filter((x) => col.has(x) && col.get(x) !== c); return ns.length ? ns.reduce((s, x) => s + pos.get(x), 0) / ns.length : pos.get(id); };
          const values = new Map(list.map((id) => [id, bary(id)]));
          list.sort((a, b) => values.get(a) - values.get(b)); list.forEach((id, i) => pos.set(id, i));
        }
      }
      const shift = keys.length ? keys[0] : 0;
      for (const c of keys) columns.get(c).forEach((id, i, list) => { const n = this.nodes.get(id); if (keepPinned && n.pinned) return; n.x = (c - shift) * COL_GAP; n.y = (i - (list.length - 1) / 2) * ROW_GAP; });
      const perCol = 6, last = keys.length ? keys[keys.length - 1] - shift : -1;
      loose.forEach((id, i) => {
        const n = this.nodes.get(id); if (keepPinned && n.pinned) return;
        const c = Math.floor(i / perCol), inCol = Math.min(perCol, loose.length - c * perCol);
        n.x = (last + 1) * COL_GAP + c * COL_GAP * 0.85; n.y = (i % perCol - (inCol - 1) / 2) * ROW_GAP;
      });
    }

    _bounds() {
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      for (const n of this.nodes.values()) {
        const w = n.isGroup ? GROUP_W / 2 : radius(n) + 40, h = n.isGroup ? GROUP_H / 2 : radius(n);
        minX = Math.min(minX, n.x - w - 30); maxX = Math.max(maxX, n.x + w + 30); minY = Math.min(minY, n.y - h - (this.lanes.length ? 60 : 0)); maxY = Math.max(maxY, n.y + h + 36);
      }
      return { minX, minY, maxX, maxY };
    }
    fit() {
      if (!this.nodes.size) { this._renderMinimap(); return; }
      const rect = this.svg.getBoundingClientRect();
      const b = this._bounds();
      const k = Math.min(1.3, (rect.width - 40) / (b.maxX - b.minX), (rect.height - 110) / (b.maxY - b.minY));
      this.view.k = Math.max(0.1, k);
      this.view.x = rect.width / 2 - ((b.minX + b.maxX) / 2) * this.view.k;
      this.view.y = (rect.height + 20) / 2 - ((b.minY + b.maxY) / 2) * this.view.k;
      this._applyView();
    }
    focus(id, zoom) {
      const n = this.nodes.get(id); if (!n) return;
      const rect = this.svg.getBoundingClientRect();
      if (zoom) this.view.k = Math.max(this.view.k, zoom);
      this.view.x = rect.width / 2 - n.x * this.view.k; this.view.y = rect.height / 2 - n.y * this.view.k;
      this._applyView();
    }
    find(text) {
      const q = String(text || "").toLowerCase().trim(); if (!q) return [];
      return [...this.nodes.values()].filter((n) => String(n.name || "").toLowerCase().includes(q) || String(n.id).toLowerCase().includes(q)).map((n) => n.id);
    }
    snapshot() {
      return { mode: this.mode, center: this.center, selected: this.selected, lanes: this.lanes, view: { ...this.view },
        nodes: [...this.nodes.values()].map((n) => ({ ...n })), edges: [...this.edges.values()].map((e) => ({ ...e })) };
    }
    restore(s) {
      this.mode = s.mode; this.center = s.center; this.selected = s.selected; this.lanes = s.lanes || []; this.selectedEdge = null;
      this.nodes = new Map(s.nodes.map((n) => [n.id, { ...n }])); this.edges = new Map(s.edges.map((e) => [edgeKey(e), { ...e }]));
      this._hlNodes = this._hlEdges = null; this.view = { ...s.view }; this._render(); this._applyView();
    }

    // ------------------------------------------------------------------ rendu
    _toward(n, x, y, r) { const dx = x - n.x, dy = y - n.y, len = Math.hypot(dx, dy) || 1; return { x: n.x + (dx / len) * r, y: n.y + (dy / len) * r }; }
    _endpoints(e) {
      if (e.status === "SUGGESTION" && e.proposed_source && e.proposed_source !== e.source_object) return [e.target_object, e.source_object];
      return [e.source_object, e.target_object];
    }
    _edgePath(e) {
      const [sa, sb] = this._endpoints(e);
      const a = this.nodes.get(sa), b = this.nodes.get(sb);
      if (this.mode === "overview") {
        const x1 = a.x + GROUP_W / 2, x2 = b.x - GROUP_W / 2 - 3;
        if (x2 > x1 + 10) { const c = (x2 - x1) / 2; return { d: `M${x1},${a.y} C${x1 + c},${a.y} ${x2 - c},${b.y} ${x2},${b.y}`, lx: (x1 + x2) / 2, ly: (a.y + b.y) / 2 }; }
        const cx = (a.x + b.x) / 2 + 60, cy = (a.y + b.y) / 2 - 60;
        return { d: `M${a.x},${a.y - GROUP_H / 2} Q${cx},${cy} ${b.x},${b.y - GROUP_H / 2}`, lx: cx, ly: cy };
      }
      const ra = radius(a), rb = radius(b);
      const directed = e.kind === "FLOW" && (e.status !== "SUGGESTION" || e.proposed_source);
      if (!directed) {
        const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2, dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1, bend = Math.min(60, len * 0.2);
        const cx = mx - (dy / len) * bend, cy = my + (dx / len) * bend;
        const p = this._toward(a, cx, cy, ra), q = this._toward(b, cx, cy, rb);
        return { d: `M${p.x},${p.y} Q${cx},${cy} ${q.x},${q.y}`, lx: (p.x + 2 * cx + q.x) / 4, ly: (p.y + 2 * cy + q.y) / 4 };
      }
      if (b.x > a.x + 10) { const x1 = a.x + ra, x2 = b.x - rb - 3, c = (x2 - x1) / 2; return { d: `M${x1},${a.y} C${x1 + c},${a.y} ${x2 - c},${b.y} ${x2},${b.y}`, lx: (x1 + x2) / 2, ly: (a.y + b.y) / 2 }; }
      const p = this._toward(a, b.x, b.y, ra), q = this._toward(b, a.x, a.y, rb + 3), cx = (p.x + q.x) / 2 + 40, cy = (p.y + q.y) / 2 - 40;
      return { d: `M${p.x},${p.y} Q${cx},${cy} ${q.x},${q.y}`, lx: cx, ly: cy };
    }

    _renderLanes() {
      this.laneLayer.innerHTML = "";
      if (this.mode === "overview" || !this.lanes.length || !this.nodes.size) return;
      let minY = Infinity, maxY = -Infinity;
      for (const n of this.nodes.values()) { minY = Math.min(minY, n.y - radius(n)); maxY = Math.max(maxY, n.y + radius(n)); }
      for (const lane of this.lanes) {
        const members = [...this.nodes.values()].filter((n) => n.tag_id === lane.id);
        if (!members.length) continue;
        const xs = members.map((n) => n.x), x1 = Math.min(...xs) - COL_GAP * 0.42, x2 = Math.max(...xs) + COL_GAP * 0.42;
        const g = el("g", { class: "lane" }, this.laneLayer);
        g.style.setProperty("--c", lane.color || DEFAULT_COLOR);
        el("rect", { x: x1, y: minY - 78, width: x2 - x1, height: maxY - minY + 130, rx: 16, class: "lane-band" }, g);
        el("rect", { x: x1 + 10, y: minY - 70, width: Math.min(x2 - x1 - 20, 9 * String(lane.name).length + 46), height: 24, rx: 12, class: "lane-pill" }, g);
        const t = el("text", { x: x1 + 22, y: minY - 53, class: "lane-label" }, g);
        t.textContent = `${short(lane.name, 26)} · ${members.length}`;
      }
    }

    _render() {
      this.edgeLayer.innerHTML = ""; this.nodeLayer.innerHTML = "";
      this._renderLanes();
      this._nodeEls = new Map(); this._edgeEls = new Map();
      for (const [key, e] of this.edges) {
        if (!this.nodes.has(e.source_object) || !this.nodes.has(e.target_object)) continue;
        const g = el("g", { class: `edge ${e.kind} ${e.status}${e.origin === "SQL" ? " SQL" : ""}${e.orphan ? " orphan" : ""}${e.against_order ? " against" : ""}`, "data-id": key }, this.edgeLayer);
        this._edgeEls.set(key, g);
        if (this.selectedEdge === key) g.classList.add("selected");
        if (this._hlEdges && this._hlEdges.size) g.classList.add(this._hlEdges.has(key) ? "hl" : "dim");
        const { d, lx, ly } = this._edgePath(e);
        const line = el("path", { d, class: "line" }, g);
        if (this.mode === "overview") line.style.strokeWidth = String(Math.min(9, 1.6 + Math.log2(1 + (e.count || 1)) * 1.4));
        const directed = e.kind === "FLOW" && (e.status !== "SUGGESTION" || e.proposed_source);
        if (directed) {
          const marker = g.classList.contains("hl") ? "hl" : e.status === "SUGGESTION" ? "sugg" : e.status === "A_VALIDER" ? "pending" : "flow";
          line.setAttribute("marker-end", `url(#arrow-${marker})`);
        }
        let label = e.label || "";
        if (!label && e.status === "SUGGESTION") label = (e.columns || []).slice(0, 2).map((c) => c.a || c.source).join(", ");
        if (!label && e.columns && e.columns.length) label = e.columns.filter((c) => c.source || c.target).slice(0, 2).map((c) => c.target || c.source).join(", ") + (e.columns.length > 2 ? "…" : "");
        if (label) el("text", { x: lx, y: ly - 5 }, g).textContent = short(label, 26);
        if (e.origin === "SQL" && this.mode !== "overview") {
          const b = el("g", { class: "sql-badge", transform: `translate(${lx},${ly + 9})` }, g);
          el("rect", { x: -13, y: -7, width: 26, height: 13, rx: 6 }, b); el("text", { y: 3 }, b).textContent = "SQL";
        }
        el("path", { d, class: "hit" }, g);
        g.addEventListener("click", (ev) => { ev.stopPropagation(); this.selectEdge(key); this.cb.onEdgeClick && this.cb.onEdgeClick(e); });
        g.addEventListener("mousemove", (ev) => this.cb.onHover && this.cb.onHover(ev, e));
        g.addEventListener("mouseleave", () => this.cb.onHover && this.cb.onHover(null));
      }
      for (const n of this.nodes.values()) (n.isGroup ? this._renderGroup(n) : this._renderNode(n));
      this._renderMinimap();
    }

    _renderNode(n) {
      const r = radius(n), cls = ["node"];
      if (n.id === this.center) cls.push("center");
      if (n.id === this.selected) cls.push("selected");
      if (n.missing) cls.push("missing");
      if (n.without_column) cls.push("nocol");
      if (this._hlNodes && this._hlNodes.size) cls.push(this._hlNodes.has(n.id) ? "hl" : "dim");
      const g = el("g", { class: cls.join(" "), "data-id": n.id, transform: `translate(${n.x},${n.y})` }, this.nodeLayer);
      this._nodeEls.set(n.id, g);
      g.style.setProperty("--c", n.color || DEFAULT_COLOR);
      el("circle", { r: r + 6, class: "ring" }, g);
      if (n.type === "OBJET_METIER") el("rect", { x: -r, y: -r, width: 2 * r, height: 2 * r, rx: r * 0.35, class: "body" }, g);
      else if (n.type === "VIZ") el("rect", { x: -r * 1.15, y: -r * 0.8, width: 2.3 * r, height: 1.6 * r, rx: 5, class: "body" }, g);
      else el("circle", { r, class: "body" }, g);
      el("text", { y: 4, class: "inner" }, g).textContent = TYPE_INNER[n.type] || n.nb_columns;
      el("text", { y: r + 15 }, g).textContent = short(n.name, 30);
      el("text", { y: r + 28, class: "db" }, g).textContent = short(this.mode === "column" && n.column_type ? `${n.column_type} · ${n.database}` : n.database, 34);
      if (n.is_new) {
        const b = el("g", { class: "new-badge", transform: `translate(${r * 0.55},${-r - 4})` }, g);
        el("rect", { x: 0, y: -8, width: 34, height: 15, rx: 7 }, b); el("text", { x: 17, y: 3 }, b).textContent = "New";
      }
      if (n.tickets) {
        const b = el("g", { class: "ticket-badge", transform: `translate(${-r * 0.8},${-r * 0.8})` }, g);
        el("circle", { r: 8 }, b); el("text", { y: 3.5 }, b).textContent = n.tickets;
      }
      if (n.has_spec) el("circle", { cx: r * 0.72, cy: r * 0.72, r: 4, class: "spec" }, g);
      if (n.type_differs) el("text", { x: -r * 0.8, y: r * 0.9, class: "warn" }, g).textContent = "!";
      el("title", {}, g).textContent = `${n.id}${n.tag ? " — tag " + n.tag : ""} — ${n.nb_columns} colonne(s)${n.is_new ? " — New (créé à la main)" : ""}${n.without_column ? " — n'a pas la colonne (lien au niveau table)" : ""}`;
      this._bindNode(g, n);
    }

    _renderGroup(n) {
      const cls = ["node", "group"];
      if (n.id === this.selected) cls.push("selected");
      const g = el("g", { class: cls.join(" "), "data-id": n.id, transform: `translate(${n.x},${n.y})` }, this.nodeLayer);
      this._nodeEls.set(n.id, g);
      g.style.setProperty("--c", n.color || DEFAULT_COLOR);
      el("rect", { x: -GROUP_W / 2 - 5, y: -GROUP_H / 2 - 5, width: GROUP_W + 10, height: GROUP_H + 10, rx: 16, class: "ring" }, g);
      el("rect", { x: -GROUP_W / 2, y: -GROUP_H / 2, width: GROUP_W, height: GROUP_H, rx: 13, class: "body" }, g);
      el("text", { x: -GROUP_W / 2 + 14, y: -GROUP_H / 2 + 24, class: "g-name" }, g).textContent = short(n.name, 22);
      el("text", { x: -GROUP_W / 2 + 14, y: -GROUP_H / 2 + 45, class: "g-count" }, g).textContent = `${n.count} objet(s)${n.inner_links ? ` · ${n.inner_links} lien(s) internes` : ""}`;
      el("text", { x: -GROUP_W / 2 + 14, y: -GROUP_H / 2 + 62, class: "g-sub" }, g).textContent = short(Object.keys(n.databases || {}).join(", "), 30);
      el("title", {}, g).textContent = `${n.name} — ${n.count} objet(s)\n${(n.sample || []).join(", ")}`;
      this._bindNode(g, n);
    }

    _bindNode(g, n) {
      g.addEventListener("click", (ev) => { ev.stopPropagation(); if (this._suppressClick) { this._suppressClick = false; return; } this.select(n.id); this.cb.onNodeClick && this.cb.onNodeClick(n, ev); });
      g.addEventListener("dblclick", (ev) => { ev.stopPropagation(); this.cb.onNodeDblClick && this.cb.onNodeDblClick(n); });
      g.addEventListener("contextmenu", (ev) => { ev.preventDefault(); this.cb.onNodeContext && this.cb.onNodeContext(n, ev); });
      g.addEventListener("mouseenter", () => this._hover(n.id));
      g.addEventListener("mouseleave", () => this._hover(null));
    }

    // Survol d'un objet : ses voisins directs restent nets, le reste s'estompe (sans tout redessiner).
    _hover(id) {
      if (this._hlNodes && this._hlNodes.size) return;
      const near = new Set(id ? [id] : []), edges = new Set();
      if (id) for (const [k, e] of this.edges) if (e.source_object === id || e.target_object === id) { edges.add(k); near.add(e.source_object); near.add(e.target_object); }
      for (const [nid, g] of this._nodeEls || []) g.classList.toggle("fade", !!id && !near.has(nid));
      for (const [k, g] of this._edgeEls || []) { g.classList.toggle("fade", !!id && !edges.has(k)); g.classList.toggle("near", !!id && edges.has(k)); }
    }

    // ------------------------------------------------------------------ mini-carte
    _initMinimap() {
      if (!this.minimap) return;
      this.minimap.addEventListener("click", (e) => {
        if (!this._mm) return;
        const rect = this.minimap.getBoundingClientRect(), m = this._mm;
        const gx = m.minX + ((e.clientX - rect.left) / rect.width) * (m.maxX - m.minX);
        const gy = m.minY + ((e.clientY - rect.top) / rect.height) * (m.maxY - m.minY);
        const r = this.svg.getBoundingClientRect();
        this.view.x = r.width / 2 - gx * this.view.k; this.view.y = r.height / 2 - gy * this.view.k; this._applyView();
      });
    }
    _renderMinimap() {
      if (!this.minimap) return;
      const mm = this.minimap; mm.innerHTML = "";
      if (this.nodes.size < 2) { mm.classList.add("hidden"); this._mm = null; return; }
      mm.classList.remove("hidden");
      const b = this._bounds(), W = 180, H = 110;
      const pad = 20, m = { minX: b.minX - pad, minY: b.minY - pad, maxX: b.maxX + pad, maxY: b.maxY + pad };
      const sx = W / (m.maxX - m.minX), sy = H / (m.maxY - m.minY), s = Math.min(sx, sy);
      m.maxX = m.minX + W / s; m.maxY = m.minY + H / s; this._mm = m;
      mm.setAttribute("viewBox", `0 0 ${W} ${H}`);
      for (const n of this.nodes.values()) {
        const c = el("circle", { cx: (n.x - m.minX) * s, cy: (n.y - m.minY) * s, r: n.isGroup ? 5 : 2.6 }, mm);
        c.style.fill = n.color || DEFAULT_COLOR; if (n.id === this.selected || n.id === this.center) c.setAttribute("r", "4.2");
      }
      const r = this.svg.getBoundingClientRect();
      const vx = (-this.view.x / this.view.k - m.minX) * s, vy = (-this.view.y / this.view.k - m.minY) * s;
      el("rect", { x: vx, y: vy, width: (r.width / this.view.k) * s, height: (r.height / this.view.k) * s, class: "mm-view" }, mm);
    }

    select(id) { this.selected = id; this.selectedEdge = null; this._render(); }
    selectEdge(key) { this.selectedEdge = key; this.selected = null; this._render(); }
    clearSelection() { this.selected = null; this.selectedEdge = null; this._render(); }
    highlight(nodeIds, edgeKeys) { this._hlNodes = nodeIds ? new Set(nodeIds) : null; this._hlEdges = edgeKeys ? new Set(edgeKeys.map(String)) : null; this._render(); }
    edgesBetween(pairs) {
      const keys = [];
      for (const [k, e] of this.edges) if (pairs.some(([a, b]) => (e.source_object === a && e.target_object === b) || (e.source_object === b && e.target_object === a))) keys.push(k);
      return keys;
    }
    neighbours(id, way) {
      const out = [];
      for (const e of this.edges.values()) {
        if (e.kind !== "FLOW" && way !== "any") continue;
        if ((way === "up" || way === "any") && e.target_object === id) out.push(e.source_object);
        if ((way === "down" || way === "any") && e.source_object === id) out.push(e.target_object);
      }
      return out.filter((x) => this.nodes.has(x));
    }
  }
  window.GraphView = GraphView;
})();
