"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const SVGNS = "http://www.w3.org/2000/svg";
const QUICK = ["RELIANCE", "HDFCBANK", "TCS", "INFY", "ITC", "SBIN"];
let realitySummary = null;
let lastForecast = null;
let status = null;
let scanCache = null;

// ── Formatting ─────────────────────────────────────────
const pct = (x, d = 1) => (x == null || Number.isNaN(x) ? "–" : `${x > 0 ? "+" : ""}${(x * 100).toFixed(d)}%`);
const inr = (x) => x == null ? "–" : "₹" + x.toLocaleString("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 2 });
const cls = (x) => (x > 0 ? "up" : x < 0 ? "down" : "");
const fmtDate = (s, opts = { day: "numeric", month: "short", year: "numeric" }) =>
  new Date(s + "T00:00:00").toLocaleDateString("en-IN", opts);
const ago = (iso) => {
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (m < 2) return "just now";
  if (m < 60) return `${m} minutes ago`;
  const h = Math.round(m / 60);
  if (h < 36) return `${h} hour${h === 1 ? "" : "s"} ago`;
  return `${Math.round(h / 24)} days ago`;
};
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function getJSON(path) {
  let res;
  try {
    res = await fetch(path, { cache: "no-cache" });
  } catch {
    throw new Error("Couldn't load the data. Check your connection and refresh the page.");
  }
  if (!res.ok) throw new Error(res.status === 404
    ? "This data hasn't been built yet. The site updates each weekday evening."
    : `Loading failed (${res.status}). Refresh to try again.`);
  return res.json();
}

// ── Tiny SVG helpers ───────────────────────────────────
function el(tag, attrs = {}, parent) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (parent) parent.appendChild(n);
  return n;
}
function linePath(pts) {
  return pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join("");
}
function niceTicks(lo, hi, count = 4) {
  const span = hi - lo || 1;
  const step0 = span / count;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0) || step0;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}

// ── Router ─────────────────────────────────────────────
function route() {
  const tab = (location.hash || "#forecast").slice(1);
  const valid = ["forecast", "scanner", "reality"].includes(tab) ? tab : "forecast";
  document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== `view-${valid}`));
  document.querySelectorAll(".tab").forEach((t) =>
    t.dataset.tab === valid ? t.setAttribute("aria-current", "page") : t.removeAttribute("aria-current"));
  if (valid === "scanner") loadScan();
  if (valid === "reality") loadReality();
  window.scrollTo({ top: 0 });
}
window.addEventListener("hashchange", route);

// ── Status ─────────────────────────────────────────────
async function loadStatus() {
  try {
    status = await getJSON("data/status.json");
    $("#model-state").textContent =
      `Updated ${ago(status.generated)}. ${status.model.split("/")[1]}, trained on data to June 2024.`;
    const dl = $("#symbol-list");
    Object.keys(status.symbols).sort().forEach((sym) =>
      dl.appendChild(Object.assign(document.createElement("option"), { value: sym })));
    if (status.watchlist_edit_url) {
      const a = $("#watch-edit");
      a.href = status.watchlist_edit_url;
      a.hidden = false;
    }
  } catch (err) {
    $("#model-state").textContent = err.message;
  }
}

// ── Verdict strip (always visible) ─────────────────────
function renderStrip(summary) {
  const strip = $("#verdict-strip");
  strip.hidden = false;
  strip.className = "verdict-strip";
  if (!summary || !summary.ready) {
    strip.classList.add("pending");
    strip.innerHTML = "<strong>Not tested yet.</strong> <span>Treat every forecast as unproven until the reality check finishes.</span>";
  } else if (summary.gate.passed) {
    strip.classList.add("pass");
    strip.innerHTML = `<strong>Passed the reality check.</strong> <span>Kronos ranked stocks better than chance over ${summary.periods} months. Paper-trade it before using real money.</span>`;
  } else if (summary.mean_rank_ic >= summary.gate.rank_ic_min && summary.t_stat >= 1.5) {
    strip.classList.add("pending");
    strip.innerHTML = `<strong>Promising, not proven.</strong> <span>Over ${summary.periods} months of unseen data Kronos ranked stocks better than random, but just short of our proof bar. Research and paper trading only.</span>`;
  } else {
    strip.classList.add("fail");
    strip.innerHTML = `<strong>No proven edge yet.</strong> <span>Over ${summary.periods} months of unseen data, Kronos's picks didn't pass our test. Use these forecasts for research, not trading.</span>`;
  }
}

// ── Forecast ───────────────────────────────────────────
function renderQuickPicks() {
  const box = $("#quick-picks");
  QUICK.forEach((s) => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "chip"; b.textContent = s;
    b.addEventListener("click", () => { $("#symbol").value = s; runForecast(); });
    box.appendChild(b);
  });
}

async function runForecast(e) {
  if (e) e.preventDefault();
  const sym = $("#symbol").value.trim().toUpperCase();
  if (!sym) return;
  const out = $("#forecast-out");
  if (!status) await loadStatus();
  const slug = status && status.symbols[sym];
  if (!slug) {
    const edit = status && status.watchlist_edit_url
      ? ` <a href="${esc(status.watchlist_edit_url)}" target="_blank" rel="noopener">Add it to your watchlist</a> and it will appear after the next evening update.`
      : " Add it to watchlist.txt and it will appear after the next evening update.";
    out.innerHTML = `<div class="error">${esc(sym)} isn't in your list yet. Use the NSE symbol, like RELIANCE or BAJAJ-AUTO.${edit}</div>`;
    return;
  }
  const btn = $("#forecast-btn");
  btn.disabled = true;
  try {
    const f = await getJSON(`data/f/${slug}.json`);
    renderForecast(f);
  } catch (err) {
    out.innerHTML = `<div class="error">${esc(err.message)}</div>`;
  } finally {
    btn.disabled = false;
  }
}

function renderForecast(f) {
  const s = f.summary;
  const isInr = f.yahoo.endsWith(".NS") || f.yahoo.endsWith(".BO");
  const price = (x) => (isInr ? inr(x) : x.toLocaleString("en-US", { maximumFractionDigits: 2, minimumFractionDigits: 2 }));
  const endDate = f.future[f.future.length - 1].d;
  const h = f.history;
  const recent = h.length > f.horizon ? h[h.length - 1].c / h[h.length - 1 - f.horizon].c - 1 : null;
  const out = $("#forecast-out");
  out.innerHTML = `
    <div class="fc-head">
      <div>
        <div class="fc-symbol">${esc(f.symbol)}</div>
        <p class="fc-price">${price(f.last_close)} at close on ${fmtDate(f.as_of)}</p>
      </div>
      <div class="fc-call">
        <div class="big ${cls(s.expected_return)}">${pct(s.expected_return)}</div>
        <p class="sub">expected by ${fmtDate(endDate, { day: "numeric", month: "short" })}, average of ${f.samples} paths</p>
      </div>
    </div>
    <div class="chart-wrap"><div id="fc-chart"></div>
      <div class="legend">
        <span><i style="background:var(--ink)"></i>Actual price</span>
        <span><i style="background:var(--cone)"></i>Kronos average path</span>
        <span><i style="background:var(--cone-soft);height:10px"></i>Range of the ${f.samples} paths</span>
      </div>
    </div>
    <div class="facts">
      <div class="fact"><div class="v">${pct(s.prob_up, 0).replace("+", "")}</div><div class="k">of paths end higher than today</div></div>
      <div class="fact"><div class="v"><span class="${cls(s.p10_return)}">${pct(s.p10_return)}</span> to <span class="${cls(s.p90_return)}">${pct(s.p90_return)}</span></div><div class="k">where most paths finish</div></div>
      <div class="fact"><div class="v ${cls(recent)}">${pct(recent)}</div><div class="k">move over the last ${f.horizon} trading days, for context</div></div>
    </div>
    <p class="caveat">This is a model's guess from price and volume alone. It doesn't know about news, results or the wider market, and the range is usually narrower than real life.</p>`;
  lastForecast = f;
  drawForecastChart($("#fc-chart"), f);
}

function chartSize(host, tall, short) {
  const W = Math.max(300, Math.round(host.clientWidth || 800));
  return { W, H: W < 600 ? short : tall };
}

function drawForecastChart(host, f) {
  const { W, H } = chartSize(host, 340, 250);
  const ml = 8, mr = 58, mt = 14, mb = 28;
  // Show enough history for context but keep the future at roughly a fifth of the width.
  const keep = Math.min(f.history.length, Math.max(60, f.future.length * 4));
  const hist = f.history.slice(-keep), fut = f.future, nh = hist.length, n = nh + fut.length;
  const ys = hist.map((d) => d.c).concat(fut.map((d) => d.lo), fut.map((d) => d.hi));
  let lo = Math.min(...ys), hi = Math.max(...ys);
  const pad = (hi - lo) * 0.08 || 1; lo -= pad; hi += pad;
  const X = (i) => ml + (i / (n - 1)) * (W - ml - mr);
  const Y = (v) => mt + (1 - (v - lo) / (hi - lo)) * (H - mt - mb);
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img",
    "aria-label": `${f.symbol} price for the last ${nh} trading days and Kronos forecast for the next ${fut.length}` });

  niceTicks(lo, hi, 4).forEach((v) => {
    el("line", { x1: ml, x2: W - mr, y1: Y(v), y2: Y(v), class: "gridline" }, svg);
    el("text", { x: W - mr + 8, y: Y(v) + 4, class: "axis-label" }, svg).textContent =
      v >= 1000 ? v.toLocaleString("en-IN", { maximumFractionDigits: 0 }) : +v.toFixed(2);
  });
  // Month labels along the bottom
  let lastMonth = null, lastX = -99;
  hist.forEach((d, i) => {
    const m = d.d.slice(0, 7);
    if (m !== lastMonth && i > 3 && X(i) - lastX > 44) {
      lastX = X(i);
      el("text", { x: X(i), y: H - 8, class: "axis-label" }, svg).textContent =
        new Date(d.d + "T00:00:00").toLocaleDateString("en-IN", { month: "short" });
    }
    lastMonth = m;
  });

  const x0 = X(nh - 1), y0 = Y(f.last_close);
  el("line", { x1: x0, x2: x0, y1: mt, y2: H - mb, class: "now" }, svg);
  el("text", { x: x0 - 6, y: mt + 10, class: "now-label", "text-anchor": "end" }, svg).textContent = "Today";

  const top = fut.map((d, i) => [X(nh + i), Y(d.hi)]);
  const bot = fut.map((d, i) => [X(nh + i), Y(d.lo)]).reverse();
  el("path", { d: linePath([[x0, y0], ...top, ...bot, [x0, y0]]) + "Z", class: "cone" }, svg);
  f.paths.forEach((p) => el("path", { d: linePath([[x0, y0], ...p.map((v, i) => [X(nh + i), Y(v)])]), class: "path" }, svg));
  el("path", { d: linePath(hist.map((d, i) => [X(i), Y(d.c)])), class: "price" }, svg);
  el("path", { d: linePath([[x0, y0], ...fut.map((d, i) => [X(nh + i), Y(d.mean)])]), class: "mean" }, svg);

  host.replaceChildren(svg);
}

// ── Scanner ────────────────────────────────────────────
function sparkSvg(vals) {
  const W = 84, H = 26, lo = Math.min(...vals), hi = Math.max(...vals);
  const pts = vals.map((v, i) => [(i / (vals.length - 1)) * W, H - 2 - ((v - lo) / (hi - lo || 1)) * (H - 4)]);
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" aria-hidden="true"><path d="${linePath(pts)}"/></svg>`;
}
function rangeSvg(r, scale) {
  const W = 150, H = 22, X = (v) => 6 + ((v + scale) / (2 * scale)) * (W - 12);
  const c = (v) => Math.max(6, Math.min(W - 6, X(v)));
  return `<svg class="range" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Range ${pct(r.low)} to ${pct(r.high)}">
    <line class="track" x1="6" x2="${W - 6}" y1="11" y2="11"/>
    <line class="span" x1="${c(r.low)}" x2="${c(r.high)}" y1="11" y2="11"/>
    <line class="zero" x1="${X(0)}" x2="${X(0)}" y1="3" y2="19"/>
    <circle class="dot" cx="${c(r.expected_return)}" cy="11" r="4"/></svg>`;
}

async function loadScan() {
  try {
    scanCache = scanCache || await getJSON("data/scan.json");
    renderScan(scanCache);
  } catch (err) {
    $("#scan-out").innerHTML = `<div class="error">${esc(err.message)}</div>`;
  }
}

function renderScan(scan) {
  const rows = scan.results;
  if (!rows.length) {
    $("#scan-out").innerHTML = `<p class="empty">The last update produced no forecasts. Yahoo Finance may have been down; the next evening update will try again.</p>`;
    return;
  }
  $("#scan-meta").textContent = `${rows.length} stocks, prices to ${fmtDate(rows[0].as_of)}, updated ${ago(scan.generated)}`;
  // Scale the range bars to the 90th percentile so one extreme stock doesn't flatten the rest; outliers clamp at the edge.
  const ext = rows.map((r) => Math.max(Math.abs(r.low), Math.abs(r.high))).sort((x, y) => x - y);
  const scale = Math.max(0.05, ext[Math.floor(ext.length * 0.9)] || 0.05);
  $("#scan-out").innerHTML = `
    <table class="scan-table">
      <thead><tr>
        <th class="rank">#</th><th>Stock</th><th class="hide-sm">Last 3 months</th>
        <th class="num">Expected</th><th>Range of paths</th><th class="num hide-sm">Paths up</th>
      </tr></thead>
      <tbody>${rows.map((r, i) => `
        <tr data-sym="${esc(r.symbol)}" tabindex="0">
          <td class="rank">${i + 1}</td>
          <td><div class="sym">${esc(r.symbol)}${r.watchlist ? '<span class="tag">Watchlist</span>' : ""}</div><div class="meta">${inr(r.last_close)}</div></td>
          <td class="hide-sm">${sparkSvg(r.spark)}</td>
          <td class="num ${cls(r.expected_return)}">${pct(r.expected_return)}</td>
          <td>${rangeSvg(r, scale)}</td>
          <td class="num hide-sm">${Math.round(r.prob_up * scan.samples)} of ${scan.samples}</td>
        </tr>`).join("")}
      </tbody>
    </table>
    ${scan.skipped.length ? `<p class="skipped">Left out: ${scan.skipped.map((k) => `${esc(k.symbol)} (${esc(k.reason)})`).join(" ")}</p>` : ""}`;
  $("#scan-out").querySelectorAll("tbody tr").forEach((tr) => {
    const go = () => { $("#symbol").value = tr.dataset.sym; location.hash = "#forecast"; runForecast(); };
    tr.addEventListener("click", go);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  });
}

// ── Reality check ──────────────────────────────────────
async function loadReality() {
  try {
    realitySummary = realitySummary || await getJSON("data/reality.json");
    renderStrip(realitySummary);
    renderReality(realitySummary);
  } catch (err) {
    $("#reality-out").innerHTML = `<div class="error">${esc(err.message)}</div>`;
  }
}

function renderReality(s) {
  const out = $("#reality-out");
  if (!s.ready) {
    out.innerHTML = `<p class="empty">The test hasn't produced results yet. It runs with the evening update.</p>`;
    return;
  }
  const passed = s.gate.passed;
  const ic = s.mean_rank_ic, mic = s.mom_mean_rank_ic;
  const close = !passed && ic >= s.gate.rank_ic_min && s.t_stat >= 1.5;
  const headline = passed
    ? "Kronos ranked stocks better than chance on data it never saw."
    : close
      ? "Promising: Kronos beat random on data it never saw, but it isn't proven yet."
      : ic > 0
        ? "Kronos leaned the right way slightly, but not reliably enough to trade."
        : "Kronos's rankings didn't beat a coin flip on data it never saw.";
  const why = close
    ? ` It clears the score bar but its consistency is ${s.t_stat.toFixed(2)} against the ${s.gate.t_min} needed, so luck can't be ruled out yet. Each new month adds evidence; this page updates itself.`
    : "";
  out.innerHTML = `
    <div class="verdict ${passed ? "pass" : close ? "pending" : "fail"}">
      <h2>${headline}</h2>
      <p>Tested on ${s.periods} months (${fmtDate(s.first, { month: "short", year: "numeric" })} to ${fmtDate(s.last, { month: "short", year: "numeric" })}), about ${s.stocks_per_period} Nifty 50 stocks each month, forecasting ${s.horizon_days} trading days ahead. To pass, the average ranking score must be at least ${s.gate.rank_ic_min} and statistically solid (t of ${s.gate.t_min} or more), and its picks must beat the index after costs.${why}</p>
    </div>
    <div class="scores">
      <div class="score">
        <div class="v ${cls(ic)}">${ic.toFixed(3)}</div><div class="vs">Simple momentum scored ${Number.isFinite(mic) ? mic.toFixed(3) : "–"}</div>
        <h3>Ranking score</h3>
        <p>How well Kronos sorted the stocks from best to worst each month. 0 is random, 1 is perfect. Good real-world signals sit around 0.03 to 0.08.</p>
      </div>
      <div class="score">
        <div class="v">${Number.isFinite(s.t_stat) ? s.t_stat.toFixed(2) : "–"}</div><div class="vs">Needs ${s.gate.t_min} or more</div>
        <h3>How consistent it was</h3>
        <p>Whether the score held up month after month or was luck. Ranked better than random in ${Math.round(s.pct_months_ic_positive * 100)}% of months.</p>
      </div>
      <div class="score">
        <div class="v ${cls(s.kronos_total - s.bench_total)}">${pct(s.kronos_total)}</div><div class="vs">Nifty 50 equal-weight: ${pct(s.bench_total)}, momentum: ${pct(s.mom_total)}</div>
        <h3>Kronos's top ${s.top_n} picks, after costs</h3>
        <p>Buying its ${s.top_n} highest-forecast stocks each month and holding ${s.horizon_days} days, with ${(s.cost_round_trip * 100).toFixed(2)}% charged for every swap.</p>
      </div>
      <div class="score">
        <div class="v">${Math.round(s.band_coverage * 100)}%</div><div class="vs">An honest range would catch about ${Math.round(s.band_target * 100)}%</div>
        <h3>Did the real price land inside its range?</h3>
        <p>${s.band_coverage < s.band_target - 0.1 ? "Much less often than it should. Kronos is overconfident, so treat its cones as too narrow." : "About as often as it should, so its ranges are roughly honest."}</p>
      </div>
    </div>
    <div class="panel"><h3>Growth of ₹1, month by month</h3><p class="note">Each line reinvests the month's return. Costs are already taken off the Kronos and momentum lines.</p><div id="eq-chart"></div>
      <div class="legend"><span><i style="background:var(--cone)"></i>Kronos top ${s.top_n}</span><span><i style="background:var(--ink-2)"></i>Momentum top ${s.top_n}</span><span><i style="background:var(--ink-3)"></i>Nifty 50 equal-weight</span></div></div>
    <div class="panel"><h3>Ranking score each month</h3><p class="note">Bars above the line are months where Kronos sorted stocks better than random.</p><div id="ic-chart"></div></div>
    <details class="method"><summary>How this test works</summary><ul>
      <li>Kronos's training data ends in June 2024, so every month tested here is one it has never seen.</li>
      <li>At each month-end it sees only the previous 256 trading days of candles, about a year. Nothing from the future leaks in.</li>
      <li>Returns are close-to-close over the next ${s.horizon_days} trading days, using split- and dividend-adjusted prices from Yahoo Finance.</li>
      <li>The stock list is today's Nifty 50, so companies that dropped out are missing. That flatters every line equally, the benchmark included.</li>
      <li>Each forecast uses ${s.samples} sampled paths with a fixed random seed, so rerunning the test gives the same answer.</li>
    </ul></details>`;
  drawEquity($("#eq-chart"), s.table);
  drawIC($("#ic-chart"), s.table);
}

function drawEquity(host, rows) {
  const { W, H } = chartSize(host, 260, 220);
  const ml = 8, mr = 56, mt = 10, mb = 26;
  const series = [
    ["b-line", [1, ...rows.map((r) => r.bench_equity)]],
    ["m-line", [1, ...rows.map((r) => r.mom_equity)]],
    ["k-line", [1, ...rows.map((r) => r.kronos_equity)]],
  ];
  const all = series.flatMap((s) => s[1]);
  let lo = Math.min(...all), hi = Math.max(...all);
  const pad = (hi - lo) * 0.1 || 0.05; lo -= pad; hi += pad;
  const n = rows.length + 1;
  const X = (i) => ml + (i / (n - 1)) * (W - ml - mr);
  const Y = (v) => mt + (1 - (v - lo) / (hi - lo)) * (H - mt - mb);
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img", "aria-label": "Growth of one rupee for Kronos picks, momentum picks and the equal-weight Nifty 50" });
  niceTicks(lo, hi, 4).forEach((v) => {
    el("line", { x1: ml, x2: W - mr, y1: Y(v), y2: Y(v), class: "gridline" }, svg);
    el("text", { x: W - mr + 8, y: Y(v) + 4, class: "axis-label" }, svg).textContent = "₹" + v.toFixed(2);
  });
  rows.forEach((r, i) => {
    if (i % Math.ceil(rows.length / 6) === 0)
      el("text", { x: X(i + 1), y: H - 6, class: "axis-label", "text-anchor": "middle" }, svg).textContent =
        new Date(r.date + "T00:00:00").toLocaleDateString("en-IN", { month: "short", year: "2-digit" });
  });
  series.forEach(([c, vals]) => el("path", { d: linePath(vals.map((v, i) => [X(i), Y(v)])), class: c }, svg));
  host.replaceChildren(svg);
}

function drawIC(host, rows) {
  const { W, H } = chartSize(host, 200, 180);
  const ml = 8, mr = 48, mt = 10, mb = 26;
  const vals = rows.map((r) => r.rank_ic ?? 0);
  const m = Math.max(0.2, ...vals.map(Math.abs));
  const Y = (v) => mt + (1 - (v + m) / (2 * m)) * (H - mt - mb);
  const bw = (W - ml - mr) / rows.length;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img", "aria-label": "Monthly ranking score" });
  [-m / 2, m / 2].forEach((v) => {
    el("line", { x1: ml, x2: W - mr, y1: Y(v), y2: Y(v), class: "gridline" }, svg);
    el("text", { x: W - mr + 8, y: Y(v) + 4, class: "axis-label" }, svg).textContent = v.toFixed(2);
  });
  vals.forEach((v, i) => {
    const x = ml + i * bw + bw * 0.18, w = bw * 0.64;
    const y = v >= 0 ? Y(v) : Y(0);
    el("rect", { x, y, width: w, height: Math.max(1, Math.abs(Y(v) - Y(0))), rx: 2, class: v >= 0 ? "bar-pos" : "bar-neg" }, svg)
      .appendChild(Object.assign(document.createElementNS(SVGNS, "title"), { textContent: `${rows[i].date}: ${v.toFixed(3)}` }));
    if (i % Math.ceil(rows.length / 6) === 0)
      el("text", { x: x + w / 2, y: H - 6, class: "axis-label", "text-anchor": "middle" }, svg).textContent =
        new Date(rows[i].date + "T00:00:00").toLocaleDateString("en-IN", { month: "short", year: "2-digit" });
  });
  el("line", { x1: ml, x2: W - mr, y1: Y(0), y2: Y(0), class: "zero-line" }, svg);
  host.replaceChildren(svg);
}

let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (lastForecast && $("#fc-chart")) drawForecastChart($("#fc-chart"), lastForecast);
    if (realitySummary && realitySummary.ready && $("#eq-chart")) {
      drawEquity($("#eq-chart"), realitySummary.table);
      drawIC($("#ic-chart"), realitySummary.table);
    }
  }, 150);
});

// ── Boot ───────────────────────────────────────────────
$("#forecast-form").addEventListener("submit", runForecast);
renderQuickPicks();
loadStatus();
route();
if (!realitySummary) getJSON("data/reality.json").then((s) => { realitySummary = s; renderStrip(s); }).catch(() => {});
