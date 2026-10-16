/* tegchess: interfaz del informe. JS sin build; funciona servido (API) o estático (file://). */
(function () {
  "use strict";

  const STATIC = window.TEGCHESS_STATIC || null;
  const SERVER = !STATIC && location.protocol.startsWith("http");
  const START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

  const state = {
    reports: {},          // rival -> informe
    rival: null,
    tab: "summary",
    openingsBy: "family", // family | detail
    openingsWho: "rival", // rival | me
    tree: { who: "rival", color: "white", path: [] },
    weakWho: "rival",
    line: [],             // [{san, uci, fen}]
    idx: -1,              // -1 = posición inicial de la línea
    lineStart: START_FEN,
    shapesAt: null,       // {idx, shapes}
    orientation: "white",
  };

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v, d = 0) => (v === null || v === undefined || Number.isNaN(v) ? "–" : Number(v).toFixed(d));
  const colorName = (c) => (c === "white" ? "blancas" : "negras");

  // ---------------------------------------------------------------- tablero
  const cg = window.Chessground($("#board"), {
    fen: START_FEN,
    viewOnly: true,
    coordinates: true,
    animation: { enabled: true, duration: 150 },
    drawable: { enabled: false, visible: true },
  });

  function setLine(line, { startFen = START_FEN, idx = null, shapes = null, orientation = null, caption = "" } = {}) {
    state.line = line;
    state.lineStart = startFen;
    state.idx = idx === null ? line.length - 1 : idx;
    state.shapesAt = shapes ? { idx: state.idx, shapes } : null;
    if (orientation) state.orientation = orientation;
    $("#board-caption").textContent = caption;
    renderBoard();
  }

  function renderBoard() {
    const cur = state.idx >= 0 ? state.line[state.idx] : null;
    const fen = cur ? cur.fen : state.lineStart;
    const lastMove = cur && cur.uci ? [cur.uci.slice(0, 2), cur.uci.slice(2, 4)] : undefined;
    cg.set({ fen, lastMove, orientation: state.orientation, turnColor: fen.split(" ")[1] === "w" ? "white" : "black" });
    cg.setAutoShapes(state.shapesAt && state.shapesAt.idx === state.idx ? state.shapesAt.shapes : []);
    renderMoveList();
  }

  function renderMoveList() {
    const startBlack = state.lineStart.split(" ")[1] === "b";
    const startNum = parseInt(state.lineStart.split(" ")[5] || "1", 10);
    let html = "";
    state.line.forEach((m, i) => {
      const plyFromStart = i + (startBlack ? 1 : 0);
      const num = startNum + Math.floor(plyFromStart / 2);
      if (plyFromStart % 2 === 0) html += `<span class="num">${num}.</span>`;
      else if (i === 0) html += `<span class="num">${num}…</span>`;
      html += `<span class="mv${i === state.idx ? " current" : ""}" data-i="${i}">${esc(m.san)}</span>`;
    });
    $("#move-list").innerHTML = html || '<span class="muted">Elegí una línea o una posición del informe.</span>';
    $$(".mv", $("#move-list")).forEach((el) => el.addEventListener("click", () => { state.idx = +el.dataset.i; renderBoard(); }));
  }

  function nav(action) {
    if (action === "first") state.idx = -1;
    if (action === "prev") state.idx = Math.max(-1, state.idx - 1);
    if (action === "next") state.idx = Math.min(state.line.length - 1, state.idx + 1);
    if (action === "last") state.idx = state.line.length - 1;
    if (action === "flip") state.orientation = state.orientation === "white" ? "black" : "white";
    renderBoard();
  }
  $$("[data-nav]").forEach((b) => b.addEventListener("click", () => nav(b.dataset.nav)));
  document.addEventListener("keydown", (e) => {
    if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
    if (e.key === "ArrowLeft") nav("prev");
    if (e.key === "ArrowRight") nav("next");
    if (e.key === "Home") nav("first");
    if (e.key === "End") nav("last");
  });

  // ---------------------------------------------------------------- helpers de render
  function wdl(w, d, l) {
    const t = w + d + l || 1;
    return `<div class="wdl" title="${w} ganadas · ${d} tablas · ${l} perdidas">` +
      `<span class="w" style="width:${(100 * w) / t}%"></span><span class="d" style="width:${(100 * d) / t}%"></span>` +
      `<span class="l" style="width:${(100 * l) / t}%"></span></div>`;
  }
  const pctClass = (p) => (p >= 60 ? "pct-good" : p <= 40 ? "pct-bad" : "");
  const card = (label, value) => `<div class="card"><div class="label">${esc(label)}</div><div class="value">${value}</div></div>`;
  function table(headers, rows) {
    return `<div class="table-wrap"><table><thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead>` +
      `<tbody>${rows.join("") || `<tr><td colspan="${headers.length}" class="muted">Sin datos</td></tr>`}</tbody></table></div>`;
  }
  function seg(name, options, current) {
    return `<div class="seg" data-seg="${name}">` +
      options.map(([v, label]) => `<button data-v="${v}" class="${v === current ? "active" : ""}">${esc(label)}</button>`).join("") + "</div>";
  }
  function bindSeg(root, name, fn) {
    $$(`[data-seg="${name}"] button`, root).forEach((b) => b.addEventListener("click", () => fn(b.dataset.v)));
  }
  const report = () => state.reports[state.rival];
  const section = (who) => (who === "me" ? report().me : report().rival);
  const whoName = (who) => (who === "me" ? `Yo (${report().meta.me})` : report().meta.rival);

  // ---------------------------------------------------------------- Resumen
  function renderSummary() {
    const r = report();
    const p = r.rival.profile;
    const s = p.summary;
    const color = p.by_color.map((c) => `<tr><td>Con ${colorName(c.color)}</td><td>${c.games}</td>` +
      `<td>${wdl(c.wins, c.draws, c.losses)}</td><td class="${pctClass(c.score_pct)}">${fmt(c.score_pct)}%</td></tr>`);
    const perf = p.by_perf.map((x) => `<tr><td>${esc(x.perf)}</td><td>${x.games}</td><td>${fmt(x.avg_rating)}</td>` +
      `<td class="${pctClass(x.score_pct)}">${fmt(x.score_pct)}%</td></tr>`);
    const endings = p.endings.map((x) => `<tr><td>${esc(endingName(x.status))}</td><td>${x.wins}</td><td>${x.draws}</td><td>${x.losses}</td></tr>`);
    const m = r.meta;
    $("#tab-summary").innerHTML = `
      <h2>${esc(m.rival)}</h2>
      <p class="muted">Modalidades: ${esc(m.perf_types.join(", "))} · desde ${esc(m.since || "el inicio")}${m.until ? " hasta " + esc(m.until) : ""}
        · máx. ${m.max_games || "∞"} partidas · Stockfish prof. ${m.engine_depth}, primeros ${m.analysed_plies || "todos los"} plies
        · posiciones evaluadas: ${fmt(m.eval_coverage_pct)}% · generado ${esc(new Date(m.generated_at).toLocaleString())}</p>
      <div class="cards">
        ${card("Partidas", s.games)}
        ${card("Puntaje", fmt(s.score_pct) + "%")}
        ${card("Rating actual", fmt(s.last_rating))}
        ${card("Jugadas por partida", fmt(s.avg_moves))}
        ${card("Precisión media", s.avg_accuracy ? fmt(s.avg_accuracy) + "%" : "–")}
      </div>
      <h3>Resultados por color</h3>
      ${table(["", "Partidas", "G / T / P", "Puntaje"], color)}
      <h3>Por modalidad</h3>
      ${table(["Modalidad", "Partidas", "Rating medio", "Puntaje"], perf)}
      <h3>Cómo terminan sus partidas</h3>
      ${table(["Final", "Ganadas", "Tablas", "Perdidas"], endings)}
      ${planHtml()}`;
  }

  function endingName(status) {
    return ({ mate: "Mate", resign: "Abandono", outoftime: "Por tiempo", draw: "Tablas acordadas", stalemate: "Ahogado",
      timeout: "Abandonó la partida", noStart: "No empezó", cheat: "Trampa", variantEnd: "Fin de variante" })[status] || status;
  }

  function planHtml() {
    // Primer borrador del plan: aperturas donde el rival puntúa peor y posiciones donde más pierde.
    const r = report().rival;
    const items = [];
    ["white", "black"].forEach((c) => {
      const weakOpenings = r.openings[c].filter((o) => o.games >= 3).sort((a, b) => a.score_pct - b.score_pct).slice(0, 2);
      weakOpenings.forEach((o) => items.push(`Con ${colorName(c)} el rival puntúa ${fmt(o.score_pct)}% en <b>${esc(o.opening)}</b> (${o.games} partidas).`));
    });
    r.weak_positions.slice(0, 3).forEach((w) => items.push(
      `Suele jugar <b>${esc(w.usual_san)}</b> en una posición de ${esc(w.opening || "apertura")} que repite ${w.occurrences} veces ` +
      `(pierde ${fmt(w.avg_wp_loss, 1)} pts de % de victoria; el motor prefiere <b>${esc(w.best_san || "–")}</b>).`));
    if (!items.length) return "";
    return `<h3>Ideas para la partida (borrador)</h3><ul>${items.map((i) => `<li>${i}</li>`).join("")}</ul>`;
  }

  // ---------------------------------------------------------------- Aperturas
  function renderOpenings() {
    const root = $("#tab-openings");
    const who = report().me ? state.openingsWho : "rival";
    const sec = section(who);
    const key = state.openingsBy === "family" ? "openings" : "openings_detail";
    const rowsFor = (c) => sec[key][c].map((o) => `<tr>
        <td>${esc(o.opening)} <span class="muted">${esc(o.eco || "")}</span></td>
        <td>${o.games}</td><td>${wdl(o.wins, o.draws, o.losses)}</td>
        <td class="${pctClass(o.score_pct)}">${fmt(o.score_pct)}%</td>
        <td>${fmt(o.avg_opp_rating)}</td>
        <td title="${o.games_with_evals} partidas con evaluación">${fmt(o.avg_cpl)}</td>
        <td>${o.avg_eval_out === null ? "–" : (o.avg_eval_out > 0 ? "+" : "") + fmt(o.avg_eval_out / 100, 2)}</td></tr>`);
    const headers = ["Apertura", "Partidas", "G / T / P", "Puntaje", "Elo rival", "CPL apertura", "Eval. ply 20"];
    root.innerHTML = `
      <div class="toolbar">
        ${report().me ? seg("who", [["rival", report().meta.rival], ["me", "Yo"]], who) : ""}
        ${seg("by", [["family", "Por familia"], ["detail", "Por variante"]], state.openingsBy)}
      </div>
      <p class="muted">CPL apertura: pérdida media de centipeones en los primeros 20 plies (menos es mejor).
        Eval. ply 20: evaluación media al salir de la apertura, desde el punto de vista de ${esc(whoName(who))}.</p>
      <h3>${esc(whoName(who))} con blancas</h3>${table(headers, rowsFor("white"))}
      <h3>${esc(whoName(who))} con negras</h3>${table(headers, rowsFor("black"))}`;
    bindSeg(root, "who", (v) => { state.openingsWho = v; renderOpenings(); });
    bindSeg(root, "by", (v) => { state.openingsBy = v; renderOpenings(); });
  }

  // ---------------------------------------------------------------- Explorador
  function treeNode() {
    let node = section(state.tree.who).trees[state.tree.color];
    const nodes = [node];
    for (const uci of state.tree.path) {
      node = node.children.find((c) => c.uci === uci);
      if (!node) break;
      nodes.push(node);
    }
    return nodes;
  }

  function renderTree() {
    const root = $("#tab-tree");
    const who = report().me ? state.tree.who : "rival";
    state.tree.who = who;
    const nodes = treeNode();
    const node = nodes[nodes.length - 1];
    const crumbs = [`<button data-depth="0">Inicio</button>`].concat(
      nodes.slice(1).map((n, i) => `<button data-depth="${i + 1}">${esc(n.san)}</button>`));
    const rows = node.children.map((c) => `<tr class="clickable" data-uci="${esc(c.uci)}">
        <td><b>${esc(c.san)}</b> <span class="muted">${c.mover === state.tree.color ? "" : "(rival)"}</span></td>
        <td>${c.count}</td><td>${fmt((100 * c.count) / (node.count || 1))}%</td>
        <td class="${pctClass(c.score_pct)}">${fmt(c.score_pct)}%</td><td>${fmt(c.avg_cpl)}</td></tr>`);
    root.innerHTML = `
      <div class="toolbar">
        ${report().me ? seg("tw", [["rival", report().meta.rival], ["me", "Yo"]], who) : ""}
        ${seg("tc", [["white", "Con blancas"], ["black", "Con negras"]], state.tree.color)}
      </div>
      <p class="muted">Partidas de ${esc(whoName(who))} con ${colorName(state.tree.color)}. Puntaje desde su punto de vista;
        CPL = pérdida media de la jugada. Hacé clic en una jugada para avanzar.</p>
      <div class="crumbs">${crumbs.join("<span class='muted'>›</span>")}</div>
      <p>${node.count} partidas llegan a esta posición · puntaje ${fmt(node.score_pct)}%</p>
      ${table(["Jugada", "Partidas", "Frecuencia", "Puntaje", "CPL"], rows)}`;
    bindSeg(root, "tw", (v) => { state.tree.who = v; state.tree.path = []; renderTree(); });
    bindSeg(root, "tc", (v) => { state.tree.color = v; state.tree.path = []; renderTree(); });
    $$("[data-depth]", root).forEach((b) => b.addEventListener("click", () => {
      state.tree.path = state.tree.path.slice(0, +b.dataset.depth); renderTree();
    }));
    $$("tr[data-uci]", root).forEach((tr) => tr.addEventListener("click", () => {
      state.tree.path.push(tr.dataset.uci); renderTree();
    }));
    const line = nodes.slice(1).map((n) => ({ san: n.san, uci: n.uci, fen: n.fen }));
    setLine(line, { orientation: state.tree.color, caption: `Explorador: ${whoName(who)} con ${colorName(state.tree.color)}` });
  }

  // ---------------------------------------------------------------- Posiciones flojas
  function renderWeak() {
    const root = $("#tab-weak");
    const who = report().me ? state.weakWho : "rival";
    const list = section(who).weak_positions;
    const items = list.map((w, i) => `
      <div class="weak" data-i="${i}">
        <div class="head">
          <span><b>${esc(w.opening || "Posición")}</b> · jugada ${Math.ceil(w.ply / 2)} de las ${colorName(w.color)}</span>
          <span class="badge bad">−${fmt(w.avg_wp_loss, 1)} pts % victoria</span>
        </div>
        <div>Llega ${w.occurrences} veces · suele jugar <b>${esc(w.usual_san)}</b> (${w.usual_count}×, CPL ${fmt(w.usual_cpl)})
          · motor: <b>${esc(w.best_san || "–")}</b> · puntaje ${fmt(w.score_pct)}%
          · eval. ${w.eval_for_player === null ? "–" : fmt(w.eval_for_player / 100, 2)}</div>
        <div class="line">${esc(w.line.map((m) => m.san).join(" "))}</div>
      </div>`);
    root.innerHTML = `
      <div class="toolbar">${report().me ? seg("ww", [["rival", report().meta.rival], ["me", "Yo"]], who) : ""}</div>
      <p class="muted">Posiciones que ${esc(whoName(who))} repite al menos ${3} veces, ordenadas por daño total
        (pérdida media de % de victoria × apariciones). Rojo: su jugada habitual; verde: la del motor.</p>
      <div class="weak-list">${items.join("") || '<p class="muted">Sin posiciones con evaluación todavía (corré el análisis).</p>'}</div>`;
    bindSeg(root, "ww", (v) => { state.weakWho = v; renderWeak(); });
    $$(".weak", root).forEach((el) => el.addEventListener("click", () => {
      $$(".weak", root).forEach((x) => x.classList.remove("active"));
      el.classList.add("active");
      const w = list[+el.dataset.i];
      const shapes = [];
      if (w.usual_uci) shapes.push({ orig: w.usual_uci.slice(0, 2), dest: w.usual_uci.slice(2, 4), brush: "red" });
      if (w.best_uci && w.best_uci !== w.usual_uci) shapes.push({ orig: w.best_uci.slice(0, 2), dest: w.best_uci.slice(2, 4), brush: "green" });
      setLine(w.line, { shapes, orientation: w.color, caption: `${w.opening || ""} · ${colorName(w.color)} juegan` });
    }));
  }

  // ---------------------------------------------------------------- Configuración y tareas
  async function renderConfig() {
    const root = $("#tab-config");
    const cfg = await api("/api/config");
    const status = await api("/api/status");
    const perfs = ["bullet", "blitz", "rapid", "classical", "correspondence"];
    root.innerHTML = `
      <form class="config" id="config-form">
        <p class="muted">Stockfish: ${status.stockfish ? esc(status.stockfish) : "<b>no encontrado</b>"} ·
          token de Lichess: ${status.token ? "sí" : "no (opcional, va en .env)"}</p>
        <label>Mi usuario de Lichess <input name="me" value="${esc(cfg.me)}" required></label>
        <label>Rivales (uno por línea) <textarea name="opponents" rows="5">${esc(cfg.opponents.join("\n"))}</textarea></label>
        <div><div class="muted">Modalidades</div><div class="checks">
          ${perfs.map((p) => `<label><input type="checkbox" name="perf" value="${p}" ${cfg.download.perf_types.includes(p) ? "checked" : ""}> ${p}</label>`).join("")}
        </div></div>
        <div class="row">
          <label>Máx. partidas por jugador <input type="number" name="max_games" min="1" value="${cfg.download.max_games ?? ""}"></label>
          <label>Desde <input type="date" name="since" value="${cfg.download.since ?? ""}"></label>
          <label>Hasta <input type="date" name="until" value="${cfg.download.until ?? ""}"></label>
          <label class="checks"><input type="checkbox" name="rated_only" ${cfg.download.rated_only ? "checked" : ""}> Solo rated</label>
        </div>
        <div class="row">
          <label>Profundidad Stockfish <input type="number" name="depth" min="6" max="30" value="${cfg.engine.depth}"></label>
          <label>Analizar hasta el ply (0 = todo) <input type="number" name="max_ply" min="0" value="${cfg.engine.max_ply}"></label>
          <label>Procesos de Stockfish <input type="number" name="workers" min="1" placeholder="auto" value="${cfg.engine.workers ?? ""}"></label>
          <label>Mín. repeticiones de posición <input type="number" name="min_occurrences" min="2" value="${cfg.analysis.min_occurrences}"></label>
        </div>
        <div><button class="primary" type="submit">Guardar</button> <span id="config-msg" class="muted"></span></div>
      </form>`;
    $("#config-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      const num = (v) => (v === "" || v === null ? null : Number(v));
      cfg.me = f.get("me").trim();
      cfg.opponents = f.get("opponents").split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
      cfg.download.perf_types = f.getAll("perf");
      cfg.download.max_games = num(f.get("max_games"));
      cfg.download.since = f.get("since") || null;
      cfg.download.until = f.get("until") || null;
      cfg.download.rated_only = f.get("rated_only") === "on";
      cfg.engine.depth = num(f.get("depth"));
      cfg.engine.max_ply = num(f.get("max_ply"));
      cfg.engine.workers = num(f.get("workers"));
      cfg.analysis.min_occurrences = num(f.get("min_occurrences"));
      try {
        await api("/api/config", { method: "PUT", body: JSON.stringify(cfg) });
        $("#config-msg").textContent = "Guardado.";
      } catch (err) {
        $("#config-msg").textContent = "Error: " + err.message;
      }
    });
  }

  async function startJob(action) {
    try {
      await api("/api/jobs", { method: "POST", body: JSON.stringify({ action }) });
      pollJob();
    } catch (err) {
      alert(err.message);
    }
  }

  async function pollJob() {
    const s = await api("/api/jobs");
    const box = $("#job-status");
    $$("[data-job]").forEach((b) => (b.disabled = s.running));
    if (!s.action) return box.classList.add("hidden");
    box.classList.remove("hidden");
    const steps = s.steps.map((st) => `<div class="step"><span>${esc(st.desc)}</span>` +
      (st.total ? `<progress max="${st.total}" value="${Math.min(st.done, st.total)}"></progress>` : "") +
      `<span class="muted">${st.done}${st.total ? " / " + st.total : ""}</span></div>`).join("");
    const head = s.running ? `Trabajando (${esc(s.action)})…` : s.error ? "La tarea falló" : "Listo ✔";
    box.innerHTML = `<b>${head}</b>${steps}${s.error ? `<pre>${esc(s.error)}</pre>` : ""}`;
    if (s.running) setTimeout(pollJob, 1000);
    else if (!s.error && ["report", "all"].includes(s.action)) loadReports();
  }

  async function api(url, opts = {}) {
    const res = await fetch(url, { headers: { "Content-Type": "application/json" }, ...opts });
    if (!res.ok) {
      let msg = res.statusText;
      try { msg = (await res.json()).detail || msg; } catch (_) { /* sin cuerpo JSON */ }
      throw new Error(msg);
    }
    return res.json();
  }

  // ---------------------------------------------------------------- navegación
  function showTab(tab) {
    state.tab = tab;
    $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    $$(".tab").forEach((t) => t.classList.toggle("hidden", t.id !== "tab-" + tab));
    renderTab();
  }

  function renderTab() {
    const hasReport = !!(state.rival && report());
    $("#empty").classList.toggle("hidden", hasReport || state.tab === "config");
    if (state.tab === "config") return renderConfig();
    $$(".tab").forEach((t) => { if (!hasReport && t.id !== "tab-config") t.innerHTML = ""; });
    if (!hasReport) return;
    ({ summary: renderSummary, openings: renderOpenings, tree: renderTree, weak: renderWeak })[state.tab]();
  }

  async function loadReports() {
    let rivals = [];
    if (STATIC) {
      state.reports = STATIC;
      rivals = Object.keys(STATIC);
    } else if (SERVER) {
      rivals = (await api("/api/reports")).map((r) => r.rival);
      await Promise.all(rivals.map(async (r) => { state.reports[r] = await api("/api/reports/" + encodeURIComponent(r)); }));
    }
    const sel = $("#rival-select");
    sel.innerHTML = rivals.map((r) => `<option value="${esc(r)}">${esc(r)}</option>`).join("");
    if (!rivals.includes(state.rival)) state.rival = rivals[0] || null;
    if (state.rival) sel.value = state.rival;
    renderTab();
  }

  $("#rival-select").addEventListener("change", (e) => {
    state.rival = e.target.value;
    state.tree.path = [];
    renderTab();
  });
  $$("#tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  $$("[data-job]").forEach((b) => b.addEventListener("click", () => startJob(b.dataset.job)));
  $("#print-btn").addEventListener("click", () => {
    // Para el PDF se imprimen todas las secciones (renderizadas) una tras otra.
    if (report()) { renderSummary(); renderOpenings(); renderWeak(); }
    window.print();
  });

  if (!SERVER) $$(".server-only").forEach((el) => el.classList.add("hidden"));
  setLine([]);
  loadReports();
  if (SERVER) pollJob();
})();
