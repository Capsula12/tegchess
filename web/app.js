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
    loadedAt: 0,          // cuándo se cargaron los informes (para recargar al terminar una tarea)
    status: null,         // /api/status: modo (lichess | pgn), yo, rivales
    me: null,             // mi perfil general (/api/me)
    view: "rival",        // rival | profile
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
  const sgn = (v, d = 0) => (v === null || v === undefined ? "–" : (v > 0 ? "+" : "") + fmt(v, d));
  const evalTxt = (cp) => (cp === null || cp === undefined ? "–" : sgn(cp / 100, 2));
  const confBadge = (c) => (c ? `<span class="badge ${c === "baja" ? "warn" : c === "alta" ? "good" : ""}" title="Confianza según la cantidad de partidas">${c}</span>` : "");
  const ciTitle = (x) => (x ? ` title="Intervalo de 90%: ${x[0]}–${x[1]}%"` : "");
  const lowSample = (n) => (n < 10 ? ' <span class="badge warn" title="Con menos de 10 partidas el porcentaje es poco confiable">poca muestra</span>' : "");
  const report = () => state.reports[state.rival];
  function debounce(fn, ms) {
    let t = null;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  }
  const section = (who) => (who === "me" ? report().me : report().rival);
  const RNAME = () => report().meta.rival_name || report().meta.rival;
  const MNAME = () => report().meta.me_name || report().meta.me;
  const whoName = (who) => (who === "me" ? `Yo (${MNAME()})` : RNAME());

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
    $("#tab-summary").innerHTML = `
      <div class="cards">
        ${card("Partidas", s.games)}
        ${card("Puntaje", fmt(s.score_pct) + "%")}
        ${card("Rating actual", fmt(s.last_rating))}
        ${card("Jugadas por partida", fmt(s.avg_moves))}
        ${card("Precisión media", s.avg_accuracy ? fmt(s.avg_accuracy) + "%" : "–")}
      </div>
      ${strengthHtml(r)}
      <h3>Resultados por color</h3>
      ${table(["", "Partidas", "G / T / P", "Puntaje"], color)}
      <h3>Por modalidad</h3>
      ${table(["Modalidad", "Partidas", "Rating medio", "Puntaje"], perf)}
      <h3>Cómo terminan sus partidas</h3>
      ${table(["Final", "Ganadas", "Tablas", "Perdidas"], endings)}
      ${planHtml()}`;
  }

  function strengthHtml(r) {
    const st = r.rival.profile.strength;
    if (!st || !st.overall) return "";
    const row = (label, x) => (x ? `<tr><td>${label}</td><td>${x.games}</td><td>${fmt(x.score_pct)}%</td>
      <td>${fmt(x.expected_pct)}%</td><td class="${x.over_pct >= 3 ? "pct-good" : x.over_pct <= -3 ? "pct-bad" : ""}">${sgn(x.over_pct)}</td>
      <td>${fmt(x.avg_opp_rating)}</td></tr>` : "");
    const rows = [row("General", st.overall)]
      .concat(st.by_color.map((x) => row(`Con ${colorName(x.key)}`, x)))
      .concat(st.by_perf.map((x) => row(esc(x.key), x)))
      .concat([row("Últimas 30 partidas", st.form.recent), row("Antes", st.form.before)]);
    const b = r.base;
    const baseCard = b ? `<div class="expect">
        <div class="expect-num">${fmt(b.expected_pct)}%</div>
        <div><b>Tu expectativa por Elo</b> contra ${esc(RNAME())} en ${esc(b.perf)}: vos ${b.my_rating}, él ${b.rival_rating}
          (${sgn(b.gap)}). De cada 10 partidas, unos ${fmt(b.expected_pct / 10, 1)} puntos para vos.
          ${b.big_gap ? "<br><b>La diferencia es grande</b>: el plan apunta a mejorar tus chances, sobre todo con la apertura." : ""}
          <div class="muted">Él rinde ${sgn(b.rival_over_pct)} puntos sobre su Elo; vos, ${sgn(b.my_over_pct)}.</div></div>
      </div>` : "";
    return `${baseCard}
      <h3>Rendimiento sobre el Elo</h3>
      <p class="muted">Puntaje real contra el esperado por los ratings de cada partida: dice si rinde más o menos que su
        rating (positivo = rinde de más). Es más justo que el porcentaje bruto porque descuenta la fuerza de los rivales.</p>
      ${table(["", "Partidas", "Puntaje", "Esperado por Elo", "Diferencia", "Elo rival medio"], rows)}`;
  }

  function endingName(status) {
    return ({ mate: "Mate", resign: "Abandono", outoftime: "Por tiempo", draw: "Tablas acordadas", stalemate: "Ahogado",
      timeout: "Abandonó la partida", noStart: "No empezó", cheat: "Trampa", variantEnd: "Fin de variante" })[status] || status;
  }

  function planHtml() {
    // Sin mi usuario no hay cruce: ideas a partir de las debilidades del rival solamente.
    if (report().plan) return `<p class="muted no-print">El plan concreto contra este rival está en la pestaña <b>Plan de partida</b>.</p>`;
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

  // ---------------------------------------------------------------- Plan de partida
  const KIND = {
    base: ["Base", "info"], tiempo: ["Tiempo", "info"], preparar: ["Preparar", "good"], apertura: ["Apertura", ""],
    linea: ["Línea", "good"], trampa: ["Trampa", "good"], evitar: ["Evitar", "bad"], reforzar: ["Reforzar", "warn"],
  };

  function showItem(item, color, caption) {
    setLine(item.line || [], { idx: item.idx ?? null, shapes: item.shapes || null, orientation: color, caption });
  }

  function battleRows(list, color) {
    return list.map((b, i) => {
      const opt = (b.options || [])[0];
      const move = b.to_move === "me"
        ? (opt ? `vos: <b>${esc(opt.san)}</b> <span class="muted">(${fmt(opt.expected_pct)}%)</span>` : "vos")
        : `él: <b>${esc(b.usual_san || "–")}</b>`;
      return `<tr class="clickable" data-i="${i}">
        <td>${esc(b.opening || "–")} <span class="muted">jug. ${Math.ceil(b.ply / 2)}</span></td>
        <td>${move}</td>
        <td${ciTitle(b.my_ci)}>${b.my_games} · ${fmt(b.my_score_pct)}%</td>
        <td${ciTitle(b.rival_ci)}>${b.rival_games} · ${fmt(b.rival_score_pct)}%</td>
        <td>${fmt(b.rival_reach_pct)}%</td>
        <td>${evalTxt(b.eval_for_me)}</td>
        <td><b>${fmt(b.expected_pct)}%</b> <span class="muted">(${sgn(b.delta_pct)})</span></td>
        <td>${confBadge(b.confidence)}</td></tr>`;
    });
  }

  function renderPlan() {
    const root = $("#tab-plan");
    const r = report();
    if (!r.plan) {
      root.innerHTML = `<p class="muted">Para cruzar repertorios completá tu usuario (<b>me</b>) en la configuración y regenerá el informe.</p>`;
      return;
    }
    const color = state.planColor || "white";
    const m = r.matchup ? r.matchup[color] : null;
    const items = r.plan[color];
    const opp = colorName(color === "white" ? "black" : "white");
    const famRows = m ? m.families.map((f) => `<tr>
        <td>${esc(f.opening)} <span class="muted">${esc(f.eco || "")}</span>${f.reinforce ? ' <span class="badge warn">reforzar</span>' : ""}</td>
        <td${ciTitle(f.my_ci)}>${f.my_games} · ${fmt(f.my_score_pct)}% <span class="muted">(${sgn(f.my_over_pct)})</span></td>
        <td${ciTitle(f.rival_ci)}>${f.rival_games} · ${fmt(f.rival_score_pct)}% <span class="muted">(${sgn(f.rival_over_pct)})</span></td>
        <td>${evalTxt(f.my_eval_out)} / ${evalTxt(f.rival_eval_out)}</td>
        <td><b>${fmt(f.expected_pct)}%</b> <span class="muted">(${sgn(f.delta_pct)})</span></td>
        <td>${confBadge(f.confidence)}</td></tr>`) : [];
    const bHeaders = ["Posición", "Le toca", "Vos (partidas · %)", "Él (partidas · %)", "Él llega", "Eval.", "Esperado (vs. Elo)", "Confianza"];
    const baseTxt = r.base ? `${fmt(r.base.expected_pct)}%` : "50%";
    root.innerHTML = `
      <div class="toolbar">${seg("pc", [["white", "Yo con blancas"], ["black", "Yo con negras"]], color)}</div>
      <p class="muted">Vos con ${colorName(color)} contra ${esc(RNAME())} con ${opp}. <b>Esperado</b> = tu expectativa por Elo
        (${baseTxt}) + la ventaja según el motor + cuánto rinde cada uno sobre su Elo en esa posición (muy suavizado: con
        pocas partidas casi no cuenta). Entre paréntesis, cuánto suma o resta sobre el Elo. Clic: la línea en el tablero.</p>
      <div class="plan-list">${items.map((it, i) => `
        <div class="plan-item${it.line ? " clickable" : ""}" data-i="${i}">
          <span class="badge ${KIND[it.kind]?.[1] || ""}">${KIND[it.kind]?.[0] || esc(it.kind)}</span>
          <span>${esc(it.text)}</span>
        </div>`).join("") || '<p class="muted">Sin recomendaciones: faltan partidas en común o evaluaciones.</p>'}</div>
      ${m ? `<h3>Aperturas que juegan los dos</h3>
      <p class="muted">Entre paréntesis, rendimiento sobre el Elo. "Salida": evaluación media al terminar la apertura
        (jugada 10), desde cada uno: es la señal más estable para elegir apertura.</p>
      ${table(["Apertura", "Vos (partidas · %)", `Él con ${opp}`, "Salida vos / él", "Esperado (vs. Elo)", "Confianza"], famRows)}
      <h3>Posiciones favorables</h3>
      <div data-battles="favorable">${table(bHeaders, battleRows(m.favorable, color))}</div>
      <h3>Posiciones a evitar</h3>
      <div data-battles="danger">${table(bHeaders, battleRows(m.danger, color))}</div>` : ""}`;
    bindSeg(root, "pc", (v) => { state.planColor = v; renderPlan(); });
    $$(".plan-item.clickable", root).forEach((el) => el.addEventListener("click", () => {
      $$(".plan-item", root).forEach((x) => x.classList.remove("active"));
      el.classList.add("active");
      const it = items[+el.dataset.i];
      showItem(it, color, KIND[it.kind]?.[0] || "");
    }));
    $$("[data-battles]", root).forEach((box) => {
      const list = m[box.dataset.battles];
      $$("tr[data-i]", box).forEach((tr) => tr.addEventListener("click", () => {
        const b = list[+tr.dataset.i];
        const opt = (b.options || [])[0];
        const shapes = b.to_move === "me" && opt ? [{ orig: opt.uci.slice(0, 2), dest: opt.uci.slice(2, 4), brush: "green" }] : null;
        showItem({ line: b.line, shapes }, color, `${b.opening || ""} · esperado ${fmt(b.expected_pct)}%`);
      }));
    });
  }

  // ---------------------------------------------------------------- Preparación
  function renderPrep() {
    const root = $("#tab-prep");
    const r = report();
    const color = state.prepColor || "white";
    const pr = r.prep ? r.prep[color] : null;
    const opp = colorName(color === "white" ? "black" : "white");
    if (!pr) {
      root.innerHTML = '<p class="muted">Este informe es anterior a la preparación: regeneralo con <b>Generar informes</b>.</p>';
      return;
    }
    const ml = pr.main_line || [];
    const targets = pr.targets || [];
    const items = targets.map((t, i) => `
      <div class="weak" data-i="${i}">
        <div class="head">
          <span><b>${t.memorize_plies} jugadas</b> · ${t.my_moves} tuyas para memorizar
            ${t.in_my_repertoire ? '<span class="badge good">ya las jugás</span>' : ""}</span>
          <span>
            <span class="badge ${t.kind === "trampa" ? "good" : "info"}">${t.kind === "trampa" ? "su error" : "ventaja"}</span>
            <span class="badge">llega ${fmt(t.reach_pct)}%</span>
            <span class="badge good">+${fmt(t.gain_pct)} pts</span>
          </span>
        </div>
        <div class="line">${esc(t.line.map((m) => m.san).join(" "))}</div>
        <div class="muted" style="font-size:12px;margin-top:4px">Evaluación al llegar: ${evalTxt(t.eval_for_me)} ·
          ${t.rival_games} partidas suyas en esa posición · valor ${fmt(t.value, 1)} (probabilidad × ganancia)</div>
      </div>`);
    root.innerHTML = `
      <div class="toolbar">${seg("prc", [["white", "Yo con blancas"], ["black", "Yo con negras"]], color)}</div>
      <p class="muted">Para una partida preparada contra ${esc(RNAME())} con ${opp} (${pr.games} partidas suyas). Lo que
        conviene memorizar: las líneas que él repite y en las que quedás mejor según el motor. "Llega" es la probabilidad de
        que siga la línea si vos jugás esas jugadas; "+pts" es lo que suma a tu expectativa la posición a la que llegás.</p>
      <div class="cards">
        ${card("Qué tan predecible es", pr.predictability_pct === null ? "–" : pr.predictability_pct + "%")}
        ${card("Línea principal hasta", ml.length ? `jugada ${Math.ceil(ml.length / 2)}` : "–")}
        ${card("Llega al final de esa línea", pr.main_line_reach_pct === null ? "–" : fmt(pr.main_line_reach_pct) + "%")}
      </div>
      <h3>Su línea más jugada</h3>
      <div class="plan-item clickable" id="prep-main"><span class="badge info">Línea</span>
        <span>${ml.length ? esc(ml.map((m, i) => (i % 2 === 0 ? `${i / 2 + 1}.` : "") + m.san).join(" ")) : "Sin datos suficientes."}</span></div>
      <p class="muted">Se sigue su jugada más frecuente (y la respuesta más común de sus rivales) mientras la probabilidad
        de que llegue sea al menos 25%. Más allá de eso, memorizar rinde poco: se va por otro lado.</p>
      <h3>Líneas para preparar</h3>
      <div class="weak-list">${items.join("") || '<p class="muted">No hay líneas con ventaja clara y suficiente probabilidad con los datos actuales.</p>'}</div>`;
    bindSeg(root, "prc", (v) => { state.prepColor = v; renderPrep(); });
    $("#prep-main", root).addEventListener("click", () => setLine(ml, { orientation: color, caption: `Línea principal de ${RNAME()}` }));
    $$(".weak", root).forEach((el) => el.addEventListener("click", () => {
      $$(".weak", root).forEach((x) => x.classList.remove("active"));
      el.classList.add("active");
      const t = targets[+el.dataset.i];
      const shapes = t.best_reply_uci ? [{ orig: t.best_reply_uci.slice(0, 2), dest: t.best_reply_uci.slice(2, 4), brush: "green" }] : null;
      setLine(t.line, { shapes, orientation: color, caption: `Preparación · llega ${fmt(t.reach_pct)}%` });
    }));
  }

  // ---------------------------------------------------------------- Mi juego
  function improvementHtml(imp, part = "all") {
    const byKey = Object.fromEntries(imp.areas.map((a) => [a.key, a]));
    const prio = imp.priorities.map((k) => byKey[k]);
    const detail = (a) => {
      if (a.key === "errors") {
        const rows = (a.by_phase || []).map((p) => `<tr><td>${PHASES[p.phase]}</td><td>${p.moves}</td>
          <td class="${p.excess_pct > 0.5 ? "pct-bad" : p.excess_pct < -0.5 ? "pct-good" : ""}">${fmt(p.error_rate_pct, 1)}% / ${fmt(p.opp_error_rate_pct, 1)}%</td>
          <td>${fmt(p.blunder_rate_pct, 1)}% / ${fmt(p.opp_blunder_rate_pct, 1)}%</td><td>${fmt(p.avg_cpl)} / ${fmt(p.opp_avg_cpl)}</td></tr>`);
        const k = a.kinds || {};
        const total = (k.missed_tactic || 0) + (k.allowed_tactic || 0) + (k.positional || 0) || 1;
        const bar = (n, cls, label) => `<div class="bar-row"><div class="lbl">${label}</div><div class="bar-track">
          <div class="bar ${cls}"><span style="width:${(100 * (n || 0)) / total}%"></span></div>
          <div class="bar-val">${n || 0} (${fmt((100 * (n || 0)) / total)}%)</div></div></div>`;
        return table(["Fase", "Tus jugadas evaluadas", "Errores vos / rivales", "Graves vos / rivales", "CPL vos / rivales"], rows) +
          `<div class="bars" style="margin-top:8px">${bar(k.missed_tactic, "", "Táctica no vista")}
            ${bar(k.allowed_tactic, "me", "Táctica permitida")}${bar(k.positional, "", "Posicional")}</div>`;
      }
      if (a.key === "openings") {
        const rows = (a.weak || []).map((o) => `<tr><td>${esc(o.opening)} <span class="muted">${colorName(o.color)}</span></td>
          <td>${o.games}</td><td>${evalTxt(o.avg_eval_out)}</td><td>${sgn(o.over_pct)}</td><td>${fmt(o.cost_per_100, 1)}</td></tr>`);
        return rows.length ? table(["Apertura", "Partidas", "Salida (motor)", "Sobre tu Elo", "Pts / 100"], rows) : "";
      }
      if (a.key === "endgames") {
        const rows = (a.types || []).map((t) => `<tr><td>${esc(t.type)}</td><td>${t.games}${lowSample(t.games)}</td>
          <td>${fmt(t.score_pct)}%</td><td>${sgn(t.over_pct)}</td></tr>`);
        return table(["Tipo de final", "Partidas", "Puntaje", "Sobre tu Elo"], rows);
      }
      if (a.key === "tendencies") {
        const row = (label, x) => `<tr><td>${label}</td><td>${x.games}</td><td>${sgn(x.over_pct)}</td><td>${confBadge(x.confidence)}</td></tr>`;
        return table(["Situación", "Partidas", "Sobre tu Elo", "Confianza"], [row("Después de perder (misma sesión)", a.after_loss),
          row("Después de ganar (misma sesión)", a.after_win), row("Primeras 4 de la sesión", a.early_session),
          row("Desde la 5ª de la sesión", a.long_session)]);
      }
      return "";
    };
    const areaHtml = (a) => `
      <div class="area">
        <div class="area-head"><h3>${esc(a.title)}</h3>
          <span class="badge ${a.cost_per_100 >= 3 ? "bad" : a.cost_per_100 >= 1 ? "warn" : ""}" title="Estimación de puntos que te cuesta cada 100 partidas">
            ${a.cost_per_100 > 0 ? "−" + fmt(a.cost_per_100, 1) + " pts / 100 partidas" : "sin costo claro"}</span></div>
        ${a.findings.length ? `<ul class="findings">${a.findings.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>` : '<p class="muted">Nada llamativo con los datos actuales.</p>'}
        ${a.tips.length ? `<div class="tips"><b>Qué hacer</b><ul>${a.tips.map((t) => `<li>${esc(t)}</li>`).join("")}</ul></div>` : ""}
        ${detail(a)}
      </div>`;
    const priorities = `
      <h2>Prioridades</h2>
      <p class="muted">Cada área estima cuántos puntos te cuesta cada 100 partidas <b>de más respecto de tus rivales en las
        mismas partidas</b> (o de tu Elo), para saber por dónde empezar. Jugadas tuyas evaluadas por el motor:
        ${fmt(imp.eval_coverage_pct)}%${imp.eval_coverage_pct < 80 ? " (errores, fases y conversión mejoran con más análisis: <code>engine.analyse_my_games_fully</code>)" : ""}.</p>
      <div class="cards">${prio.map((a, i) => `<div class="card prio"><div class="label">${i + 1}. ${esc(a.title)}</div>
        <div class="value">−${fmt(a.cost_per_100, 1)}</div><div class="sub">puntos cada 100 partidas</div></div>`).join("") ||
        '<p class="muted">Nada que se destaque todavía.</p>'}</div>`;
    const areas = `<h2>Qué mejorar</h2>${imp.areas.map(areaHtml).join("")}`;
    return part === "priorities" ? priorities : part === "areas" ? areas : priorities + areas;
  }

  // ---------------------------------------------------------------- Mi perfil
  function metricValue(fmtKind, v) {
    if (v === null || v === undefined) return "–";
    if (fmtKind === "signed") return sgn(v, 1);
    if (fmtKind === "pct") return fmt(v, 1) + "%";
    if (fmtKind === "eval") return evalTxt(v);
    if (fmtKind === "int") return fmt(v);
    return fmt(v, 1);
  }

  function openingRows(list) {
    return list.map((o) => `<tr><td>${esc(o.opening)} <span class="muted">con ${colorName(o.color)}</span></td>
      <td>${o.games}</td><td${ciTitle(o.score_ci)}>${fmt(o.score_pct)}%</td><td>${evalTxt(o.avg_eval_out)}</td>
      <td>${sgn(o.over_pct)}</td><td class="${o.rating_pts > 0 ? "pct-good" : "pct-bad"}">${sgn(o.rating_pts, 1)}</td>
      <td>${confBadge(o.confidence)}</td></tr>`);
  }

  function renderProfile() {
    const root = $("#tab-profile");
    const pr = state.me;
    if (!pr) {
      root.innerHTML = `<p class="muted">Todavía no hay perfil propio. Completá tu usuario en <b>Configuración</b> y tocá
        <b>Generar informes</b>.</p>`;
      return;
    }
    const cmp = pr.comparison;
    const metricRows = cmp.metrics.map((m) => `<tr><td>${esc(m.label)}</td>
      <td class="verdict-${m.verdict || ""}">${metricValue(m.format, m.me)}</td>
      <td>${metricValue(m.format, m.rivals_avg)}</td>
      <td>${m.best ? `${metricValue(m.format, m.best.value)} <span class="muted">${esc(m.best.name)}</span>` : "–"}</td>
      <td>${m.rank ? `<span class="rank">${m.rank}º de ${m.of}</span>` : "–"}</td></tr>`);
    const weak = (pr.me.weak_positions || []).slice(0, 8);
    const weakItems = weak.map((w, i) => `
      <div class="weak" data-i="${i}">
        <div class="head"><span><b>${esc(w.opening || "Posición")}</b> · jugada ${Math.ceil(w.ply / 2)} con ${colorName(w.color)}</span>
          <span class="badge bad">−${fmt(w.avg_wp_loss, 1)} pts</span></div>
        <div>Llegás ${w.occurrences} veces · solés jugar <b>${esc(w.usual_san)}</b> · el motor prefiere <b>${esc(w.best_san || "–")}</b></div>
      </div>`);
    const op = cmp.openings;
    const oHeaders = ["Apertura", "Partidas", "Puntaje", "Salida (motor)", "Sobre tu Elo", "Puntos", "Confianza"];
    root.innerHTML = `
      ${improvementHtml(pr.improvement, "priorities")}
      <h2>Vos vs. tus rivales</h2>
      <p class="muted">Tus números al lado de los de tus rivales con al menos 20 partidas (todos con el mismo criterio: la calidad de juego
        se mide en la apertura, que es lo que está analizado para todos). En verde, donde estás mejor que el promedio; en rojo, peor.</p>
      ${table(["Métrica", "Vos", "Promedio rivales", "Mejor del grupo", "Tu puesto"], metricRows)}
      <h2>Tus aperturas</h2>
      <p class="muted">"Puntos": cuánto suma o resta cada apertura a tu expectativa = ventaja con que salís según el motor
        (la señal estable) + tu rendimiento sobre el Elo, muy suavizado. Solo aperturas con al menos 8 partidas.</p>
      <div class="split">
        <div><h3>Mejores</h3>${table(oHeaders, openingRows(op.best))}</div>
        <div><h3>Peores</h3>${table(oHeaders, openingRows(op.worst))}</div>
      </div>
      ${improvementHtml(pr.improvement, "areas")}
      <h2>Tus posiciones flojas</h2>
      <p class="muted">Posiciones que repetís y donde tu jugada habitual pierde. Clic: en el tablero, en rojo tu jugada y en
        verde la del motor.</p>
      <div class="weak-list">${weakItems.join("") || '<p class="muted">Sin posiciones flojas con evaluación todavía.</p>'}</div>`;
    $$(".weak", root).forEach((el) => el.addEventListener("click", () => {
      $$(".weak", root).forEach((x) => x.classList.remove("active"));
      el.classList.add("active");
      const w = weak[+el.dataset.i];
      const shapes = [];
      if (w.usual_uci) shapes.push({ orig: w.usual_uci.slice(0, 2), dest: w.usual_uci.slice(2, 4), brush: "red" });
      if (w.best_uci && w.best_uci !== w.usual_uci) shapes.push({ orig: w.best_uci.slice(0, 2), dest: w.best_uci.slice(2, 4), brush: "green" });
      setLine(w.line, { shapes, orientation: w.color, caption: `${w.opening || ""} · ${colorName(w.color)} juegan` });
    }));
  }

  function renderMeEntry() {
    const name = state.me ? (state.me.meta.me_name || state.me.meta.me) : (state.status && state.status.me) || null;
    $("#me-block").classList.toggle("hidden", !name);
    if (!name) return;
    const s = state.me ? state.me.me.profile.summary : null;
    $("#me-entry").innerHTML = `<li data-me class="${state.view === "profile" ? "active" : ""}${state.me ? "" : " no-report"}">
      <span class="r-avatar me">${esc(name.slice(0, 1).toUpperCase())}</span>
      <span style="min-width:0"><div class="r-name">${esc(name)}</div>
        <div class="r-sub">${s ? `Mi perfil · ${s.games} partidas · ${fmt(s.last_rating)}` : "Mi perfil · sin generar"}</div></span></li>`;
    $("#me-entry li").addEventListener("click", () => showProfile());
  }

  function showProfile() {
    state.view = "profile";
    state.tab = "profile";
    $$("[data-tab]").forEach((b) => b.classList.remove("active"));
    $$(".tab").forEach((t) => t.classList.toggle("hidden", t.id !== "tab-profile"));
    $("#tabs").classList.add("hidden");
    renderRivalList();
    renderTab();
    $(".main").scrollTop = 0;
  }

  // ---------------------------------------------------------------- Tiempo
  const PHASES = { opening: "Apertura", middlegame: "Medio juego", endgame: "Final" };

  function renderTime() {
    const root = $("#tab-time");
    const r = report();
    const tp = r.rival.profile.time;
    const mine = r.me ? r.me.profile.time : null;
    if (!tp) {
      root.innerHTML = '<p class="muted">Este informe es anterior al perfil de tiempo: regeneralo con <b>Generar informes</b>.</p>';
      return;
    }
    const s = tp.summary;
    const th = tp.thresholds;
    const pct = (v) => (v === null || v === undefined ? "–" : fmt(v) + "%");
    const hints = tp.hints.length
      ? `<div class="hints">${tp.hints.map((h) => `<div class="hint">${esc(h)}</div>`).join("")}</div>`
      : '<p class="muted">Nada llamativo en su manejo del reloj con los datos actuales.</p>';

    // Comparación rival vs. yo, fila por métrica.
    const metrics = [
      ["Partidas con reloj", (x) => x.summary.games],
      ["Derrotas por tiempo", (x) => `${x.summary.lost_on_time} <span class="muted">(${pct(x.summary.lost_on_time_of_losses_pct)} de las derrotas)</span>`],
      ["Victorias por tiempo", (x) => x.summary.won_on_time],
      [`Partidas con apuro (&lt; ${th.trouble_pct}% del reloj)`, (x) => pct(x.summary.trouble_pct)],
      ["Jugada media en que se apura", (x) => fmt(x.summary.avg_trouble_move)],
      ["Puntaje con apuro / sin apuro", (x) => `${pct(x.summary.score_in_trouble_pct)} / ${pct(x.summary.score_no_trouble_pct)}`],
      ["Pensada más larga (% del tiempo inicial)", (x) => pct(x.summary.avg_longest_think_pct)],
      [`Pensadas de ≥ ${th.long_think_pct}% por partida`, (x) => fmt(x.summary.long_thinks_per_game, 2)],
    ];
    const cols = mine ? [tp, mine] : [tp];
    const cmpRows = metrics.map(([label, get]) => `<tr><td>${label}</td>${cols.map((x) => `<td>${get(x)}</td>`).join("")}</tr>`);

    // Reloj restante en las jugadas 10/20/30/40.
    const curve = (x) => Object.fromEntries((x.clock_curve || []).map((c) => [c.move_no, c]));
    const cr = curve(tp), cm = mine ? curve(mine) : {};
    const moves = [...new Set([...Object.keys(cr), ...Object.keys(cm)].map(Number))].sort((a, b) => a - b);
    const bar = (c, cls) => c
      ? `<div class="bar ${cls}"><span style="width:${Math.min(100, c.clock_left_pct)}%"></span></div>`
        + `<div class="bar-val">${fmt(c.clock_left_pct)}% · ${c.games} partidas</div>`
      : "";
    const bars = moves.map((n) => `<div class="bar-row"><div class="lbl">Jugada ${n}</div>
        <div class="bar-track">${bar(cr[n], "")}${bar(cm[n], "me")}</div></div>`).join("");

    const phaseRows = (x) => {
      const byPerf = {};
      x.by_phase.forEach((p) => { (byPerf[p.perf] = byPerf[p.perf] || {})[p.phase] = p; });
      return Object.entries(byPerf).map(([perf, ph]) => `<tr><td>${esc(perf)}</td>${Object.keys(PHASES).map((k) =>
        `<td>${ph[k] ? `${fmt(ph[k].avg_seconds, 1)} s <span class="muted">(${fmt(ph[k].share_pct)}%)</span>` : "–"}</td>`).join("")}</tr>`);
    };
    const pressRows = (x) => ["normal", "low"].map((k) => {
      const p = x.pressure[k];
      const label = k === "low" ? `Con menos del ${th.trouble_pct}% del reloj` : "Con tiempo";
      return p ? `<tr><td>${label}</td><td>${p.moves}</td><td>${fmt(p.error_rate_pct, 1)}%</td>
        <td>${fmt(p.blunder_rate_pct, 1)}%</td><td>${fmt(p.avg_wp_loss, 1)}</td></tr>` : `<tr><td>${label}</td><td colspan="4" class="muted">Sin jugadas evaluadas</td></tr>`;
    });
    const lowMoves = tp.pressure.low ? tp.pressure.low.moves : 0;

    root.innerHTML = `
      <h3>Qué aprovechar</h3>
      ${hints}
      <div class="cards">
        ${card("Derrotas por tiempo", pct(s.lost_on_time_of_losses_pct))}
        ${card("Partidas con apuro", pct(s.trouble_pct))}
        ${card("Se apura en la jugada", fmt(s.avg_trouble_move))}
        ${card("Pensada más larga", pct(s.avg_longest_think_pct))}
      </div>
      <h3>${esc(RNAME())}${mine ? " vs. vos" : ""}</h3>
      ${table(["", esc(RNAME()), ...(mine ? ["Vos"] : [])], cmpRows)}
      <h3>Reloj que le queda</h3>
      <p class="muted">Porcentaje medio del tiempo inicial que le queda al jugar esa jugada (con incremento puede pasar del 100%).</p>
      <div class="bars">
        ${mine ? `<div class="legend-inline"><span><i style="background:var(--accent)"></i>${esc(RNAME())}</span><span><i style="background:var(--me)"></i>Vos</span></div>` : ""}
        ${bars || '<span class="muted">Sin datos de reloj.</span>'}
      </div>
      <h3>Tiempo por fase</h3>
      <p class="muted">Segundos medios por jugada y, entre paréntesis, qué parte de su tiempo usa en cada fase
        (fases según la división de Lichess).</p>
      ${table(["Modalidad", ...Object.values(PHASES)], phaseRows(tp))}
      <h3>Calidad de juego con poco reloj</h3>
      <p class="muted">Solo cuentan las jugadas con evaluación del motor (las primeras ${r.meta.analysed_plies || "todas las"} jugadas
        o partidas analizadas en Lichess)${lowMoves < 30 ? ". <b>Hay pocas jugadas evaluadas con poco reloj</b>: tomalo como orientativo" : ""}.</p>
      ${table(["", "Jugadas", "Errores", "Errores graves", "Pérdida media (pts)"], pressRows(tp))}`;
  }

  // ---------------------------------------------------------------- Trampas
  function renderTraps() {
    const root = $("#tab-traps");
    const all = report().rival.traps || [];
    const onlyMine = !!state.trapsMine && !!report().me;
    const list = onlyMine ? all.filter((t) => t.in_my_repertoire) : all;
    const items = list.map((t, i) => `
      <div class="weak" data-i="${i}">
        <div class="head">
          <span><b>${esc(t.opening || "Posición")}</b> · jugada ${Math.ceil(t.ply / 2)} de las ${colorName(t.rival_color)}</span>
          <span>
            ${t.in_my_repertoire ? `<span class="badge good">en tu repertorio (${t.my_games})</span>` : ""}
            <span class="badge ${t.difficulty === "fácil" ? "good" : "warn"}">refutación ${esc(t.difficulty)}</span>
            <span class="badge bad">−${fmt(t.usual_wp_loss, 1)} pts</span>
          </span>
        </div>
        <div>Juega <b>${esc(t.usual_san)}</b> ${t.usual_count} de ${t.occurrences} veces (mejor: ${esc(t.best_san || "–")}).
          Castigo: <b>${esc(t.refutation_san)}</b>${t.refutation_line ? ` <span class="muted">${esc(t.refutation_line)}</span>` : ""}</div>
        <div class="line">${t.reach_pct !== undefined ? `Llega a la posición en el <b>${fmt(t.reach_pct)}%</b> de sus partidas con
          ${colorName(t.rival_color)} · juega el error el ${fmt(t.error_pct)}% · valor esperado <b>+${fmt(t.expected_value_pct, 1)} pts</b><br>` : ""}
          Sus rivales encontraron la refutación ${t.refutation_found} de ${t.games_after_mistake} veces ·
          él hizo ${fmt(t.rival_score_pct)}% (castigado: ${fmt(t.rival_score_when_punished_pct)}%, sin castigo: ${fmt(t.rival_score_when_missed_pct)}%)
          ${t.forcing ? " · refutación forzante (captura o jaque)" : ""}</div>
      </div>`);
    root.innerHTML = `
      <div class="toolbar">${report().me ? seg("tm", [["all", "Todas"], ["mine", "Solo en mi repertorio"]], onlyMine ? "mine" : "all") : ""}</div>
      <p class="muted">Posiciones que ${esc(RNAME())} repite donde su jugada habitual es un error según el motor, ordenadas por
        valor esperado: probabilidad de que llegue × probabilidad de que se equivoque × lo que ganás al castigarlo.
        "Fácil": la refutación es forzante o hay varias jugadas que ganan; "precisa": hay una sola. Clic: el tablero muestra
        la posición tras su error con la refutación en verde (→ para seguir la variante).</p>
      <div class="weak-list">${items.join("") || '<p class="muted">No se encontraron trampas con los datos actuales.</p>'}</div>`;
    bindSeg(root, "tm", (v) => { state.trapsMine = v === "mine"; renderTraps(); });
    $$(".weak", root).forEach((el) => el.addEventListener("click", () => {
      $$(".weak", root).forEach((x) => x.classList.remove("active"));
      el.classList.add("active");
      const t = list[+el.dataset.i];
      showItem({ line: t.line, idx: t.line_mistake_index,
        shapes: [{ orig: t.refutation_uci.slice(0, 2), dest: t.refutation_uci.slice(2, 4), brush: "green" }] },
        t.punisher_color, `Trampa: ${t.opening || ""}`);
    }));
  }

  // ---------------------------------------------------------------- Aperturas
  function renderOpenings() {
    const root = $("#tab-openings");
    const who = report().me ? state.openingsWho : "rival";
    const sec = section(who);
    const key = state.openingsBy === "family" ? "openings" : "openings_detail";
    const rowsFor = (c) => sec[key][c].map((o) => `<tr>
        <td>${esc(o.opening)} <span class="muted">${esc(o.eco || "")}</span></td>
        <td>${o.games}${lowSample(o.games)}</td><td>${wdl(o.wins, o.draws, o.losses)}</td>
        <td class="${pctClass(o.score_pct)}"${ciTitle(o.score_ci)}>${fmt(o.score_pct)}%</td>
        <td class="${o.over_pct >= 5 ? "pct-good" : o.over_pct <= -5 ? "pct-bad" : ""}">${sgn(o.over_pct)}</td>
        <td>${fmt(o.avg_opp_rating)}</td>
        <td title="${o.games_with_evals} partidas con evaluación">${fmt(o.avg_cpl)}</td>
        <td>${o.avg_eval_out === null ? "–" : (o.avg_eval_out > 0 ? "+" : "") + fmt(o.avg_eval_out / 100, 2)}</td></tr>`);
    const headers = ["Apertura", "Partidas", "G / T / P", "Puntaje", "Sobre Elo", "Elo rival", "CPL apertura", "Eval. ply 20"];
    root.innerHTML = `
      <div class="toolbar">
        ${report().me ? seg("who", [["rival", RNAME()], ["me", "Yo"]], who) : ""}
        ${seg("by", [["family", "Por familia"], ["detail", "Por variante"]], state.openingsBy)}
      </div>
      <p class="muted">Sobre Elo: puntaje real menos el esperado por los ratings (lo que importa; el puntaje bruto depende de
        contra quién jugó). Pasá el mouse por el puntaje para ver el intervalo. Con pocas partidas, cualquier % engaña.
        CPL apertura: pérdida media de centipeones en los primeros 20 plies (menos es mejor).
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
        ${report().me ? seg("tw", [["rival", RNAME()], ["me", "Yo"]], who) : ""}
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
      <div class="toolbar">${report().me ? seg("ww", [["rival", RNAME()], ["me", "Yo"]], who) : ""}</div>
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
    const pgn = cfg.pgn || { folder: "pgn", me: "", opponents: [] };
    root.innerHTML = `
      <form class="config" id="config-form">
        <p class="muted">Stockfish: ${status.stockfish ? esc(status.stockfish) : "<b>no encontrado</b>"} ·
          token de Lichess: ${status.token ? "sí" : "no (opcional, va en .env)"}</p>
        <div><div class="muted">Origen de las partidas</div>
          <div class="source-toggle">
            <label><input type="radio" name="source" value="lichess" ${cfg.source !== "pgn" ? "checked" : ""}> Lichess</label>
            <label><input type="radio" name="source" value="pgn" ${cfg.source === "pgn" ? "checked" : ""}> Archivos PGN</label>
          </div></div>
        <fieldset data-for="pgn"><legend>Archivos PGN (p.ej. exportados de ChessBase)</legend>
          <p class="muted" style="margin:0">Copiá los PGN en la carpeta y tocá <b>Importar PGN</b>: se usan todas las partidas
            que haya ahí (si sacás un archivo, se sacan sus partidas). Después buscá a tus rivales en la barra lateral.</p>
          <label>Carpeta <input name="pgn_folder" value="${esc(pgn.folder)}"></label>
          <span class="muted" style="font-size:12px">Ruta completa: ${esc(status.pgn_folder || "")}</span>
          <label>Mi nombre en los PGN (como figura, p.ej. "Apellido, Nombre")
            <input name="pgn_me" value="${esc(pgn.me)}" list="pgn-me-list" autocomplete="off"></label>
          <datalist id="pgn-me-list"></datalist>
          <label>Rivales (uno por línea; también se agregan desde el buscador) <textarea name="pgn_opponents" rows="4">${esc(pgn.opponents.join("\n"))}</textarea></label>
        </fieldset>
        <fieldset data-for="lichess"><legend>Lichess</legend>
        <label>Mi usuario de Lichess <input name="me" value="${esc(cfg.me)}"></label>
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
        </fieldset>
        <div class="row">
          <label>Profundidad Stockfish <input type="number" name="depth" min="6" max="30" value="${cfg.engine.depth}"></label>
          <label>Analizar hasta el ply (0 = todo) <input type="number" name="max_ply" min="0" value="${cfg.engine.max_ply}"></label>
          <label>Procesos de Stockfish <input type="number" name="workers" min="1" placeholder="auto" value="${cfg.engine.workers ?? ""}"></label>
          <label>Mín. repeticiones de posición <input type="number" name="min_occurrences" min="2" value="${cfg.analysis.min_occurrences}"></label>
        </div>
        <div><button class="primary" type="submit">Guardar</button> <span id="config-msg" class="muted"></span></div>
      </form>`;
    const form = $("#config-form");
    const syncSource = () => $$("fieldset[data-for]", form).forEach((fs) =>
      fs.classList.toggle("hidden", fs.dataset.for !== form.source.value));
    $$('input[name="source"]', form).forEach((r) => r.addEventListener("change", syncSource));
    syncSource();
    form.pgn_me.addEventListener("input", debounce(async () => {
      const res = await api("/api/players?limit=10&q=" + encodeURIComponent(form.pgn_me.value));
      $("#pgn-me-list").innerHTML = res.map((p) => `<option value="${esc(p.name)}">${p.games} partidas</option>`).join("");
    }, 250));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      const num = (v) => (v === "" || v === null ? null : Number(v));
      cfg.source = f.get("source");
      cfg.me = f.get("me").trim();
      cfg.opponents = f.get("opponents").split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
      // En los PGN los nombres llevan coma y espacios: uno por línea.
      cfg.pgn = { folder: f.get("pgn_folder").trim() || "pgn", me: f.get("pgn_me").trim(),
        opponents: f.get("pgn_opponents").split("\n").map((s) => s.trim()).filter(Boolean) };
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
        await loadReports();
      } catch (err) {
        $("#config-msg").textContent = "Error: " + err.message;
      }
    });
  }

  async function startJob(action, users = null) {
    try {
      await api("/api/jobs", { method: "POST", body: JSON.stringify({ action, users }) });
      jobDismissed = false;
      pollJob();
    } catch (err) {
      alert(err.message);
    }
  }

  const JOB_NAMES = { fetch: "Descarga / importación", analyze: "Análisis", report: "Informes", all: "Todo", pdf: "PDF" };
  let jobDismissed = false;

  async function pollJob() {
    const s = await api("/api/jobs");
    const box = $("#job-status");
    $$("[data-job]").forEach((b) => (b.disabled = s.running));
    $("#pdf-ok").disabled = s.running;
    const show = s.action && !(jobDismissed && !s.running);
    box.classList.toggle("hidden", !show);
    document.body.classList.toggle("has-player", !!show);
    if (!show) return;
    const cur = s.steps[s.steps.length - 1];
    const name = JOB_NAMES[s.action] || s.action;
    const head = s.running ? `${name}: trabajando…` : s.error ? `${name}: falló` : `${name}: listo`;
    let sub = cur ? cur.desc : "Arrancando…";
    if (!s.running && !s.error && s.action === "fetch" && s.result && s.result.fetch && s.result.fetch.pgn) {
      const sm = s.result.fetch.pgn;
      const files = Object.values(sm.files);
      const imported = files.filter((v) => typeof v === "number").reduce((a, b) => a + b, 0);
      sub = `${files.length} archivos · ${imported} partidas nuevas · ${sm.players} jugadores` +
        (sm.removed.length ? ` · ${sm.removed.length} archivos quitados` : "");
    }
    if (!s.running && !s.error && s.action === "pdf" && s.result && s.result.pdf) {
      const ok = Object.values(s.result.pdf).filter((v) => v.ok).length;
      const bad = Object.entries(s.result.pdf).filter(([, v]) => !v.ok);
      sub = `${ok} PDF en ${s.result.pdf_dir}` + (bad.length ? ` · con error: ${bad.map(([k, v]) => `${k} (${v.error})`).join(", ")}` : "");
    }
    const pctDone = cur && cur.total ? Math.min(100, (100 * cur.done) / cur.total) : (s.running ? 0 : 100);
    box.className = "player-bar" + (s.error ? " error" : !s.running ? " done" : "");
    box.innerHTML = `
      <div><div class="job-head">${esc(head)}</div><div class="job-sub" title="${esc(sub)}">${esc(sub)}</div></div>
      ${s.error
        ? `<div class="job-error" title="${esc(s.error)}">${esc(s.error.split("\n")[0])}</div>`
        : `<div class="job-progress"><span>${cur ? cur.done : 0}</span><div class="track"><span style="width:${pctDone}%"></span></div>
           <span>${cur && cur.total ? cur.total : ""}</span></div>`}
      <button class="close" title="Ocultar" ${s.running ? "disabled" : ""}>✕</button>`;
    $(".close", box).addEventListener("click", () => { jobDismissed = true; pollJob(); });
    if (s.running) setTimeout(pollJob, 1000);
    else if (!s.error && ["report", "all", "fetch"].includes(s.action) && state.loadedAt < s.finished_at) loadReports();
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

  // ---------------------------------------------------------------- PDF
  const rivalName = (rv) => (state.reports[rv] && state.reports[rv].meta.rival_name) || rv;

  function renderAllForPrint() {
    // Para el PDF se imprimen todas las secciones (renderizadas) una tras otra.
    if (!report()) return;
    renderHero(); renderSummary(); renderPlan(); renderPrep(); renderTime(); renderTraps(); renderOpenings(); renderWeak();
  }

  function openPdfDialog() {
    if (state.view === "profile") {
      if (!SERVER) { renderProfile(); window.print(); return; }
      startJob("pdf", ["_me"]);
      return;
    }
    if (!SERVER) { renderAllForPrint(); window.print(); return; }
    const dlg = $("#pdf-dialog");
    const rivals = Object.keys(state.reports);
    $("#pdf-current").textContent = state.rival ? `(${rivalName(state.rival)})` : "";
    $("#pdf-rivals").innerHTML = rivals.map((rv) =>
      `<label><input type="checkbox" value="${esc(rv)}" ${rv === state.rival ? "checked" : ""}> ${esc(rivalName(rv))}</label>`).join("");
    const sync = () => $("#pdf-rivals").classList.toggle("disabled", $("#pdf-form").scope.value !== "some");
    $$('input[name="scope"]', dlg).forEach((r) => (r.onchange = sync));
    sync();
    dlg.showModal();
  }

  $("#pdf-dialog").addEventListener("close", () => {
    const dlg = $("#pdf-dialog");
    if (dlg.returnValue !== "ok") return;
    const scope = $("#pdf-form").scope.value;
    const users = scope === "current" ? [state.rival]
      : scope === "all" ? Object.keys(state.reports)
      : $$("#pdf-rivals input:checked").map((c) => c.value);
    if (!users.length || !users[0]) return alert("Elegí al menos un rival.");
    startJob("pdf", users);
  });
  $("#pdf-print").addEventListener("click", () => {
    $("#pdf-dialog").close("cancel");
    renderAllForPrint();
    window.print();
  });

  // ---------------------------------------------------------------- navegación
  function hueOf(name) {
    let h = 0;
    for (const ch of String(name)) h = (h * 31 + ch.charCodeAt(0)) % 360;
    return h;
  }
  const rivalColor = (name, l = 38) => `hsl(${hueOf(name)}, 45%, ${l}%)`;

  function renderHero() {
    const hero = $("#hero");
    if (state.view === "profile") {
      const pr = state.me;
      const name = pr ? (pr.meta.me_name || pr.meta.me) : "Mi perfil";
      const s = pr ? pr.me.profile.summary : null;
      hero.style.setProperty("--hero", "#1f6f43");
      $("#hero-avatar").textContent = name.slice(0, 1).toUpperCase();
      $("#hero-eyebrow").textContent = "Mi perfil";
      $("#hero-title").textContent = name;
      $("#hero-meta").innerHTML = s ? `<b>${s.games} partidas</b> · rating ${fmt(s.last_rating)} · comparado con
        ${pr.comparison.players.length} rivales · generado ${esc(new Date(pr.meta.generated_at).toLocaleString())}` : "";
      document.title = `${name} · tegchess`;
      return;
    }
    if (state.tab === "config") {
      hero.style.setProperty("--hero", "#3a3a3a");
      $("#hero-avatar").textContent = "⚙";
      $("#hero-eyebrow").textContent = "tegchess";
      $("#hero-title").textContent = "Configuración";
      $("#hero-meta").textContent = "Usuarios, modalidades y motor. Los cambios se usan en la próxima descarga o análisis.";
      return;
    }
    const r = report();
    if (!r) {
      hero.style.setProperty("--hero", "#2d5a43");
      $("#hero-avatar").textContent = "♞";
      $("#hero-eyebrow").textContent = "Preparación de rivales";
      $("#hero-title").textContent = "tegchess";
      $("#hero-meta").textContent = "";
      return;
    }
    const m = r.meta;
    const s = r.rival.profile.summary;
    const name = m.rival_name || m.rival;
    hero.style.setProperty("--hero", rivalColor(m.rival));
    $("#hero-avatar").textContent = name.slice(0, 1).toUpperCase();
    $("#hero-eyebrow").textContent = m.me ? `Informe de rival · preparado para ${m.me_name || m.me}` : "Informe de rival";
    $("#hero-title").textContent = name;
    const b = r.base;
    $("#hero-meta").innerHTML = (b ? `<span class="hero-expect">Por Elo esperás <b>${fmt(b.expected_pct)}%</b> · vos ${b.my_rating} vs. ${b.rival_rating} en ${esc(b.perf)}</span><br>` : "") +
      `<b>${s.games} partidas</b> · rating ${fmt(s.last_rating)} · ` + (m.source === "pgn"
        ? "todas las partidas de los archivos PGN<br>"
        : `${esc(m.perf_types.join(", "))} · desde ${esc(m.since || "el inicio")}${m.until ? " hasta " + esc(m.until) : ""}
      · máx. ${m.max_games || "∞"} partidas<br>`) +
      `
      Stockfish prof. ${m.engine_depth}, primeros ${m.analysed_plies || "todos los"} plies · posiciones evaluadas ${fmt(m.eval_coverage_pct)}%
      · generado ${esc(new Date(m.generated_at).toLocaleString())}`;
    document.title = `${name} · tegchess`;
  }

  // Rivales de la barra lateral: los elegidos en la configuración (con o sin informe) y los informes que haya.
  function rivalEntries() {
    const st = state.status;
    const entries = [];
    const seen = new Set();
    if (st) {
      st.opponents.forEach((name, i) => {
        const key = st.opponent_keys[i];
        if (seen.has(key)) return;
        seen.add(key);
        entries.push({ key, name: state.reports[key] ? rivalName(key) : name, removable: name });
      });
    }
    Object.keys(state.reports).forEach((key) => {
      if (!seen.has(key)) { seen.add(key); entries.push({ key, name: rivalName(key), removable: null }); }
    });
    return entries;
  }

  function renderRivalList() {
    const entries = rivalEntries();
    $("#rival-list").innerHTML = entries.map((e) => {
      const r = state.reports[e.key];
      const s = r ? r.rival.profile.summary : null;
      return `<li data-rival="${esc(e.key)}" class="${e.key === state.rival && state.tab !== "config" && state.view !== "profile" ? "active" : ""}${r ? "" : " no-report"}">
        <span class="r-avatar" style="--c:${rivalColor(e.key)}">${esc(e.name.slice(0, 1).toUpperCase())}</span>
        <span style="min-width:0"><div class="r-name">${esc(e.name)}</div>
          <div class="r-sub">${s ? `${s.games} partidas · ${fmt(s.last_rating)}` : "sin informe todavía"}</div></span>
        ${e.removable && SERVER ? `<button class="remove" title="Quitar de mis rivales" data-remove="${esc(e.removable)}">✕</button>` : ""}</li>`;
    }).join("") || `<li class="muted" style="cursor:default">${state.status && state.status.source === "pgn"
      ? "Buscá rivales arriba para agregarlos" : "Sin informes todavía"}</li>`;
    $$("#rival-list [data-remove]").forEach((b) => b.addEventListener("click", (ev) => {
      ev.stopPropagation();
      editOpponents((list) => list.filter((o) => o !== b.dataset.remove));
    }));
    $$("#rival-list li[data-rival]").forEach((li) => li.addEventListener("click", () => {
      state.rival = li.dataset.rival;
      state.tree.path = [];
      if (state.tab === "config" || state.view === "profile") { state.view = "rival"; showTab("summary"); }
      else { renderRivalList(); renderTab(); }
      $(".main").scrollTop = 0;
    }));
  }

  function showTab(tab) {
    if (tab !== "profile") state.view = tab === "config" ? state.view : "rival";
    state.tab = tab;
    $$("[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    $$(".tab").forEach((t) => t.classList.toggle("hidden", t.id !== "tab-" + tab));
    $("#tabs").classList.toggle("hidden", tab === "config");
    if (tab === "config") state.view = "config";
    renderRivalList();
    renderTab();
  }

  function renderTab() {
    renderMeEntry();
    if (state.view === "profile") {
      renderHero();
      $("#empty").classList.add("hidden");
      $("#print-btn").classList.toggle("hidden", !state.me);
      return renderProfile();
    }
    const hasReport = !!(state.rival && report());
    renderHero();
    $("#print-btn").classList.toggle("hidden", !hasReport || state.tab === "config");
    $("#empty").classList.toggle("hidden", hasReport || state.tab === "config");
    if (!hasReport) renderEmpty();
    if (state.tab === "config") return renderConfig();
    $$(".tab").forEach((t) => { if (!hasReport && t.id !== "tab-config") t.innerHTML = ""; });
    if (!hasReport) return;
    ({ summary: renderSummary, plan: renderPlan, prep: renderPrep, time: renderTime, traps: renderTraps,
      openings: renderOpenings, tree: renderTree, weak: renderWeak })[state.tab]();
  }

  function renderEmpty() {
    const st = state.status;
    const pgn = st && st.source === "pgn";
    const name = state.rival ? (rivalEntries().find((e) => e.key === state.rival) || {}).name : null;
    $("#empty").innerHTML = name
      ? `<h2>Todavía no hay informe de ${esc(name)}</h2>
         <p>Tocá <b>▶ Todo</b> (${pgn ? "importa los PGN" : "descarga"}, analiza y genera los informes) o, si las partidas ya
           están, <b>Analizar</b> y después <b>Generar informes</b>.</p>`
      : pgn
        ? `<h2>Modo PGN</h2>
           <p>1. Copiá tus archivos PGN en <code>${esc(st.pgn_folder)}</code> y tocá <b>⤓ Importar PGN</b>.<br>
              2. Buscá a tus rivales en el buscador de la izquierda y agregalos (y poné tu nombre en <b>Configuración</b>).<br>
              3. Tocá <b>▶ Todo</b> para analizar y generar los informes.</p>`
        : `<h2>Todavía no hay informes</h2>
           <p>Completá tu usuario y los rivales en <b>Configuración</b> y apretá <b>Todo</b>
             (descarga, análisis e informe). Desde la consola: <code>tegchess fetch</code>,
             <code>tegchess analyze</code>, <code>tegchess report</code>.</p>`;
  }

  // ---------------------------------------------------------------- buscador de rivales
  async function editOpponents(fn) {
    const cfg = await api("/api/config");
    if (cfg.source === "pgn") cfg.pgn.opponents = fn(cfg.pgn.opponents || []);
    else cfg.opponents = fn(cfg.opponents || []);
    await api("/api/config", { method: "PUT", body: JSON.stringify(cfg) });
    await loadReports();
  }

  async function searchRivals() {
    const box = $("#search-results");
    const q = $("#rival-search").value.trim();
    const pgn = state.status && state.status.source === "pgn";
    if (!q && !pgn) { box.classList.add("hidden"); return; }
    const chosen = new Set((state.status ? state.status.opponent_keys : []));
    let html = "";
    if (pgn) {
      const res = await api("/api/players?limit=40&q=" + encodeURIComponent(q));
      html = res.map((p) => {
        const added = chosen.has(p.id.replace(/[^\p{L}\p{N}_-]+/gu, "_").replace(/^_+|_+$/g, ""));
        return `<div class="res${added ? " added" : ""}" data-add="${esc(p.name)}">
          <span class="r-avatar" style="--c:${rivalColor(p.id)};width:32px;height:32px;font-size:13px">${esc(p.name.slice(0, 1))}</span>
          <span class="r-name"><div>${esc(p.name)}</div><div class="r-sub">${p.games} partidas · Elo ${fmt(p.rating)} · ${esc(p.last || "")}</div></span>
          <span class="add">${added ? "✓" : "+"}</span></div>`;
      }).join("") || (st_indexed() ? '<div class="muted" style="padding:8px">Sin coincidencias</div>'
        : '<div class="muted" style="padding:8px">Todavía no importaste PGN: tocá <b>Importar PGN</b>.</div>');
    } else {
      html = `<div class="res" data-add="${esc(q)}"><span class="r-name">Agregar el usuario de Lichess <b>${esc(q)}</b></span><span class="add">+</span></div>`;
    }
    box.innerHTML = html;
    box.classList.remove("hidden");
    $$("[data-add]", box).forEach((el) => el.addEventListener("click", async () => {
      if (el.classList.contains("added")) return;
      const name = el.dataset.add;
      await editOpponents((list) => (list.includes(name) ? list : [...list, name]));
      searchRivals();
    }));
  }
  const st_indexed = () => state.status && state.status.pgn_players_indexed;
  $("#rival-search").addEventListener("input", debounce(searchRivals, 200));
  $("#rival-search").addEventListener("focus", searchRivals);
  $("#rival-search").addEventListener("keydown", (e) => {
    if (e.key === "Escape") { $("#search-results").classList.add("hidden"); e.target.blur(); }
    if (e.key === "Enter") { const first = $("#search-results [data-add]:not(.added)"); if (first) first.click(); }
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".rival-search")) $("#search-results").classList.add("hidden");
  });

  function applySource() {
    const pgn = state.status && state.status.source === "pgn";
    const chip = $("#source-chip");
    chip.textContent = pgn ? "PGN" : "Lichess";
    chip.classList.toggle("pgn", !!pgn);
    const fetchBtn = $("#fetch-btn");
    fetchBtn.textContent = pgn ? "⤓ Importar PGN" : "⤓ Descargar";
    fetchBtn.title = pgn ? "Importar las partidas de la carpeta de PGN" : "Descargar partidas nuevas de Lichess";
    $("#rival-search").placeholder = pgn ? "Buscar jugador en los PGN…" : "Agregar usuario de Lichess…";
  }

  async function loadReports(only = null) {
    let rivals = [];
    if (STATIC) {
      state.me = STATIC._me || null;
      state.reports = Object.fromEntries(Object.entries(STATIC).filter(([k]) => !k.startsWith("_")));
      rivals = Object.keys(state.reports);
    } else if (SERVER) {
      if (!only) {
        state.status = await api("/api/status");
        applySource();
      }
      if (!only) state.me = await api("/api/me").catch(() => null);
      rivals = only ? [only] : (await api("/api/reports")).map((r) => r.rival);
      const loaded = {};
      await Promise.all(rivals.map(async (r) => { loaded[r] = await api("/api/reports/" + encodeURIComponent(r)); }));
      state.reports = Object.fromEntries(rivals.map((r) => [r, loaded[r]]));
      state.loadedAt = Date.now() / 1000;
    }
    const keys = only ? rivals : rivalEntries().map((e) => e.key);
    if (!keys.includes(state.rival)) state.rival = keys[0] || null;
    renderRivalList();
    renderTab();
  }

  $$("[data-tab]").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  $$("[data-job]").forEach((b) => b.addEventListener("click", () => startJob(b.dataset.job)));
  $("#print-btn").addEventListener("click", openPdfDialog);

  // Modo impresión (lo usa la generación de PDF): ?print=<rival> muestra todas las secciones de ese rival.
  const PRINT = new URLSearchParams(location.search).get("print");
  if (!SERVER) $$(".server-only").forEach((el) => el.classList.add("hidden"));
  setLine([]);
  if (PRINT === "_me") {
    loadReports().then(() => { showProfile(); document.body.dataset.ready = "1"; });
  } else if (PRINT) {
    state.rival = PRINT.toLowerCase();
    loadReports(STATIC ? null : state.rival).then(() => {
      renderAllForPrint();
      document.body.dataset.ready = "1";
    });
  } else {
    loadReports();
    if (SERVER) pollJob();
  }
})();
