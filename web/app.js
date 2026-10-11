/* Lineage Explorer — logique de l'interface (JS vanilla, aucun appel externe).
 * Toutes les données affichées passent par esc() avant insertion dans le HTML.
 */
(function () {
  "use strict";

  // ------------------------------------------------------------------ outils
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => [...(root || document).querySelectorAll(sel)];
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const enc = encodeURIComponent;
  const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
  const fmtScore = (s) => (s == null ? "–" : Number(s).toFixed(2));
  const fmtDate = (s) => (s ? new Date(s).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" }) : "");
  const STATUS_LABEL = { CERTAIN: "certain", VALIDATED: "validé", SUGGESTED: "à valider", MANUAL: "manuel", REJECTED: "rejeté" };
  const ORIGIN_LABEL = { MANUAL: "manuel", DECLARED: "déclaré (ETL)", SQL_PARSED: "SQL", INFERRED: "inféré", EXACT: "N1 exact",
    NORMALIZED: "N2 normalisé", PARTIAL: "N3 partiel", DATAVAULT: "Data Vault" };

  function storageGet(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }
  function storageSet(key, v) { try { localStorage.setItem(key, v); } catch (e) { /* stockage indisponible */ } }

  async function api(path, opts) {
    const res = await fetch("/api/" + path, opts);
    const ct = res.headers.get("Content-Type") || "";
    const data = ct.includes("json") ? await res.json() : await res.text();
    if (!res.ok) { const err = new Error((data && data.error) || res.statusText); err.status = res.status; throw err; }
    return data;
  }
  const post = (path, body, raw) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: raw ? body : JSON.stringify(body) });

  function toast(msg, isError) {
    const t = $("#toast");
    t.textContent = msg; t.className = "toast" + (isError ? " error" : "");
    clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), isError ? 5000 : 2500);
  }
  const fail = (e) => toast(e.message || String(e), true);

  // ------------------------------------------------------------------ état
  const EMPTY_HTML = $("#graph-empty").innerHTML;
  const state = {
    stats: null, center: null, selected: null, dir: "both", page: 1,
    detail: null, colLineage: null,
  };
  const user = () => $("#user").value.trim() || (state.stats && state.stats.user) || "anonyme";

  const graph = new GraphView($("#graph"), {
    onNodeClick: (n) => { state.selected = n.id; showObject(n.id); },
    onNodeDblClick: (n) => expand(n.id),
    onNodeContext: (n) => { if (n.id === state.center) return toast("Impossible de masquer le centre de la vue."); graph.hide(n.id); updateInfo(); toast(`${n.id} masqué`); },
    onEdgeClick: (e) => showEdge(e.id),
    onHover: hoverEdge,
  });

  function hoverEdge(ev, e) {
    const tip = $("#tooltip");
    if (!ev) { tip.classList.add("hidden"); return; }
    const wrap = $(".graph-wrap").getBoundingClientRect();
    tip.innerHTML = `<b>${esc(e.kind === "FLOW" ? "Flux" : "Relation")}</b> · ${esc(ORIGIN_LABEL[e.origin] || e.origin)} ·
      <span class="status ${esc(e.status)}">${esc(STATUS_LABEL[e.status] || e.status)}</span> · score <b>${fmtScore(e.score)}</b>
      ${e.path_score != null && e.path_score !== e.score ? ` · chemin ${fmtScore(e.path_score)}` : ""}
      <div class="muted" style="margin-top:4px">${esc(e.explanation || "")}</div>`;
    tip.classList.remove("hidden");
    const x = Math.min(ev.clientX - wrap.left + 14, wrap.width - 370);
    tip.style.left = Math.max(8, x) + "px"; tip.style.top = (ev.clientY - wrap.top + 14) + "px";
  }

  // ------------------------------------------------------------------ recherche
  function layerFilter() {
    const boxes = $$("input[name=layer]");
    const checked = boxes.filter((b) => b.checked).map((b) => b.value);
    return checked.length === boxes.length ? "" : checked.join(",") || "NONE";
  }

  async function runSearch(page) {
    const q = $("#q").value.trim();
    const dtype = $("#dtype").value.trim();
    state.page = page || 1;
    if (q.length < 2 && !dtype) { $("#results").innerHTML = '<p class="hint">Tapez au moins 2 caractères pour chercher.</p>'; $("#pager").innerHTML = ""; $("#results-count").textContent = ""; return; }
    const params = new URLSearchParams({ q, page: state.page, desc: $("#in-desc").checked ? "1" : "0" });
    const lf = layerFilter(); if (lf) params.set("layer", lf);
    if (dtype) { params.set("dtype", dtype); params.set("kind", "COLUMN"); }
    try {
      const t0 = performance.now();
      const data = await api("search?" + params);
      const ms = Math.round(performance.now() - t0);
      $("#results-count").textContent = data.total;
      selectTab("results");
      if (!data.results.length) { $("#results").innerHTML = '<p class="hint">Aucun résultat.</p>'; $("#pager").innerHTML = ""; return; }
      $("#results").innerHTML = data.results.map((r) => r.kind === "OBJECT"
        ? `<div class="item" data-obj="${esc(r.object_id)}"><i class="dot ${esc(r.layer)}"></i><div class="txt"><div class="title">${esc(r.name.split(".").pop())}</div><div class="sub">${esc(r.database)} · table</div></div></div>`
        : `<div class="item" data-obj="${esc(r.object_id)}" data-col="${esc(r.name)}"><i class="dot ${esc(r.layer)}"></i><div class="txt"><div class="title mono">${esc(r.name)}</div><div class="sub">${esc(r.object_id)}${r.data_type ? " · " + esc(r.data_type) : ""}</div></div></div>`).join("");
      const pages = Math.ceil(data.total / data.page_size);
      $("#pager").innerHTML = `<span>${data.total} résultat(s) · ${ms} ms</span><span>
        ${state.page > 1 ? '<button class="btn sm ghost" data-page="prev">‹</button>' : ""} ${state.page}/${pages}
        ${state.page < pages ? '<button class="btn sm ghost" data-page="next">›</button>' : ""}</span>`;
    } catch (e) { fail(e); }
  }

  $("#results").addEventListener("click", (ev) => {
    const item = ev.target.closest(".item"); if (!item) return;
    $$(".item.active", $("#results")).forEach((x) => x.classList.remove("active"));
    item.classList.add("active");
    openObject(item.dataset.obj).then(() => { if (item.dataset.col) showColumnLineage(item.dataset.obj, item.dataset.col, "up"); });
  });
  $("#pager").addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-page]"); if (!b) return;
    runSearch(state.page + (b.dataset.page === "next" ? 1 : -1));
  });
  const debouncedSearch = debounce(() => runSearch(1), 250);
  $("#q").addEventListener("input", debouncedSearch);
  $("#dtype").addEventListener("input", debouncedSearch);
  $$("input[name=layer]").forEach((b) => b.addEventListener("change", () => runSearch(1)));
  $("#in-desc").addEventListener("change", () => runSearch(1));

  function selectTab(name) {
    $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
    $("#tab-results").classList.toggle("hidden", name !== "results");
    $("#tab-review").classList.toggle("hidden", name !== "review");
    if (name === "review") loadReview();
  }
  $$(".tab").forEach((t) => t.addEventListener("click", () => selectTab(t.dataset.tab)));

  // ------------------------------------------------------------------ graphe
  function graphParams(extra) {
    return new URLSearchParams({
      depth: $("#depth").value || 2, dir: state.dir, rel: $("#rel").checked ? "1" : "0",
      min_score: $("#min-score").value, rejected: $("#show-rejected").checked ? "1" : "0", ...(extra || {}),
    });
  }

  async function openObject(id) {
    state.center = id; state.selected = id;
    location.hash = "obj=" + enc(id);
    await Promise.all([loadGraph(), showObject(id)]);
  }

  async function loadGraph() {
    if (!state.center) return;
    try {
      const data = await api(`graph/${enc(state.center)}?` + graphParams());
      $("#graph-empty").classList.add("hidden");
      graph.setData(data);
      if (state.selected && state.selected !== state.center) graph.select(state.selected);
      state.truncated = data.truncated;
      updateInfo();
      if (state.colLineage) highlightColumnLineage(state.colLineage);
    } catch (e) { fail(e); }
  }

  function updateInfo() {
    const n = graph.nodes.size;
    $("#graph-info").textContent = `${n} bulle(s) · ${graph.edges.size} lien(s)` +
      (state.truncated ? ` · +${state.truncated} objet(s) non affiché(s) — réduisez la profondeur ou filtrez` : "");
  }

  async function expand(id) {
    try {
      const data = await api(`graph/${enc(id)}?` + graphParams({ depth: 1, dir: "both" }));
      graph.merge(data); updateInfo();
    } catch (e) { fail(e); }
  }

  $$(".btn-seg").forEach((b) => b.addEventListener("click", () => {
    $$(".btn-seg").forEach((x) => x.classList.toggle("active", x === b));
    state.dir = b.dataset.dir; loadGraph();
  }));
  $("#depth").addEventListener("change", loadGraph);
  $$(".stepper [data-step]").forEach((b) => b.addEventListener("click", () => {
    const input = $("#depth");
    input.value = Math.max(1, Math.min(20, (Number(input.value) || 2) + Number(b.dataset.step)));
    loadGraph();
  }));
  $("#btn-options").addEventListener("click", (e) => { e.stopPropagation(); $("#options").classList.toggle("hidden"); $("#btn-options").classList.toggle("active"); });
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".popover-wrap")) { $("#options").classList.add("hidden"); $("#btn-options").classList.remove("active"); }
  });
  $("#rel").addEventListener("change", loadGraph);
  $("#show-rejected").addEventListener("change", loadGraph);
  $("#min-score").addEventListener("input", () => { $("#min-score-val").textContent = Number($("#min-score").value).toFixed(2); });
  $("#min-score").addEventListener("change", loadGraph);
  $("#btn-fit").addEventListener("click", () => graph.fit());
  $("#btn-recenter").addEventListener("click", () => { if (state.selected) openObject(state.selected); });
  window.addEventListener("resize", debounce(() => graph.fit(), 200));

  // ------------------------------------------------------------------ détail d'un objet
  async function showObject(id) {
    try {
      const d = await api("object/" + enc(id));
      state.detail = d; state.colLineage = null; graph.highlight(null, null);
      renderObject(d);
    } catch (e) { fail(e); }
  }

  function scorebar(s) { return `<span class="scorebar" title="${fmtScore(s)}"><i style="width:${Math.round((s || 0) * 100)}%"></i></span>`; }
  const layerTag = (layer, extra) => `<span class="tag"><i class="dot ${esc(layer)}"></i>${esc(layer)}${extra ? " · " + esc(extra) : ""}</span>`;

  function linkCard(e, selfId, open) {
    const other = e.source_object === selfId ? e.target_object : e.source_object;
    const cols = e.kind === "RELATION" && e.source_column ? `<div class="meta mono">${esc(e.source_column)} = ${esc(e.target_column || "")}</div>` : "";
    const actions = [];
    if (e.status === "SUGGESTED" || e.status === "CERTAIN") {
      actions.push(`<button class="btn sm ok" data-act="VALIDATED">Valider</button>`, `<button class="btn sm danger" data-act="REJECTED">Rejeter</button>`);
    } else if (e.status === "VALIDATED" || e.status === "REJECTED") {
      actions.push(`<button class="btn sm" data-act="DELETE">Annuler</button>`);
    } else if (e.status === "MANUAL") {
      actions.push(`<button class="btn sm danger" data-act="DELETE">Supprimer</button>`);
    }
    if (e.kind === "FLOW") actions.push(`<button class="btn sm ghost" data-act="detail">Colonnes</button>`);
    const who = e.annotation ? `<div class="meta">${esc(STATUS_LABEL[e.annotation.status] || "")} par ${esc(e.annotation.author)} · ${esc(fmtDate(e.annotation.updated_at))}</div>` : "";
    return `<div class="link-card${open ? " open" : ""}" data-edge="${esc(e.id)}">
      <div class="row"><span class="obj" data-open="${esc(other)}">${esc(other)}</span><span class="status ${esc(e.status)}">${esc(STATUS_LABEL[e.status] || e.status)}</span></div>
      <div class="meta">${esc(ORIGIN_LABEL[e.origin] || e.origin)}${e.job ? " · " + esc(e.job) : ""} · ${scorebar(e.score)}<span class="score">${fmtScore(e.score)}</span>
        ${open ? "" : ' · <span class="more-link" data-why>pourquoi ?</span>'}</div>
      ${cols}<div class="expl">${esc(e.explanation || "")}${who}</div>
      <div class="btns">${actions.join("")}</div></div>`;
  }

  function setDetailTab(name) {
    state.detailTab = name;
    $$("[data-dtab]").forEach((b) => b.classList.toggle("active", b.dataset.dtab === name));
    $$("[data-pane]").forEach((p) => p.classList.toggle("hidden", p.dataset.pane !== name));
  }

  function renderObject(d) {
    const o = d.object, a = d.annotations || {};
    const unexpl = new Set(d.unexplained_columns);
    const showRej = $("#show-rejected").checked;
    const vis = (list) => list.filter((e) => e.status !== "REJECTED" || showRej);
    const up = vis(d.upstream), down = vis(d.downstream), rel = vis(d.relations);
    const notes = a.notes || [];
    const list = (items, empty) => items.map((e) => linkCard(e, o.id)).join("") || `<p class="hint">${empty}</p>`;
    $("#detail").innerHTML = `<div class="detail">
      <div class="d-head">${layerTag(o.layer, o.type)}
        <h2>${esc(o.name)}</h2><div class="sub mono">${esc(o.database)}</div>
        ${o.description ? `<p class="d-desc">${esc(o.description)}</p>` : ""}</div>
      <div class="d-actions">
        <button class="btn sm" data-go="up">Amont</button>
        <button class="btn sm" data-go="down">Aval</button>
        <button class="btn sm" id="btn-add-link">Ajouter un lien</button>
        <button class="btn sm" id="btn-path">Chemin vers…</button>
        <a class="btn sm" href="/api/impact/${enc(o.id)}.csv" download>Impact CSV</a>
        ${o.id !== state.center ? '<button class="btn sm ghost" id="btn-hide">Masquer</button>' : ""}
      </div>
      <div class="d-tabs">
        <button class="d-tab" data-dtab="cols">Colonnes · ${d.columns.length}</button>
        <button class="d-tab" data-dtab="links">Liens · ${up.length + down.length + rel.length}</button>
        <button class="d-tab" data-dtab="doc">Documentation${notes.length ? " · " + notes.length : ""}</button>
      </div>

      <div data-pane="cols">
        <table class="cols"><thead><tr><th>Colonne</th><th>Type</th><th>Description</th></tr></thead><tbody>
        ${d.columns.map((c) => `<tr class="col-row" data-col="${esc(c.name)}">
          <td class="mono">${esc(c.name)}${c.is_key ? '<span class="key">clé</span>' : ""}${unexpl.has(c.name) ? '<span class="unexpl" title="Aucune source trouvée (renommage, calcul ?)">· sans source</span>' : ""}</td>
          <td class="mono muted">${esc(c.data_type || "")}</td><td class="muted">${esc(c.description || "")}</td></tr>`).join("")}
        </tbody></table>
        <p class="hint">Cliquez sur une colonne pour voir son lineage.</p>
        <div id="col-lineage"></div>
        ${d.unexplained_columns.length ? `<div class="section-title">Colonnes sans source (${d.unexplained_columns.length})</div>
          <p class="hint" style="padding-top:0">Renommages ou calculs non retrouvés automatiquement.</p>
          ${d.unexplained_columns.map((c) => `<div class="link-card"><div class="row"><span class="mono">${esc(c)}</span><button class="btn sm" data-link-col="${esc(c)}">Relier…</button></div></div>`).join("")}` : ""}
      </div>

      <div data-pane="links">
        <div class="section-title">Alimentée par · ${up.length}</div>${list(up, "Aucune source connue.")}
        <div class="section-title">Alimente · ${down.length}</div>${list(down, "Aucun objet alimenté.")}
        <div class="section-title">Jointures · ${rel.length}</div>${list(rel, "Aucune jointure détectée.")}
      </div>

      <div data-pane="doc">
        <div class="section-title">Spécification</div>
        <div class="spec-row" id="spec-row">${a.spec_url
          ? `<a href="${esc(a.spec_url)}" target="_blank" rel="noopener noreferrer">${esc(a.spec_url)}</a>`
          : '<span class="muted">Aucune URL de spec</span>'}
          <button class="btn sm ghost" id="edit-spec">Modifier</button></div>
        ${a.spec_author ? `<div class="meta muted" style="font-size:12px">par ${esc(a.spec_author)} · ${esc(fmtDate(a.spec_updated_at))}</div>` : ""}
        <div class="section-title">Informations</div>
        <dl class="kv"><dt>Base</dt><dd>${esc(o.database)}</dd><dt>Type</dt><dd>${esc(o.type)}</dd><dt>Colonnes</dt><dd>${o.nb_columns}</dd>
          ${o.owner ? `<dt>Propriétaire</dt><dd>${esc(o.owner)}</dd>` : ""}
          ${state.stats && state.stats.extracted_at ? `<dt>Extraction</dt><dd>${esc(fmtDate(state.stats.extracted_at))}</dd>` : ""}</dl>
        <div class="section-title">Notes</div>
        ${notes.map((n) => `<div class="note">${esc(n.text)}<div class="meta">${esc(n.author)} · ${esc(fmtDate(n.updated_at))} ·
          <a href="#" data-del-note="${esc(n.id)}">supprimer</a></div></div>`).join("")}
        <div class="form" style="margin-top:4px"><textarea id="note-text" rows="2" placeholder="Partager une information utile…"></textarea>
          <button class="btn sm primary" id="add-note" style="justify-self:start">Ajouter la note</button></div>
      </div>
    </div>`;
    setDetailTab(state.detailTab || "cols");
    bindObjectDetail(d);
  }

  function findEdge(d, id) {
    return [...d.upstream, ...d.downstream, ...d.relations].find((e) => String(e.id) === String(id));
  }

  function bindObjectDetail(d) {
    const root = $("#detail"), o = d.object;
    root.onclick = async (ev) => {
      const t = ev.target;
      const tab = t.closest("[data-dtab]");
      if (tab) return setDetailTab(tab.dataset.dtab);
      if (t.closest("[data-why]")) return t.closest(".link-card").classList.toggle("open");
      if (t.closest("[data-open]")) return openObject(t.closest("[data-open]").dataset.open);
      const act = t.closest("[data-act]");
      if (act) {
        const e = findEdge(d, act.closest("[data-edge]").dataset.edge);
        if (act.dataset.act === "detail") return showEdge(e.id);
        return annotateEdge(e, act.dataset.act);
      }
      const row = t.closest(".col-row");
      if (row) return showColumnLineage(o.id, row.dataset.col, "up");
      const go = t.closest("[data-go]");
      if (go) { $$(".btn-seg").forEach((x) => x.classList.toggle("active", x.dataset.dir === go.dataset.go)); state.dir = go.dataset.go; return openObject(o.id); }
      const lc = t.closest("[data-link-col]");
      if (lc) return openAddLink(d, { kind: "FLOW", way: "in", target_column: lc.dataset.linkCol });
      const dn = t.closest("[data-del-note]");
      if (dn) { ev.preventDefault(); return annotate(o.id, { delete_note: dn.dataset.delNote }).then(() => showObject(o.id)); }
    };
    $("#edit-spec").addEventListener("click", () => {
      const cur = (d.annotations && d.annotations.spec_url) || "";
      $("#spec-row").innerHTML = `<input id="spec-input" class="input" style="flex:1;min-width:200px" placeholder="https://confluence…" value="${esc(cur)}">
        <button class="btn sm primary" id="save-spec">Enregistrer</button>`;
      $("#spec-input").focus();
      const save = async () => { await annotate(o.id, { spec_url: $("#spec-input").value }); toast("Spec enregistrée"); showObject(o.id); loadGraph(); };
      $("#save-spec").addEventListener("click", save);
      $("#spec-input").addEventListener("keydown", (e) => { if (e.key === "Enter") save(); });
    });
    $("#add-note").addEventListener("click", async () => {
      const text = $("#note-text").value.trim(); if (!text) return;
      await annotate(o.id, { note: text }); toast("Note ajoutée"); showObject(o.id);
    });
    $("#btn-add-link").addEventListener("click", () => openAddLink(d, { kind: "FLOW", way: "out" }));
    $("#btn-path").addEventListener("click", () => openPath(o.id));
    const hide = $("#btn-hide");
    if (hide) hide.addEventListener("click", () => { graph.hide(o.id); updateInfo(); });
  }

  async function annotate(objectId, body) {
    try { return await post("annotations/" + enc(objectId), { author: user(), ...body }); }
    catch (e) { fail(e); throw e; }
  }

  async function annotateEdge(e, status) {
    await annotate(e.source_object, { link: { kind: e.kind, target: e.target_object,
      source_column: e.kind === "RELATION" ? e.source_column : (e.origin === "MANUAL" ? e.source_column : null),
      target_column: e.kind === "RELATION" ? e.target_column : (e.origin === "MANUAL" ? e.target_column : null), status } });
    toast({ VALIDATED: "Lien validé", REJECTED: "Lien rejeté — il ne réapparaîtra plus", DELETE: "Annotation retirée" }[status] || "Enregistré");
    if (state.selected) await showObject(state.selected);
    loadGraph(); refreshReviewCount();
  }

  // ------------------------------------------------------------------ lineage colonne
  async function showColumnLineage(objId, column, dir) {
    try {
      const data = await api(`lineage/column?object=${enc(objId)}&column=${enc(column)}&dir=${dir}&min_score=${$("#min-score").value}`);
      state.colLineage = data;
      if ($("[data-dtab]")) setDetailTab("cols");
      $$(".col-row").forEach((r) => r.classList.toggle("active", r.dataset.col === column));
      const box = $("#col-lineage"); if (!box) return;
      box.innerHTML = `<div class="section-title"><span>Lineage de <span class="mono" style="text-transform:none">${esc(column)}</span></span>
        <span class="seg"><button class="btn-seg ${dir === "up" ? "active" : ""}" data-cl="up">Origine</button><button class="btn-seg ${dir === "down" ? "active" : ""}" data-cl="down">Usage</button>
        <button class="btn-seg" data-cl="off" title="Fermer">×</button></span></div>
        <div class="col-lineage">${data.steps.length ? data.steps.map((s) => `<div class="step" style="--d:${s.depth - 1}">
          <span class="muted">${dir === "up" ? "←" : "→"}</span> <span class="mono">${esc(s.object)}.<b>${esc(s.column)}</b></span>
          <span class="muted">· ${esc(ORIGIN_LABEL[s.origin] || s.origin)} · ${fmtScore(s.path_score)}</span>
          ${s.transformation ? `<div class="muted mono" style="padding-left:16px">${esc(s.transformation)}</div>` : ""}</div>`).join("")
          : `<p class="hint">Aucun lineage ${dir === "up" ? "amont" : "aval"} connu pour cette colonne.</p>`}</div>`;
      box.querySelectorAll("[data-cl]").forEach((b) => b.addEventListener("click", () => {
        if (b.dataset.cl === "off") { state.colLineage = null; box.innerHTML = ""; graph.highlight(null, null); $$(".col-row").forEach((r) => r.classList.remove("active")); return; }
        showColumnLineage(objId, column, b.dataset.cl);
      }));
      highlightColumnLineage(data);
      const missing = data.objects.filter((id) => !graph.nodes.has(id));
      if (missing.length) toast(`${missing.length} objet(s) du lineage hors de la vue : augmentez la profondeur.`);
    } catch (e) { fail(e); }
  }

  function highlightColumnLineage(data) {
    const pairs = data.steps.map((s) => [s.from.split(".").slice(0, 2).join("."), s.object]);
    graph.highlight(data.objects, graph.edgesBetween(pairs).filter((k) => graph.edges.get(k).kind === "FLOW"));
  }

  // ------------------------------------------------------------------ détail d'un lien
  async function showEdge(id) {
    try {
      const d = await api("edge/" + enc(id));
      const e = d.edge;
      const actions = e.status === "SUGGESTED" || e.status === "CERTAIN"
        ? `<button class="btn sm ok" data-act="VALIDATED">Valider</button> <button class="btn sm danger" data-act="REJECTED">Rejeter</button>`
        : `<button class="btn sm" data-act="DELETE">${e.status === "MANUAL" ? "Supprimer" : "Annuler l'annotation"}</button>`;
      $("#detail").onclick = null;
      $("#detail").innerHTML = `<div class="detail">
        <div class="d-head"><span class="tag">${e.kind === "FLOW" ? "FLUX DE DONNÉES" : "JOINTURE"}</span>
        <h2 style="font-size:15px"><span class="obj link" data-open="${esc(e.source_object)}">${esc(e.source_object)}</span>
          <span class="muted">${e.kind === "FLOW" ? "→" : "="}</span> <span class="obj link" data-open="${esc(e.target_object)}">${esc(e.target_object)}</span></h2></div>
        <div class="d-actions">${actions}</div>
        <dl class="kv"><dt>Origine</dt><dd>${esc(ORIGIN_LABEL[e.origin] || e.origin)}</dd>
          <dt>Statut</dt><dd><span class="status ${esc(e.status)}">${esc(STATUS_LABEL[e.status] || e.status)}</span></dd>
          <dt>Score</dt><dd>${scorebar(e.score)} <b>${fmtScore(e.score)}</b>${e.base_score !== e.score ? ` <span class="muted">(calculé : ${fmtScore(e.base_score)})</span>` : ""}</dd>
          ${e.job ? `<dt>Job</dt><dd class="mono">${esc(e.job)}</dd>` : ""}
          ${e.kind === "RELATION" ? `<dt>Colonnes</dt><dd class="mono">${esc(e.source_column || "")} ↔ ${esc(e.target_column || "")}</dd>` : ""}
          ${e.annotation ? `<dt>Annoté</dt><dd>${esc(e.annotation.author)} · ${esc(fmtDate(e.annotation.updated_at))}${e.annotation.comment ? " — " + esc(e.annotation.comment) : ""}</dd>` : ""}</dl>
        <div class="section-title">Pourquoi ce lien ?</div><p style="margin:0">${esc(e.explanation || "")}</p>
        ${e.details && e.details.unexplained && e.details.unexplained.length ? `<p class="muted">Colonnes sans source : <span class="mono">${e.details.unexplained.map(esc).join(", ")}</span></p>` : ""}
        ${e.kind === "FLOW" ? `<div class="section-title">Correspondances de colonnes · ${d.columns.length}</div>
        <table class="cols"><thead><tr><th>Source</th><th>Cible</th><th>Transformation</th><th>Score</th></tr></thead><tbody>
        ${d.columns.map((c) => `<tr><td class="mono">${esc(c.source_column)}</td><td class="mono">${esc(c.target_column)}</td>
          <td class="mono muted">${esc(c.transformation || "copie")}${c.status ? ` <span class="status ${esc(c.status)}">${esc(STATUS_LABEL[c.status])}</span>` : ""}</td><td>${fmtScore(c.score)}</td></tr>`).join("")}
        </tbody></table>` : ""}
      </div>`;
      $("#detail").querySelectorAll("[data-open]").forEach((x) => x.addEventListener("click", () => openObject(x.dataset.open)));
      $("#detail").querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", async () => {
        await annotateEdge(e, b.dataset.act); showEdge(id);
      }));
    } catch (err) { fail(err); }
  }

  // ------------------------------------------------------------------ file de revue
  async function loadReview() {
    try {
      const d = await api("review?limit=50");
      $("#review-count").textContent = d.total_inferred - d.reviewed > 0 ? d.total_inferred - d.reviewed : "";
      $("#review").innerHTML = d.items.length ? d.items.map((e) => `<div class="link-card" data-edge="${esc(e.id)}" style="padding:10px 6px">
          <div class="row"><span class="obj" data-open="${esc(e.source_object)}">${esc(e.source_object)}</span><span>${scorebar(e.score)}<span class="score">${fmtScore(e.score)}</span></span></div>
          <div class="meta">→ <span class="obj" data-open="${esc(e.target_object)}">${esc(e.target_object)}</span> <i class="dot ${esc(e.target_layer)}"></i> · <span class="more-link" data-why>pourquoi ?</span></div>
          <div class="expl">${esc(e.explanation)}</div>
          <div class="btns"><button class="btn sm ok" data-act="VALIDATED">Valider</button><button class="btn sm danger" data-act="REJECTED">Rejeter</button>
          <button class="btn sm ghost" data-act="detail">Voir</button></div></div>`).join("")
        : '<p class="hint">Aucun flux en attente de validation.</p>';
      $("#review").onclick = async (ev) => {
        const card = ev.target.closest("[data-edge]"); if (!card) return;
        if (ev.target.closest("[data-why]")) return card.classList.toggle("open");
        const e = d.items.find((x) => String(x.id) === card.dataset.edge);
        const open = ev.target.closest("[data-open]");
        const act = ev.target.closest("[data-act]");
        if (act && act.dataset.act === "detail") { await openObject(e.target_object); return showEdge(e.id); }
        if (act) { await annotateEdge(e, act.dataset.act); return loadReview(); }
        if (open) return openObject(open.dataset.open);
      };
    } catch (e) { if (e.status !== 503) fail(e); }
  }
  async function refreshReviewCount() {
    try { const d = await api("review?limit=1"); $("#review-count").textContent = d.total_inferred - d.reviewed > 0 ? d.total_inferred - d.reviewed : ""; }
    catch (e) { /* base absente */ }
    if (!$("#tab-review").classList.contains("hidden")) loadReview();
  }

  // ------------------------------------------------------------------ modales
  function openModal(html) {
    $("#modal-body").innerHTML = html; $("#modal").classList.remove("hidden");
    return $("#modal-body");
  }
  function closeModal() { $("#modal").classList.add("hidden"); $("#modal-body").innerHTML = ""; }
  $(".modal-close").addEventListener("click", closeModal);
  $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

  function objectAutocomplete(input, onPick) {
    const wrap = input.parentElement; wrap.classList.add("suggest");
    const list = document.createElement("div"); list.className = "suggest-list hidden"; wrap.appendChild(list);
    input.addEventListener("input", debounce(async () => {
      const q = input.value.trim(); if (q.length < 2) { list.classList.add("hidden"); return; }
      const d = await api(`search?kind=OBJECT&q=${enc(q)}`);
      list.innerHTML = d.results.map((r) => `<div data-id="${esc(r.object_id)}"><i class="dot ${esc(r.layer)}"></i>${esc(r.object_id)}</div>`).join("");
      list.classList.toggle("hidden", !d.results.length);
    }, 200));
    list.addEventListener("click", (e) => {
      const it = e.target.closest("[data-id]"); if (!it) return;
      input.value = it.dataset.id; list.classList.add("hidden"); onPick && onPick(it.dataset.id);
    });
  }

  function openAddLink(d, preset) {
    const o = d.object;
    const colOptions = (cols, sel) => `<option value="">(objet entier)</option>` + cols.map((c) => `<option ${c === sel ? "selected" : ""}>${esc(c)}</option>`).join("");
    const body = openModal(`<h2>Ajouter un lien</h2>
      <div class="form">
        <div class="row2"><label>Type<select id="al-kind"><option value="FLOW">Flux (lineage)</option><option value="RELATION">Relation (clé)</option></select></label>
          <label id="al-way-l">Sens<select id="al-way"><option value="out">${esc(o.id)} alimente…</option><option value="in">… alimente ${esc(o.id)}</option></select></label></div>
        <label>Autre objet<input id="al-target" placeholder="tapez pour chercher…" autocomplete="off"></label>
        <div class="row2"><label>Colonne de ${esc(o.name)}<select id="al-mycol">${colOptions(d.columns.map((c) => c.name), preset.target_column)}</select></label>
          <label>Colonne de l'autre objet<select id="al-othercol"><option value="">(objet entier)</option></select></label></div>
        <label>Commentaire / transformation<input id="al-comment" placeholder="ex. renommage de segment, sum(montant)…"></label>
        <div class="muted" style="font-size:12px">Auteur : ${esc(user())} · date renseignée automatiquement</div>
        <button class="btn" id="al-save" style="justify-self:start">Enregistrer le lien</button>
      </div>`);
    $("#al-kind", body).value = preset.kind; $("#al-way", body).value = preset.way;
    const syncKind = () => { $("#al-way-l", body).style.visibility = $("#al-kind", body).value === "FLOW" ? "visible" : "hidden"; };
    $("#al-kind", body).addEventListener("change", syncKind); syncKind();
    objectAutocomplete($("#al-target", body), async (id) => {
      try {
        const other = await api("object/" + enc(id));
        $("#al-othercol", body).innerHTML = colOptions(other.columns.map((c) => c.name), preset.target_column);
      } catch (e) { fail(e); }
    });
    $("#al-save", body).addEventListener("click", async () => {
      const kind = $("#al-kind", body).value, other = $("#al-target", body).value.trim().toLowerCase();
      if (!other) return toast("Choisissez l'autre objet.", true);
      const mine = $("#al-mycol", body).value || null, theirs = $("#al-othercol", body).value || null;
      const outgoing = kind === "RELATION" || $("#al-way", body).value === "out";
      const src = outgoing ? o.id : other, tgt = outgoing ? other : o.id;
      await annotate(src, { link: { kind, target: tgt, status: "MANUAL", comment: $("#al-comment", body).value,
        source_column: outgoing ? mine : theirs, target_column: outgoing ? theirs : mine } });
      closeModal(); toast("Lien manuel ajouté"); showObject(o.id); loadGraph();
    });
  }

  function openPath(fromId) {
    const body = openModal(`<h2>Chemin entre deux tables</h2><div class="form">
      <label>Depuis<input value="${esc(fromId)}" disabled></label>
      <label>Vers<input id="pt-target" placeholder="tapez pour chercher…" autocomplete="off"></label>
      <button class="btn" id="pt-go" style="justify-self:start">Trouver le chemin</button><div id="pt-result"></div></div>`);
    objectAutocomplete($("#pt-target", body));
    $("#pt-go", body).addEventListener("click", async () => {
      const to = $("#pt-target", body).value.trim().toLowerCase(); if (!to) return;
      try {
        const d = await api(`path?from=${enc(fromId)}&to=${enc(to)}&min_score=${$("#min-score").value}`);
        if (!d.found) { $("#pt-result", body).innerHTML = '<p class="hint">Aucun chemin trouvé (flux + relations, 8 sauts max).</p>'; return; }
        closeModal();
        state.center = fromId; graph.setData({ center: fromId, nodes: d.nodes, edges: d.edges });
        graph.highlight(d.nodes.map((n) => n.id), d.edges.map((e) => e.id)); updateInfo();
        $("#graph-empty").classList.add("hidden");
        toast(`Chemin en ${d.edges.length} saut(s) : ` + d.nodes.map((n) => n.name).join(" → "));
      } catch (e) { fail(e); }
    });
  }

  // ---- import JSON
  $("#btn-import").addEventListener("click", openImport);
  async function openImport() {
    let examples = [];
    try { examples = await api("examples"); } catch (e) { /* pas d'exemples */ }
    const body = openModal(`<h2>Importer une structure</h2>
      <p class="muted" style="margin:0">Un fichier JSON décrivant vos tables, ou vos <b>requêtes SQL</b> (CREATE TABLE, CREATE VIEW, INSERT … SELECT, SELECT) : l'outil en déduit la structure, le lineage et les jointures. L'import remplace les données actuelles ; vos annotations sont conservées.</p>
      <label class="dropzone" id="drop"><input type="file" id="file" accept=".json,.jsonl,.sql,application/json" multiple hidden>
        <b>Déposez un fichier ici</b><br><span class="muted">.json, .jsonl ou un ou plusieurs .sql — ou cliquez pour choisir</span></label>
      <details class="more" style="margin-top:12px"><summary>Ou coller des requêtes SQL</summary>
        <div class="form" style="margin-top:6px"><textarea id="sql-text" rows="7" class="mono" placeholder="CREATE VIEW bv_x.clients AS SELECT c.id_client, c.nom FROM lac_x.clients_raw c;"></textarea>
          <button class="btn sm primary" id="sql-go" style="justify-self:start">Analyser le SQL</button></div></details>
      ${examples.length ? `<h3>Ou utiliser un exemple</h3><div style="display:flex;gap:6px;flex-wrap:wrap">
        ${examples.map((x) => `<button class="btn sm" data-example="${esc(x)}">${esc(x)}</button>`).join("")}</div>` : ""}
      <details class="more" style="margin-top:14px"><summary>Format attendu</summary>
        <pre class="mono" style="white-space:pre-wrap;margin:6px 0 0">{"objects": [{"id": "lac_x.table", "database": "lac_x", "name": "table", "type": "TABLE",
  "columns": [{"name": "col", "type": "string", "is_key": true}]}],
 "flows": [...facultatif], "relations": [...facultatif]}</pre></details>
      <div id="import-result" style="margin-top:14px"></div>`);
    const drop = $("#drop", body);
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); if (e.dataTransfer.files.length) importFiles(e.dataTransfer.files); });
    $("#file", body).addEventListener("change", (e) => { if (e.target.files.length) importFiles(e.target.files); });
    $("#sql-go", body).addEventListener("click", () => {
      const sql = $("#sql-text", body).value.trim();
      if (sql) doImport("import?format=sql&name=" + enc("SQL collé"), sql, "SQL collé");
    });
    body.querySelectorAll("[data-example]").forEach((b) => b.addEventListener("click", () => doImport(`import?example=${enc(b.dataset.example)}`, null, b.dataset.example)));
  }
  async function importFiles(fileList) {
    const files = [...fileList];
    const sqlFiles = files.filter((f) => f.name.toLowerCase().endsWith(".sql"));
    if (sqlFiles.length) {                       // plusieurs scripts SQL : analysés ensemble
      const texts = await Promise.all(sqlFiles.map((f) => f.text()));
      const label = sqlFiles.length > 1 ? `${sqlFiles.length} fichiers SQL` : sqlFiles[0].name;
      return doImport(`import?format=sql&name=${enc(label)}`, texts.join(";\n"), label);
    }
    const file = files[0], text = await file.text();
    const fmt = file.name.toLowerCase().endsWith(".jsonl") ? "&format=jsonl" : "";
    doImport(`import?name=${enc(file.name)}${fmt}`, text, file.name);
  }
  async function doImport(path, text, label) {
    const out = $("#import-result");
    out.innerHTML = `<p class="hint">⏳ Build en cours pour <b>${esc(label)}</b>…</p>`;
    try {
      const d = await api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: text || "" });
      if (!d.ok) {
        out.innerHTML = `<p><b>JSON refusé</b> — ${d.check.errors.length} erreur(s) :</p><div class="errors">${d.check.errors.slice(0, 50).map((x) => `<div>• ${esc(x)}</div>`).join("")}</div>`;
        return;
      }
      const r = d.report;
      out.innerHTML = `<p>✅ <b>${esc(label)}</b> importé en ${r.duration_s} s.</p>${reportHtml(r)}
        ${d.check.warnings.length ? `<details><summary>${d.check.warnings.length} avertissement(s)</summary><div class="errors" style="color:var(--warn)">${d.check.warnings.slice(0, 100).map((x) => `<div>• ${esc(x)}</div>`).join("")}</div></details>` : ""}
        <button class="btn" id="imp-close" style="margin-top:10px">Explorer</button>`;
      $("#imp-close").addEventListener("click", closeModal);
      await loadStats();
      resetView();
      refreshReviewCount();
    } catch (e) { out.innerHTML = `<p class="errors">${esc(e.message)}</p>`; }
  }
  function resetView() {
    state.center = null; state.selected = null; location.hash = "";
    graph.setData({ center: null, nodes: [], edges: [] });
    $("#graph-empty").innerHTML = EMPTY_HTML;
    $("#graph-empty").classList.remove("hidden"); $("#graph-info").textContent = "";
    $("#detail").onclick = null;
    $("#detail").innerHTML = '<div class="empty-detail"><p class="hint">Sélectionnez une bulle ou un lien pour voir le détail.</p></div>';
    runSearch(1);
  }

  function reportHtml(r) {
    const flows = r.flows_by_origin || {}, rels = r.relations_by_level || {};
    const nbRel = Object.entries(rels).filter(([k]) => k !== "groupes_ignores_trop_frequents" && k !== "relations_ecartees_plafond").reduce((s, [, v]) => s + v, 0);
    const expl = r.mart_columns_explained || {};
    const layers = r.objects_by_layer || {};
    return `<div class="stats-grid">
      <div class="stat"><b>${r.nb_objects}</b><span>objets (${Object.entries(layers).map(([k, v]) => `${esc(k)} ${v}`).join(", ")})</span></div>
      <div class="stat"><b>${r.nb_columns}</b><span>colonnes</span></div>
      <div class="stat"><b>${Object.values(flows).reduce((s, v) => s + v, 0)}</b><span>flux (${Object.entries(flows).map(([k, v]) => `${esc(ORIGIN_LABEL[k] || k)} ${v}`).join(", ") || "aucun"})</span></div>
      <div class="stat"><b>${nbRel}</b><span>relations de clés</span></div>
      <div class="stat"><b>${r.nb_orphans}</b><span>BV / marts sans source</span></div>
      <div class="stat"><b>${expl.rate == null ? "–" : Math.round(expl.rate * 100) + " %"}</b><span>colonnes de marts expliquées jusqu'au lac</span></div>
    </div>`;
  }

  $("#btn-stats").addEventListener("click", async () => {
    try {
      const s = await api("stats");
      const r = s.report || {};
      const body = openModal(`<h2>Rapport du build</h2>
        <dl class="kv"><dt>Source</dt><dd>${esc(s.input || "")}</dd><dt>Extraction</dt><dd>${esc(fmtDate(s.extracted_at))} ${esc(s.source || "")}</dd>
          <dt>Build</dt><dd>${esc(fmtDate(s.built_at))} · ${r.duration_s} s · recherche ${esc(s.fts)}</dd></dl>
        ${reportHtml(r)}
        <h3 style="font-size:13px">Flux inférés par score</h3>
        <div class="stats-grid">${Object.entries(r.inferred_by_score || {}).sort().map(([k, v]) => `<div class="stat"><b>${v}</b><span>${esc(k)}</span></div>`).join("") || '<span class="muted">aucun</span>'}</div>
        <h3 style="font-size:13px">Annotations de l'équipe</h3>
        <div class="stats-grid"><div class="stat"><b>${s.annotations.spec_urls}</b><span>specs (${Math.round((s.annotations.spec_coverage || 0) * 100)} % des objets)</span></div>
          <div class="stat"><b>${s.annotations.validated}</b><span>liens validés</span></div><div class="stat"><b>${s.annotations.rejected}</b><span>liens rejetés</span></div>
          <div class="stat"><b>${s.annotations.manual}</b><span>liens manuels</span></div></div>
        ${r.orphans && r.orphans.length ? `<h3 style="font-size:13px">Objets sans source trouvée (${r.nb_orphans})</h3>
          <div style="max-height:180px;overflow:auto">${r.orphans.map((o) => `<div class="obj link mono" data-open="${esc(o)}" style="cursor:pointer">${esc(o)}</div>`).join("")}</div>` : ""}
        ${r.frequent_columns && r.frequent_columns.length ? `<p class="muted" style="font-size:12px">Colonnes jugées trop fréquentes (exclues du blocking) : ${r.frequent_columns.map(esc).join(", ")}</p>` : ""}`);
      body.querySelectorAll("[data-open]").forEach((x) => x.addEventListener("click", () => { closeModal(); openObject(x.dataset.open); }));
    } catch (e) { if (e.status === 503) openImport(); else fail(e); }
  });

  // ------------------------------------------------------------------ démarrage
  async function loadStats() {
    try {
      state.stats = await api("stats");
      const ms = state.stats.min_score;
      if (ms != null && !loadStats.done) { $("#min-score").value = ms; $("#min-score-val").textContent = Number(ms).toFixed(2); }
      if (!loadStats.done) $("#depth").value = state.stats.ui.default_depth;
      loadStats.done = true;
      return true;
    } catch (e) {
      if (e.status === 503) {
        $("#graph-empty").innerHTML = `<div class="empty-dot"></div><h2>Aucune donnée pour l'instant</h2>
          <p>Importez un fichier JSON décrivant vos tables pour commencer.</p>
          <button class="btn primary" id="empty-import" style="margin-top:14px;pointer-events:auto">Importer un JSON</button>`;
        $("#empty-import").addEventListener("click", openImport);
        $("#results").innerHTML = '<p class="hint">Aucune donnée importée.</p>';
        return false;
      }
      fail(e); return false;
    }
  }

  $("#btn-theme").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    storageSet("lineage.theme", next);
  });

  $("#user").value = storageGet("lineage.user") || "";
  $("#user").addEventListener("change", () => storageSet("lineage.user", $("#user").value.trim()));

  (async function init() {
    const ok = await loadStats();
    if (!$("#user").value && state.stats) $("#user").placeholder = state.stats.user;
    if (!ok) return;
    refreshReviewCount();
    const m = location.hash.match(/obj=([^&]+)/);
    if (m) openObject(decodeURIComponent(m[1]));
  })();
})();
