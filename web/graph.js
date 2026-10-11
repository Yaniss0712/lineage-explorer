/* Lineage Explorer — rendu du graphe en bulles (SVG, sans dépendance externe).
 *
 * GraphView(svg, callbacks)
 *   .setData({nodes, edges, center})   remplace le graphe
 *   .merge({nodes, edges})             ajoute des voisins (double-clic)
 *   .hide(id)                          masque une bulle
 *   .highlight(nodeIds, edgeIds)       met en évidence (lineage colonne, chemin)
 *   .select(id) / .selectEdge(id) / .fit()
 *
 * Disposition « en couches » : rang = plus long chemin dans le graphe des flux
 * (amont à gauche, aval à droite), ordre vertical par barycentre des voisins.
 */
(function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const COL_GAP = 250, ROW_GAP = 105;

  function el(name, attrs, parent) {
    const node = document.createElementNS(NS, name);
    for (const k in attrs || {}) node.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(node);
    return node;
  }
  function radius(n) { return 16 + Math.min(24, Math.sqrt(n.nb_columns || 1) * 3.2); }
  function short(text, max) { return text.length > max ? text.slice(0, max - 1) + "…" : text; }

  class GraphView {
    constructor(svg, callbacks) {
      this.svg = svg;
      this.cb = callbacks || {};
      this.nodes = new Map();
      this.edges = new Map();
      this.center = null;
      this.selected = null;
      this.selectedEdge = null;
      this.view = { x: 0, y: 0, k: 1 };
      this._initSvg();
      this._initPanZoom();
    }

    _initSvg() {
      this.svg.innerHTML = "";
      const defs = el("defs", {}, this.svg);
      const markers = { flow: "var(--flow)", inferred: "var(--inferred)", rejected: "var(--rejected)", hl: "var(--accent)" };
      for (const [id, color] of Object.entries(markers)) {
        const m = el("marker", { id: "arrow-" + id, viewBox: "0 0 10 10", refX: "9", refY: "5",
          markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse" }, defs);
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
        if (nodeEl) {
          const n = this.nodes.get(nodeEl.dataset.id);
          drag = { type: "node", n, sx: e.clientX, sy: e.clientY, x: n.x, y: n.y, moved: false };
        } else if (!e.target.closest(".edge")) {
          drag = { type: "pan", sx: e.clientX, sy: e.clientY, x: this.view.x, y: this.view.y };
          this.svg.classList.add("panning");
        }
      });
      window.addEventListener("mousemove", (e) => {
        if (!drag) return;
        const dx = e.clientX - drag.sx, dy = e.clientY - drag.sy;
        if (drag.type === "pan") {
          this.view.x = drag.x + dx; this.view.y = drag.y + dy; this._applyView();
        } else if (Math.abs(dx) + Math.abs(dy) > 3 || drag.moved) {
          drag.moved = true;
          drag.n.x = drag.x + dx / this.view.k; drag.n.y = drag.y + dy / this.view.k;
          this._render();
        }
      });
      window.addEventListener("mouseup", () => {
        if (drag && drag.type === "node" && drag.moved) this._suppressClick = true;
        drag = null; this.svg.classList.remove("panning");
      });
      this.svg.addEventListener("wheel", (e) => {
        e.preventDefault();
        const rect = this.svg.getBoundingClientRect();
        const mx = e.clientX - rect.left, my = e.clientY - rect.top;
        const factor = Math.exp(-e.deltaY * 0.0015);
        const k = Math.max(0.15, Math.min(3, this.view.k * factor));
        this.view.x = mx - (mx - this.view.x) * (k / this.view.k);
        this.view.y = my - (my - this.view.y) * (k / this.view.k);
        this.view.k = k; this._applyView();
      }, { passive: false });
    }

    _applyView() {
      this.root.setAttribute("transform", `translate(${this.view.x},${this.view.y}) scale(${this.view.k})`);
    }

    // ------------------------------------------------------------------ données

    setData(data) {
      this.nodes.clear(); this.edges.clear();
      this.center = data.center; this.selected = data.center; this.selectedEdge = null;
      this._add(data);
      this._layout();
      this._render();
      this.fit();
    }

    merge(data) {
      const before = new Set(this.nodes.keys());
      this._add(data);
      this._layout(before);
      this._render();
    }

    _add(data) {
      for (const n of data.nodes) if (!this.nodes.has(n.id)) this.nodes.set(n.id, { ...n });
      for (const e of data.edges) this.edges.set(String(e.id), { ...e });
    }

    hide(id) {
      if (id === this.center) return;
      this.nodes.delete(id);
      for (const [k, e] of this.edges) if (e.source_object === id || e.target_object === id) this.edges.delete(k);
      this._render();
    }

    isEmpty() { return this.nodes.size === 0; }

    // ------------------------------------------------------------------ disposition

    _layout(keepPositions) {
      const ids = [...this.nodes.keys()];
      const flows = [...this.edges.values()].filter((e) => e.kind === "FLOW"
        && this.nodes.has(e.source_object) && this.nodes.has(e.target_object));
      const rank = new Map(ids.map((id) => [id, 0]));
      // Plus long chemin (borné pour résister aux cycles).
      for (let iter = 0; iter < ids.length; iter++) {
        let changed = false;
        for (const e of flows) {
          const r = rank.get(e.source_object) + 1;
          if (r > rank.get(e.target_object) && r < ids.length + 1) { rank.set(e.target_object, r); changed = true; }
        }
        if (!changed) break;
      }
      // Nœuds reliés seulement par des relations : zone dédiée, à droite du lineage.
      const inFlow = new Set(flows.flatMap((e) => [e.source_object, e.target_object]));
      if (this.center) inFlow.add(this.center);
      const relOnly = ids.filter((id) => !inFlow.has(id)).sort();
      const columns = new Map();
      for (const id of ids) {
        if (!inFlow.has(id)) continue;
        const r = rank.get(id);
        if (!columns.has(r)) columns.set(r, []);
        columns.get(r).push(id);
      }
      const ranks = [...columns.keys()].sort((a, b) => a - b);
      const neighbours = new Map(ids.map((id) => [id, []]));
      for (const e of this.edges.values()) {
        if (neighbours.has(e.source_object) && neighbours.has(e.target_object)) {
          neighbours.get(e.source_object).push(e.target_object);
          neighbours.get(e.target_object).push(e.source_object);
        }
      }
      const pos = new Map();
      for (const r of ranks) columns.get(r).sort().forEach((id, i) => pos.set(id, i));
      for (let sweep = 0; sweep < 4; sweep++) {
        const seq = sweep % 2 ? [...ranks].reverse() : ranks;
        for (const r of seq) {
          const col = columns.get(r);
          const bary = (id) => {
            const ns = neighbours.get(id).filter((n) => inFlow.has(n) && rank.get(n) !== r);
            return ns.length ? ns.reduce((s, n) => s + pos.get(n), 0) / ns.length : pos.get(id);
          };
          const values = new Map(col.map((id) => [id, bary(id)]));
          col.sort((a, b) => values.get(a) - values.get(b));
          col.forEach((id, i) => pos.set(id, i));
        }
      }
      const centerRank = rank.get(this.center) ?? 0;
      for (const r of ranks) {
        const col = columns.get(r);
        col.forEach((id, i) => {
          const n = this.nodes.get(id);
          if (keepPositions && keepPositions.has(id)) return;
          n.x = (r - centerRank) * COL_GAP;
          n.y = (i - (col.length - 1) / 2) * ROW_GAP;
        });
      }
      // Grille des relations (6 bulles par colonne).
      const perCol = 6, maxRank = ranks.length ? Math.max(...ranks) : 0;
      relOnly.forEach((id, i) => {
        const n = this.nodes.get(id);
        if (keepPositions && keepPositions.has(id)) return;
        const c = Math.floor(i / perCol), inCol = Math.min(perCol, relOnly.length - c * perCol);
        n.x = (maxRank + 1 - centerRank) * COL_GAP + c * COL_GAP * 0.85;
        n.y = (i % perCol - (inCol - 1) / 2) * ROW_GAP;
      });
    }

    fit() {
      if (!this.nodes.size) return;
      const rect = this.svg.getBoundingClientRect();
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      for (const n of this.nodes.values()) {
        const r = radius(n) + 40;
        minX = Math.min(minX, n.x - r - 40); maxX = Math.max(maxX, n.x + r + 40);
        minY = Math.min(minY, n.y - r); maxY = Math.max(maxY, n.y + r + 20);
      }
      const k = Math.min(1.3, (rect.width - 40) / (maxX - minX), (rect.height - 90) / (maxY - minY));
      this.view.k = Math.max(0.15, k);
      this.view.x = rect.width / 2 - ((minX + maxX) / 2) * this.view.k;
      this.view.y = (rect.height - 40) / 2 - ((minY + maxY) / 2) * this.view.k;
      this._applyView();
    }

    // ------------------------------------------------------------------ rendu

    _edgePath(e) {
      const a = this.nodes.get(e.source_object), b = this.nodes.get(e.target_object);
      const ra = radius(a), rb = radius(b);
      if (e.kind === "RELATION") {
        const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
        const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1;
        const bend = Math.min(60, len * 0.25);
        const cx = mx - (dy / len) * bend, cy = my + (dx / len) * bend;
        const sa = this._toward(a, cx, cy, ra), sb = this._toward(b, cx, cy, rb);
        return { d: `M${sa.x},${sa.y} Q${cx},${cy} ${sb.x},${sb.y}`, lx: (sa.x + 2 * cx + sb.x) / 4, ly: (sa.y + 2 * cy + sb.y) / 4 };
      }
      if (b.x > a.x + 10) {
        const x1 = a.x + ra, x2 = b.x - rb - 3, c = (x2 - x1) / 2;
        return { d: `M${x1},${a.y} C${x1 + c},${a.y} ${x2 - c},${b.y} ${x2},${b.y}`, lx: (x1 + x2) / 2, ly: (a.y + b.y) / 2 };
      }
      const sa = this._toward(a, b.x, b.y, ra), sb = this._toward(b, a.x, a.y, rb + 3);
      const cx = (sa.x + sb.x) / 2 + 40, cy = (sa.y + sb.y) / 2 - 40;
      return { d: `M${sa.x},${sa.y} Q${cx},${cy} ${sb.x},${sb.y}`, lx: cx, ly: cy };
    }

    _toward(n, x, y, r) {
      const dx = x - n.x, dy = y - n.y, len = Math.hypot(dx, dy) || 1;
      return { x: n.x + (dx / len) * r, y: n.y + (dy / len) * r };
    }

    _render() {
      this.edgeLayer.innerHTML = "";
      this.nodeLayer.innerHTML = "";
      for (const [key, e] of this.edges) {
        if (!this.nodes.has(e.source_object) || !this.nodes.has(e.target_object)) continue;
        const g = el("g", { class: `edge ${e.kind} ${e.status}`, "data-id": key }, this.edgeLayer);
        if (this.selectedEdge === key) g.classList.add("selected");
        if (this._hlEdges && this._hlEdges.size) g.classList.add(this._hlEdges.has(key) ? "hl" : "dim");
        const { d, lx, ly } = this._edgePath(e);
        const width = e.status === "SUGGESTED" ? 1 + 3 * (e.score || 0) : 2.2;
        const line = el("path", { d, class: "line", "stroke-width": width.toFixed(2) }, g);
        if (e.kind === "FLOW") {
          const marker = g.classList.contains("hl") ? "hl" : e.status === "REJECTED" ? "rejected" : e.status === "SUGGESTED" ? "inferred" : "flow";
          line.setAttribute("marker-end", `url(#arrow-${marker})`);
        }
        if (e.status === "SUGGESTED" || e.kind === "RELATION") {
          const label = e.kind === "RELATION" ? (e.source_column || "") : (e.score || 0).toFixed(2);
          if (label) el("text", { x: lx, y: ly - 4 }, g).textContent = short(label, 22);
        }
        el("path", { d, class: "hit" }, g);
        g.addEventListener("click", (ev) => { ev.stopPropagation(); this.selectEdge(key); this.cb.onEdgeClick && this.cb.onEdgeClick(e); });
        g.addEventListener("mousemove", (ev) => this.cb.onHover && this.cb.onHover(ev, e));
        g.addEventListener("mouseleave", () => this.cb.onHover && this.cb.onHover(null));
      }
      for (const n of this.nodes.values()) {
        const r = radius(n);
        const cls = ["node", n.layer];
        if (n.id === this.center) cls.push("center");
        if (n.id === this.selected) cls.push("selected");
        if (this._hlNodes && this._hlNodes.size) cls.push(this._hlNodes.has(n.id) ? "hl" : "dim");
        const g = el("g", { class: cls.join(" "), "data-id": n.id, transform: `translate(${n.x},${n.y})` }, this.nodeLayer);
        el("circle", { r: r + 6, class: "ring" }, g);
        el("circle", { r, class: "body" }, g);
        el("text", { y: 4, class: "inner" }, g).textContent = n.nb_columns;
        el("text", { y: r + 15 }, g).textContent = short(n.name, 28);
        el("text", { y: r + 28, class: "db" }, g).textContent = short(n.database, 30);
        if (n.has_spec) el("circle", { cx: r * 0.72, cy: -r * 0.72, r: 4, style: "fill:var(--accent);stroke:var(--panel);stroke-width:1.5" }, g);
        const title = el("title", {}, g);
        title.textContent = `${n.id} (${n.layer}) — ${n.nb_columns} colonnes`;
        g.addEventListener("click", (ev) => {
          ev.stopPropagation();
          if (this._suppressClick) { this._suppressClick = false; return; }
          this.select(n.id); this.cb.onNodeClick && this.cb.onNodeClick(n);
        });
        g.addEventListener("dblclick", (ev) => { ev.stopPropagation(); this.cb.onNodeDblClick && this.cb.onNodeDblClick(n); });
        g.addEventListener("contextmenu", (ev) => { ev.preventDefault(); this.cb.onNodeContext && this.cb.onNodeContext(n); });
      }
    }

    select(id) { this.selected = id; this.selectedEdge = null; this._render(); }
    selectEdge(key) { this.selectedEdge = key; this._render(); }
    setCenter(id) { this.center = id; this._render(); }

    highlight(nodeIds, edgeKeys) {
      this._hlNodes = nodeIds ? new Set(nodeIds) : null;
      this._hlEdges = edgeKeys ? new Set(edgeKeys.map(String)) : null;
      this._render();
    }

    edgesBetween(pairs) {
      const keys = [];
      for (const [k, e] of this.edges) {
        if (pairs.some(([a, b]) => (e.source_object === a && e.target_object === b) || (e.source_object === b && e.target_object === a))) keys.push(k);
      }
      return keys;
    }
  }

  window.GraphView = GraphView;
})();
