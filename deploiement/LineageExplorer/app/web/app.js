/* Lineage Explorer — logique de l'interface (JS vanilla, aucun appel externe).
 * Toutes les données affichées passent par esc() avant insertion dans le HTML.
 */
(function () {
  "use strict";

  // ================================================================== outils
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => [...(root || document).querySelectorAll(sel)];
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const enc = encodeURIComponent;
  const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
  const fmtScore = (s) => (s == null ? "–" : Number(s).toFixed(2));
  const fmtDate = (s) => (s ? new Date(s).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" }) : "");
  const short = (id) => String(id || "").split(".").pop();
  const STATUS_LABEL = { VALIDE: "validé", A_VALIDER: "à valider", SUGGESTION: "lu dans le SQL" };
  const RULE_KIND = { VIEW: "Vue", INSERT: "Alimentation (INSERT)", CTAS: "Création (CREATE TABLE AS)", SELECT: "Requête" };
  const KIND_LABEL = { FLOW: "Flux", JOIN: "Jointure" };
  const ORIGIN_LABEL = { STRUCTURE: "colonnes communes", SQL: "requête SQL", DECLARE: "fichier importé" };
  const TICKET_KIND = { ANOMALIE: "Anomalie", MODIFICATION: "Modification", AMELIORATION: "Amélioration de l'outil" };
  const TICKET_STATUS = { OUVERT: "ouvert", EN_COURS: "en cours", RESOLU: "résolu", REJETE: "rejeté" };
  const PRIORITY = { HAUTE: "haute", NORMALE: "normale", BASSE: "basse" };
  const LEVEL_LABEL = { column: "colonne", name: "même nom", table: "niveau table" };

  function storageGet(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }
  function storageSet(key, v) { try { localStorage.setItem(key, v); } catch (e) { /* stockage indisponible */ } }

  const userName = () => $("#user").value.trim();
  async function api(path, opts) {
    opts = opts || {};
    opts.headers = { ...(opts.headers || {}), ...(userName() ? { "X-User": enc(userName()) } : {}) };
    const res = await fetch("/api/" + path, opts);
    const ct = res.headers.get("Content-Type") || "";
    const data = ct.includes("json") ? await res.json() : await res.text();
    if (!res.ok) { const err = new Error((data && data.error) || res.statusText); err.status = res.status; throw err; }
    return data;
  }
  const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

  // Message en bas de l'écran, avec un bouton d'action facultatif (ex. « Annuler »).
  function toast(msg, isError, action) {
    const t = $("#toast"), btn = $("#toast-action");
    $("#toast-text").textContent = msg; t.className = "toast" + (isError ? " error" : "");
    btn.classList.toggle("hidden", !action);
    if (action) { btn.textContent = action.label; btn.onclick = () => { t.classList.add("hidden"); action.run(); }; }
    clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), action ? 7000 : isError ? 5000 : 2800);
  }
  const fail = (e) => toast(e.message || String(e), true);
  const undoAction = { label: "Annuler (Ctrl+Z)", run: () => undo() };

  // ================================================================== état
  const EMPTY_HTML = $("#graph-empty").innerHTML;
  const state = {
    info: null, revision: null, view: null, // view : {type:"object", id} | {type:"column", name} | {type:"overview"}
    center: null, selected: null, selEdge: null, dir: "both", detailTab: "cols",
    openGroups: new Set(), selectMode: false, selection: new Set(), colLineage: null,
    expanded: new Set(), hiddenNodes: new Set(), nav: [], ovTags: new Set(),
  };
  const tags = () => (state.info ? state.info.tags : []);
  const tagById = (id) => tags().find((t) => t.id === id);
  const tagChip = (name, color) => name ? `<span class="tag-chip"><i class="swatch" style="background:${esc(color || "")}"></i>${esc(name)}</span>` : '<span class="tag-chip none">sans tag</span>';
  const newBadge = (o) => o && o.is_new ? '<span class="badge-new">New</span>' : "";
  const typeLabel = (t) => (state.info && state.info.object_types && state.info.object_types[t]) || (t === "VIEW" ? "Vue" : t);

  const graph = new GraphView($("#graph"), {
    onNodeClick: (n) => {
      if (n.isGroup) return showGroup(n);
      state.selected = n.id; state.selEdge = null; showObject(n.id);
    },
    onNodeDblClick: (n) => {
      if (n.isGroup) return openOverviewObjects([n.key]);
      if (state.view && state.view.type !== "object") return openObject(n.id);
      expand(n.id);
    },
    onNodeContext: (n) => { if (n.isGroup || n.id === state.center) return; state.hiddenNodes.add(n.id); graph.hide(n.id); updateInfo(); toast(`${n.name} masqué du graphe`, false, { label: "Réafficher", run: () => { state.hiddenNodes.delete(n.id); loadGraph(true); } }); },
    onEdgeClick: (e) => { state.selEdge = e; state.selected = null; showEdge(e); },
    onBackground: () => { state.selEdge = null; graph.clearSelection(); },
    onHover: hoverEdge,
    onViewChange: (v) => { $("#zoom-level").textContent = Math.round(v.k * 100) + " %"; },
  }, $("#minimap"));

  function hoverEdge(ev, e) {
    const tip = $("#tooltip");
    if (!ev) { tip.classList.add("hidden"); return; }
    const wrap = $(".canvas").getBoundingClientRect();
    let title, body;
    if (e.id && String(e.id).startsWith("o:")) { title = `${e.count} lien(s)`; body = `${e.validated} validé(s), ${e.pending} à valider`; }
    else if (e.status === "SUGGESTION") { title = `Suggestion · score ${fmtScore(e.score)}`; body = esc(e.explanation || ""); }
    else {
      title = `${KIND_LABEL[e.kind]} · ${STATUS_LABEL[e.status] || ""}${e.origin === "SQL" ? " · lu dans le SQL" : ""}`;
      body = `${esc(e.source_object)} ${e.kind === "FLOW" ? "→" : "="} ${esc(e.target_object)}${(e.columns || []).length ? "<br>" + e.columns.slice(0, 8).map((c) => `${esc(c.source || "?")} → ${esc(c.target || "?")}`).join("<br>") : ""}`
        + (e.orphan ? "<br>⚠ objet absent de la structure" : "") + (e.against_order ? "<br>⚠ sens inverse de l'ordre des tags" : "");
    }
    tip.innerHTML = `<b>${esc(title)}</b><div class="muted" style="margin-top:4px">${body}</div><div class="muted" style="margin-top:4px;font-size:11px">Clic : détail · Entrée : valider · Suppr / E : supprimer</div>`;
    tip.classList.remove("hidden");
    tip.style.left = Math.max(8, Math.min(ev.clientX - wrap.left + 14, wrap.width - 350)) + "px"; tip.style.top = (ev.clientY - wrap.top + 14) + "px";
  }

  // ================================================================== chargement / synchronisation
  async function loadState() {
    state.info = await api("state");
    state.revision = state.info.revision;
    $("#user").placeholder = state.info.user;
    $("#user").title = "Par défaut : " + state.info.user;
    $("#review-count").textContent = state.info.counts.links_pending || "";
    $("#track-count").textContent = state.info.counts.tickets_open || "";
    fillTagSelect($("#selection-tag"), true);
    renderUndo(state.info.undo);
    renderLegend();
    return state.info;
  }
  function renderUndo(u) {
    const undoBtn = $("#btn-undo"), redoBtn = $("#btn-redo");
    undoBtn.disabled = !(u && u.undo); redoBtn.disabled = !(u && u.redo);
    undoBtn.title = u && u.undo ? `Annuler (Ctrl+Z) : ${u.undo.label}${u.undo.detail ? " — " + u.undo.detail : ""}` : "Rien à annuler";
    redoBtn.title = u && u.redo ? `Rétablir (Ctrl+Y) : ${u.redo} modification(s)` : "Rien à rétablir";
  }
  function renderLegend() {
    const tagItems = tags().filter((t) => t.count).slice(0, 10).map((t) => `<span class="ln"><i class="swatch" style="background:${esc(t.color)}"></i>${esc(t.name)}</span>`).join("");
    $("#legend").innerHTML = `<span class="ln"><svg width="22" height="8"><line x1="0" y1="4" x2="22" y2="4" class="lg-flow"/></svg>validé</span>
      <span class="ln"><svg width="22" height="8"><line x1="0" y1="4" x2="22" y2="4" class="lg-pending"/></svg>à valider</span>
      <span class="ln"><svg width="22" height="8"><line x1="0" y1="4" x2="22" y2="4" class="lg-sugg"/></svg>suggestion</span>
      <span class="ln"><svg width="22" height="8"><line x1="0" y1="4" x2="22" y2="4" class="lg-join"/></svg>jointure</span>
      <span class="ln"><span class="badge-sql">SQL</span>lu dans le SQL</span><span class="ln"><span class="badge-new">New</span>créé à la main</span>
      ${tagItems ? `<span class="lg-sep"></span>${tagItems}` : ""}`;
  }

  function setSync(text, cls) { const s = $("#sync"); s.textContent = text; s.className = "sync" + (cls ? " " + cls : ""); }

  // Connexion au serveur local : seules les erreurs réseau comptent comme « déconnecté ».
  let failures = 0;
  function setOnline(ok) {
    failures = ok ? 0 : failures + 1;
    $("#offline").classList.toggle("hidden", failures < 2);
    if (!ok && failures >= 2) setSync("hors ligne", "off");
  }
  function stamp() { return new Date().toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" }); }

  async function poll() {
    let r;
    try { r = await api("revision?since=" + enc(state.revision || "")); }
    catch (e) { if (!e.status) return setOnline(false); r = null; }
    setOnline(true);
    if (!r) return;
    try {
      if (r.revision !== state.revision) {
        const me = (userName() || state.info.user || "").toLowerCase();
        const others = r.changes.filter((c) => (c.user || "").toLowerCase() !== me);
        const users = [...new Set(others.map((c) => c.user))].join(", ");
        state.revision = r.revision;
        await refreshAll();
        if (others.length) toast(`${others.length} modification(s) de ${users}`);
      }
      setSync("à jour " + stamp());
    } catch (e) { console.error(e); setSync("à jour " + stamp()); }
  }

  async function manualRefresh() {
    const btn = $("#btn-refresh");
    btn.classList.add("spinning"); setSync("actualisation…", "busy");
    try {
      await post("refresh", {});
      setOnline(true);
      await refreshAll();
      setSync("à jour " + stamp());
      toast("Données actualisées");
    } catch (e) {
      if (!e.status) { setOnline(false); setOnline(false); } else fail(e);
    } finally { btn.classList.remove("spinning"); }
  }
  $("#btn-refresh").addEventListener("click", manualRefresh);
  $("#offline-retry").addEventListener("click", manualRefresh);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });

  // Rafraîchit tout sans perdre ce que l'utilisateur a ouvert dans le graphe (branches, sélection, zoom).
  async function refreshAll() {
    try { await loadState(); } catch (e) { if (!e.status) throw e; return fail(e); }
    await Promise.all([refreshCatalog(), refreshCounts()]);
    const active = $(".tab.active").dataset.tab;
    if (active === "review") loadReview();
    if (active === "sugg") loadSuggestions();
    if (active === "track") loadTracking();
    if (state.view && state.view.type === "object") {
      await loadGraph(true);
      if (state.selEdge && state.selEdge.id && !String(state.selEdge.edge_id || "").startsWith("s:")) {
        const fresh = graph.edges.get(String(state.selEdge.edge_id));
        if (fresh) showEdge(fresh, true); else if (state.selected) showObject(state.selected, true);
      } else if (state.selected) showObject(state.selected, true);
    }
    if (state.view && state.view.type === "column") openColumn(state.view.name, true);
    if (state.view && state.view.type === "overview") loadOverview(true);
  }

  async function refreshCounts() {
    try { const s = await api("suggestions?limit=1"); $("#sugg-count").textContent = s.total || ""; } catch (e) { /* pas de données */ }
  }

  // ---- annuler / rétablir
  async function undo() {
    try { const r = await post("history/undo", {}); renderUndo(r.undo); toast(`Annulé : ${r.done.label}${r.done.detail ? " — " + r.done.detail : ""}`, false, { label: "Rétablir (Ctrl+Y)", run: redo }); await refreshAll(); }
    catch (e) { fail(e); }
  }
  async function redo() {
    try { const r = await post("history/redo", {}); renderUndo(r.undo); toast(`Rétabli : ${r.done.detail}`); await refreshAll(); }
    catch (e) { fail(e); }
  }
  $("#btn-undo").addEventListener("click", undo);
  $("#btn-redo").addEventListener("click", redo);

  // ================================================================== navigation (bouton retour)
  function viewLabel() {
    if (!state.view) return null;
    if (state.view.type === "object") return short(state.view.id);
    if (state.view.type === "column") return "colonne " + state.view.name;
    if (state.view.type === "overview") return "vue d'ensemble";
    if (state.view.type === "path") return "chemin";
    return null;
  }
  function pushNav() {
    if (!state.view || graph.isEmpty()) return;
    state.nav.push({ view: { ...state.view }, label: viewLabel(), center: state.center, selected: state.selected, dir: state.dir,
      depth: $("#depth").value, expanded: [...state.expanded], hidden: [...state.hiddenNodes], graph: graph.snapshot() });
    if (state.nav.length > 30) state.nav.shift();
    renderCrumbs();
  }
  function renderCrumbs() {
    $("#btn-back").disabled = !state.nav.length;
    const items = state.nav.slice(-4).map((n, i, list) => `<button class="crumb" data-crumb="${state.nav.length - list.length + i}">${esc(n.label)}</button>`);
    const current = viewLabel();
    $("#crumbs").innerHTML = items.join('<span class="crumb-sep">›</span>') + (current ? `${items.length ? '<span class="crumb-sep">›</span>' : ""}<span class="crumb current">${esc(current)}</span>` : "");
  }
  function goBack(index) {
    if (!state.nav.length) return;
    const target = index == null ? state.nav.length - 1 : index;
    const snap = state.nav[target];
    state.nav = state.nav.slice(0, target);
    state.view = snap.view; state.center = snap.center; state.selected = snap.selected; state.selEdge = null;
    state.dir = snap.dir; $("#depth").value = snap.depth;
    $$("#dir-seg .btn-seg").forEach((x) => x.classList.toggle("active", x.dataset.dir === state.dir));
    state.expanded = new Set(snap.expanded); state.hiddenNodes = new Set(snap.hidden);
    showBars(state.view.type);
    $("#graph-empty").classList.add("hidden");
    graph.restore(snap.graph); updateInfo();
    if (state.view.type === "object") { location.hash = "obj=" + enc(state.view.id); if (state.selected) showObject(state.selected); }
    if (state.view.type === "column") openColumn(state.view.name, true, true);
    if (state.view.type === "overview") loadOverview(true);
    renderCrumbs();
  }
  $("#btn-back").addEventListener("click", () => goBack());
  $("#crumbs").addEventListener("click", (e) => { const c = e.target.closest("[data-crumb]"); if (c) goBack(Number(c.dataset.crumb)); });
  function showBars(type) {
    $("#floatbar").classList.toggle("hidden", type !== "object" && type !== "path");
    $("#colbar").classList.toggle("hidden", type !== "column");
    $("#ovbar").classList.toggle("hidden", type !== "overview");
  }

  // ================================================================== onglets
  function selectTab(name) {
    $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
    ["catalog", "review", "sugg", "cols", "track"].forEach((n) => $("#tab-" + n).classList.toggle("hidden", n !== name));
    if (name === "review") loadReview();
    if (name === "sugg") loadSuggestions();
    if (name === "cols") loadColumns();
    if (name === "track") loadTracking();
  }
  $$(".tab").forEach((t) => t.addEventListener("click", () => selectTab(t.dataset.tab)));

  // ================================================================== catalogue (arborescence tag → base → objets)
  async function refreshCatalog() {
    if ($("#q").value.trim()) return runSearch();
    if (!state.info.has_data) { $("#tree").innerHTML = '<p class="hint">Aucune donnée importée. Importez un JSON ou du SQL, ou créez vos propres objets avec « + Objet ».</p>'; $("#catalog-info").textContent = ""; return; }
    const groups = await api("tree");
    const nNew = state.info.counts.new_objects;
    $("#catalog-info").textContent = `${state.info.counts.objects} objet(s)${nNew ? ` · ${nNew} New` : ""}`;
    if (!state.openGroups.size && groups.length === 1) state.openGroups.add("g:" + groups[0].tag_id);
    $("#tree").innerHTML = groups.map((g) => `<div class="tree-group ${state.openGroups.has("g:" + g.tag_id) ? "open" : ""}" data-key="g:${esc(g.tag_id)}">
        <div class="tree-head" data-toggle="g:${esc(g.tag_id)}"><span class="caret">▶</span><i class="swatch" style="background:${esc(g.color || "")}"></i><span class="lbl" title="${esc(g.name)}">${esc(g.name)}</span>${g.order != null ? `<span class="ord" title="Ordre du tag : sens d'alimentation">${esc(g.order)}</span>` : ""}<span class="n">${g.count}</span></div>
        <div class="tree-children">${g.databases.map((d) => { const k = `d:${g.tag_id}:${d.database}`; return `<div class="tree-db ${state.openGroups.has(k) ? "open" : ""}" data-key="${esc(k)}" data-tag="${esc(g.tag_id)}" data-db="${esc(d.database)}">
            <div class="tree-head" data-toggle="${esc(k)}"><span class="caret">▶</span><span class="lbl" title="${esc(d.database)}">${esc(d.database)}</span><span class="n">${d.count}</span></div>
            <div class="tree-children" data-objects></div></div>`; }).join("")}</div></div>`).join("") || '<p class="hint">Aucun objet.</p>';
    for (const db of $$(".tree-db.open", $("#tree"))) await loadDbObjects(db);
  }

  async function loadDbObjects(dbEl) {
    const box = $("[data-objects]", dbEl);
    const items = await api(`tree/objects?tag=${enc(dbEl.dataset.tag)}&db=${enc(dbEl.dataset.db)}`);
    box.innerHTML = items.map(objectItem).join("");
  }

  function objectItem(o) {
    const checked = state.selection.has(o.id) ? "checked" : "";
    return `<div class="item ${o.id === state.center ? "active" : ""}" data-obj="${esc(o.id)}">
      ${state.selectMode ? `<input type="checkbox" data-pick="${esc(o.id)}" ${checked}>` : ""}
      <div class="txt"><div class="title" title="${esc(o.id)}">${esc(o.name)} ${newBadge(o)}${o.tickets ? `<span class="badge-ticket" title="Sujets de suivi ouverts">${o.tickets}</span>` : ""}</div>
      <div class="sub">${o.custom ? esc(typeLabel(o.type)) + " · " : o.type === "VIEW" ? "vue · " : ""}${o.nb_columns} col.</div>
      ${o.matched_columns && o.matched_columns.length ? `<div class="cols-hit">${o.matched_columns.slice(0, 4).map((c) => esc(c.name)).join(", ")}${o.matched_columns.length > 4 ? "…" : ""}</div>` : ""}</div></div>`;
  }

  $("#tree").addEventListener("click", async (ev) => {
    const pick = ev.target.closest("[data-pick]");
    if (pick) { pick.checked ? state.selection.add(pick.dataset.pick) : state.selection.delete(pick.dataset.pick); updateSelectionBar(); ev.stopPropagation(); return; }
    const toggle = ev.target.closest("[data-toggle]");
    if (toggle) {
      const node = toggle.parentElement, key = toggle.dataset.toggle;
      node.classList.toggle("open");
      node.classList.contains("open") ? state.openGroups.add(key) : state.openGroups.delete(key);
      if (node.classList.contains("tree-db") && node.classList.contains("open") && !$("[data-objects]", node).children.length) await loadDbObjects(node);
      return;
    }
    const item = ev.target.closest(".item");
    if (!item) return;
    if (state.selectMode) { const cb = $("[data-pick]", item); cb.checked = !cb.checked; cb.checked ? state.selection.add(cb.dataset.pick) : state.selection.delete(cb.dataset.pick); updateSelectionBar(); return; }
    $$(".item.active", $("#tree")).forEach((x) => x.classList.remove("active"));
    item.classList.add("active");
    openObject(item.dataset.obj);
  });

  // ---- sélection multiple + tag en masse
  function fillTagSelect(select, withNone) {
    const current = select.value;
    select.innerHTML = (withNone ? '<option value="">— retirer le tag —</option>' : "") + tags().map((t) => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join("");
    if ([...select.options].some((o) => o.value === current)) select.value = current;
  }
  function updateSelectionBar() {
    $("#selection-bar").classList.toggle("hidden", !state.selectMode);
    $("#selection-count").textContent = `${state.selection.size} sélectionné(s)`;
  }
  $("#select-mode").addEventListener("change", (e) => { state.selectMode = e.target.checked; state.selection.clear(); updateSelectionBar(); refreshCatalog(); });
  $("#selection-clear").addEventListener("click", () => { state.selection.clear(); $$("[data-pick]").forEach((c) => (c.checked = false)); updateSelectionBar(); });
  $("#selection-apply").addEventListener("click", async () => {
    if (!state.selection.size) return toast("Cochez des objets dans le catalogue.", true);
    try {
      const r = await post("tags/assign", { object_ids: [...state.selection], tag_id: $("#selection-tag").value || null });
      toast(`${r.count} objet(s) tagué(s)`, false, undoAction); state.selection.clear(); updateSelectionBar(); await refreshAll();
    } catch (e) { fail(e); }
  });

  // ---- recherche (tout le catalogue)
  async function runSearch() {
    const q = $("#q").value.trim();
    if (!q) return refreshCatalog();
    selectTab("catalog");
    try {
      const r = await api("search?q=" + enc(q));
      const groups = new Map();
      for (const o of r.objects) {
        const key = o.tag_id || "__none__";
        if (!groups.has(key)) groups.set(key, { name: o.tag || "Sans tag", color: o.color, dbs: new Map() });
        const g = groups.get(key);
        if (!g.dbs.has(o.database)) g.dbs.set(o.database, []);
        g.dbs.get(o.database).push(o);
      }
      $("#catalog-info").textContent = `${r.total} résultat(s)${r.truncated ? " (affichage limité)" : ""}`;
      $("#tree").innerHTML = (r.tags.length ? `<p class="hint">Tag(s) : ${r.tags.map((t) => tagChip(t.name, t.color)).join(" ")}</p>` : "")
        + ([...groups.values()].map((g) => `<div class="tree-group open"><div class="tree-head"><span class="caret">▶</span><i class="swatch" style="background:${esc(g.color || "")}"></i>${esc(g.name)}<span class="n">${[...g.dbs.values()].reduce((s, l) => s + l.length, 0)}</span></div>
          <div class="tree-children">${[...g.dbs.entries()].map(([db, list]) => `<div class="tree-db open"><div class="tree-head"><span class="caret">▶</span>${esc(db)}<span class="n">${list.length}</span></div>
          <div class="tree-children">${list.map(objectItem).join("")}</div></div>`).join("")}</div></div>`).join("") || '<p class="hint">Aucun résultat.</p>');
    } catch (e) { if (e.status !== 503) fail(e); }
  }
  $("#q").addEventListener("input", debounce(runSearch, 250));

  // ================================================================== vue « objet » : graphe
  function graphParams(extra) {
    return new URLSearchParams({ depth: $("#depth").value || 2, dir: state.dir, sugg: $("#opt-sugg").checked ? "1" : "0",
      pending: $("#opt-pending").checked ? "1" : "0", join: $("#opt-join").checked ? "1" : "0", ...(extra || {}) });
  }
  async function openObject(id, opts) {
    opts = opts || {};
    if (!opts.noPush) pushNav();
    state.view = { type: "object", id }; state.center = id; state.selected = id; state.selEdge = null;
    state.expanded = new Set(); state.hiddenNodes = new Set();
    location.hash = "obj=" + enc(id);
    showBars("object");
    renderCrumbs();
    await Promise.all([loadGraph(false), showObject(id)]);
  }
  // Ouvre un objet : s'il est déjà dans le graphe, on le sélectionne sans rien refermer.
  function goToObject(id) {
    if (state.view && state.view.type === "object" && graph.nodes.has(id)) {
      state.selected = id; state.selEdge = null; graph.select(id); graph.focus(id); return showObject(id);
    }
    return openObject(id);
  }
  async function loadGraph(keep) {
    if (!state.center) return;
    try {
      const view = keep ? { ...graph.view } : null;
      const data = await api(`graph/${enc(state.center)}?` + graphParams());
      for (const id of state.expanded) {
        if (id === state.center) continue;
        try {
          const more = await api(`graph/${enc(id)}?` + graphParams({ depth: 1, dir: "both" }));
          const known = new Set(data.nodes.map((n) => n.id));
          data.nodes.push(...more.nodes.filter((n) => !known.has(n.id)));
          data.edges.push(...more.edges);
          data.lanes = mergeLanes(data.lanes, more.lanes);
        } catch (e) { state.expanded.delete(id); }
      }
      data.nodes = data.nodes.filter((n) => !state.hiddenNodes.has(n.id));
      $("#graph-empty").classList.add("hidden");
      const pinned = new Map([...graph.nodes.values()].filter((n) => n.pinned).map((n) => [n.id, n]));
      graph.setData(data);
      if (keep) {
        for (const [id, n] of pinned) { const m = graph.nodes.get(id); if (m) { m.x = n.x; m.y = n.y; m.pinned = true; } }
        graph.select(state.selected); if (view) { graph.view = view; graph._applyView(); }
      }
      if (state.selected && state.selected !== state.center) graph.select(state.selected);
      if (state.selEdge) graph.selectEdge(String(state.selEdge.edge_id || "l:" + state.selEdge.id));
      state.truncated = data.truncated; updateInfo();
      if (state.colLineage) highlightColumnLineage(state.colLineage);
    } catch (e) { fail(e); }
  }
  function mergeLanes(a, b) {
    const m = new Map((a || []).map((l) => [l.id, l])); for (const l of b || []) m.set(l.id, l);
    return [...m.values()].sort((x, y) => ((x.order ?? 9999) - (y.order ?? 9999)) || String(x.name).localeCompare(y.name));
  }
  function updateInfo() {
    $("#graph-info").textContent = `${graph.nodes.size} objet(s) · ${graph.edges.size} lien(s)` + (state.truncated ? ` · +${state.truncated} non affiché(s)` : "")
      + (state.expanded.size ? ` · ${state.expanded.size} branche(s) ouverte(s)` : "");
  }
  async function expand(id) {
    try { state.expanded.add(id); graph.merge(await api(`graph/${enc(id)}?` + graphParams({ depth: 1, dir: "both" }))); updateInfo(); } catch (e) { fail(e); }
  }
  $$("#dir-seg .btn-seg").forEach((b) => b.addEventListener("click", () => {
    $$("#dir-seg .btn-seg").forEach((x) => x.classList.toggle("active", x === b)); state.dir = b.dataset.dir; loadGraph(true);
  }));
  $("#depth").addEventListener("change", () => loadGraph(true));
  $$(".stepper [data-step]").forEach((b) => b.addEventListener("click", () => {
    $("#depth").value = Math.max(1, Math.min(20, (Number($("#depth").value) || 2) + Number(b.dataset.step))); loadGraph(true);
  }));
  ["#opt-sugg", "#opt-pending", "#opt-join"].forEach((s) => $(s).addEventListener("change", () => loadGraph(true)));
  function view360(id) {
    $("#depth").value = 20; state.dir = "both";
    $$("#dir-seg .btn-seg").forEach((x) => x.classList.toggle("active", x.dataset.dir === "both"));
    return openObject(id || state.selected || state.center);
  }
  $("#btn-360").addEventListener("click", () => { if (state.selected || state.center) view360(); });
  function popover(btn, box) {
    $(btn).addEventListener("click", (e) => { e.stopPropagation(); $$(".popover").forEach((p) => p !== $(box) && p.classList.add("hidden")); $(box).classList.toggle("hidden"); });
  }
  popover("#btn-options", "#options"); popover("#btn-more", "#more-menu");
  document.addEventListener("click", (e) => { if (!e.target.closest(".popover-wrap")) $$(".popover").forEach((p) => p.classList.add("hidden")); });
  $$(".zoombar [data-zoom]").forEach((b) => b.addEventListener("click", () => zoom(b.dataset.zoom)));
  function zoom(what) {
    if (what === "in") graph.zoomBy(1.25);
    if (what === "out") graph.zoomBy(0.8);
    if (what === "fit") graph.fit();
    if (what === "sel") { const id = state.selected || state.center; if (id) graph.focus(id, 1); }
  }
  window.addEventListener("resize", debounce(() => graph.fit(), 200));

  // ---- trouver dans le graphe
  let findHits = [], findIndex = 0;
  $("#find").addEventListener("input", debounce(() => {
    findHits = graph.find($("#find").value); findIndex = 0;
    $("#find-count").textContent = $("#find").value ? `${findHits.length}` : "";
    graph.highlight(findHits.length ? findHits : null, findHits.length ? [] : null);
    if (findHits.length) graph.focus(findHits[0]);
  }, 150));
  $("#find").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && findHits.length) { findIndex = (findIndex + 1) % findHits.length; graph.focus(findHits[findIndex]); graph.select(findHits[findIndex]); showObject(findHits[findIndex]); state.selected = findHits[findIndex]; }
    if (e.key === "Escape") { $("#find").value = ""; findHits = []; $("#find-count").textContent = ""; graph.highlight(null, null); $("#find").blur(); }
  });

  // ================================================================== détail d'un objet
  async function showObject(id, keepScroll) {
    try {
      const d = await api("object/" + enc(id));
      const scroll = $("#detail").scrollTop;
      state.detail = d; state.colLineage = null;
      if (!keepScroll) graph.highlight(null, null);
      renderObject(d);
      if (keepScroll) $("#detail").scrollTop = scroll;
    } catch (e) { fail(e); }
  }

  function scorebar(s) { return `<span class="scorebar" title="${fmtScore(s)}"><i style="width:${Math.round((s || 0) * 100)}%"></i></span>`; }

  function linkCard(l, selfId) {
    const other = l.source === selfId ? l.target : l.source;
    const cols = (l.columns || []).filter((c) => c.source || c.target);
    const head = selfId ? `<span class="obj" data-open="${esc(other)}">${esc(short(other))}</span>`
      : `<span class="obj" data-open="${esc(l.source)}">${esc(short(l.source))}</span><span class="muted">${l.kind === "FLOW" ? "→" : "="}</span><span class="obj" data-open="${esc(l.target)}">${esc(short(l.target))}</span>`;
    return `<div class="link-card" data-link="${esc(l.id)}">
      <div class="row">${head}<span class="status ${esc(l.status)}">${esc(STATUS_LABEL[l.status])}</span></div>
      <div class="meta">${KIND_LABEL[l.kind]}${l.origin === "SQL" ? ' · <span class="badge-sql">SQL</span>' : ""} · par ${esc(l.created_by)} le ${esc(fmtDate(l.created_at))}${l.validated_by ? ` · validé par ${esc(l.validated_by)}` : ""}</div>
      ${cols.length ? `<div class="meta mono">${cols.slice(0, 12).map((c) => `${esc(c.source || "·")} → ${esc(c.target || "·")}${c.transformation ? " (" + esc(c.transformation) + ")" : ""}`).join("<br>")}${cols.length > 12 ? `<br>… ${cols.length - 12} autre(s)` : ""}</div>` : ""}
      ${l.comment ? `<div class="meta">${esc(l.comment)}</div>` : ""}
      ${(l.problems || []).map((p) => `<div class="problem">⚠ ${esc(p)}</div>`).join("")}
      ${l.against_order ? '<div class="problem">⚠ Sens inverse de l\'ordre des tags</div>' : ""}
      <div class="btns">${l.status === "A_VALIDER" ? '<button class="btn sm ok" data-act="validate" title="Entrée">Valider</button>' : ""}
        <button class="btn sm" data-act="edit">Modifier</button><button class="btn sm danger" data-act="delete" title="Suppr ou E">Supprimer</button>
        <button class="btn sm ghost" data-act="ticket" title="Signaler une anomalie sur ce lien">Signaler</button></div></div>`;
  }

  function suggCard(s, selfId) {
    const other = selfId ? (s.a === selfId ? s.b : s.a) : null;
    const dir = s.proposed_source ? `${short(s.proposed_source)} → ${short(s.proposed_source === s.a ? s.b : s.a)}` : "sens à choisir";
    return `<div class="link-card" data-sugg="${esc(s.key)}">
      <div class="row">${other ? `<span class="obj" data-open="${esc(other)}">${esc(short(other))}</span>` : `<span class="obj" data-open="${esc(s.a)}">${esc(short(s.a))}</span><span class="muted">↔</span><span class="obj" data-open="${esc(s.b)}">${esc(short(s.b))}</span>`}
        <span>${scorebar(s.score)}<span class="score">${fmtScore(s.score)}</span></span></div>
      <div class="meta">${esc(ORIGIN_LABEL[s.origin] || s.origin)} · ${esc(dir)}</div>
      <div class="meta">${esc(s.explanation || "")}</div>
      <div class="btns"><button class="btn sm ok" data-act="create" title="Entrée">Créer le lien</button><button class="btn sm danger" data-act="dismiss" title="Suppr ou E">Écarter</button></div></div>`;
  }

  function ruleCard(r, selfId) {
    const tbl = (id) => id ? (String(id).startsWith("(") ? `<span class="mono">${esc(id)}</span>` : `<span class="obj" data-open="${esc(id)}">${esc(short(id))}</span>`) : "";
    const head = !selfId ? `${esc(RULE_KIND[r.kind] || r.kind)}${r.target ? ` de ${tbl(r.target)}` : ""} à partir de ${r.sources.map(tbl).join(", ") || "—"}`
      : r.role === "target"
        ? `${esc(RULE_KIND[r.kind] || r.kind)} de cette table à partir de ${r.sources.map(tbl).join(", ") || "—"}`
        : `${esc(RULE_KIND[r.kind] || r.kind)}${r.target ? ` de ${tbl(r.target)}` : ""} qui utilise cette table`;
    const line = (label, value) => value ? `<div class="rule-line"><span class="rule-label">${label}</span><code>${esc(value)}</code></div>` : "";
    const mine = !selfId || r.role === "target" ? r.columns : r.columns.filter((c) => c.expression.toLowerCase().includes(short(selfId).toLowerCase() + "."));
    const steps = (r.steps || []).map((s) => `<div class="rule-step"><div class="rule-step-name">étape <b>${esc(s.name)}</b>${s.from ? ` · depuis ${tbl(s.from)}` : ""}</div>
      ${(s.joins || []).map((j) => line(esc(j.type.toLowerCase()), `${short(j.table)}${j.condition ? "  ON  " + j.condition : ""}`)).join("")}
      ${line("filtre", s.filters)}${line("regroupement", s.group_by)}${line("condition sur groupe", s.having)}</div>`).join("");
    return `<div class="rule-card">
      <div class="rule-head">${head}</div>
      ${steps}
      ${r.joins.map((j) => line(esc(j.type.toLowerCase()), `${short(j.table)}${j.condition ? "  ON  " + j.condition : ""}`)).join("")}
      ${line("filtre", r.filters)}${line("regroupement", r.group_by)}${line("condition sur groupe", r.having)}
      ${mine.length ? `<table class="col-table rule-cols">${mine.map((c) => `<tr><td class="mono">${esc(c.column)}</td><td><code>${esc(c.expression)}</code></td></tr>`).join("")}</table>` : ""}
      <details class="more"><summary>Code SQL${r.source_file ? " · " + esc(r.source_file) : ""}</summary><pre class="sql">${esc(r.sql)}</pre></details></div>`;
  }

  function ticketCard(t) {
    return `<div class="link-card ticket ${esc(t.status)}" data-ticket="${esc(t.id)}">
      <div class="row"><b>${esc(t.title)}</b><span class="status t-${esc(t.status)}">${esc(TICKET_STATUS[t.status] || t.status)}</span></div>
      <div class="meta">${esc(TICKET_KIND[t.kind] || t.kind)} · priorité ${esc(PRIORITY[t.priority] || "normale")}${t.object_id ? ` · <span class="obj" data-open="${esc(t.object_id)}">${esc(short(t.object_id))}</span>${t.column ? "." + esc(t.column) : ""}` : ""}</div>
      <div class="meta">${esc(t.created_by)} · ${esc(fmtDate(t.created_at))}${t.assignee ? ` · suivi par ${esc(t.assignee)}` : ""}${(t.comments || []).length ? ` · ${t.comments.length} commentaire(s)` : ""}</div></div>`;
  }

  function setDetailTab(name) {
    state.detailTab = name;
    $$("[data-dtab]").forEach((b) => b.classList.toggle("active", b.dataset.dtab === name));
    $$("[data-pane]").forEach((p) => p.classList.toggle("hidden", p.dataset.pane !== name));
  }

  function renderObject(d) {
    const o = d.object, doc = d.doc || {}, custom = d.custom;
    const nbLinks = d.upstream.length + d.downstream.length + d.joins.length;
    const list = (items, empty) => items.map((l) => linkCard(l, o.id)).join("") || `<p class="hint">${empty}</p>`;
    const openTickets = (d.tickets || []).filter((t) => t.status === "OUVERT" || t.status === "EN_COURS").length;
    $("#detail").innerHTML = `<div class="detail">
      <div class="d-head">${tagChip(o.tag, o.color)} <span class="muted" style="font-size:12px">${esc(typeLabel(o.type))}</span> ${newBadge(o)}
        <h2>${esc(o.name)}</h2><div class="sub mono">${esc(o.database)}</div>
        ${doc.description || (custom && custom.description) ? `<p class="d-desc">${esc(doc.description || custom.description)}</p>` : ""}</div>
      ${custom ? `<div class="new-box ${o.is_new ? "" : "done"}">${o.is_new
        ? `<p>Objet <b>créé à la main</b> par ${esc(custom.created_by)} le ${esc(fmtDate(custom.created_at))}. Quand la vraie table existe, fusionnez-la avec sa définition (SQL ou JSON) pour garder ses liens, ou validez-la telle quelle.</p>
           <div class="btns"><button class="btn sm primary" id="obj-merge">Fusionner avec sa définition…</button><button class="btn sm ok" id="obj-confirm">Valider l'objet</button>`
        : `<p>Objet créé à la main, validé${custom.confirmed_by ? ` par ${esc(custom.confirmed_by)}` : ""}.</p><div class="btns"><button class="btn sm" id="obj-merge">Fusionner avec sa définition…</button>`}
           <button class="btn sm" id="obj-edit">Modifier</button><button class="btn sm danger" id="obj-delete">Supprimer</button></div></div>` : ""}
      <div class="form" style="margin-top:12px"><label>Tag<select id="obj-tag"><option value="">— sans tag —</option>${tags().map((t) => `<option value="${esc(t.id)}" ${t.id === o.tag_id ? "selected" : ""}>${esc(t.name)}${t.order != null ? ` (${esc(t.order)})` : ""}</option>`).join("")}</select></label></div>
      <div class="d-actions">
        <button class="btn sm primary" id="btn-add-link">Ajouter un lien</button>
        <button class="btn sm" data-go="up">Amont</button><button class="btn sm" data-go="down">Aval</button>
        <button class="btn sm" id="btn-obj-360" title="Tout l'amont et tout l'aval">360°</button>
        <button class="btn sm" id="btn-path">Chemin vers…</button>
        <button class="btn sm ghost" id="btn-ticket" title="Signaler une anomalie ou une modification">Signaler</button>
        <a class="btn sm ghost" href="/api/impact/${enc(o.id)}.csv" download>Impact CSV</a>
        ${o.id !== state.center ? '<button class="btn sm ghost" id="btn-hide">Masquer</button>' : ""}
      </div>
      <div class="d-tabs">
        <button class="d-tab" data-dtab="cols">Colonnes ${d.columns.length}</button>
        <button class="d-tab" data-dtab="links">Liens ${nbLinks}</button>
        <button class="d-tab" data-dtab="rules">Règles ${(d.rules || []).length}</button>
        <button class="d-tab" data-dtab="sugg" title="Suggestions">Sugg. ${d.suggestions.length}</button>
        <button class="d-tab" data-dtab="track">Suivi${openTickets ? ` <span class="badge-ticket">${openTickets}</span>` : ` ${(d.tickets || []).length}`}</button>
        <button class="d-tab" data-dtab="doc">Doc</button>
      </div>
      <div data-pane="cols">
        <table class="cols"><thead><tr><th>Colonne</th><th>Type</th><th></th></tr></thead><tbody>
        ${d.columns.map((c) => `<tr class="col-row" data-col="${esc(c.name)}"><td class="mono">${esc(c.name)}</td><td class="mono muted">${esc(c.data_type || "")}</td>
          <td style="white-space:nowrap"><button class="btn sm ghost" data-colview="${esc(c.name)}" title="Toutes les tables qui ont cette colonne, et sa chaîne d'existence">pipeline</button>${c.nb_links ? `<span class="muted" title="liens colonne"> · ${c.nb_links}</span>` : ""}</td></tr>`).join("")}
        </tbody></table>
        <p class="hint">Clic sur une colonne : son origine à travers les liens. « pipeline » : toutes les tables qui la portent et sa chaîne d'existence.</p>
        <div id="col-lineage"></div>
      </div>
      <div data-pane="links">
        <div class="section-title">Alimentée par · ${d.upstream.length}</div>${list(d.upstream, "Aucune source documentée.")}
        <div class="section-title">Alimente · ${d.downstream.length}</div>${list(d.downstream, "Aucune table alimentée documentée.")}
        <div class="section-title">Jointures · ${d.joins.length}</div>${list(d.joins, "Aucune jointure documentée.")}
      </div>
      <div data-pane="rules">${(d.rules || []).length
        ? `<p class="hint" style="padding-top:0">Règles de gestion lues dans le SQL importé : jointures, filtres, regroupements, étapes (CTE) et calcul des colonnes.</p>${d.rules.map((r) => ruleCard(r, o.id)).join("")}`
        : '<p class="hint">Aucune requête SQL ne concerne cette table. Importez du SQL (mode « Compléter ») pour voir ses règles de gestion.</p>'}</div>
      <div data-pane="sugg">${d.suggestions.map((s) => suggCard(s, o.id)).join("") || '<p class="hint">Aucune suggestion pour cette table.</p>'}</div>
      <div data-pane="track">${(d.tickets || []).map(ticketCard).join("") || '<p class="hint">Aucun sujet de suivi sur cet objet.</p>'}
        <button class="btn sm primary" id="btn-ticket2" style="margin-top:8px">+ Signaler</button></div>
      <div data-pane="doc">
        <div class="form">
          <label>Description<textarea id="doc-desc" rows="3">${esc(doc.description || "")}</textarea></label>
          <div class="row2"><label>Propriétaire<input id="doc-owner" value="${esc(doc.owner || "")}"></label>
            <label>URL de spec<input id="doc-spec" placeholder="https://…" value="${esc(doc.spec_url || "")}"></label></div>
          ${doc.spec_url ? `<a href="${esc(doc.spec_url)}" target="_blank" rel="noopener noreferrer">Ouvrir la spec</a>` : ""}
          <button class="btn sm primary" id="doc-save" style="justify-self:start">Enregistrer</button>
          ${doc.updated_by ? `<div class="muted" style="font-size:12px">Modifié par ${esc(doc.updated_by)} le ${esc(fmtDate(doc.updated_at))}</div>` : ""}
        </div>
        <div class="section-title">Notes</div>
        ${(d.notes || []).map((n) => `<div class="note">${esc(n.text)}<div class="meta">${esc(n.author)} · ${esc(fmtDate(n.at))} · <a href="#" data-del-note="${esc(n.id)}">supprimer</a></div></div>`).join("")}
        <div class="form"><textarea id="note-text" rows="2" placeholder="Partager une information utile…"></textarea>
          <button class="btn sm" id="add-note" style="justify-self:start">Ajouter la note</button></div>
      </div></div>`;
    setDetailTab(state.detailTab || "cols");
    bindObject(d);
  }

  function bindObject(d) {
    const o = d.object;
    const all = [...d.upstream, ...d.downstream, ...d.joins];
    $("#detail").onclick = async (ev) => {
      const t = ev.target;
      const tab = t.closest("[data-dtab]"); if (tab) return setDetailTab(tab.dataset.dtab);
      const cv = t.closest("[data-colview]"); if (cv) { ev.stopPropagation(); return openColumn(cv.dataset.colview); }
      if (t.closest("[data-open]")) return goToObject(t.closest("[data-open]").dataset.open);
      const act = t.closest("[data-act]");
      if (act && act.closest("[data-link]")) return linkAction(all.find((l) => l.id === act.closest("[data-link]").dataset.link), act.dataset.act);
      if (act && act.closest("[data-sugg]")) return suggAction(d.suggestions.find((s) => s.key === act.closest("[data-sugg]").dataset.sugg), act.dataset.act);
      const tk = t.closest("[data-ticket]"); if (tk) return openTicket((d.tickets || []).find((x) => x.id === tk.dataset.ticket));
      const row = t.closest(".col-row"); if (row) return showColumnLineage(o.id, row.dataset.col, "up");
      const go = t.closest("[data-go]");
      if (go) { $$("#dir-seg .btn-seg").forEach((x) => x.classList.toggle("active", x.dataset.dir === go.dataset.go)); state.dir = go.dataset.go; return openObject(o.id); }
      const dn = t.closest("[data-del-note]");
      if (dn) { ev.preventDefault(); await write("notes/delete", { object_id: o.id, id: dn.dataset.delNote }); return showObject(o.id, true); }
    };
    $("#obj-tag").addEventListener("change", async (e) => { await write("tags/assign", { object_ids: [o.id], tag_id: e.target.value || null }, "Tag enregistré", true); refreshAll(); });
    $("#btn-add-link").addEventListener("click", () => openLinkEditor({ source: o.id }));
    $("#btn-path").addEventListener("click", () => openPath(o.id));
    $("#btn-obj-360").addEventListener("click", () => view360(o.id));
    $("#btn-ticket").addEventListener("click", () => openTicket(null, { object_id: o.id }));
    $("#btn-ticket2").addEventListener("click", () => openTicket(null, { object_id: o.id }));
    const hide = $("#btn-hide"); if (hide) hide.addEventListener("click", () => { state.hiddenNodes.add(o.id); graph.hide(o.id); updateInfo(); });
    $("#doc-save").addEventListener("click", async () => {
      await write("object/update", { id: o.id, description: $("#doc-desc").value, owner: $("#doc-owner").value, spec_url: $("#doc-spec").value }, "Documentation enregistrée", true);
      showObject(o.id, true);
    });
    $("#add-note").addEventListener("click", async () => {
      const text = $("#note-text").value.trim(); if (!text) return;
      await write("notes/add", { object_id: o.id, text }, "Note ajoutée", true); showObject(o.id, true);
    });
    if (d.custom) {
      const c = (id, fn) => { const b = $(id); if (b) b.addEventListener("click", fn); };
      c("#obj-merge", () => openImport("merge", o.id));
      c("#obj-confirm", () => confirmBox(`Valider l'objet « ${o.name} » ?`, "Il garde ses liens et sa documentation, et perd l'étiquette New. Vous pourrez toujours le fusionner plus tard avec sa définition.", "Valider l'objet", async () => {
        await write("objects/confirm", { id: o.id }, "Objet validé", true); refreshAll();
      }));
      c("#obj-edit", () => openObjectEditor(d.custom));
      c("#obj-delete", () => confirmBox(`Supprimer l'objet « ${o.name} » ?`, "Ses liens deviendront orphelins (signalés en orange). Ctrl+Z pour revenir en arrière.", "Supprimer", async () => {
        await write("objects/delete", { id: o.id }, "Objet supprimé", true); refreshAll();
      }, true));
    }
  }

  async function write(path, body, okMessage, withUndo) {
    try { const r = await post(path, body); if (okMessage) toast(okMessage, false, withUndo ? undoAction : null); return r; }
    catch (e) { fail(e); throw e; }
  }

  async function linkAction(l, act) {
    if (!l) return;
    if (act === "edit") return openLinkEditor({ link: l });
    if (act === "ticket") return openTicket(null, { object_id: l.target, link_id: l.id, title: `Lien ${short(l.source)} → ${short(l.target)} : ` });
    if (act === "validate") { if (l.status !== "A_VALIDER") return; await write("links/validate", { id: l.id }, "Lien validé", true); }
    if (act === "delete") await write("links/delete", { id: l.id }, `Lien ${short(l.source)} ${l.kind === "FLOW" ? "→" : "="} ${short(l.target)} supprimé`, true);
    if (act === "delete" && state.selEdge && state.selEdge.id === l.id) state.selEdge = null;
    await refreshAll();
  }

  async function suggAction(s, act) {
    if (!s) return;
    if (act === "create") return openLinkEditor({ suggestion: s });
    if (act === "dismiss") { await write("suggestions/dismiss", { key: s.key }, "Suggestion écartée", true); state.selEdge = null; await refreshAll(); }
  }

  // ---- lineage d'une colonne (via les liens colonne → colonne)
  async function showColumnLineage(objId, column, dir) {
    try {
      const data = await api(`lineage/column?object=${enc(objId)}&column=${enc(column)}&dir=${dir}&tables=1`);
      state.colLineage = data;
      if ($("[data-dtab]")) setDetailTab("cols");
      $$(".col-row").forEach((r) => r.classList.toggle("active", r.dataset.col === column));
      const box = $("#col-lineage"); if (!box) return;
      box.innerHTML = `<div class="section-title"><span>${dir === "up" ? "Origine" : "Usage"} de <span class="mono" style="text-transform:none">${esc(column)}</span></span>
        <span class="seg"><button class="btn-seg ${dir === "up" ? "active" : ""}" data-cl="up">Origine</button><button class="btn-seg ${dir === "down" ? "active" : ""}" data-cl="down">Usage</button><button class="btn-seg" data-cl="off">×</button></span></div>
        <div class="col-lineage">${data.steps.length ? chainHtml(data.steps, dir) : `<p class="hint">Aucun lien documenté pour cette colonne. Ajoutez-en un avec « Ajouter un lien » ou importez le SQL.</p>`}</div>`;
      box.querySelectorAll("[data-cl]").forEach((b) => b.addEventListener("click", (ev) => {
        ev.stopPropagation();
        if (b.dataset.cl === "off") { state.colLineage = null; box.innerHTML = ""; graph.highlight(null, null); $$(".col-row").forEach((r) => r.classList.remove("active")); return; }
        showColumnLineage(objId, column, b.dataset.cl);
      }));
      highlightColumnLineage(data);
    } catch (e) { fail(e); }
  }
  function chainHtml(steps, dir) {
    return steps.map((s) => `<div class="step" style="--d:${s.depth - 1}">
      <span class="muted">${dir === "up" ? "←" : "→"}</span> <i class="swatch" style="background:${esc(s.color || "")}"></i><span class="mono obj" data-open="${esc(s.object)}">${esc(short(s.object))}${s.column ? `.<b>${esc(s.column)}</b>` : ""}</span>
      ${s.tag ? `<span class="muted" style="font-size:11px">${esc(s.tag)}</span>` : ""}
      <span class="status ${esc(s.status)}">${esc(STATUS_LABEL[s.status])}</span>${s.level && s.level !== "column" ? `<span class="lvl">${esc(LEVEL_LABEL[s.level])}</span>` : ""}
      ${s.transformation ? `<div class="muted mono" style="padding-left:16px">${esc(s.transformation)}</div>` : ""}</div>`).join("");
  }
  function highlightColumnLineage(data) {
    if (!data.steps.length) return;
    const pairs = data.steps.map((s) => [s.from.split(".").slice(0, 2).join("."), s.object]);
    graph.highlight(data.objects, graph.edgesBetween(pairs));
  }

  // ---- détail d'un lien ou d'une suggestion (clic sur une arête)
  async function showEdge(e, keepScroll) {
    if (e.id && String(e.id).startsWith("o:")) return;
    $("#detail").onclick = null;
    if (e.status === "SUGGESTION") {
      $("#detail").innerHTML = `<div class="detail"><div class="d-head"><span class="tag-chip">SUGGESTION</span><h2 style="font-size:15px">${esc(e.a || e.source)} ↔ ${esc(e.b || e.target)}</h2>
        <p class="hint" style="padding:4px 0">Entrée : créer le lien · Suppr ou E : écarter</p></div>${e.key ? suggCard(e) : ""}
        ${(e.columns || []).length && e.key ? `<div class="section-title">Colonnes communes</div><table class="col-table">${e.columns.map((c) => `<tr><td class="mono">${esc(c.a)}</td><td class="mono">${esc(c.b)}</td><td class="${c.mismatch ? "type-warn" : "muted"}">${esc(c.type_a || "?")} / ${esc(c.type_b || "?")}</td></tr>`).join("")}</table>` : ""}</div>`;
      $("#detail").onclick = (ev) => { const a = ev.target.closest("[data-act]"); if (a) suggAction(e, a.dataset.act); const op = ev.target.closest("[data-open]"); if (op) goToObject(op.dataset.open); };
      return;
    }
    let detail = null;
    try { detail = await api("link/" + enc(e.id)); } catch (err) { /* lien supprimé entre-temps */ }
    const l = detail ? detail.link : e;
    const scroll = $("#detail").scrollTop;
    $("#detail").innerHTML = `<div class="detail"><div class="d-head"><span class="tag-chip">${esc(KIND_LABEL[l.kind].toUpperCase())}</span>${l.origin === "SQL" ? ' <span class="badge-sql">SQL</span>' : ""}
      <h2 style="font-size:15px"><span class="obj link" data-open="${esc(l.source)}">${esc(l.source)}</span> <span class="muted">${l.kind === "FLOW" ? "→" : "="}</span> <span class="obj link" data-open="${esc(l.target)}">${esc(l.target)}</span></h2>
      <p class="hint" style="padding:4px 0">Raccourcis : Entrée = valider · Suppr ou E = supprimer · Ctrl+Z = annuler</p></div>
      ${linkCard(l, "")}
      ${detail && detail.rules.length ? `<div class="section-title">Règles de gestion de la requête</div>${detail.rules.map((r) => ruleCard(r, "")).join("")}` : ""}</div>`;
    if (keepScroll) $("#detail").scrollTop = scroll;
    $("#detail").onclick = (ev) => { const a = ev.target.closest("[data-act]"); if (a) return linkAction(l, a.dataset.act); const op = ev.target.closest("[data-open]"); if (op) goToObject(op.dataset.open); };
  }

  // ================================================================== vue colonne : tables + chaîne d'existence
  async function openColumn(name, keep, noPush) {
    try {
      const data = await api(`column?name=${enc(name)}&similar=${$("#col-similar").checked ? "1" : "0"}`);
      if (!keep && !noPush) pushNav();
      state.view = { type: "column", name }; state.center = null; location.hash = "col=" + enc(name);
      showBars("column"); $("#colbar-name").textContent = name;
      $("#graph-empty").classList.add("hidden");
      if (!noPush) graph.setColumn(data);
      $("#graph-info").textContent = `${data.tables.length} objet(s) · ${data.links.length} lien(s)`;
      if (!keep) state.picked = [];
      renderColumnPanel(data);
      renderCrumbs();
    } catch (e) { fail(e); }
  }
  function renderColumnPanel(data) {
    state.picked = (state.picked || []).filter((id) => data.tables.some((t) => t.id === id));
    const withCol = data.tables.filter((t) => !t.without_column);
    $("#detail").onclick = null;
    $("#detail").innerHTML = `<div class="detail"><div class="d-head"><span class="tag-chip">COLONNE</span><h2 class="mono">${esc(data.name)}</h2>
      <div class="sub">${withCol.length} table(s) · types : ${Object.entries(data.types).map(([t, n]) => `${esc(t)} (${n})`).join(", ") || "—"}</div>
      ${data.type_conflict ? '<p class="problem">⚠ Types différents selon les tables : vérifiez les jointures.</p>' : ""}</div>
      ${(data.chains || []).length ? `<div class="section-title">Chaîne d'existence</div><p class="hint" style="padding-top:0">D'où vient la colonne, de la table la plus en aval jusqu'à l'origine (objet métier, source…).</p>
        ${data.chains.map((c) => `<div class="chain"><div class="chain-head"><span class="obj" data-open="${esc(c.object)}">${esc(short(c.object))}</span>.<b class="mono">${esc(data.name)}</b></div><div class="col-lineage">${chainHtml(c.steps, "up")}</div></div>`).join("")}` : ""}
      <div class="section-title">Tables qui portent la colonne</div>
      <p class="hint" style="padding-top:0">Cochez deux tables puis « Créer un lien » : la colonne est préremplie.</p>
      <div class="d-actions"><button class="btn sm primary" id="col-link" ${state.picked.length === 2 ? "" : "disabled"}>Créer un lien (${state.picked.length}/2)</button></div>
      <table class="col-table">${withCol.map((t) => `<tr class="pickable ${state.picked.includes(t.id) ? "picked" : ""}" data-pickt="${esc(t.id)}">
        <td><input type="checkbox" ${state.picked.includes(t.id) ? "checked" : ""}></td><td><div>${esc(t.name)} ${newBadge(t)}</div><div class="muted" style="font-size:11.5px">${t.tag ? esc(t.tag) + " · " : ""}${esc(t.database)}</div></td>
        <td class="mono ${t.type_differs ? "type-warn" : "muted"}">${esc(t.columns.map((c) => c.name === data.name ? (c.type || "?") : `${c.name}`).join(", "))}</td>
        <td><button class="btn sm ghost" data-open="${esc(t.id)}">ouvrir</button></td></tr>`).join("")}</table>
      ${data.similar_names.length ? `<div class="section-title">Noms proches</div><p style="margin:0">${data.similar_names.map((n) => `<a href="#" data-colname="${esc(n)}" class="mono">${esc(n)}</a>`).join(" · ")}</p>` : ""}
      ${data.links.filter((l) => l.status !== "SUGGESTION").length ? `<div class="section-title">Liens entre ces tables</div>${data.links.filter((l) => l.status !== "SUGGESTION").map((l) => linkCard(l, "")).join("")}` : ""}</div>`;
    $("#detail").onclick = async (ev) => {
      const op = ev.target.closest("[data-open]"); if (op) return openObject(op.dataset.open);
      const cn = ev.target.closest("[data-colname]"); if (cn) { ev.preventDefault(); return openColumn(cn.dataset.colname); }
      const act = ev.target.closest("[data-act]"); if (act) return linkAction(data.links.find((l) => l.id === act.closest("[data-link]").dataset.link), act.dataset.act);
      if (ev.target.closest("#col-link")) return openLinkEditor({ source: state.picked[0], target: state.picked[1], column: data.name });
      const row = ev.target.closest("[data-pickt]");
      if (row) {
        const id = row.dataset.pickt;
        state.picked = state.picked.includes(id) ? state.picked.filter((x) => x !== id) : [...state.picked, id].slice(-2);
        renderColumnPanel(data);
        graph.highlight(state.picked.length ? [...state.picked] : null, null);
      }
    };
  }
  $("#colbar-close").addEventListener("click", () => { if (state.nav.length) goBack(); else { state.view = null; resetCanvas(); } });
  $("#col-similar").addEventListener("change", () => { if (state.view && state.view.type === "column") openColumn(state.view.name, true); });

  // ---- dictionnaire des colonnes
  async function loadColumns() {
    try {
      const r = await api(`columns?q=${enc($("#col-filter").value.trim())}&conflicts=${$("#col-conflicts").checked ? "1" : "0"}&limit=300`);
      $("#cols").innerHTML = r.items.map((c) => `<div class="item" data-colname="${esc(c.name)}"><div class="txt">
        <div class="title mono">${esc(c.name)}</div><div class="sub">${c.nb_objects} table(s) · <span class="${c.conflict ? "type-warn" : ""}">${Object.keys(c.types).map(esc).join(" / ")}${c.conflict ? " ⚠" : ""}</span></div></div></div>`).join("")
        + (r.total > r.items.length ? `<p class="hint">${r.total - r.items.length} autre(s) : affinez le filtre.</p>` : "") || '<p class="hint">Aucune colonne.</p>';
    } catch (e) { if (e.status !== 503) fail(e); }
  }
  $("#cols").addEventListener("click", (ev) => { const it = ev.target.closest("[data-colname]"); if (it) openColumn(it.dataset.colname); });
  $("#col-filter").addEventListener("input", debounce(loadColumns, 250));
  $("#col-conflicts").addEventListener("change", loadColumns);

  // ================================================================== à valider / suggestions
  async function loadReview() {
    try {
      const r = await api("review");
      $("#review-count").textContent = r.items.length || "";
      const sql = r.items.filter((l) => l.origin === "SQL").length;
      $("#review").innerHTML = (r.items.length ? `${sql > 1 ? `<div class="bulk"><span class="muted">${sql} lien(s) lus dans le SQL</span><button class="btn sm ok" id="validate-sql">Tout valider</button></div>` : ""}`
        + r.items.map((l) => `<div class="link-card" data-link="${esc(l.id)}" style="padding:10px 6px">
          <div class="row"><span class="obj" data-open="${esc(l.source)}">${esc(short(l.source))}</span><span class="muted">${l.kind === "FLOW" ? "→" : "="}</span><span class="obj" data-open="${esc(l.target)}">${esc(short(l.target))}</span></div>
          <div class="meta">${KIND_LABEL[l.kind]}${l.origin === "SQL" ? ' · <span class="badge-sql">SQL</span>' : ""} · ${esc(l.created_by)} · ${esc(fmtDate(l.created_at))}${(l.columns || []).length ? ` · ${l.columns.length} colonne(s)` : ""}</div>
          ${(l.problems || []).map((p) => `<div class="problem">⚠ ${esc(p)}</div>`).join("")}
          <div class="btns"><button class="btn sm ok" data-act="validate">Valider</button><button class="btn sm" data-act="edit">Modifier</button><button class="btn sm danger" data-act="delete">Supprimer</button></div></div>`).join("")
        : '<p class="hint">Aucun lien en attente de validation.</p>')
        + (r.recently_deleted.length ? `<div class="section-title" style="padding:0 6px">Supprimés récemment</div>${r.recently_deleted.slice(0, 8).map((l) => `<div class="meta" style="padding:2px 6px">${esc(short(l.source))} ${l.kind === "FLOW" ? "→" : "="} ${esc(short(l.target))} — ${esc(l.deleted_by)}, ${esc(fmtDate(l.deleted_at))}</div>`).join("")}` : "");
      $("#review").onclick = async (ev) => {
        if (ev.target.closest("#validate-sql")) {
          const items = r.items.filter((l) => l.origin === "SQL");
          return confirmBox(`Valider les ${items.length} liens lus dans le SQL ?`, "Chacun pourra être annulé depuis l'historique.", "Tout valider", async () => {
            for (const l of items) await post("links/validate", { id: l.id });
            toast(`${items.length} lien(s) validé(s)`); refreshAll();
          });
        }
        const card = ev.target.closest("[data-link]"); if (!card) return;
        const l = r.items.find((x) => x.id === card.dataset.link);
        const act = ev.target.closest("[data-act]"); if (act) return linkAction(l, act.dataset.act);
        const op = ev.target.closest("[data-open]"); if (op) openObject(op.dataset.open);
      };
    } catch (e) { if (e.status !== 503) fail(e); }
  }
  async function loadSuggestions() {
    try {
      const r = await api("suggestions?limit=100");
      $("#sugg-count").textContent = r.total || "";
      $("#sugg").innerHTML = r.items.map((s) => suggCard(s)).join("") + (r.total > r.items.length ? `<p class="hint">+ ${r.total - r.items.length} autres (les plus fortes sont en haut).</p>` : "") || '<p class="hint">Aucune suggestion.</p>';
      $("#sugg").onclick = (ev) => {
        const card = ev.target.closest("[data-sugg]"); if (!card) return;
        const s = r.items.find((x) => x.key === card.dataset.sugg);
        const act = ev.target.closest("[data-act]"); if (act) return suggAction(s, act.dataset.act);
        const op = ev.target.closest("[data-open]"); if (op) openObject(op.dataset.open);
      };
    } catch (e) { if (e.status !== 503) fail(e); }
  }

  // ================================================================== suivi (anomalies, modifications, améliorations)
  async function loadTracking() {
    try {
      const [r, a] = await Promise.all([api("tickets?status=" + enc($("#track-filter").value)), api("anomalies")]);
      $("#track").innerHTML = (a.items.length ? `<details class="anomalies"><summary>Anomalies détectées automatiquement · ${a.items.length}</summary>
          ${a.items.slice(0, 60).map((x, i) => `<div class="anomaly"><div><b class="ak">${esc(x.kind)}</b> ${esc(x.title)}</div><div class="muted" style="font-size:12px">${esc(x.detail || "")}</div>
            <div class="btns">${x.object_id ? `<button class="btn sm ghost" data-aopen="${esc(x.object_id)}">ouvrir</button>` : ""}${x.into ? `<button class="btn sm" data-amerge="${i}">Fusionner</button>` : ""}<button class="btn sm" data-aticket="${i}">Créer un sujet</button></div></div>`).join("")}</details>` : "")
        + (r.items.map(ticketCard).join("") || '<p class="hint">Aucun sujet. « + Signaler » pour déclarer une anomalie, une demande de modification ou une amélioration de l\'outil.</p>');
      $("#track").onclick = async (ev) => {
        const op = ev.target.closest("[data-aopen], [data-open]"); if (op) return goToObject(op.dataset.aopen || op.dataset.open);
        const at = ev.target.closest("[data-aticket]"); if (at) { const x = a.items[Number(at.dataset.aticket)]; return openTicket(null, { title: x.title, description: x.detail, object_id: x.object_id, column: x.column, link_id: x.link_id }); }
        const am = ev.target.closest("[data-amerge]"); if (am) { const x = a.items[Number(am.dataset.amerge)]; await write("objects/merge", { id: x.object_id, into: x.into }, "Table New fusionnée : ses liens ont suivi", true); return refreshAll(); }
        const tk = ev.target.closest("[data-ticket]"); if (tk) return openTicket(r.items.find((t) => t.id === tk.dataset.ticket));
      };
    } catch (e) { if (e.status !== 503) fail(e); }
  }
  $("#track-filter").addEventListener("change", loadTracking);
  $("#btn-new-ticket").addEventListener("click", () => openTicket(null, { object_id: state.selected && !String(state.selected).startsWith("g:") ? state.selected : null }));

  function openTicket(t, preset) {
    const p = t || { kind: "ANOMALIE", priority: "NORMALE", status: "OUVERT", ...(preset || {}) };
    const opt = (map, value) => Object.entries(map).map(([k, v]) => `<option value="${k}" ${k === value ? "selected" : ""}>${esc(v)}</option>`).join("");
    const body = openModal(`<h2>${t ? "Sujet de suivi" : "Signaler"}</h2>
      <div class="form">
        <label>Titre<input id="tk-title" value="${esc(p.title || "")}" placeholder="ex. Montant négatif dans vue_360"></label>
        <div class="row2"><label>Type<select id="tk-kind">${opt(TICKET_KIND, p.kind)}</select></label><label>Priorité<select id="tk-prio">${opt(PRIORITY, p.priority)}</select></label></div>
        <div class="row2"><label>Objet concerné<span class="suggest"><input id="tk-object" class="input" value="${esc(p.object_id || "")}" placeholder="facultatif (amélioration de l'outil)"></span></label><label>Colonne<input id="tk-column" value="${esc(p.column || "")}"></label></div>
        <label>Description<textarea id="tk-desc" rows="3">${esc(p.description || "")}</textarea></label>
        ${t ? `<div class="row2"><label>Statut<select id="tk-status">${opt(TICKET_STATUS, p.status)}</select></label><label>Suivi par<input id="tk-assignee" value="${esc(p.assignee || "")}"></label></div>` : ""}
        <div style="display:flex;gap:8px"><button class="btn primary" id="tk-save">${t ? "Enregistrer" : "Créer le sujet"}</button>${t ? '<button class="btn danger" id="tk-del">Supprimer</button>' : ""}</div>
      </div>
      ${t ? `<div class="section-title">Historique</div>${(t.history || []).map((h) => `<div class="meta">${esc(TICKET_STATUS[h.status] || h.status)} — ${esc(h.by)}, ${esc(fmtDate(h.at))}</div>`).join("")}
        <div class="section-title">Commentaires</div>${(t.comments || []).map((c) => `<div class="note">${esc(c.text)}<div class="meta">${esc(c.author)} · ${esc(fmtDate(c.at))}</div></div>`).join("") || '<p class="hint">Aucun commentaire.</p>'}
        <div class="form"><textarea id="tk-comment" rows="2" placeholder="Ajouter un commentaire…"></textarea><button class="btn sm" id="tk-add" style="justify-self:start">Commenter</button></div>` : ""}`);
    objectAutocomplete($("#tk-object", body));
    $("#tk-save", body).onclick = async () => {
      const data = { title: $("#tk-title", body).value, kind: $("#tk-kind", body).value, priority: $("#tk-prio", body).value,
        object_id: $("#tk-object", body).value.trim() || null, column: $("#tk-column", body).value.trim() || null, description: $("#tk-desc", body).value };
      if (t) { data.id = t.id; data.status = $("#tk-status", body).value; data.assignee = $("#tk-assignee", body).value.trim() || null; }
      if (p.link_id) data.link_id = p.link_id;
      try { await post("tickets/save", data); closeModal(); toast(t ? "Sujet mis à jour" : "Sujet créé dans « Suivi »", false, undoAction); refreshAll(); } catch (e) { fail(e); }
    };
    if (t) {
      $("#tk-del", body).onclick = () => confirmBox("Supprimer ce sujet ?", "Ctrl+Z pour revenir en arrière.", "Supprimer", async () => { await write("tickets/delete", { id: t.id }, "Sujet supprimé", true); refreshAll(); }, true);
      $("#tk-add", body).onclick = async () => {
        const text = $("#tk-comment", body).value.trim(); if (!text) return;
        try { await post("tickets/comment", { id: t.id, text }); const r = await api("tickets"); openTicket(r.items.find((x) => x.id === t.id)); refreshAll(); } catch (e) { fail(e); }
      };
    }
  }

  // ================================================================== modales (ne se ferment jamais sur un clic à côté)
  function openModal(html, wide) {
    $("#modal-body").innerHTML = html; $("#modal").classList.remove("hidden"); $(".modal-box").classList.toggle("wide", !!wide);
    const first = $("#modal-body input:not([type=hidden]):not([disabled]), #modal-body textarea"); if (first) setTimeout(() => first.focus(), 30);
    return $("#modal-body");
  }
  function closeModal() { $("#modal").classList.add("hidden"); $("#modal-body").innerHTML = ""; }
  const modalOpen = () => !$("#modal").classList.contains("hidden");
  $(".modal-close").addEventListener("click", closeModal);

  // Fenêtre de confirmation (remplace confirm() : même style, Entrée = confirmer, Échap = annuler).
  function confirmBox(title, text, okLabel, onOk, danger) {
    const body = openModal(`<h2>${esc(title)}</h2><p class="muted">${esc(text)}</p>
      <div style="display:flex;gap:8px;margin-top:14px"><button class="btn ${danger ? "danger" : "primary"}" id="cf-ok">${esc(okLabel)}</button><button class="btn" id="cf-cancel">Annuler</button></div>`);
    $("#cf-ok", body).focus();
    $("#cf-ok", body).onclick = async () => { closeModal(); try { await onOk(); } catch (e) { /* message déjà affiché */ } };
    $("#cf-cancel", body).onclick = closeModal;
  }

  function objectAutocomplete(input, onPick) {
    const wrap = input.parentElement; wrap.classList.add("suggest");
    const list = document.createElement("div"); list.className = "suggest-list hidden"; wrap.appendChild(list);
    input.addEventListener("input", debounce(async () => {
      const q = input.value.trim(); if (q.length < 2) { list.classList.add("hidden"); return; }
      const r = await api(`search?q=${enc("table:" + q)}&limit=30`);
      list.innerHTML = r.objects.map((o) => `<div data-id="${esc(o.id)}"><i class="swatch" style="background:${esc(o.color || "")}"></i>${esc(o.id)} ${newBadge(o)}</div>`).join("");
      list.classList.toggle("hidden", !r.objects.length);
    }, 200));
    list.addEventListener("click", (e) => { const it = e.target.closest("[data-id]"); if (!it) return; input.value = it.dataset.id; list.classList.add("hidden"); onPick && onPick(it.dataset.id); });
  }

  // ---- éditeur de lien (création depuis une table, une suggestion, la vue colonne ; modification)
  async function openLinkEditor(opts) {
    const link = opts.link, sug = opts.suggestion;
    const st = { kind: link ? link.kind : (sug && sug.kind) || "FLOW", source: "", target: "", columns: [], comment: link ? link.comment : "", keep: false };
    if (link) { st.source = link.source; st.target = link.target; st.columns = (link.columns || []).map((c) => ({ ...c })); }
    else if (sug) {
      st.source = sug.proposed_source || sug.a; st.target = st.source === sug.a ? sug.b : sug.a;
      const flip = st.source !== sug.a;
      st.columns = (sug.mapping && sug.mapping.length ? sug.mapping.map((m) => ({ source: m.source, target: m.target, transformation: m.transformation }))
        : (sug.columns || []).slice(0, 3).map((c) => ({ source: flip ? c.b : c.a, target: flip ? c.a : c.b })));
      if (sug.direction && sug.direction !== st.source) st.columns = st.columns.map((c) => ({ source: c.target, target: c.source, transformation: c.transformation }));
    } else { st.source = opts.source || ""; st.target = opts.target || ""; if (opts.column) st.columns = [{ source: opts.column, target: opts.column }]; }
    const cols = {}, meta = {};
    const loadCols = async (id) => { if (!id || cols[id]) return; try { const d = await api("object/" + enc(id)); cols[id] = d.columns.map((c) => c.name); meta[id] = d.object; } catch (e) { cols[id] = []; } };
    await Promise.all([loadCols(st.source), loadCols(st.target)]);
    const body = openModal(`<h2>${link ? "Modifier le lien" : "Ajouter un lien"}</h2>
      <p class="muted" style="margin:0">Un flux va dans « À valider » (vous pouvez le valider vous-même) ; une jointure est validée directement. Le sens d'un flux suit l'ordre des tags : le tag le plus à gauche alimente l'autre.</p>
      <div class="form">
        <div class="seg-wide" id="le-kind"><button data-k="FLOW">Flux : une table alimente l'autre</button><button data-k="JOIN">Jointure : les tables se relient</button></div>
        <div id="le-dir"></div>
        <div><div class="section-title" style="margin-top:4px">Colonnes (facultatif) <button class="btn sm ghost" id="le-addcol">+ ajouter</button></div><div id="le-cols" style="display:grid;gap:6px"></div></div>
        <label>Commentaire / règle<input id="le-comment" value="${esc(st.comment || "")}" placeholder="ex. agrégat mensuel, filtre sur les actifs…"></label>
        <div style="display:flex;gap:8px"><button class="btn primary" id="le-save">${link ? "Enregistrer" : "Créer le lien"}</button>
          <button class="btn ok" id="le-save-validate">${link ? "Enregistrer et valider" : "Créer et valider"}</button></div>
      </div>`);
    const renderKind = () => $$("#le-kind button", body).forEach((b) => b.classList.toggle("active", b.dataset.k === st.kind));
    const box = (id, role, end) => id ? `<div class="dir-box"><small>${role}</small>${esc(id)} ${meta[id] ? tagChip(meta[id].tag, meta[id].color) : ""}</div>` : `<div class="dir-box"><small>${role}</small><span class="suggest"><input class="input" data-pickend="${end}" placeholder="chercher une table…"></span></div>`;
    const orderNote = () => {
      if (st.kind !== "FLOW" || !st.source || !st.target) return "";
      const a = meta[st.source], b = meta[st.target];
      if (!a || !b || a.tag_order == null || b.tag_order == null || a.tag_order <= b.tag_order) return "";
      return `<div class="problem" style="margin-top:6px">Selon l'ordre des tags, <b>${esc(a.tag)}</b> (${esc(a.tag_order)}) vient après <b>${esc(b.tag)}</b> (${esc(b.tag_order)}) : le sens sera inversé automatiquement.
        <label class="check small" style="margin-top:4px"><input type="checkbox" id="le-keep" ${st.keep ? "checked" : ""}> garder ce sens quand même</label></div>`;
    };
    const renderDir = () => {
      const a = st.kind === "FLOW" ? "Source (alimente)" : "Table A", b = st.kind === "FLOW" ? "Cible (est alimentée)" : "Table B";
      $("#le-dir", body).innerHTML = `<div class="dir-choice">${box(st.source, a, "source")}<button class="btn sm" id="le-swap" title="Inverser le sens">${st.kind === "FLOW" ? "→ ⇄" : "⇄"}</button>${box(st.target, b, "target")}</div>
        ${st.source && st.target ? `<p class="muted" style="font-size:12.5px;margin:6px 0 0">${st.kind === "FLOW" ? `<b>${esc(short(st.source))}</b> alimente <b>${esc(short(st.target))}</b>.` : `Jointure entre <b>${esc(short(st.source))}</b> et <b>${esc(short(st.target))}</b>.`}</p>` : ""}
        ${orderNote()}
        ${st.source && st.target && !link ? '<button class="btn sm ghost" id="le-reset" style="margin-top:4px">changer de table</button>' : ""}`;
      $$("[data-pickend]", body).forEach((inp) => objectAutocomplete(inp, async (id) => { st[inp.dataset.pickend] = id; await loadCols(id); renderDir(); renderCols(); }));
      const swap = $("#le-swap", body);
      if (swap) swap.onclick = () => { [st.source, st.target] = [st.target, st.source]; st.columns = st.columns.map((c) => ({ source: c.target, target: c.source, transformation: c.transformation })); renderDir(); renderCols(); };
      const reset = $("#le-reset", body); if (reset) reset.onclick = () => { st.target = ""; renderDir(); renderCols(); };
      const keep = $("#le-keep", body); if (keep) keep.onchange = () => { st.keep = keep.checked; };
    };
    const options = (id, sel) => `<option value="">(colonne)</option>` + (cols[id] || []).map((c) => `<option ${c === sel ? "selected" : ""}>${esc(c)}</option>`).join("");
    const renderCols = () => {
      $("#le-cols", body).innerHTML = st.columns.map((c, i) => `<div class="map-row" data-i="${i}"><select data-f="source">${options(st.source, c.source)}</select>
        <select data-f="target">${options(st.target, c.target)}</select><input data-f="transformation" placeholder="transformation" value="${esc(c.transformation || "")}">
        <button class="btn sm ghost" data-rm="${i}">×</button></div>`).join("") || '<p class="hint" style="padding:0">Lien table → table. Ajoutez des colonnes pour un lien colonne → colonne.</p>';
    };
    $("#le-kind", body).onclick = (e) => { const b = e.target.closest("[data-k]"); if (b) { st.kind = b.dataset.k; renderKind(); renderDir(); } };
    $("#le-cols", body).addEventListener("change", (e) => { const row = e.target.closest("[data-i]"); if (row) st.columns[row.dataset.i][e.target.dataset.f] = e.target.value; });
    $("#le-cols", body).addEventListener("input", (e) => { const row = e.target.closest("[data-i]"); if (row && e.target.dataset.f === "transformation") st.columns[row.dataset.i].transformation = e.target.value; });
    $("#le-cols", body).addEventListener("click", (e) => { const rm = e.target.closest("[data-rm]"); if (rm) { st.columns.splice(Number(rm.dataset.rm), 1); renderCols(); } });
    $("#le-addcol", body).onclick = () => { st.columns.push({ source: "", target: "" }); renderCols(); };
    const save = async (validate) => {
      if (!st.source || !st.target) return toast("Choisissez les deux tables.", true);
      const payload = { kind: st.kind, source: st.source, target: st.target, columns: st.columns.filter((c) => c.source || c.target), comment: $("#le-comment", body).value, validate, keep_direction: st.keep };
      try {
        const r = link ? await post("links/update", { ...payload, id: link.id }) : await post("links/create", { ...payload, suggestion_key: sug ? sug.key : null });
        closeModal();
        const where = st.kind === "JOIN" || validate ? "validé" : link ? "à revalider" : "dans « À valider »";
        toast(`${link ? "Lien modifié" : "Lien créé"} (${where})${r.swapped ? " — sens corrigé selon l'ordre des tags" : ""}`, false, undoAction);
        await refreshAll();
      } catch (e) { fail(e); }
    };
    $("#le-save", body).onclick = () => save(false);
    $("#le-save-validate", body).onclick = () => save(true);
    renderKind(); renderDir(); renderCols();
  }

  function openPath(fromId) {
    const body = openModal(`<h2>Chemin entre deux tables</h2><div class="form"><label>Depuis<input value="${esc(fromId)}" disabled></label>
      <label>Vers<span class="suggest"><input id="pt-target" class="input" placeholder="chercher une table…" autocomplete="off"></span></label>
      <button class="btn primary" id="pt-go" style="justify-self:start">Trouver le chemin</button><div id="pt-result"></div></div>`);
    objectAutocomplete($("#pt-target", body));
    $("#pt-go", body).onclick = async () => {
      const to = $("#pt-target", body).value.trim().toLowerCase(); if (!to) return;
      const d = await api(`path?from=${enc(fromId)}&to=${enc(to)}`);
      if (!d.found) { $("#pt-result", body).innerHTML = '<p class="hint">Aucun chemin par les liens documentés.</p>'; return; }
      closeModal(); pushNav(); state.view = { type: "path", id: fromId }; state.center = fromId;
      const lanes = mergeLanes([], d.nodes.filter((n) => n.tag_id).map((n) => ({ id: n.tag_id, name: n.tag, color: n.color, order: n.tag_order })));
      graph.setData({ center: fromId, nodes: d.nodes, edges: d.edges, lanes }); graph.highlight(d.nodes.map((n) => n.id), d.edges.map((e) => e.edge_id));
      $("#graph-empty").classList.add("hidden"); showBars("path"); updateInfo(); renderCrumbs();
      toast(`Chemin : ${d.nodes.map((n) => n.name).join(" → ")}`);
    };
  }

  // ---- objets créés à la main
  function openObjectEditor(existing, preset) {
    const p = existing || { type: "OBJET_METIER", database: "manuel", ...(preset || {}) };
    const types = state.info.object_types || {};
    const body = openModal(`<h2>${existing ? "Modifier l'objet" : "Créer un objet"}</h2>
      <p class="muted" style="margin:0">Objet métier à l'origine des données, source externe, viz finale, ou table prévue mais pas encore construite. Il porte l'étiquette <span class="badge-new">New</span> jusqu'à sa validation ou sa fusion avec sa vraie définition (SQL / JSON).</p>
      <div class="form">
        <div class="row2"><label>Nom<input id="ob-name" value="${esc(p.name || "")}" ${existing ? "disabled" : ""} placeholder="ex. dossier_credit"></label>
          <label>Base / espace<input id="ob-db" value="${esc(p.database || "manuel")}" ${existing ? "disabled" : ""} placeholder="ex. metier, powerbi"></label></div>
        <div class="row2"><label>Type<select id="ob-type">${Object.entries(types).map(([k, v]) => `<option value="${k}" ${k === p.type ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></label>
          ${existing ? "" : `<label>Tag<select id="ob-tag"><option value="">— sans tag —</option>${tags().map((t) => `<option value="${esc(t.id)}" ${t.id === p.tag_id ? "selected" : ""}>${esc(t.name)}</option>`).join("")}</select></label>`}</div>
        <label>Description<textarea id="ob-desc" rows="2">${esc(p.description || "")}</textarea></label>
        <label>Colonnes (facultatif, une par ligne : nom [type])<textarea id="ob-cols" rows="4" class="mono" placeholder="code_client string&#10;montant decimal(18,2)">${esc((p.columns || []).map((c) => c.name + (c.type ? " " + c.type : "")).join("\n"))}</textarea></label>
        ${!existing && p.link_with ? `<label class="check"><input type="checkbox" id="ob-link" checked> relier à <b>${esc(short(p.link_with))}</b>
          <select id="ob-link-dir" class="input sm" style="width:auto"><option value="source">(le nouvel objet l'alimente)</option><option value="target">(il est alimenté par lui)</option></select></label>` : ""}
        <button class="btn primary" id="ob-save" style="justify-self:start">${existing ? "Enregistrer" : "Créer l'objet"}</button>
      </div>`);
    $("#ob-save", body).onclick = async () => {
      try {
        if (existing) {
          await post("objects/edit", { id: existing.id, type: $("#ob-type", body).value, description: $("#ob-desc", body).value, columns: $("#ob-cols", body).value });
          closeModal(); toast("Objet modifié", false, undoAction); return refreshAll();
        }
        const r = await post("objects/create", { name: $("#ob-name", body).value, database: $("#ob-db", body).value, type: $("#ob-type", body).value,
          tag_id: $("#ob-tag", body).value || null, description: $("#ob-desc", body).value, columns: $("#ob-cols", body).value });
        const lk = $("#ob-link", body);
        if (lk && lk.checked) {
          const asSource = $("#ob-link-dir", body).value === "source";
          await post("links/create", { kind: "FLOW", source: asSource ? r.id : p.link_with, target: asSource ? p.link_with : r.id });
        }
        closeModal(); toast(`Objet ${short(r.id)} créé (New)`, false, undoAction);
        await refreshAll();
        if (lk && lk.checked && state.view && state.view.type === "object") { expand(r.id); } else openObject(r.id);
      } catch (e) { fail(e); }
    };
  }
  $("#btn-new-object").addEventListener("click", () => openObjectEditor(null, { link_with: state.selected && state.view && state.view.type === "object" ? state.selected : null }));

  // ---- tags et règles
  async function openTags() {
    await loadState();
    const body = openModal(`<h2>Tags</h2><p class="muted" style="margin:0">Un tag par objet. L'<b>ordre</b> définit le sens d'alimentation et la place dans le graphe : un tag d'ordre 1 est tout à gauche et alimente les tags d'ordre plus grand (ex. 1 objet métier, 2 lac, 3 BV, 4 mart, 5 viz). Des tags de même ordre sont au même niveau.</p>
      <div class="section-title">Tags</div><div id="tg-list"></div>
      <div class="tag-row"><input type="color" id="tg-color" value="${esc(state.info.palette[tags().length % state.info.palette.length])}"><input type="text" id="tg-name" placeholder="Nouveau tag (ex. Mart risques)"><input type="number" id="tg-order" placeholder="ordre"><button class="btn sm primary" id="tg-add">Ajouter</button></div>
      <div class="section-title">Règles de nommage</div><p class="hint" style="padding-top:0">Exemple : <span class="mono">fiche_client_*</span> → « Fiche client ». Appliquées à chaque import ; un tag choisi à la main est prioritaire.</p>
      <div id="rl-list"></div>
      <div class="rule-row"><input class="input" id="rl-pattern" placeholder="motif, ex. tracking_*" style="flex:1"><select id="rl-tag" class="input" style="width:auto"></select><button class="btn sm primary" id="rl-add">Ajouter</button></div>`);
    const render = () => {
      $("#tg-list", body).innerHTML = (tags().length ? `<div class="flow-preview">${tags().filter((t) => t.order != null).map((t) => `<span class="tag-chip"><i class="swatch" style="background:${esc(t.color)}"></i>${esc(t.name)}</span>`).join('<span class="muted">→</span>')}</div>` : "")
        + tags().map((t) => `<div class="tag-row" data-tid="${esc(t.id)}"><input type="color" data-f="color" value="${esc(t.color || "#a1a1aa")}">
        <input type="text" data-f="name" value="${esc(t.name)}"><input type="number" data-f="order" value="${t.order ?? ""}" placeholder="ordre">
        <span><span class="muted" style="font-size:12px">${t.count} obj.</span> <button class="btn sm ghost" data-del="${esc(t.id)}">×</button></span></div>`).join("") || '<p class="hint">Aucun tag pour l\'instant.</p>';
      $("#rl-list", body).innerHTML = state.info.rules.map((r) => { const t = tagById(r.tag_id); return `<div class="rule-row"><span class="mono">${esc(r.pattern)}</span>→ ${tagChip(t && t.name, t && t.color)}<button class="btn sm ghost" data-delrule="${esc(r.id)}">×</button></div>`; }).join("") || '<p class="hint">Aucune règle.</p>';
      fillTagSelect($("#rl-tag", body), false);
    };
    const reload = async () => { await loadState(); render(); refreshCatalog(); };
    $("#tg-list", body).addEventListener("change", async (e) => {
      const row = e.target.closest("[data-tid]"); if (!row) return;
      const t = tagById(row.dataset.tid);
      const data = { id: t.id, name: $("[data-f=name]", row).value, color: $("[data-f=color]", row).value, order: $("[data-f=order]", row).value, description: t.description };
      try { await post("tags/save", data); await reload(); } catch (err) { fail(err); }
    });
    body.addEventListener("click", async (e) => {
      const del = e.target.closest("[data-del]");
      if (del) { const t = tagById(del.dataset.del); if (!t) return; await write("tags/delete", { id: t.id }, `Tag « ${t.name} » supprimé`, true); await reload(); }
      const dr = e.target.closest("[data-delrule]"); if (dr) { await write("rules/delete", { id: dr.dataset.delrule }, "Règle supprimée", true); await reload(); }
    });
    $("#tg-add", body).onclick = async () => {
      try { await post("tags/save", { name: $("#tg-name", body).value, color: $("#tg-color", body).value, order: $("#tg-order", body).value }); toast("Tag créé"); $("#tg-name", body).value = ""; $("#tg-order", body).value = ""; await reload();
        $("#tg-color", body).value = state.info.palette[tags().length % state.info.palette.length]; } catch (err) { fail(err); }
    };
    $("#rl-add", body).onclick = async () => {
      try { const r = await post("rules/save", { pattern: $("#rl-pattern", body).value, tag_id: $("#rl-tag", body).value }); toast(`Règle ajoutée : ${r.matched} objet(s) concerné(s)`); $("#rl-pattern", body).value = ""; await reload(); } catch (err) { fail(err); }
    };
    render();
  }
  $("#btn-tags").addEventListener("click", openTags);

  // ---- versions et historique (retour dans le temps)
  $("#btn-save-version").addEventListener("click", () => {
    const body = openModal(`<h2>Enregistrer la version actuelle</h2><p class="muted" style="margin:0">Fige l'état complet (tags, liens, objets créés, documentation, suivi) dans l'espace partagé et ouvre un nouveau fichier de journal. Chaque modification est déjà sauvegardée automatiquement ; une version sert de point de repère que l'on peut comparer ou restaurer.</p>
      <div class="form"><label>Nom<input id="v-label" placeholder="ex. Validation équipe data — octobre"></label><label>Commentaire<textarea id="v-comment" rows="3"></textarea></label>
      <button class="btn primary" id="v-save" style="justify-self:start">Enregistrer</button></div>`);
    $("#v-save", body).onclick = async () => {
      try { const r = await post("versions/save", { label: $("#v-label", body).value, comment: $("#v-comment", body).value }); closeModal(); toast(`Version enregistrée : ${r.label}`); } catch (e) { fail(e); }
    };
  });

  const diffHtml = (d) => {
    const li = (title, items) => items && items.length ? `<div><b>${title}</b><ul class="diff-list">${items.slice(0, 30).map((x) => `<li class="mono">${esc(x)}</li>`).join("")}${items.length > 30 ? `<li>… ${items.length - 30} autre(s)</li>` : ""}</ul></div>` : "";
    return li("Liens ajoutés", d.links_added) + li("Liens supprimés", d.links_removed) + li("Liens validés", d.links_validated)
      + li("Tags ajoutés", d.tags_added) + li("Tags supprimés", d.tags_removed) + li("Objets créés", d.objects_created) + li("Objets retirés", d.objects_removed)
      + li("Sujets de suivi", d.tickets_added)
      + `<p class="muted" style="font-size:12px">${d.assignments_changed} changement(s) de tag · ${d.rules_changed} règle(s) · ${d.objects_documented} objet(s) documenté(s) · ${d.notes_changed} objet(s) avec notes modifiées${d.metadata_changed ? " · métadonnées réimportées" : ""}</p>`;
  };
  async function openHistory(tab) {
    const [versions, activity] = await Promise.all([api("versions"), api("activity?limit=300")]);
    const body = openModal(`<h2>Historique</h2><div class="seg-wide" id="h-tabs"><button data-h="activity">Journal des modifications</button><button data-h="versions">Versions enregistrées (${versions.length})</button></div>
      <div id="h-activity"><p class="hint" style="padding-top:8px">Chaque ligne peut être annulée (elle reste visible, barrée) ou servir de point de retour : « voir » montre ce qui a changé depuis, « revenir ici » annule tout ce qui a suivi.</p>
        <input type="range" id="h-range" min="0" max="${Math.max(0, activity.length - 1)}" value="0" class="h-range" title="Remonter le temps">
        <div id="h-point" class="h-point"></div>
        <div id="h-list">${activity.map((a, i) => `<div class="activity-row ${a.reverted ? "reverted" : ""} ${a.control ? "control" : ""}" data-ev="${esc(a.id)}" data-i="${i}">
          <span class="muted">${esc(fmtDate(a.at))}</span><b>${esc(a.user)}</b><span>${esc(a.label)}${a.detail ? ` : <span class="mono">${esc(a.detail)}</span>` : ""}</span>
          <span class="h-btns">${a.control ? "" : `<button class="btn sm ghost" data-hact="at">voir</button>${a.reverted ? '<button class="btn sm ghost" data-hact="restore">rétablir</button>' : '<button class="btn sm ghost" data-hact="revert">annuler</button>'}`}</span></div>`).join("") || '<p class="hint">Aucune activité.</p>'}</div></div>
      <div id="h-versions" class="hidden">${versions.map((v) => `<div class="version-row" data-file="${esc(v.file)}"><div><b>${esc(v.label)}</b><div class="muted" style="font-size:12px">${esc(v.by)} · ${esc(fmtDate(v.at))}${v.comment ? " — " + esc(v.comment) : ""}</div><div data-diff></div></div>
        <div style="display:flex;gap:6px"><button class="btn sm" data-vact="diff">Comparer</button><button class="btn sm danger" data-vact="restore">Restaurer</button></div></div>`).join("") || '<p class="hint">Aucune version enregistrée. Utilisez « Enregistrer ».</p>'}</div>`, true);
    const show = (h) => { $$("#h-tabs button", body).forEach((b) => b.classList.toggle("active", b.dataset.h === h)); $("#h-versions", body).classList.toggle("hidden", h !== "versions"); $("#h-activity", body).classList.toggle("hidden", h !== "activity"); };
    $("#h-tabs", body).onclick = (e) => { const b = e.target.closest("[data-h]"); if (b) show(b.dataset.h); };
    const showPoint = async (ev) => {
      const box = $("#h-point", body);
      $$(".activity-row", body).forEach((r) => r.classList.toggle("at", r.dataset.ev === ev.id));
      try {
        const d = await api("history/at?event=" + enc(ev.id));
        box.innerHTML = `<div class="h-card"><div><b>État juste après</b> : ${esc(d.event.label)} ${d.event.detail ? `<span class="mono">${esc(d.event.detail)}</span>` : ""} — ${esc(d.event.user)}, ${esc(fmtDate(d.event.at))}</div>
          <p class="muted" style="font-size:12.5px;margin:4px 0">À ce moment : ${d.counts.links} lien(s), ${d.counts.tags} tag(s), ${d.counts.custom} objet(s) créé(s), ${d.counts.tickets} sujet(s). Depuis : ${d.nb_later} modification(s).</p>
          ${diffHtml(d.diff)}${d.nb_later ? `<button class="btn sm danger" id="h-revert-to">Revenir à ce point (annule les ${d.nb_later} modification(s) suivantes)</button>` : ""}</div>`;
        const btn = $("#h-revert-to", box);
        if (btn) btn.onclick = () => confirmBox("Revenir à ce point pour toute l'équipe ?", `Les ${d.nb_later} modification(s) suivantes sont annulées, mais restent dans le journal : vous pourrez les rétablir.`, "Revenir à ce point", async () => {
          const r = await post("history/revert_to", { event: ev.id }); toast(`${r.done.detail}`, false, undoAction); refreshAll();
        }, true);
      } catch (e) { fail(e); }
    };
    $("#h-range", body).oninput = debounce((e) => { const a = activity[Number(e.target.value)]; if (a && !a.control) showPoint(a); }, 120);
    $("#h-list", body).onclick = async (e) => {
      const b = e.target.closest("[data-hact]"); if (!b) return;
      const row = b.closest("[data-ev]"), ev = activity[Number(row.dataset.i)];
      if (b.dataset.hact === "at") return showPoint(ev);
      try {
        if (b.dataset.hact === "revert") { await post("history/revert", { id: ev.id }); toast(`Annulé : ${ev.label}`); }
        if (b.dataset.hact === "restore") { await post("history/restore", { id: ev.id }); toast(`Rétabli : ${ev.label}`); }
        await refreshAll(); openHistory("activity");
      } catch (err) { fail(err); }
    };
    $("#h-versions", body).onclick = async (e) => {
      const b = e.target.closest("[data-vact]"); if (!b) return;
      const row = b.closest("[data-file]"), file = row.dataset.file;
      if (b.dataset.vact === "diff") {
        try { const d = (await api("version?file=" + enc(file))).diff; $("[data-diff]", row).innerHTML = `<p class="muted" style="font-size:12.5px;margin:6px 0">Depuis cette version :</p>` + diffHtml(d); } catch (err) { fail(err); }
      }
      if (b.dataset.vact === "restore") confirmBox("Restaurer cette version pour toute l'équipe ?", "L'état actuel reste consultable dans l'historique (enregistrez une version avant si besoin).", "Restaurer", async () => {
        await write("versions/restore", { file }, "Version restaurée", true); refreshAll();
      }, true);
    };
    show(tab || "activity");
  }
  $("#btn-history").addEventListener("click", () => openHistory());

  // ================================================================== vue d'ensemble d'un périmètre
  async function loadOverview(keep) {
    const p = new URLSearchParams({ by: $("#ov-by").value, tags: [...state.ovTags].join(","), detail: $("#ov-detail").checked ? "1" : "0" });
    try {
      const d = await api("overview?" + p);
      $("#graph-empty").classList.add("hidden");
      const view = keep ? { ...graph.view } : null;
      if (d.mode === "groups") {
        graph.setOverview(d);
        $("#graph-info").textContent = `${d.groups.length} bloc(s) · ${d.groups.reduce((s, g) => s + g.count, 0)} objet(s) · ${d.edges.reduce((s, e) => s + e.count, 0)} lien(s) entre blocs`;
      } else {
        graph.setData({ center: null, nodes: d.nodes, edges: d.edges, lanes: d.lanes });
        $("#graph-info").textContent = `${d.nodes.length} objet(s) · ${d.edges.length} lien(s)${d.truncated ? ` · +${d.truncated} non affiché(s)` : ""}`;
      }
      if (view) { graph.view = view; graph._applyView(); }
      if (!keep) $("#detail").innerHTML = `<div class="detail"><div class="d-head"><span class="tag-chip">VUE D'ENSEMBLE</span><h2>Périmètre${state.ovTags.size ? ` · ${state.ovTags.size} tag(s)` : " complet"}</h2></div>
        <p class="hint">Un bloc par ${$("#ov-by").value === "tag" ? "tag, rangés de gauche à droite selon leur ordre" : "base"} ; l'épaisseur des flèches suit le nombre de liens. Clic : contenu du bloc. Double-clic : ses objets.</p></div>`;
    } catch (e) { fail(e); }
  }
  function openOverview() {
    pushNav(); state.view = { type: "overview" }; state.center = null; state.selected = null;
    showBars("overview"); renderCrumbs(); loadOverview(false);
  }
  function openOverviewObjects(keys) {
    if ($("#ov-by").value !== "tag") { toast("Passez en « par tag » pour ouvrir les objets d'un bloc.", true); return; }
    pushNav(); state.ovTags = new Set(keys); $("#ov-detail").checked = true; state.view = { type: "overview" }; renderCrumbs(); loadOverview(false);
  }
  function showGroup(g) {
    $("#detail").onclick = null;
    $("#detail").innerHTML = `<div class="detail"><div class="d-head">${tagChip(g.name, g.color)}<h2>${esc(g.name)}</h2><div class="sub">${g.count} objet(s)${g.new ? ` · ${g.new} New` : ""}${g.inner_links ? ` · ${g.inner_links} lien(s) internes` : ""}</div></div>
      <div class="section-title">Bases</div><p style="margin:0;font-size:13px">${Object.entries(g.databases || {}).map(([db, n]) => `${esc(db)} (${n})`).join(" · ")}</p>
      <div class="section-title">Exemples</div><p style="margin:0" class="mono">${(g.sample || []).map(esc).join("<br>")}</p>
      <div class="d-actions"><button class="btn sm primary" id="g-open">Ouvrir ses objets</button></div></div>`;
    $("#g-open").onclick = () => openOverviewObjects([g.key]);
  }
  $("#btn-overview").addEventListener("click", openOverview);
  $("#ov-by").addEventListener("change", () => { state.ovTags.clear(); loadOverview(false); });
  $("#ov-detail").addEventListener("change", () => loadOverview(false));
  $("#ov-close").addEventListener("click", () => { if (state.nav.length) goBack(); else resetCanvas(); });
  $("#ov-tags").addEventListener("click", () => {
    const body = openModal(`<h2>Périmètre</h2><p class="muted" style="margin:0">Choisissez les tags à afficher (aucun = tout le catalogue).</p>
      <div class="chips">${tags().map((t) => `<label class="chip"><input type="checkbox" value="${esc(t.id)}" ${state.ovTags.has(t.id) ? "checked" : ""}><i class="swatch" style="background:${esc(t.color)}"></i>${esc(t.name)} <span class="muted">${t.count}</span></label>`).join("")}
        <label class="chip"><input type="checkbox" value="__none__" ${state.ovTags.has("__none__") ? "checked" : ""}>Sans tag</label></div>
      <button class="btn primary" id="ov-apply" style="margin-top:12px">Afficher</button>`);
    $("#ov-apply", body).onclick = () => { state.ovTags = new Set($$("input:checked", body).map((i) => i.value)); closeModal(); $("#ov-by").value = "tag"; loadOverview(false); };
  });

  // ---- menu « plus »
  $("#more-menu").addEventListener("click", (e) => {
    const b = e.target.closest("[data-menu]"); if (!b) return;
    $("#more-menu").classList.add("hidden");
    if (b.dataset.menu === "export") window.location.href = "/api/export.xlsx";
    if (b.dataset.menu === "report") openReport();
    if (b.dataset.menu === "settings") openSettings();
    if (b.dataset.menu === "help") openHelp();
  });

  async function openReport() {
    try {
      const r = await api("report"), i = r.indicators, b = r.build || {}, m = r.metadata || {};
      const diff = (m.diff || {});
      openModal(`<h2>Rapport</h2>
        <dl class="kv"><dt>Espace</dt><dd class="mono" style="font-size:12px">${esc(state.info.workspace)}</dd><dt>Import</dt><dd>${esc(m.name || "—")} · ${esc(m.by || "")} · ${esc(fmtDate(m.at))}</dd>
        <dt>Analyse SQL</dt><dd>${state.info.sqlglot ? "sqlglot (CTE, sous-requêtes, UNION)" : "expressions régulières (sqlglot absent)"}</dd></dl>
        <div class="stats-grid">
          <div class="stat"><b>${i.objects}</b><span>objets</span></div><div class="stat"><b>${b.nb_columns ?? "–"}</b><span>colonnes</span></div>
          <div class="stat"><b>${Math.round(i.tagged_rate * 100)} %</b><span>objets tagués</span></div><div class="stat"><b>${Math.round(i.linked_rate * 100)} %</b><span>objets reliés</span></div>
          <div class="stat"><b>${i.links_validated}</b><span>liens validés</span></div><div class="stat"><b>${i.links_pending}</b><span>liens à valider</span></div>
          <div class="stat"><b>${i.documented}</b><span>objets documentés</span></div><div class="stat"><b>${b.nb_suggestions ?? "–"}</b><span>suggestions</span></div>
          <div class="stat"><b>${state.info.counts.new_objects}</b><span>objets New</span></div><div class="stat"><b>${state.info.counts.tickets_open}</b><span>sujets ouverts</span></div>
          <div class="stat"><b>${i.events}</b><span>modifications</span></div></div>
        ${diff.first_import ? "" : `<h3>Depuis l'import précédent</h3><p style="font-size:13px">${(diff.objects_added || []).length} objet(s) ajouté(s), ${(diff.objects_removed || []).length} supprimé(s), ${diff.nb_columns_added || 0} colonne(s) ajoutée(s), ${diff.nb_columns_removed || 0} supprimée(s).</p>
          ${(diff.objects_removed || []).length ? `<p class="problem">Objets disparus : ${diff.objects_removed.slice(0, 20).map(esc).join(", ")}</p>` : ""}`}
        ${(b.most_shared_columns || []).length ? `<h3>Colonnes les plus partagées</h3><p style="font-size:13px">${b.most_shared_columns.map((c) => `<a href="#" data-colname="${esc(c.name)}" class="mono">${esc(c.name)}</a> (${c.nb_objects})`).join(" · ")}</p>` : ""}`);
      $("#modal-body").onclick = (e) => { const c = e.target.closest("[data-colname]"); if (c) { e.preventDefault(); closeModal(); openColumn(c.dataset.colname); } };
    } catch (e) { fail(e); }
  }

  function openSettings() {
    const s = state.info.settings;
    const body = openModal(`<h2>Réglages de l'équipe</h2>
      <div class="form"><label>Colonnes techniques à ignorer dans les suggestions (une par ligne)<textarea id="st-ignore" rows="5" class="mono">${esc((s.ignore_columns || []).join("\n"))}</textarea></label>
      <label>Objets à masquer (identifiants complets, un par ligne)<textarea id="st-hidden" rows="4" class="mono">${esc((s.hidden_objects || []).join("\n"))}</textarea></label>
      <button class="btn primary" id="st-save" style="justify-self:start">Enregistrer</button></div>`);
    $("#st-save", body).onclick = async () => {
      const lines = (id) => $(id, body).value.split(/\n|,/).map((x) => x.trim()).filter(Boolean);
      await write("settings/save", { ignore_columns: lines("#st-ignore"), hidden_objects: lines("#st-hidden") }, "Réglages enregistrés (suggestions recalculées)", true);
      closeModal(); refreshAll();
    };
  }

  const SHORTCUTS = [
    ["Ctrl+Z / Ctrl+Y", "annuler / rétablir votre dernière modification"],
    ["Entrée", "lien sélectionné : valider · suggestion : créer le lien"],
    ["Suppr ou E", "lien sélectionné : supprimer · suggestion : écarter"],
    ["← → ↑ ↓", "passer à l'objet en amont, en aval, au-dessus, en dessous"],
    ["Retour arrière", "revenir à la vue précédente"],
    ["F", "vue 360 de l'objet sélectionné"],
    ["+ / − / 0 / C", "zoomer, dézoomer, tout afficher, centrer sur la sélection"],
    ["G", "trouver un objet dans le graphe"],
    ["/", "rechercher dans le catalogue"],
    ["[ / ]", "replier le panneau de gauche / de droite"],
    ["N", "créer un objet"],
    ["Double-clic", "ouvrir les voisins d'un objet (la branche reste ouverte)"],
    ["Clic droit", "masquer un objet du graphe"],
    ["Échap", "fermer la fenêtre ou effacer la mise en évidence"],
  ];
  function openHelp() {
    openModal(`<h2>Raccourcis clavier</h2><table class="col-table shortcuts">${SHORTCUTS.map(([k, v]) => `<tr><td><kbd>${esc(k)}</kbd></td><td>${esc(v)}</td></tr>`).join("")}</table>
      <p class="hint">Les fenêtres ne se ferment jamais en cliquant à côté : utilisez × ou Échap.</p>`);
  }

  // ================================================================== import + fusion d'une table New
  $("#btn-import").addEventListener("click", () => openImport());
  async function openImport(tab, newId) {
    let examples = [];
    try { examples = await api("examples"); } catch (e) { /* aucun exemple */ }
    const news = (state.info && state.info.new_objects) || [];
    const body = openModal(`<h2>Importer</h2>
      <div class="seg-wide" id="imp-tabs"><button data-itab="import">Importer des métadonnées</button><button data-itab="merge">Fusionner une table New${news.length ? ` (${news.length})` : ""}</button></div>
      <div id="imp-import">
      <p class="muted" style="margin:8px 0 0">Un JSON de structure (tables + colonnes) ou des requêtes SQL. Tags, liens, objets créés à la main, documentation et suivi ne sont jamais touchés par un import.</p>
      ${state.info && state.info.has_data ? `<div class="seg-wide" id="imp-mode" style="margin-top:12px">
        <button data-mode="" class="active" title="SQL : complète la structure ; JSON : met à jour la structure en gardant ce que le SQL a apporté">Automatique</button>
        <button data-mode="merge" title="Ajoute les tables, règles et jointures du fichier à la structure actuelle">Compléter</button>
        <button data-mode="refresh" title="Le JSON devient la structure ; règles de gestion et liens lus dans le SQL sont conservés">Mettre à jour</button>
        <button data-mode="replace" title="Repartir de ce seul fichier (tags et liens conservés)">Remplacer</button></div>
        <p class="hint" style="padding:4px 2px 0">Automatique : un <b>.sql</b> complète la structure (tables reconnues, liens, jointures et règles de gestion ajoutés) ; un <b>.json</b> la met à jour sans perdre ce que le SQL a apporté.</p>` : ""}
      <label class="dropzone" id="drop"><input type="file" id="file" accept=".json,.jsonl,.sql,application/json" multiple hidden>
        <b>Déposez un fichier ici</b><br><span class="muted">.json, .jsonl ou un ou plusieurs .sql — ou cliquez pour choisir</span></label>
      <details class="more" style="margin-top:12px"><summary>Ou coller des requêtes SQL</summary>
        <div class="form" style="margin-top:6px"><textarea id="sql-text" rows="6" class="mono"></textarea><button class="btn sm primary" id="sql-go" style="justify-self:start">Analyser le SQL</button></div></details>
      ${examples.length ? `<details class="more" style="margin-top:8px"><summary>Exemples fictifs</summary><div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px">${examples.map((x) => `<button class="btn sm" data-example="${esc(x)}">${esc(x)}</button>`).join("")}</div></details>` : ""}
      <div id="import-result" style="margin-top:14px"></div></div>
      <div id="imp-merge" class="hidden">
        <p class="muted" style="margin:8px 0 0">Une table créée à la main (<span class="badge-new">New</span>) existe maintenant ? Collez son code (<span class="mono">CREATE TABLE … AS SELECT</span>, <span class="mono">INSERT … SELECT</span>, vue) ou son bout de JSON : la table New devient la vraie table, <b>ses liens et sa documentation sont conservés</b>, sans doublon.</p>
        ${news.length ? "" : '<p class="problem">Aucune table New pour l\'instant : créez-en avec « + Objet ».</p>'}
        <div class="form" style="margin-top:8px">
          <div class="seg" id="mg-fmt"><button class="btn-seg active" data-f="sql">SQL</button><button class="btn-seg" data-f="json">JSON</button></div>
          <textarea id="mg-text" rows="8" class="mono" placeholder="CREATE TABLE base.ma_table AS SELECT …"></textarea>
          <button class="btn primary" id="mg-preview" style="justify-self:start">Analyser</button>
          <div id="mg-result"></div></div></div>`, true);
    const showTab = (t) => { $$("#imp-tabs button", body).forEach((b) => b.classList.toggle("active", b.dataset.itab === t)); $("#imp-import", body).classList.toggle("hidden", t !== "import"); $("#imp-merge", body).classList.toggle("hidden", t !== "merge"); };
    $("#imp-tabs", body).onclick = (e) => { const b = e.target.closest("[data-itab]"); if (b) showTab(b.dataset.itab); };
    showTab(tab || "import");
    const modeBox = $("#imp-mode", body);
    if (modeBox) modeBox.onclick = (e) => { const b = e.target.closest("[data-mode]"); if (b) { $$("button", modeBox).forEach((x) => x.classList.toggle("active", x === b)); state.importMode = b.dataset.mode; } };
    state.importMode = "";
    const drop = $("#drop", body);
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); if (e.dataTransfer.files.length) importFiles(e.dataTransfer.files); });
    $("#file", body).addEventListener("change", (e) => { if (e.target.files.length) importFiles(e.target.files); });
    $("#sql-go", body).onclick = () => { const sql = $("#sql-text", body).value.trim(); if (sql) doImport("import?format=sql&name=" + enc("sql-colle"), sql, "SQL collé"); };
    body.querySelectorAll("[data-example]").forEach((b) => b.addEventListener("click", () => doImport(`import?example=${enc(b.dataset.example)}`, null, b.dataset.example)));
    // ---- fusion
    let fmt = "sql";
    $("#mg-fmt", body).onclick = (e) => { const b = e.target.closest("[data-f]"); if (!b) return; fmt = b.dataset.f; $$("#mg-fmt .btn-seg", body).forEach((x) => x.classList.toggle("active", x === b)); };
    $("#mg-preview", body).onclick = async () => {
      const text = $("#mg-text", body).value.trim(); if (!text) return;
      const out = $("#mg-result", body);
      try {
        const p = await api(`merge/preview?format=${fmt}`, { method: "POST", headers: { "Content-Type": "text/plain" }, body: text });
        if (!p.objects.length) { out.innerHTML = '<p class="problem">Aucune table trouvée dans ce code.</p>'; return; }
        out.innerHTML = `<div class="section-title">Table(s) définie(s) par ce code${p.nb_rules ? ` · ${p.nb_rules} règle(s) de gestion` : ""}</div>` + p.objects.map((o, i) => `<div class="merge-row" data-i="${i}">
          <div><b class="mono">${esc(o.object)}</b><div class="muted" style="font-size:12px">${esc(o.type)} · ${o.nb_columns} colonne(s)${o.proposed ? ` · correspondance proposée (${Math.round(o.score * 100)} %)` : ""}</div></div>
          <select class="input">${p.news.map((n) => `<option value="${esc(n.id)}" ${n.id === (newId || o.proposed) ? "selected" : ""}>${esc(n.name)} (New)</option>`).join("")}</select>
          <button class="btn sm primary" data-merge="${i}" ${p.news.length ? "" : "disabled"}>Fusionner</button></div>`).join("");
        out.onclick = async (e) => {
          const b = e.target.closest("[data-merge]"); if (!b) return;
          const o = p.objects[Number(b.dataset.merge)], target = $("select", b.closest(".merge-row")).value;
          try {
            const r = await api(`merge/apply?format=${fmt}&object=${enc(o.object)}&new=${enc(target)}&name=${enc("fusion " + short(o.object))}`, { method: "POST", headers: { "Content-Type": "text/plain" }, body: text });
            if (!r.ok) { out.innerHTML = `<div class="errors">${r.check.errors.map((x) => `<div>• ${esc(x)}</div>`).join("")}</div>`; return; }
            closeModal(); toast(`${short(r.merged.new)} fusionnée avec ${r.merged.into} : liens et documentation conservés`, false, undoAction);
            await refreshAll(); openObject(r.merged.into);
          } catch (err) { fail(err); }
        };
      } catch (err) { out.innerHTML = `<p class="errors">${esc(err.message)}</p>`; }
    };
    if (newId) $("#mg-text", body).focus();
  }
  async function importFiles(fileList) {
    const files = [...fileList], sqlFiles = files.filter((f) => f.name.toLowerCase().endsWith(".sql"));
    if (sqlFiles.length) {
      const texts = await Promise.all(sqlFiles.map((f) => f.text()));
      const label = sqlFiles.length > 1 ? `${sqlFiles.length} fichiers SQL` : sqlFiles[0].name;
      return doImport(`import?format=sql&name=${enc(label)}`, texts.join(";\n"), label);
    }
    const file = files[0], fmt = file.name.toLowerCase().endsWith(".jsonl") ? "jsonl" : "json";
    doImport(`import?format=${fmt}&name=${enc(file.name)}`, await file.text(), file.name);
  }
  const MODE_LABEL = { merge: "ajouté à la structure", refresh: "structure mise à jour (apports SQL conservés)", replace: "importé" };
  async function doImport(path, text, label) {
    const out = $("#import-result");
    if (state.importMode) path += (path.includes("?") ? "&" : "?") + "mode=" + state.importMode;
    out.innerHTML = `<p class="hint">⏳ Import de <b>${esc(label)}</b>…</p>`;
    try {
      const d = await api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: text || "" });
      if (!d.ok) { out.innerHTML = `<p><b>Fichier refusé</b> :</p><div class="errors">${d.check.errors.slice(0, 50).map((x) => `<div>• ${esc(x)}</div>`).join("")}</div>`; return; }
      const r = d.report || {}, diff = d.diff || {}, rec = d.recognition, lk = d.links || {};
      out.innerHTML = `<p>✅ <b>${esc(label)}</b> ${esc(MODE_LABEL[d.mode] || "importé")} : ${r.nb_objects} objets, ${r.nb_columns} colonnes, ${r.nb_rules || 0} règle(s) de gestion.</p>
        ${lk.flows || lk.joins || lk.skipped ? `<div class="stats-grid"><div class="stat"><b>${lk.flows}</b><span>flux lus dans le SQL, ajoutés au graphe (à valider)</span></div>
          <div class="stat"><b>${lk.joins}</b><span>jointures ajoutées (validées)</span></div><div class="stat"><b>${lk.skipped}</b><span>déjà présents ou supprimés auparavant : non recréés</span></div>
          ${d.parsers ? `<div class="stat"><b>${d.parsers.sqlglot}/${d.parsers.sqlglot + d.parsers.regex}</b><span>requêtes comprises par l'analyseur complet</span></div>` : ""}</div>` : ""}
        ${rec && rec.matched ? `<div class="stats-grid"><div class="stat"><b>${rec.matched.length}</b><span>tables reconnues dans la structure</span></div>
          <div class="stat"><b>${rec.added.length}</b><span>tables ajoutées (absentes de la structure)</span></div></div>
          ${rec.added.length ? `<details><summary>Tables ajoutées (${rec.added.length})</summary><div class="mono" style="font-size:12px">${rec.added.map(esc).join("<br>")}</div></details>` : ""}
          ${rec.nb_unknown_columns ? `<details><summary class="type-warn">${rec.nb_unknown_columns} colonne(s) utilisée(s) dans le SQL mais absentes de la structure</summary><div class="mono" style="font-size:12px">${rec.unknown_columns.map(esc).join("<br>")}</div></details>` : ""}
          ${rec.ambiguous.length ? `<details><summary>${rec.ambiguous.length} nom(s) ambigu(s)</summary><div class="mono" style="font-size:12px">${rec.ambiguous.map(esc).join("<br>")}</div></details>` : ""}` : ""}
        ${Object.keys(d.renamed || {}).length ? `<p class="muted" style="font-size:13px">${Object.keys(d.renamed).length} table(s) lue(s) dans le SQL retrouvée(s) dans la structure : leurs liens ont suivi (${Object.entries(d.renamed).slice(0, 5).map(([a, b]) => `${esc(a)} → ${esc(b)}`).join(", ")}).</p>` : ""}
        ${(d.new_matches || []).length ? `<div class="new-box"><p><b>${d.new_matches.length} table(s) New</b> portent le nom d'une table du fichier. Fusionnez-les pour garder leurs liens sans doublon :</p>
          ${d.new_matches.map((m, i) => `<div class="merge-row"><span class="mono">${esc(short(m.new))} <span class="badge-new">New</span> → ${esc(m.object)}</span><button class="btn sm primary" data-nm="${i}">Fusionner</button></div>`).join("")}</div>` : ""}
        ${diff.first_import ? "" : `<p class="muted" style="font-size:13px">Par rapport à l'import précédent : +${(diff.objects_added || []).length} / −${(diff.objects_removed || []).length} objet(s), +${diff.nb_columns_added || 0} / −${diff.nb_columns_removed || 0} colonne(s).</p>`}
        ${d.check.warnings.length ? `<details><summary>${d.check.warnings.length} avertissement(s)</summary><div class="errors" style="color:var(--warn)">${d.check.warnings.slice(0, 100).map((x) => `<div>• ${esc(x)}</div>`).join("")}</div></details>` : ""}
        <button class="btn primary" id="imp-close" style="margin-top:10px">Explorer</button>`;
      $("#imp-close").onclick = closeModal;
      out.onclick = async (e) => {
        const b = e.target.closest("[data-nm]"); if (!b) return;
        const m = d.new_matches[Number(b.dataset.nm)];
        try { await post("objects/merge", { id: m.new, into: m.object }); b.disabled = true; b.textContent = "Fusionnée ✓"; toast("Table New fusionnée : ses liens ont suivi"); refreshAll(); } catch (err) { fail(err); }
      };
      await refreshAll();
      if (!state.view) resetCanvas();
    } catch (e) { out.innerHTML = `<p class="errors">${esc(e.message)}</p>`; }
  }
  function resetCanvas() {
    state.center = null; state.selected = null; state.selEdge = null; state.view = null; location.hash = "";
    graph.setData({ center: null, nodes: [], edges: [] });
    $("#graph-empty").innerHTML = EMPTY_HTML; $("#graph-empty").classList.remove("hidden"); $("#graph-info").textContent = "";
    showBars("object");
    $("#detail").onclick = null;
    $("#detail").innerHTML = '<div class="empty-detail"><p class="hint">Sélectionnez une table pour voir son détail.</p></div>';
    renderCrumbs();
  }

  // ================================================================== clavier
  const typing = (e) => { const t = e.target; return t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable); };
  function moveSelection(key) {
    const id = state.selected || state.center; if (!id || !graph.nodes.has(id)) return;
    let next = null;
    if (key === "ArrowLeft" || key === "ArrowRight") {
      const list = graph.neighbours(id, key === "ArrowLeft" ? "up" : "down");
      next = list.sort((a, b) => Math.abs(graph.nodes.get(a).y - graph.nodes.get(id).y) - Math.abs(graph.nodes.get(b).y - graph.nodes.get(id).y))[0];
    } else {
      const me = graph.nodes.get(id);
      const same = [...graph.nodes.values()].filter((n) => Math.abs(n.x - me.x) < 60 && n.id !== id && (key === "ArrowUp" ? n.y < me.y : n.y > me.y));
      next = same.sort((a, b) => Math.abs(a.y - me.y) - Math.abs(b.y - me.y))[0];
      next = next && next.id;
    }
    if (!next) return;
    state.selected = next; state.selEdge = null; graph.select(next); graph.focus(next); showObject(next);
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (modalOpen()) { closeModal(); return; }
      graph.highlight(null, null); state.colLineage = null; return;
    }
    if (modalOpen()) {
      if (e.key === "Enter" && !typing(e) && $("#cf-ok")) { e.preventDefault(); $("#cf-ok").click(); }
      return;
    }
    const ctrl = e.ctrlKey || e.metaKey;
    if (ctrl && !typing(e) && (e.key === "z" || e.key === "Z") && !e.shiftKey) { e.preventDefault(); return undo(); }
    if (ctrl && !typing(e) && (e.key === "y" || e.key === "Y" || ((e.key === "z" || e.key === "Z") && e.shiftKey))) { e.preventDefault(); return redo(); }
    if (typing(e) || ctrl || e.altKey && e.key !== "ArrowLeft") return;
    const edge = state.selEdge;
    if (e.key === "Enter" && edge) {
      e.preventDefault();
      if (edge.status === "SUGGESTION" && edge.key) return suggAction(edge, "create");
      if (edge.status === "A_VALIDER") return linkAction(edge, "validate");
      return toast("Ce lien est déjà validé.");
    }
    if ((e.key === "Delete" || e.key === "e" || e.key === "E") && edge) {
      e.preventDefault();
      if (edge.status === "SUGGESTION" && edge.key) return suggAction(edge, "dismiss");
      if (edge.id && !String(edge.id).startsWith("o:") && !String(edge.id).startsWith("s:")) return linkAction(edge, "delete");
      return;
    }
    if (e.key === "Backspace" || (e.altKey && e.key === "ArrowLeft")) { e.preventDefault(); return goBack(); }
    if (e.key.startsWith("Arrow")) { e.preventDefault(); return moveSelection(e.key); }
    if (e.key === "+" || e.key === "=") return zoom("in");
    if (e.key === "-" || e.key === "_") return zoom("out");
    if (e.key === "0") return zoom("fit");
    if (e.key === "c" || e.key === "C") return zoom("sel");
    if ((e.key === "f" || e.key === "F") && (state.selected || state.center)) return view360();
    if (e.key === "g" || e.key === "G") { e.preventDefault(); return $("#find").focus(); }
    if (e.key === "/") { e.preventDefault(); return $("#q").focus(); }
    if (e.key === "?") return openHelp();
    if (e.key === "n" || e.key === "N") return $("#btn-new-object").click();
    if (e.key === "[") return togglePanel("left");
    if (e.key === "]") return togglePanel("right");
  });

  // ================================================================== démarrage
  $("#btn-theme").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next; storageSet("lineage.theme", next);
  });
  $("#user").value = storageGet("lineage.user") || "";
  $("#user").addEventListener("change", () => { storageSet("lineage.user", $("#user").value.trim()); loadState(); });

  // ---- panneaux : séparateurs déplaçables, repliables (largeurs mémorisées)
  function togglePanel(side, force) {
    const layout = $(".layout"), cls = side + "-collapsed";
    const collapsed = force != null ? force : !layout.classList.contains(cls);
    layout.classList.toggle(cls, collapsed);
    storageSet("lineage.collapsed." + side, collapsed ? "1" : "");
    $(`[data-collapse="${side}"]`).textContent = side === "left" ? (collapsed ? "›" : "‹") : (collapsed ? "‹" : "›");
    setTimeout(() => graph.fit(), 60);
  }
  $$("[data-collapse]").forEach((b) => b.addEventListener("mousedown", (e) => e.stopPropagation()));
  $$("[data-collapse]").forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); togglePanel(b.dataset.collapse); }));
  (function splitters() {
    const layout = $(".layout");
    for (const side of ["left", "right"]) {
      const saved = Number(storageGet("lineage.w." + side));
      if (saved) layout.style.setProperty(side === "left" ? "--lw" : "--rw", saved + "px");
      if (storageGet("lineage.collapsed." + side) === "1") togglePanel(side, true);
    }
    $$(".splitter").forEach((sp) => sp.addEventListener("mousedown", (e) => {
      if (e.target.closest("[data-collapse]")) return;
      e.preventDefault();
      const side = sp.dataset.split, rect = layout.getBoundingClientRect();
      if (layout.classList.contains(side + "-collapsed")) togglePanel(side, false);
      sp.classList.add("dragging");
      const move = (ev) => {
        const w = side === "left" ? ev.clientX - rect.left : rect.right - ev.clientX;
        const width = Math.max(220, Math.min(rect.width * 0.45, w));
        layout.style.setProperty(side === "left" ? "--lw" : "--rw", width + "px");
        storageSet("lineage.w." + side, Math.round(width));
      };
      const up = () => { sp.classList.remove("dragging"); window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); graph.fit(); };
      window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
    }));
  })();

  let polling = null;
  (async function init() {
    try { await loadState(); } catch (e) { setOnline(false); setOnline(false); setTimeout(init, 4000); return; }
    setOnline(true);
    if (!state.info.has_data) {
      $("#graph-empty").innerHTML = `<div class="empty-dot"></div><h2>Aucune donnée pour l'instant</h2><p>Importez un fichier JSON ou des requêtes SQL décrivant vos tables, ou créez vos propres objets (objet métier, viz…).</p>
        <div style="display:flex;gap:8px;justify-content:center;margin-top:14px"><button class="btn primary" id="empty-import" style="pointer-events:auto">Importer</button><button class="btn" id="empty-object" style="pointer-events:auto">+ Objet</button></div>`;
      $("#empty-import").addEventListener("click", () => openImport());
      $("#empty-object").addEventListener("click", () => openObjectEditor(null));
    }
    await Promise.all([refreshCatalog(), refreshCounts()]);
    setSync("à jour");
    renderCrumbs();
    const m = location.hash.match(/(obj|col)=([^&]+)/);
    if (m && state.info.has_data) (m[1] === "obj" ? openObject : openColumn)(decodeURIComponent(m[2]));
    if (!polling) polling = setInterval(poll, Math.max(5, state.info.ui.refresh_seconds || 15) * 1000);
  })();
})();
