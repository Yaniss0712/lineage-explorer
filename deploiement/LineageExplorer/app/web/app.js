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

  function toast(msg, isError) {
    const t = $("#toast");
    t.textContent = msg; t.className = "toast" + (isError ? " error" : "");
    clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), isError ? 5000 : 2800);
  }
  const fail = (e) => toast(e.message || String(e), true);

  // ================================================================== état
  const EMPTY_HTML = $("#graph-empty").innerHTML;
  const state = {
    info: null, revision: null, view: null, // view : {type:"object", id} | {type:"column", name}
    center: null, selected: null, dir: "both", detailTab: "cols",
    openGroups: new Set(), selectMode: false, selection: new Set(), colLineage: null,
  };
  const tags = () => (state.info ? state.info.tags : []);
  const tagById = (id) => tags().find((t) => t.id === id);
  const tagChip = (name, color) => name ? `<span class="tag-chip"><i class="swatch" style="background:${esc(color || "")}"></i>${esc(name)}</span>` : '<span class="tag-chip none">sans tag</span>';

  const graph = new GraphView($("#graph"), {
    onNodeClick: (n) => { state.selected = n.id; showObject(n.id); },
    onNodeDblClick: (n) => { if (state.view && state.view.type === "column") openObject(n.id); else expand(n.id); },
    onNodeContext: (n) => { if (n.id === state.center) return; graph.hide(n.id); updateInfo(); },
    onEdgeClick: (e) => showEdge(e),
    onHover: hoverEdge,
  });

  function hoverEdge(ev, e) {
    const tip = $("#tooltip");
    if (!ev) { tip.classList.add("hidden"); return; }
    const wrap = $(".canvas").getBoundingClientRect();
    const title = e.status === "SUGGESTION" ? `Suggestion · score ${fmtScore(e.score)}` : `${KIND_LABEL[e.kind]} · ${STATUS_LABEL[e.status] || ""}`;
    const body = e.status === "SUGGESTION" ? e.explanation
      : `${esc(e.source_object)} ${e.kind === "FLOW" ? "→" : "="} ${esc(e.target_object)}${(e.columns || []).length ? "<br>" + e.columns.map((c) => `${esc(c.source || "?")} → ${esc(c.target || "?")}`).join("<br>") : ""}`;
    tip.innerHTML = `<b>${esc(title)}</b><div class="muted" style="margin-top:4px">${e.status === "SUGGESTION" ? esc(body) : body}</div>`;
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
    fillTagSelect($("#selection-tag"), true);
    return state.info;
  }

  function setSync(text, cls) { const s = $("#sync"); s.textContent = text; s.className = "sync" + (cls ? " " + cls : ""); }

  // Connexion au serveur local : seules les erreurs réseau comptent comme « déconnecté »
  // (une erreur d'affichage ne doit pas faire croire que l'outil est fermé).
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

  async function refreshAll() {
    try { await loadState(); } catch (e) { if (!e.status) throw e; return fail(e); }
    await Promise.all([refreshCatalog(), refreshCounts()]);
    const active = $(".tab.active").dataset.tab;
    if (active === "review") loadReview();
    if (active === "sugg") loadSuggestions();
    if (state.view && state.view.type === "object") { loadGraph(); if (state.selected) showObject(state.selected, true); }
    if (state.view && state.view.type === "column") openColumn(state.view.name, true);
  }

  async function refreshCounts() {
    try { const s = await api("suggestions?limit=1"); $("#sugg-count").textContent = s.total || ""; } catch (e) { /* pas de données */ }
  }

  // ================================================================== onglets
  function selectTab(name) {
    $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
    ["catalog", "review", "sugg", "cols"].forEach((n) => $("#tab-" + n).classList.toggle("hidden", n !== name));
    if (name === "review") loadReview();
    if (name === "sugg") loadSuggestions();
    if (name === "cols") loadColumns();
  }
  $$(".tab").forEach((t) => t.addEventListener("click", () => selectTab(t.dataset.tab)));

  // ================================================================== catalogue (arborescence tag → base → objets)
  async function refreshCatalog() {
    if ($("#q").value.trim()) return runSearch();
    if (!state.info.has_data) { $("#tree").innerHTML = '<p class="hint">Aucune donnée importée.</p>'; $("#catalog-info").textContent = ""; return; }
    const groups = await api("tree");
    $("#catalog-info").textContent = `${state.info.counts.objects} objet(s) · ${tags().length} tag(s)`;
    if (!state.openGroups.size && groups.length === 1) state.openGroups.add("g:" + groups[0].tag_id);
    $("#tree").innerHTML = groups.map((g) => `<div class="tree-group ${state.openGroups.has("g:" + g.tag_id) ? "open" : ""}" data-key="g:${esc(g.tag_id)}">
        <div class="tree-head" data-toggle="g:${esc(g.tag_id)}"><span class="caret">▶</span><i class="swatch" style="background:${esc(g.color || "")}"></i><span class="lbl" title="${esc(g.name)}">${esc(g.name)}</span><span class="n">${g.count}</span></div>
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
      <div class="txt"><div class="title" title="${esc(o.id)}">${esc(o.name)}</div>
      <div class="sub">${o.type === "VIEW" ? "vue · " : ""}${o.nb_columns} col.${o.matched_columns && o.matched_columns.length ? "" : ""}</div>
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
    const col = item.dataset.col;
    openObject(item.dataset.obj).then(() => { if (col) showColumnLineage(item.dataset.obj, col, "up"); });
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
      toast(`${r.count} objet(s) tagué(s)`); state.selection.clear(); updateSelectionBar(); await refreshAll();
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
  async function openObject(id) {
    state.view = { type: "object", id }; state.center = id; state.selected = id;
    location.hash = "obj=" + enc(id);
    $("#colbar").classList.add("hidden"); $("#floatbar").classList.remove("hidden");
    await Promise.all([loadGraph(), showObject(id)]);
  }
  async function loadGraph() {
    if (!state.center) return;
    try {
      const data = await api(`graph/${enc(state.center)}?` + graphParams());
      $("#graph-empty").classList.add("hidden");
      graph.setData(data);
      if (state.selected && state.selected !== state.center) graph.select(state.selected);
      state.truncated = data.truncated; updateInfo();
      if (state.colLineage) highlightColumnLineage(state.colLineage);
    } catch (e) { fail(e); }
  }
  function updateInfo() {
    $("#graph-info").textContent = `${graph.nodes.size} objet(s) · ${graph.edges.size} lien(s)` + (state.truncated ? ` · +${state.truncated} non affiché(s)` : "");
  }
  async function expand(id) {
    try { graph.merge(await api(`graph/${enc(id)}?` + graphParams({ depth: 1, dir: "both" }))); updateInfo(); } catch (e) { fail(e); }
  }
  $$("#dir-seg .btn-seg").forEach((b) => b.addEventListener("click", () => {
    $$("#dir-seg .btn-seg").forEach((x) => x.classList.toggle("active", x === b)); state.dir = b.dataset.dir; loadGraph();
  }));
  $("#depth").addEventListener("change", loadGraph);
  $$(".stepper [data-step]").forEach((b) => b.addEventListener("click", () => {
    $("#depth").value = Math.max(1, Math.min(20, (Number($("#depth").value) || 2) + Number(b.dataset.step))); loadGraph();
  }));
  ["#opt-sugg", "#opt-pending", "#opt-join"].forEach((s) => $(s).addEventListener("change", loadGraph));
  function popover(btn, box) {
    $(btn).addEventListener("click", (e) => { e.stopPropagation(); $$(".popover").forEach((p) => p !== $(box) && p.classList.add("hidden")); $(box).classList.toggle("hidden"); });
  }
  popover("#btn-options", "#options"); popover("#btn-more", "#more-menu");
  document.addEventListener("click", (e) => { if (!e.target.closest(".popover-wrap")) $$(".popover").forEach((p) => p.classList.add("hidden")); });
  $("#btn-fit").addEventListener("click", () => graph.fit());
  $("#btn-recenter").addEventListener("click", () => { if (state.selected && !String(state.selected).startsWith("__col__")) openObject(state.selected); });
  window.addEventListener("resize", debounce(() => graph.fit(), 200));

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
    return `<div class="link-card" data-link="${esc(l.id)}">
      <div class="row"><span class="obj" data-open="${esc(other)}">${esc(short(other))}</span><span class="status ${esc(l.status)}">${esc(STATUS_LABEL[l.status])}</span></div>
      <div class="meta">${KIND_LABEL[l.kind]} · par ${esc(l.created_by)} le ${esc(fmtDate(l.created_at))}${l.validated_by ? ` · validé par ${esc(l.validated_by)}` : ""}</div>
      ${cols.length ? `<div class="meta mono">${cols.map((c) => `${esc(c.source || "·")} → ${esc(c.target || "·")}${c.transformation ? " (" + esc(c.transformation) + ")" : ""}`).join("<br>")}</div>` : ""}
      ${l.comment ? `<div class="meta">${esc(l.comment)}</div>` : ""}
      ${(l.problems || []).map((p) => `<div class="problem">⚠ ${esc(p)}</div>`).join("")}
      <div class="btns">${l.status === "A_VALIDER" ? '<button class="btn sm ok" data-act="validate">Valider</button>' : ""}
        <button class="btn sm" data-act="edit">Modifier</button><button class="btn sm danger" data-act="delete">Supprimer</button></div></div>`;
  }

  function suggCard(s, selfId) {
    const other = selfId ? (s.a === selfId ? s.b : s.a) : null;
    const dir = s.proposed_source ? `${short(s.proposed_source)} → ${short(s.proposed_source === s.a ? s.b : s.a)}` : "sens à choisir";
    return `<div class="link-card" data-sugg="${esc(s.key)}">
      <div class="row">${other ? `<span class="obj" data-open="${esc(other)}">${esc(short(other))}</span>` : `<span class="obj" data-open="${esc(s.a)}">${esc(short(s.a))}</span><span class="muted">↔</span><span class="obj" data-open="${esc(s.b)}">${esc(short(s.b))}</span>`}
        <span>${scorebar(s.score)}<span class="score">${fmtScore(s.score)}</span></span></div>
      <div class="meta">${esc(ORIGIN_LABEL[s.origin] || s.origin)} · ${esc(dir)}</div>
      <div class="meta">${esc(s.explanation || "")}</div>
      <div class="btns"><button class="btn sm primary" data-act="create">Créer le lien…</button><button class="btn sm ghost" data-act="dismiss">Écarter</button></div></div>`;
  }

  function ruleCard(r, selfId) {
    const tbl = (id) => id ? `<span class="obj" data-open="${esc(id)}">${esc(short(id))}</span>` : "";
    const head = r.role === "target"
      ? `${esc(RULE_KIND[r.kind] || r.kind)} de cette table à partir de ${r.sources.map(tbl).join(", ") || "—"}`
      : `${esc(RULE_KIND[r.kind] || r.kind)}${r.target ? ` de ${tbl(r.target)}` : ""} qui utilise cette table`;
    const line = (label, value) => value ? `<div class="rule-line"><span class="rule-label">${label}</span><code>${esc(value)}</code></div>` : "";
    const mine = r.role === "target" ? r.columns : r.columns.filter((c) => c.expression.toLowerCase().includes(short(selfId).toLowerCase() + "."));
    return `<div class="rule-card">
      <div class="rule-head">${head}</div>
      ${r.joins.map((j) => line(esc(j.type.toLowerCase()), `${short(j.table)}${j.condition ? "  ON  " + j.condition : ""}`)).join("")}
      ${line("filtre", r.filters)}${line("regroupement", r.group_by)}${line("condition sur groupe", r.having)}
      ${mine.length ? `<table class="col-table rule-cols">${mine.map((c) => `<tr><td class="mono">${esc(c.column)}</td><td><code>${esc(c.expression)}</code></td></tr>`).join("")}</table>` : ""}
      <details class="more"><summary>Code SQL${r.source_file ? " · " + esc(r.source_file) : ""}</summary><pre class="sql">${esc(r.sql)}</pre></details></div>`;
  }

  function setDetailTab(name) {
    state.detailTab = name;
    $$("[data-dtab]").forEach((b) => b.classList.toggle("active", b.dataset.dtab === name));
    $$("[data-pane]").forEach((p) => p.classList.toggle("hidden", p.dataset.pane !== name));
  }

  function renderObject(d) {
    const o = d.object, doc = d.doc || {};
    const nbLinks = d.upstream.length + d.downstream.length + d.joins.length;
    const list = (items, empty) => items.map((l) => linkCard(l, o.id)).join("") || `<p class="hint">${empty}</p>`;
    $("#detail").innerHTML = `<div class="detail">
      <div class="d-head">${tagChip(o.tag, o.color)} <span class="muted" style="font-size:12px">${esc(o.type)}</span>
        <h2>${esc(o.name)}</h2><div class="sub mono">${esc(o.database)}</div>
        ${doc.description ? `<p class="d-desc">${esc(doc.description)}</p>` : ""}</div>
      <div class="form" style="margin-top:12px"><label>Tag<select id="obj-tag"><option value="">— sans tag —</option>${tags().map((t) => `<option value="${esc(t.id)}" ${t.id === o.tag_id ? "selected" : ""}>${esc(t.name)}</option>`).join("")}</select></label></div>
      <div class="d-actions">
        <button class="btn sm primary" id="btn-add-link">Ajouter un lien</button>
        <button class="btn sm" data-go="up">Amont</button><button class="btn sm" data-go="down">Aval</button>
        <button class="btn sm" id="btn-path">Chemin vers…</button>
        <a class="btn sm" href="/api/impact/${enc(o.id)}.csv" download>Impact CSV</a>
        ${o.id !== state.center ? '<button class="btn sm ghost" id="btn-hide">Masquer</button>' : ""}
      </div>
      <div class="d-tabs">
        <button class="d-tab" data-dtab="cols">Colonnes ${d.columns.length}</button>
        <button class="d-tab" data-dtab="links">Liens ${nbLinks}</button>
        <button class="d-tab" data-dtab="sugg" title="Suggestions">Sugg. ${d.suggestions.length}</button>
        <button class="d-tab" data-dtab="rules">Règles ${(d.rules || []).length}</button>
        <button class="d-tab" data-dtab="doc">Doc</button>
      </div>
      <div data-pane="cols">
        <table class="cols"><thead><tr><th>Colonne</th><th>Type</th><th></th></tr></thead><tbody>
        ${d.columns.map((c) => `<tr class="col-row" data-col="${esc(c.name)}"><td class="mono">${esc(c.name)}</td><td class="mono muted">${esc(c.data_type || "")}</td>
          <td style="white-space:nowrap"><button class="btn sm ghost" data-colview="${esc(c.name)}" title="Toutes les tables qui ont cette colonne">tables</button>${c.nb_links ? `<span class="muted" title="liens colonne"> · ${c.nb_links}</span>` : ""}</td></tr>`).join("")}
        </tbody></table>
        <p class="hint">Clic sur une colonne : son lineage (via les liens colonne → colonne). « tables » : toutes les tables qui la portent.</p>
        <div id="col-lineage"></div>
      </div>
      <div data-pane="links">
        <div class="section-title">Alimentée par · ${d.upstream.length}</div>${list(d.upstream, "Aucune source documentée.")}
        <div class="section-title">Alimente · ${d.downstream.length}</div>${list(d.downstream, "Aucune table alimentée documentée.")}
        <div class="section-title">Jointures · ${d.joins.length}</div>${list(d.joins, "Aucune jointure documentée.")}
      </div>
      <div data-pane="rules">${(d.rules || []).length
        ? `<p class="hint" style="padding-top:0">Règles de gestion lues dans le SQL importé : jointures, filtres, regroupements et calcul des colonnes.</p>${d.rules.map((r) => ruleCard(r, o.id)).join("")}`
        : '<p class="hint">Aucune requête SQL ne concerne cette table. Importez du SQL (mode « Compléter ») pour voir ses règles de gestion.</p>'}</div>
      <div data-pane="sugg">${d.suggestions.map((s) => suggCard(s, o.id)).join("") || '<p class="hint">Aucune suggestion pour cette table.</p>'}</div>
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
      if (t.closest("[data-open]")) return openObject(t.closest("[data-open]").dataset.open);
      const act = t.closest("[data-act]");
      if (act && act.closest("[data-link]")) return linkAction(all.find((l) => l.id === act.closest("[data-link]").dataset.link), act.dataset.act);
      if (act && act.closest("[data-sugg]")) return suggAction(d.suggestions.find((s) => s.key === act.closest("[data-sugg]").dataset.sugg), act.dataset.act);
      const row = t.closest(".col-row"); if (row) return showColumnLineage(o.id, row.dataset.col, "up");
      const go = t.closest("[data-go]");
      if (go) { $$("#dir-seg .btn-seg").forEach((x) => x.classList.toggle("active", x.dataset.dir === go.dataset.go)); state.dir = go.dataset.go; return openObject(o.id); }
      const dn = t.closest("[data-del-note]");
      if (dn) { ev.preventDefault(); await write("notes/delete", { object_id: o.id, id: dn.dataset.delNote }); return showObject(o.id, true); }
    };
    $("#obj-tag").addEventListener("change", async (e) => { await write("tags/assign", { object_ids: [o.id], tag_id: e.target.value || null }, "Tag enregistré"); refreshAll(); });
    $("#btn-add-link").addEventListener("click", () => openLinkEditor({ source: o.id }));
    $("#btn-path").addEventListener("click", () => openPath(o.id));
    const hide = $("#btn-hide"); if (hide) hide.addEventListener("click", () => { graph.hide(o.id); updateInfo(); });
    $("#doc-save").addEventListener("click", async () => {
      await write("object/update", { id: o.id, description: $("#doc-desc").value, owner: $("#doc-owner").value, spec_url: $("#doc-spec").value }, "Documentation enregistrée");
      showObject(o.id, true);
    });
    $("#add-note").addEventListener("click", async () => {
      const text = $("#note-text").value.trim(); if (!text) return;
      await write("notes/add", { object_id: o.id, text }, "Note ajoutée"); showObject(o.id, true);
    });
  }

  async function write(path, body, okMessage) {
    try { const r = await post(path, body); if (okMessage) toast(okMessage); return r; }
    catch (e) { fail(e); throw e; }
  }

  async function linkAction(l, act) {
    if (!l) return;
    if (act === "edit") return openLinkEditor({ link: l });
    if (act === "validate") await write("links/validate", { id: l.id }, "Lien validé");
    if (act === "delete") {
      if (!confirm(`Supprimer le lien ${short(l.source)} ${l.kind === "FLOW" ? "→" : "="} ${short(l.target)} ?`)) return;
      await write("links/delete", { id: l.id }, "Lien supprimé");
    }
    await refreshAll();
  }

  async function suggAction(s, act) {
    if (!s) return;
    if (act === "create") return openLinkEditor({ suggestion: s });
    if (act === "dismiss") { await write("suggestions/dismiss", { key: s.key }, "Suggestion écartée"); await refreshAll(); }
  }

  // ---- lineage d'une colonne (via les liens colonne → colonne)
  async function showColumnLineage(objId, column, dir) {
    try {
      const data = await api(`lineage/column?object=${enc(objId)}&column=${enc(column)}&dir=${dir}`);
      state.colLineage = data;
      if ($("[data-dtab]")) setDetailTab("cols");
      $$(".col-row").forEach((r) => r.classList.toggle("active", r.dataset.col === column));
      const box = $("#col-lineage"); if (!box) return;
      box.innerHTML = `<div class="section-title"><span>Lineage de <span class="mono" style="text-transform:none">${esc(column)}</span></span>
        <span class="seg"><button class="btn-seg ${dir === "up" ? "active" : ""}" data-cl="up">Origine</button><button class="btn-seg ${dir === "down" ? "active" : ""}" data-cl="down">Usage</button><button class="btn-seg" data-cl="off">×</button></span></div>
        <div class="col-lineage">${data.steps.length ? data.steps.map((s) => `<div class="step" style="--d:${s.depth - 1}">
          <span class="muted">${dir === "up" ? "←" : "→"}</span> <span class="mono">${esc(s.object)}.<b>${esc(s.column || "?")}</b></span> <span class="status ${esc(s.status)}">${esc(STATUS_LABEL[s.status])}</span>
          ${s.transformation ? `<div class="muted mono" style="padding-left:16px">${esc(s.transformation)}</div>` : ""}</div>`).join("")
          : `<p class="hint">Aucun lien colonne → colonne documenté pour cette colonne. Ajoutez-en un avec « Ajouter un lien » (option colonnes).</p>`}</div>`;
      box.querySelectorAll("[data-cl]").forEach((b) => b.addEventListener("click", (ev) => {
        ev.stopPropagation();
        if (b.dataset.cl === "off") { state.colLineage = null; box.innerHTML = ""; graph.highlight(null, null); $$(".col-row").forEach((r) => r.classList.remove("active")); return; }
        showColumnLineage(objId, column, b.dataset.cl);
      }));
      highlightColumnLineage(data);
    } catch (e) { fail(e); }
  }
  function highlightColumnLineage(data) {
    if (!data.steps.length) return;
    const pairs = data.steps.map((s) => [s.from.split(".").slice(0, 2).join("."), s.object]);
    graph.highlight(data.objects, graph.edgesBetween(pairs));
  }

  // ---- détail d'un lien ou d'une suggestion (clic sur une arête)
  function showEdge(e) {
    if (e.kind === "COLREF") return;
    $("#detail").onclick = null;
    if (e.status === "SUGGESTION") {
      $("#detail").innerHTML = `<div class="detail"><div class="d-head"><span class="tag-chip">SUGGESTION</span><h2 style="font-size:15px">${esc(e.a)} ↔ ${esc(e.b)}</h2></div>${suggCard(e)}
        ${(e.columns || []).length ? `<div class="section-title">Colonnes communes</div><table class="col-table">${e.columns.map((c) => `<tr><td class="mono">${esc(c.a)}</td><td class="mono">${esc(c.b)}</td><td class="${c.mismatch ? "type-warn" : "muted"}">${esc(c.type_a || "?")} / ${esc(c.type_b || "?")}</td></tr>`).join("")}</table>` : ""}</div>`;
      $("#detail").onclick = (ev) => { const a = ev.target.closest("[data-act]"); if (a) suggAction(e, a.dataset.act); const op = ev.target.closest("[data-open]"); if (op) openObject(op.dataset.open); };
      return;
    }
    $("#detail").innerHTML = `<div class="detail"><div class="d-head"><span class="tag-chip">${esc(KIND_LABEL[e.kind].toUpperCase())}</span>
      <h2 style="font-size:15px"><span class="obj link" data-open="${esc(e.source)}">${esc(e.source)}</span> <span class="muted">${e.kind === "FLOW" ? "→" : "="}</span> <span class="obj link" data-open="${esc(e.target)}">${esc(e.target)}</span></h2></div>
      ${linkCard(e, e.source)}</div>`;
    $("#detail").onclick = (ev) => { const a = ev.target.closest("[data-act]"); if (a) return linkAction(e, a.dataset.act); const op = ev.target.closest("[data-open]"); if (op) openObject(op.dataset.open); };
  }

  // ================================================================== vue colonne
  async function openColumn(name, keep) {
    try {
      const data = await api(`column?name=${enc(name)}&similar=${$("#col-similar").checked ? "1" : "0"}`);
      state.view = { type: "column", name }; state.center = null; location.hash = "col=" + enc(name);
      $("#floatbar").classList.add("hidden"); $("#colbar").classList.remove("hidden"); $("#colbar-name").textContent = name;
      $("#graph-empty").classList.add("hidden");
      graph.setColumn(data);
      $("#graph-info").textContent = `${data.tables.length} table(s) · ${data.links.length} lien(s) entre elles`;
      if (!keep) state.picked = [];
      renderColumnPanel(data);
    } catch (e) { fail(e); }
  }
  function renderColumnPanel(data) {
    state.picked = (state.picked || []).filter((id) => data.tables.some((t) => t.id === id));
    $("#detail").onclick = null;
    $("#detail").innerHTML = `<div class="detail"><div class="d-head"><span class="tag-chip">COLONNE</span><h2 class="mono">${esc(data.name)}</h2>
      <div class="sub">${data.tables.length} table(s) · types : ${Object.entries(data.types).map(([t, n]) => `${esc(t)} (${n})`).join(", ") || "—"}</div>
      ${data.type_conflict ? '<p class="problem">⚠ Types différents selon les tables : vérifiez les jointures.</p>' : ""}</div>
      <p class="hint">Cochez deux tables puis « Créer un lien » : la colonne est préremplie.</p>
      <div class="d-actions"><button class="btn sm primary" id="col-link" ${state.picked.length === 2 ? "" : "disabled"}>Créer un lien (${state.picked.length}/2)</button></div>
      <table class="col-table">${data.tables.map((t) => `<tr class="pickable ${state.picked.includes(t.id) ? "picked" : ""}" data-pickt="${esc(t.id)}">
        <td><input type="checkbox" ${state.picked.includes(t.id) ? "checked" : ""}></td><td><div>${esc(t.name)}</div><div class="muted" style="font-size:11.5px">${t.tag ? esc(t.tag) + " · " : ""}${esc(t.database)}</div></td>
        <td class="mono ${t.type_differs ? "type-warn" : "muted"}">${esc(t.columns.map((c) => c.name === data.name ? (c.type || "?") : `${c.name}`).join(", "))}</td>
        <td><button class="btn sm ghost" data-open="${esc(t.id)}">ouvrir</button></td></tr>`).join("")}</table>
      ${data.similar_names.length ? `<div class="section-title">Noms proches</div><p style="margin:0">${data.similar_names.map((n) => `<a href="#" data-colname="${esc(n)}" class="mono">${esc(n)}</a>`).join(" · ")}</p>` : ""}
      ${data.links.length ? `<div class="section-title">Liens existants entre ces tables</div>${data.links.map((l) => linkCard(l, "")).join("")}` : ""}</div>`;
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
  $("#colbar-close").addEventListener("click", () => { $("#colbar").classList.add("hidden"); $("#floatbar").classList.remove("hidden"); if (state.view && state.view.type === "column") { state.view = null; resetCanvas(); } });
  $("#col-similar").addEventListener("change", () => { if (state.view && state.view.type === "column") openColumn(state.view.name); });

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
      $("#review").innerHTML = (r.items.length ? r.items.map((l) => `<div class="link-card" data-link="${esc(l.id)}" style="padding:10px 6px">
          <div class="row"><span class="obj" data-open="${esc(l.source)}">${esc(short(l.source))}</span><span class="muted">${l.kind === "FLOW" ? "→" : "="}</span><span class="obj" data-open="${esc(l.target)}">${esc(short(l.target))}</span></div>
          <div class="meta">${KIND_LABEL[l.kind]} · ${esc(l.created_by)} · ${esc(fmtDate(l.created_at))}${(l.columns || []).length ? ` · ${l.columns.length} colonne(s)` : ""}</div>
          ${(l.problems || []).map((p) => `<div class="problem">⚠ ${esc(p)}</div>`).join("")}
          <div class="btns"><button class="btn sm ok" data-act="validate">Valider</button><button class="btn sm" data-act="edit">Modifier</button><button class="btn sm danger" data-act="delete">Supprimer</button></div></div>`).join("")
        : '<p class="hint">Aucun lien en attente de validation.</p>')
        + (r.recently_deleted.length ? `<div class="section-title" style="padding:0 6px">Supprimés récemment</div>${r.recently_deleted.slice(0, 8).map((l) => `<div class="meta" style="padding:2px 6px">${esc(short(l.source))} ${l.kind === "FLOW" ? "→" : "="} ${esc(short(l.target))} — ${esc(l.deleted_by)}, ${esc(fmtDate(l.deleted_at))}</div>`).join("")}` : "");
      $("#review").onclick = (ev) => {
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

  // ================================================================== modales
  function openModal(html) { $("#modal-body").innerHTML = html; $("#modal").classList.remove("hidden"); return $("#modal-body"); }
  function closeModal() { $("#modal").classList.add("hidden"); $("#modal-body").innerHTML = ""; }
  $(".modal-close").addEventListener("click", closeModal);
  $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

  function objectAutocomplete(input, onPick) {
    const wrap = input.parentElement; wrap.classList.add("suggest");
    const list = document.createElement("div"); list.className = "suggest-list hidden"; wrap.appendChild(list);
    input.addEventListener("input", debounce(async () => {
      const q = input.value.trim(); if (q.length < 2) { list.classList.add("hidden"); return; }
      const r = await api(`search?q=${enc("table:" + q)}&limit=30`);
      list.innerHTML = r.objects.map((o) => `<div data-id="${esc(o.id)}"><i class="swatch" style="background:${esc(o.color || "")}"></i>${esc(o.id)}</div>`).join("");
      list.classList.toggle("hidden", !r.objects.length);
    }, 200));
    list.addEventListener("click", (e) => { const it = e.target.closest("[data-id]"); if (!it) return; input.value = it.dataset.id; list.classList.add("hidden"); onPick && onPick(it.dataset.id); });
  }

  // ---- éditeur de lien (création depuis une table, une suggestion, la vue colonne ; modification)
  async function openLinkEditor(opts) {
    const link = opts.link, sug = opts.suggestion;
    const st = { kind: link ? link.kind : (sug && sug.kind) || "FLOW", source: "", target: "", columns: [], comment: link ? link.comment : "" };
    if (link) { st.source = link.source; st.target = link.target; st.columns = (link.columns || []).map((c) => ({ ...c })); }
    else if (sug) {
      st.source = sug.proposed_source || sug.a; st.target = st.source === sug.a ? sug.b : sug.a;
      const flip = st.source !== sug.a;
      st.columns = (sug.mapping && sug.mapping.length ? sug.mapping.map((m) => ({ source: m.source, target: m.target, transformation: m.transformation }))
        : (sug.columns || []).slice(0, 3).map((c) => ({ source: flip ? c.b : c.a, target: flip ? c.a : c.b })));
      if (sug.direction && sug.direction !== st.source) st.columns = st.columns.map((c) => ({ source: c.target, target: c.source, transformation: c.transformation }));
    } else { st.source = opts.source || ""; st.target = opts.target || ""; if (opts.column) st.columns = [{ source: opts.column, target: opts.column }]; }
    const cols = {};
    const loadCols = async (id) => { if (!id || cols[id]) return; try { cols[id] = (await api("object/" + enc(id))).columns.map((c) => c.name); } catch (e) { cols[id] = []; } };
    await Promise.all([loadCols(st.source), loadCols(st.target)]);
    const body = openModal(`<h2>${link ? "Modifier le lien" : "Ajouter un lien"}</h2>
      <p class="muted" style="margin:0">Le lien sera ajouté à « À valider ». Vous pouvez le valider vous-même ensuite, ou le supprimer en cas d'erreur.</p>
      <div class="form">
        <div class="seg-wide" id="le-kind"><button data-k="FLOW">Flux : une table alimente l'autre</button><button data-k="JOIN">Jointure : les tables se relient</button></div>
        <div id="le-dir"></div>
        <div><div class="section-title" style="margin-top:4px">Colonnes (facultatif) <button class="btn sm ghost" id="le-addcol">+ ajouter</button></div><div id="le-cols" style="display:grid;gap:6px"></div></div>
        <label>Commentaire / règle<input id="le-comment" value="${esc(st.comment || "")}" placeholder="ex. agrégat mensuel, filtre sur les actifs…"></label>
        <div style="display:flex;gap:8px"><button class="btn primary" id="le-save">${link ? "Enregistrer" : "Créer le lien"}</button>
          ${link ? "" : '<button class="btn" id="le-save-validate">Créer et valider</button>'}</div>
      </div>`);
    const renderKind = () => $$("#le-kind button", body).forEach((b) => b.classList.toggle("active", b.dataset.k === st.kind));
    const box = (id, role, end) => id ? `<div class="dir-box"><small>${role}</small>${esc(id)}</div>` : `<div class="dir-box"><small>${role}</small><span class="suggest"><input class="input" data-pickend="${end}" placeholder="chercher une table…"></span></div>`;
    const renderDir = () => {
      const a = st.kind === "FLOW" ? "Source (alimente)" : "Table A", b = st.kind === "FLOW" ? "Cible (est alimentée)" : "Table B";
      $("#le-dir", body).innerHTML = `<div class="dir-choice">${box(st.source, a, "source")}<button class="btn sm" id="le-swap" title="Inverser le sens">${st.kind === "FLOW" ? "→ ⇄" : "⇄"}</button>${box(st.target, b, "target")}</div>
        ${st.source && st.target ? `<p class="muted" style="font-size:12.5px;margin:6px 0 0">${st.kind === "FLOW" ? `<b>${esc(short(st.source))}</b> alimente <b>${esc(short(st.target))}</b> (et donc ${esc(short(st.target))} prend ses données de ${esc(short(st.source))}).` : `Jointure entre <b>${esc(short(st.source))}</b> et <b>${esc(short(st.target))}</b>.`}</p>` : ""}
        ${st.source && st.target && !link ? '<button class="btn sm ghost" id="le-reset" style="margin-top:4px">changer de table</button>' : ""}`;
      $$("[data-pickend]", body).forEach((inp) => objectAutocomplete(inp, async (id) => { st[inp.dataset.pickend] = id; await loadCols(id); renderDir(); renderCols(); }));
      const swap = $("#le-swap", body);
      if (swap) swap.onclick = () => { [st.source, st.target] = [st.target, st.source]; st.columns = st.columns.map((c) => ({ source: c.target, target: c.source, transformation: c.transformation })); renderDir(); renderCols(); };
      const reset = $("#le-reset", body); if (reset) reset.onclick = () => { st.target = ""; renderDir(); renderCols(); };
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
      const payload = { kind: st.kind, source: st.source, target: st.target, columns: st.columns.filter((c) => c.source || c.target), comment: $("#le-comment", body).value, validate };
      try {
        if (link) await post("links/update", { ...payload, id: link.id }); else await post("links/create", { ...payload, suggestion_key: sug ? sug.key : null });
        closeModal(); toast(validate ? "Lien créé et validé" : link ? "Lien modifié (à revalider)" : "Lien créé : il est dans « À valider »");
        await refreshAll();
      } catch (e) { fail(e); }
    };
    $("#le-save", body).onclick = () => save(false);
    const sv = $("#le-save-validate", body); if (sv) sv.onclick = () => save(true);
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
      closeModal(); state.view = { type: "object", id: fromId }; state.center = fromId;
      graph.setData({ center: fromId, nodes: d.nodes, edges: d.edges }); graph.highlight(d.nodes.map((n) => n.id), d.edges.map((e) => e.edge_id));
      $("#graph-empty").classList.add("hidden"); updateInfo();
      toast(`Chemin : ${d.nodes.map((n) => n.name).join(" → ")}`);
    };
  }

  // ---- tags et règles
  async function openTags() {
    await loadState();
    const body = openModal(`<h2>Tags</h2><p class="muted" style="margin:0">Un tag par objet. L'ordre (facultatif, ex. 1 = source, 2 = préparation, 3 = restitution) sert à proposer le sens des liens.</p>
      <div class="section-title">Tags</div><div id="tg-list"></div>
      <div class="tag-row"><input type="color" id="tg-color" value="${esc(state.info.palette[tags().length % state.info.palette.length])}"><input type="text" id="tg-name" placeholder="Nouveau tag (ex. Fiche client)"><input type="number" id="tg-order" placeholder="ordre"><button class="btn sm primary" id="tg-add">Ajouter</button></div>
      <div class="section-title">Règles de nommage</div><p class="hint" style="padding-top:0">Exemple : <span class="mono">fiche_client_*</span> → « Fiche client ». Appliquées à chaque import ; un tag choisi à la main est prioritaire.</p>
      <div id="rl-list"></div>
      <div class="rule-row"><input class="input" id="rl-pattern" placeholder="motif, ex. tracking_*" style="flex:1"><select id="rl-tag" class="input" style="width:auto"></select><button class="btn sm primary" id="rl-add">Ajouter</button></div>`);
    const render = () => {
      $("#tg-list", body).innerHTML = tags().map((t) => `<div class="tag-row" data-tid="${esc(t.id)}"><input type="color" data-f="color" value="${esc(t.color || "#a1a1aa")}">
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
      if (del && confirm("Supprimer ce tag ? Les objets concernés n'auront plus de tag.")) { await write("tags/delete", { id: del.dataset.del }, "Tag supprimé"); await reload(); }
      const dr = e.target.closest("[data-delrule]"); if (dr) { await write("rules/delete", { id: dr.dataset.delrule }, "Règle supprimée"); await reload(); }
    });
    $("#tg-add", body).onclick = async () => {
      try { await post("tags/save", { name: $("#tg-name", body).value, color: $("#tg-color", body).value, order: $("#tg-order", body).value }); toast("Tag créé"); await reload();
        $("#tg-color", body).value = state.info.palette[tags().length % state.info.palette.length]; } catch (err) { fail(err); }
    };
    $("#rl-add", body).onclick = async () => {
      try { const r = await post("rules/save", { pattern: $("#rl-pattern", body).value, tag_id: $("#rl-tag", body).value }); toast(`Règle ajoutée : ${r.matched} objet(s) concerné(s)`); $("#rl-pattern", body).value = ""; await reload(); } catch (err) { fail(err); }
    };
    render();
  }
  $("#btn-tags").addEventListener("click", openTags);

  // ---- versions et historique
  $("#btn-save-version").addEventListener("click", () => {
    const body = openModal(`<h2>Enregistrer la version actuelle</h2><p class="muted" style="margin:0">Fige l'état complet (tags, liens, documentation, notes) dans l'espace partagé. Chaque modification est déjà sauvegardée automatiquement ; une version sert de point de repère que l'on peut comparer ou restaurer.</p>
      <div class="form"><label>Nom<input id="v-label" placeholder="ex. Validation équipe data — octobre"></label><label>Commentaire<textarea id="v-comment" rows="3"></textarea></label>
      <button class="btn primary" id="v-save" style="justify-self:start">Enregistrer</button></div>`);
    $("#v-save", body).onclick = async () => {
      try { const r = await post("versions/save", { label: $("#v-label", body).value, comment: $("#v-comment", body).value }); closeModal(); toast(`Version enregistrée : ${r.label}`); } catch (e) { fail(e); }
    };
  });

  async function openHistory(tab) {
    const [versions, activity] = await Promise.all([api("versions"), api("activity?limit=200")]);
    const body = openModal(`<h2>Historique</h2><div class="seg-wide" id="h-tabs"><button data-h="versions">Versions enregistrées (${versions.length})</button><button data-h="activity">Activité</button></div>
      <div id="h-versions">${versions.map((v) => `<div class="version-row" data-file="${esc(v.file)}"><div><b>${esc(v.label)}</b><div class="muted" style="font-size:12px">${esc(v.by)} · ${esc(fmtDate(v.at))}${v.comment ? " — " + esc(v.comment) : ""}</div><div data-diff></div></div>
        <div style="display:flex;gap:6px"><button class="btn sm" data-vact="diff">Comparer</button><button class="btn sm danger" data-vact="restore">Restaurer</button></div></div>`).join("") || '<p class="hint">Aucune version enregistrée. Utilisez « Enregistrer la version ».</p>'}</div>
      <div id="h-activity" class="hidden">${activity.map((a) => `<div class="activity-row"><span class="muted">${esc(fmtDate(a.at))}</span><b>${esc(a.user)}</b><span>${esc(a.label)}${a.detail ? ` : <span class="mono">${esc(a.detail)}</span>` : ""}</span></div>`).join("") || '<p class="hint">Aucune activité.</p>'}</div>`);
    const show = (h) => { $$("#h-tabs button", body).forEach((b) => b.classList.toggle("active", b.dataset.h === h)); $("#h-versions", body).classList.toggle("hidden", h !== "versions"); $("#h-activity", body).classList.toggle("hidden", h !== "activity"); };
    $("#h-tabs", body).onclick = (e) => { const b = e.target.closest("[data-h]"); if (b) show(b.dataset.h); };
    $("#h-versions", body).onclick = async (e) => {
      const b = e.target.closest("[data-vact]"); if (!b) return;
      const row = b.closest("[data-file]"), file = row.dataset.file;
      if (b.dataset.vact === "diff") {
        try {
          const d = (await api("version?file=" + enc(file))).diff;
          const li = (title, items) => items.length ? `<div><b>${title}</b><ul class="diff-list">${items.slice(0, 30).map((x) => `<li class="mono">${esc(x)}</li>`).join("")}</ul></div>` : "";
          $("[data-diff]", row).innerHTML = `<p class="muted" style="font-size:12.5px;margin:6px 0">Depuis cette version :</p>` + li("Liens ajoutés", d.links_added) + li("Liens supprimés", d.links_removed) + li("Liens validés", d.links_validated)
            + li("Tags ajoutés", d.tags_added) + li("Tags supprimés", d.tags_removed)
            + `<p class="muted" style="font-size:12px">${d.assignments_changed} changement(s) de tag · ${d.rules_changed} règle(s) · ${d.objects_documented} objet(s) documenté(s) · ${d.notes_changed} objet(s) avec notes modifiées</p>`;
        } catch (err) { fail(err); }
      }
      if (b.dataset.vact === "restore" && confirm("Restaurer cette version pour toute l'équipe ? L'état actuel reste consultable dans l'historique (enregistrez une version avant si besoin).")) {
        await write("versions/restore", { file }, "Version restaurée"); closeModal(); refreshAll();
      }
    };
    show(tab || "versions");
  }
  $("#btn-history").addEventListener("click", () => openHistory());

  // ---- menu « plus »
  $("#more-menu").addEventListener("click", (e) => {
    const b = e.target.closest("[data-menu]"); if (!b) return;
    $("#more-menu").classList.add("hidden");
    if (b.dataset.menu === "export") window.location.href = "/api/export.xlsx";
    if (b.dataset.menu === "report") openReport();
    if (b.dataset.menu === "settings") openSettings();
  });

  async function openReport() {
    try {
      const r = await api("report"), i = r.indicators, b = r.build || {}, m = r.metadata || {};
      const diff = (m.diff || {});
      openModal(`<h2>Rapport</h2>
        <dl class="kv"><dt>Espace</dt><dd class="mono" style="font-size:12px">${esc(state.info.workspace)}</dd><dt>Import</dt><dd>${esc(m.name || "—")} · ${esc(m.by || "")} · ${esc(fmtDate(m.at))}</dd></dl>
        <div class="stats-grid">
          <div class="stat"><b>${i.objects}</b><span>objets</span></div><div class="stat"><b>${b.nb_columns ?? "–"}</b><span>colonnes</span></div>
          <div class="stat"><b>${Math.round(i.tagged_rate * 100)} %</b><span>objets tagués</span></div><div class="stat"><b>${Math.round(i.linked_rate * 100)} %</b><span>objets reliés</span></div>
          <div class="stat"><b>${i.links_validated}</b><span>liens validés</span></div><div class="stat"><b>${i.links_pending}</b><span>liens à valider</span></div>
          <div class="stat"><b>${i.documented}</b><span>objets documentés</span></div><div class="stat"><b>${b.nb_suggestions ?? "–"}</b><span>suggestions</span></div>
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
      await write("settings/save", { ignore_columns: lines("#st-ignore"), hidden_objects: lines("#st-hidden") }, "Réglages enregistrés (suggestions recalculées)");
      closeModal(); refreshAll();
    };
  }

  // ---- import
  $("#btn-import").addEventListener("click", openImport);
  async function openImport() {
    let examples = [];
    try { examples = await api("examples"); } catch (e) { /* aucun exemple */ }
    const body = openModal(`<h2>Importer des métadonnées</h2>
      <p class="muted" style="margin:0">Un JSON de structure (tables + colonnes) ou des requêtes SQL. Le fichier est enregistré dans l'espace partagé : toute l'équipe voit la nouvelle version. Tags, liens et documentation sont conservés ; les objets ou colonnes disparus sont signalés.</p>
      ${state.info && state.info.has_data ? `<div class="seg-wide" id="imp-mode" style="margin-top:12px">
        <button data-mode="" class="active" title="SQL : complète la structure actuelle ; JSON : remplace la structure">Automatique</button>
        <button data-mode="merge" title="Ajoute les tables, règles et jointures du fichier à la structure actuelle">Compléter la structure</button>
        <button data-mode="replace" title="Le fichier devient la nouvelle structure (tags et liens conservés)">Remplacer la structure</button></div>
        <p class="hint" style="padding:4px 2px 0">Automatique : un fichier <b>.sql</b> complète la structure actuelle (tables reconnues, règles de gestion et jointures ajoutées) ; un <b>.json</b> la remplace.</p>` : ""}
      <label class="dropzone" id="drop"><input type="file" id="file" accept=".json,.jsonl,.sql,application/json" multiple hidden>
        <b>Déposez un fichier ici</b><br><span class="muted">.json, .jsonl ou un ou plusieurs .sql — ou cliquez pour choisir</span></label>
      <details class="more" style="margin-top:12px"><summary>Ou coller des requêtes SQL</summary>
        <div class="form" style="margin-top:6px"><textarea id="sql-text" rows="6" class="mono"></textarea><button class="btn sm primary" id="sql-go" style="justify-self:start">Analyser le SQL</button></div></details>
      ${examples.length ? `<details class="more" style="margin-top:8px"><summary>Exemples fictifs</summary><div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px">${examples.map((x) => `<button class="btn sm" data-example="${esc(x)}">${esc(x)}</button>`).join("")}</div></details>` : ""}
      <div id="import-result" style="margin-top:14px"></div>`);
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
  async function doImport(path, text, label) {
    const out = $("#import-result");
    if (state.importMode) path += (path.includes("?") ? "&" : "?") + "mode=" + state.importMode;
    out.innerHTML = `<p class="hint">⏳ Import de <b>${esc(label)}</b>…</p>`;
    try {
      const d = await api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: text || "" });
      if (!d.ok) { out.innerHTML = `<p><b>Fichier refusé</b> :</p><div class="errors">${d.check.errors.slice(0, 50).map((x) => `<div>• ${esc(x)}</div>`).join("")}</div>`; return; }
      const r = d.report || {}, diff = d.diff || {};
      const rec = d.recognition;
      out.innerHTML = `<p>✅ <b>${esc(label)}</b> ${d.mode === "merge" ? "ajouté à la structure" : "importé"} : ${r.nb_objects} objets, ${r.nb_columns} colonnes, ${r.nb_suggestions} suggestions, ${r.nb_rules || 0} règle(s) de gestion.</p>
        ${rec ? `<div class="stats-grid"><div class="stat"><b>${rec.matched.length}</b><span>tables du SQL reconnues dans la structure</span></div>
          <div class="stat"><b>${rec.added.length}</b><span>tables ajoutées (absentes de la structure)</span></div>
          <div class="stat"><b>${rec.nb_rules}</b><span>règles de gestion</span></div><div class="stat"><b>${rec.nb_joins}</b><span>jointures</span></div></div>
          ${rec.added.length ? `<details><summary>Tables ajoutées (${rec.added.length})</summary><div class="mono" style="font-size:12px">${rec.added.map(esc).join("<br>")}</div></details>` : ""}
          ${rec.nb_unknown_columns ? `<details><summary class="type-warn">${rec.nb_unknown_columns} colonne(s) utilisée(s) dans le SQL mais absentes de la structure</summary><div class="mono" style="font-size:12px">${rec.unknown_columns.map(esc).join("<br>")}</div></details>` : ""}
          ${rec.ambiguous.length ? `<details><summary>${rec.ambiguous.length} nom(s) ambigu(s)</summary><div class="mono" style="font-size:12px">${rec.ambiguous.map(esc).join("<br>")}</div></details>` : ""}` : ""}
        ${diff.first_import ? "" : `<p class="muted" style="font-size:13px">Par rapport à l'import précédent : +${(diff.objects_added || []).length} / −${(diff.objects_removed || []).length} objet(s), +${diff.nb_columns_added || 0} / −${diff.nb_columns_removed || 0} colonne(s).</p>`}
        ${d.check.warnings.length ? `<details><summary>${d.check.warnings.length} avertissement(s)</summary><div class="errors" style="color:var(--warn)">${d.check.warnings.slice(0, 100).map((x) => `<div>• ${esc(x)}</div>`).join("")}</div></details>` : ""}
        <button class="btn primary" id="imp-close" style="margin-top:10px">Explorer</button>`;
      $("#imp-close").onclick = closeModal;
      await refreshAll(); resetCanvas();
    } catch (e) { out.innerHTML = `<p class="errors">${esc(e.message)}</p>`; }
  }
  function resetCanvas() {
    state.center = null; state.selected = null; state.view = null; location.hash = "";
    graph.setData({ center: null, nodes: [], edges: [] });
    $("#graph-empty").innerHTML = EMPTY_HTML; $("#graph-empty").classList.remove("hidden"); $("#graph-info").textContent = "";
    $("#colbar").classList.add("hidden"); $("#floatbar").classList.remove("hidden");
    $("#detail").onclick = null;
    $("#detail").innerHTML = '<div class="empty-detail"><p class="hint">Sélectionnez une table pour voir son détail.</p></div>';
  }

  // ================================================================== démarrage
  $("#btn-theme").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next; storageSet("lineage.theme", next);
  });
  $("#user").value = storageGet("lineage.user") || "";
  $("#user").addEventListener("change", () => { storageSet("lineage.user", $("#user").value.trim()); });

  // ---- séparateurs déplaçables entre les panneaux (largeurs mémorisées)
  (function splitters() {
    const layout = $(".layout");
    for (const side of ["left", "right"]) {
      const saved = Number(storageGet("lineage.w." + side));
      if (saved) layout.style.setProperty(side === "left" ? "--lw" : "--rw", saved + "px");
    }
    $$(".splitter").forEach((sp) => sp.addEventListener("mousedown", (e) => {
      e.preventDefault();
      const side = sp.dataset.split, rect = layout.getBoundingClientRect();
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
      $("#graph-empty").innerHTML = `<div class="empty-dot"></div><h2>Aucune donnée pour l'instant</h2><p>Importez un fichier JSON ou des requêtes SQL décrivant vos tables.</p>
        <button class="btn primary" id="empty-import" style="margin-top:14px;pointer-events:auto">Importer</button>`;
      $("#empty-import").addEventListener("click", openImport);
    }
    await Promise.all([refreshCatalog(), refreshCounts()]);
    setSync("à jour");
    const m = location.hash.match(/(obj|col)=([^&]+)/);
    if (m && state.info.has_data) (m[1] === "obj" ? openObject : openColumn)(decodeURIComponent(m[2]));
    if (!polling) polling = setInterval(poll, Math.max(5, state.info.ui.refresh_seconds || 15) * 1000);
  })();
})();
