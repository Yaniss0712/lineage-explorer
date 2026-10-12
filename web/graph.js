/* Lineage Explorer — rendu du graphe (SVG, sans dépendance externe).
 *
 * GraphView(svg, callbacks)
 *   .setData({nodes, edges, center})     vue « lineage » : amont à gauche, aval à droite
 *   .setColumn({name, tables, links})    vue « colonne » : la colonne au centre, les tables autour (par tag)
 *   .merge({nodes, edges}) / .hide(id) / .highlight(nodeIds, edgeIds) / .select(id) / .fit()
 *
 * Arêtes : kind FLOW (flèche) | JOIN (sans flèche) | COLREF (vue colonne) ;
 *          status VALIDE (plein) | A_VALIDER (tirets) | SUGGESTION (pointillés).
 */
(function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const COL_GAP = 250, ROW_GAP = 105, DEFAULT_COLOR = "#a1a1aa";

  function el(name, attrs, parent) {
    const node = document.createElementNS(NS, name);
    for (const k in attrs || {}) node.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(node);
    return node;
  }
  function radius(n) { return n.isColumn ? 34 : 16 + Math.min(24, Math.sqrt(n.nb_columns || 1) * 3.2); }
  function short(text, max) { text = String(text || ""); return text.length > max ? text.slice(0, max - 1) + "…" : text; }
  const edgeKey = (e) => String(e.edge_id || e.id);

  class GraphView {
    constructor(svg, callbacks) {
      this.svg = svg; this.cb = callbacks || {};
      this.nodes = new Map(); this.edges = new Map();
      this.center = null; this.selected = null; this.selectedEdge = null; this.mode = "lineage";
      this.view = { x: 0, y: 0, k: 1 };
      this._initSvg(); this._initPanZoom();
    }

    _initSvg() {
      this.svg.innerHTML = "";
      const defs = el("defs", {}, this.svg);
      for (const [id, color] of Object.entries({ flow: "var(--flow)", pending: "var(--pending)", sugg: "var(--sugg)", hl: "var(--accent)" })) {
        const m = el("marker", { id: "arrow-" + id, viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse" }, defs);
        el("path", { d: "M0,0 L10,5 L0,10 z", style: "fill:" + color }, m);
      }
      this.root = el("g", {}, this.svg);
      this.edgeLayer = el("g", {}, this.root);
      this.nodeLayer = el("g", {}, this.root);
    }

    _initPanZoom() {
      let drag = null;
      this.svg.addEventListener("mousedown", (e) => {
        if (e.button !== 0) return;
        const nodeEl = e.target.closest(".node");
        if (nodeEl) { const n = this.nodes.get(nodeEl.dataset.id); drag = { type: "node", n, sx: e.clientX, sy: e.clientY, x: n.x, y: n.y, moved: false }; }
        else if (!e.target.closest(".edge")) { drag = { type: "pan", sx: e.clientX, sy: e.clientY, x: this.view.x, y: this.view.y }; this.svg.classList.add("panning"); }
      });
      window.addEventListener("mousemove", (e) => {
        if (!drag) return;
        const dx = e.clientX - drag.sx, dy = e.clientY - drag.sy;
        if (drag.type === "pan") { this.view.x = drag.x + dx; this.view.y = drag.y + dy; this._applyView(); }
        else if (Math.abs(dx) + Math.abs(dy) > 3 || drag.moved) { drag.moved = true; drag.n.x = drag.x + dx / this.view.k; drag.n.y = drag.y + dy / this.view.k; this._render(); }
      });
      window.addEventListener("mouseup", () => { if (drag && drag.type === "node" && drag.moved) this._suppressClick = true; drag = null; this.svg.classList.remove("panning"); });
      this.svg.addEventListener("wheel", (e) => {
        e.preventDefault();
        const rect = this.svg.getBoundingClientRect(), mx = e.clientX - rect.left, my = e.clientY - rect.top;
        const k = Math.max(0.15, Math.min(3, this.view.k * Math.exp(-e.deltaY * 0.0015)));
        this.view.x = mx - (mx - this.view.x) * (k / this.view.k); this.view.y = my - (my - this.view.y) * (k / this.view.k);
        this.view.k = k; this._applyView();
      }, { passive: false });
    }
    _applyView() { this.root.setAttribute("transform", `translate(${this.view.x},${this.view.y}) scale(${this.view.k})`); }

    // ------------------------------------------------------------------ données
    setData(data) {
      this.mode = "lineage"; this.nodes.clear(); this.edges.clear();
      this.center = data.center; this.selected = data.center; this.selectedEdge = null; this._hlNodes = this._hlEdges = null;
      this._add(data); this._layout(); this._render(); this.fit();
    }
    merge(data) { const before = new Set(this.nodes.keys()); this._add(data); this._layout(before); this._render(); }
    _add(data) {
      for (const n of data.nodes) if (!this.nodes.has(n.id)) this.nodes.set(n.id, { ...n });
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
      const cid = "__col__:" + data.name;
      this.center = cid; this.selected = null; this.selectedEdge = null;
      this.nodes.set(cid, { id: cid, isColumn: true, name: data.name, database: `${data.tables.length} table(s)`, x: 0, y: 0, color: "var(--accent)" });
      const n = data.tables.length, R = Math.max(220, n * 26);
      data.tables.forEach((t, i) => {
        const a = (2 * Math.PI * i) / Math.max(1, n) - Math.PI / 2;
        this.nodes.set(t.id, { ...t, x: Math.cos(a) * R, y: Math.sin(a) * R * 0.78 });
        this.edges.set("c:" + t.id, { edge_id: "c:" + t.id, kind: "COLREF", status: t.type_differs ? "WARN" : "VALIDE", source_object: cid, target_object: t.id, label: t.column_type });
      });
      for (const l of data.links) this.edges.set(edgeKey(l), { ...l });
      this._render(); this.fit();
    }

    // ------------------------------------------------------------------ disposition « lineage »
    _layout(keep) {
      const ids = [...this.nodes.keys()];
      const flows = [...this.edges.values()].filter((e) => e.kind === "FLOW" && e.status !== "SUGGESTION" && this.nodes.has(e.source_object) && this.nodes.has(e.target_object));
      const rank = new Map(ids.map((id) => [id, 0]));
      for (let iter = 0; iter < ids.length; iter++) {
        let changed = false;
        for (const e of flows) { const r = rank.get(e.source_object) + 1; if (r > rank.get(e.target_object) && r < ids.length + 1) { rank.set(e.target_object, r); changed = true; } }
        if (!changed) break;
      }
      const inFlow = new Set(flows.flatMap((e) => [e.source_object, e.target_object]));
      if (this.center) inFlow.add(this.center);
      const relOnly = ids.filter((id) => !inFlow.has(id)).sort();
      const columns = new Map();
      for (const id of ids) { if (!inFlow.has(id)) continue; const r = rank.get(id); if (!columns.has(r)) columns.set(r, []); columns.get(r).push(id); }
      const ranks = [...columns.keys()].sort((a, b) => a - b);
      const neighbours = new Map(ids.map((id) => [id, []]));
      for (const e of this.edges.values()) if (neighbours.has(e.source_object) && neighbours.has(e.target_object)) { neighbours.get(e.source_object).push(e.target_object); neighbours.get(e.target_object).push(e.source_object); }
      const pos = new Map();
      for (const r of ranks) columns.get(r).sort().forEach((id, i) => pos.set(id, i));
      for (let sweep = 0; sweep < 4; sweep++) {
        for (const r of (sweep % 2 ? [...ranks].reverse() : ranks)) {
          const col = columns.get(r);
          const bary = (id) => { const ns = neighbours.get(id).filter((x) => inFlow.has(x) && rank.get(x) !== r); return ns.length ? ns.reduce((s, x) => s + pos.get(x), 0) / ns.length : pos.get(id); };
          const values = new Map(col.map((id) => [id, bary(id)]));
          col.sort((a, b) => values.get(a) - values.get(b)); col.forEach((id, i) => pos.set(id, i));
        }
      }
      const centerRank = rank.get(this.center) ?? 0;
      for (const r of ranks) columns.get(r).forEach((id, i, col) => { const n = this.nodes.get(id); if (keep && keep.has(id)) return; n.x = (r - centerRank) * COL_GAP; n.y = (i - (col.length - 1) / 2) * ROW_GAP; });
      const perCol = 6, maxRank = ranks.length ? Math.max(...ranks) : 0;
      relOnly.forEach((id, i) => {
        const n = this.nodes.get(id); if (keep && keep.has(id)) return;
        const c = Math.floor(i / perCol), inCol = Math.min(perCol, relOnly.length - c * perCol);
        n.x = (maxRank + 1 - centerRank) * COL_GAP + c * COL_GAP * 0.85; n.y = (i % perCol - (inCol - 1) / 2) * ROW_GAP;
      });
    }

    fit() {
      if (!this.nodes.size) return;
      const rect = this.svg.getBoundingClientRect();
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      for (const n of this.nodes.values()) { const r = radius(n) + 40; minX = Math.min(minX, n.x - r - 40); maxX = Math.max(maxX, n.x + r + 40); minY = Math.min(minY, n.y - r); maxY = Math.max(maxY, n.y + r + 20); }
      const k = Math.min(1.3, (rect.width - 40) / (maxX - minX), (rect.height - 110) / (maxY - minY));
      this.view.k = Math.max(0.15, k);
      this.view.x = rect.width / 2 - ((minX + maxX) / 2) * this.view.k;
      this.view.y = (rect.height + 20) / 2 - ((minY + maxY) / 2) * this.view.k;
      this._applyView();
    }

    // ------------------------------------------------------------------ rendu
    _toward(n, x, y, r) { const dx = x - n.x, dy = y - n.y, len = Math.hypot(dx, dy) || 1; return { x: n.x + (dx / len) * r, y: n.y + (dy / len) * r }; }
    _endpoints(e) {
      // Une suggestion avec un sens proposé est dessinée dans ce sens.
      if (e.status === "SUGGESTION" && e.proposed_source && e.proposed_source !== e.source_object) return [e.target_object, e.source_object];
      return [e.source_object, e.target_object];
    }
    _edgePath(e) {
      const [sa, sb] = this._endpoints(e);
      const a = this.nodes.get(sa), b = this.nodes.get(sb), ra = radius(a), rb = radius(b);
      if (e.kind === "COLREF") { const p = this._toward(a, b.x, b.y, ra), q = this._toward(b, a.x, a.y, rb); return { d: `M${p.x},${p.y} L${q.x},${q.y}`, lx: (p.x * 0.35 + q.x * 0.65), ly: (p.y * 0.35 + q.y * 0.65) }; }
      const directed = e.kind === "FLOW" && (e.status !== "SUGGESTION" || e.proposed_source);
      if (!directed || this.mode === "column") {
        const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2, dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1, bend = Math.min(60, len * 0.2);
        const cx = mx - (dy / len) * bend, cy = my + (dx / len) * bend;
        const p = this._toward(a, cx, cy, ra), q = this._toward(b, cx, cy, rb + (directed ? 3 : 0));
        return { d: `M${p.x},${p.y} Q${cx},${cy} ${q.x},${q.y}`, lx: (p.x + 2 * cx + q.x) / 4, ly: (p.y + 2 * cy + q.y) / 4 };
      }
      if (b.x > a.x + 10) { const x1 = a.x + ra, x2 = b.x - rb - 3, c = (x2 - x1) / 2; return { d: `M${x1},${a.y} C${x1 + c},${a.y} ${x2 - c},${b.y} ${x2},${b.y}`, lx: (x1 + x2) / 2, ly: (a.y + b.y) / 2 }; }
      const p = this._toward(a, b.x, b.y, ra), q = this._toward(b, a.x, a.y, rb + 3), cx = (p.x + q.x) / 2 + 40, cy = (p.y + q.y) / 2 - 40;
      return { d: `M${p.x},${p.y} Q${cx},${cy} ${q.x},${q.y}`, lx: cx, ly: cy };
    }

    _render() {
      this.edgeLayer.innerHTML = ""; this.nodeLayer.innerHTML = "";
      for (const [key, e] of this.edges) {
        if (!this.nodes.has(e.source_object) || !this.nodes.has(e.target_object)) continue;
        const g = el("g", { class: `edge ${e.kind} ${e.status}`, "data-id": key }, this.edgeLayer);
        if (this.selectedEdge === key) g.classList.add("selected");
        if (this._hlEdges && this._hlEdges.size) g.classList.add(this._hlEdges.has(key) ? "hl" : "dim");
        const { d, lx, ly } = this._edgePath(e);
        const line = el("path", { d, class: "line" }, g);
        const directed = e.kind === "FLOW" && (e.status !== "SUGGESTION" || e.proposed_source);
        if (directed && e.kind !== "COLREF") {
          const marker = g.classList.contains("hl") ? "hl" : e.status === "SUGGESTION" ? "sugg" : e.status === "A_VALIDER" ? "pending" : "flow";
          line.setAttribute("marker-end", `url(#arrow-${marker})`);
        }
        let label = e.label || "";
        if (!label && e.status === "SUGGESTION") label = (e.columns || []).slice(0, 2).map((c) => c.a).join(", ");
        if (!label && e.columns && e.columns.length && e.kind !== "COLREF") label = e.columns.filter((c) => c.source || c.target).slice(0, 2).map((c) => c.source || c.target).join(", ");
        if (label) el("text", { x: lx, y: ly - 4 }, g).textContent = short(label, 26);
        if (e.kind !== "COLREF") {
          el("path", { d, class: "hit" }, g);
          g.addEventListener("click", (ev) => { ev.stopPropagation(); this.selectEdge(key); this.cb.onEdgeClick && this.cb.onEdgeClick(e); });
          g.addEventListener("mousemove", (ev) => this.cb.onHover && this.cb.onHover(ev, e));
          g.addEventListener("mouseleave", () => this.cb.onHover && this.cb.onHover(null));
        }
      }
      for (const n of this.nodes.values()) {
        const r = radius(n), cls = ["node"];
        if (n.isColumn) cls.push("colnode");
        if (n.id === this.center) cls.push("center");
        if (n.id === this.selected) cls.push("selected");
        if (n.missing) cls.push("missing");
        if (this._hlNodes && this._hlNodes.size) cls.push(this._hlNodes.has(n.id) ? "hl" : "dim");
        const g = el("g", { class: cls.join(" "), "data-id": n.id, transform: `translate(${n.x},${n.y})` }, this.nodeLayer);
        g.style.setProperty("--c", n.color || DEFAULT_COLOR);
        el("circle", { r: r + 6, class: "ring" }, g);
        el("circle", { r, class: "body" }, g);
        el("text", { y: 4, class: "inner" }, g).textContent = n.isColumn ? "col" : n.nb_columns;
        el("text", { y: r + 15 }, g).textContent = short(n.name, 30);
        el("text", { y: r + 28, class: "db" }, g).textContent = short(n.isColumn ? n.database : (n.tag ? `${n.tag} · ${n.database}` : n.database), 34);
        if (n.has_spec) el("circle", { cx: r * 0.72, cy: -r * 0.72, r: 4, class: "spec" }, g);
        if (n.type_differs) el("text", { x: -r * 0.8, y: -r * 0.6, class: "warn" }, g).textContent = "!";
        el("title", {}, g).textContent = n.isColumn ? `Colonne ${n.name}` : `${n.id}${n.tag ? " — tag " + n.tag : ""} — ${n.nb_columns} colonnes${n.column_type ? " — type " + n.column_type : ""}`;
        if (n.isColumn) continue;
        g.addEventListener("click", (ev) => { ev.stopPropagation(); if (this._suppressClick) { this._suppressClick = false; return; } this.select(n.id); this.cb.onNodeClick && this.cb.onNodeClick(n, ev); });
        g.addEventListener("dblclick", (ev) => { ev.stopPropagation(); this.cb.onNodeDblClick && this.cb.onNodeDblClick(n); });
        g.addEventListener("contextmenu", (ev) => { ev.preventDefault(); this.cb.onNodeContext && this.cb.onNodeContext(n); });
      }
    }

    select(id) { this.selected = id; this.selectedEdge = null; this._render(); }
    selectEdge(key) { this.selectedEdge = key; this._render(); }
    highlight(nodeIds, edgeKeys) { this._hlNodes = nodeIds ? new Set(nodeIds) : null; this._hlEdges = edgeKeys ? new Set(edgeKeys.map(String)) : null; this._render(); }
    edgesBetween(pairs) {
      const keys = [];
      for (const [k, e] of this.edges) if (pairs.some(([a, b]) => (e.source_object === a && e.target_object === b) || (e.source_object === b && e.target_object === a))) keys.push(k);
      return keys;
    }
  }
  window.GraphView = GraphView;
})();
