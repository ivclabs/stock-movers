/*
  app.js - fills the website with data from results.json
  -------------------------------------------------------
  Steps:
    1. fetch results.json (today's movers)
    2. keep only the movers labelled "Solid" or "Just talk"
    3. sort them: Solid first, then biggest % move first
    4. build one card per stock (price, label, reason, chart, headlines)

      results.json ──▶ app.js ──▶ cards with badges: Solid / Just talk

  Safety note: news headlines come from the internet, so we never put them
  into the page with innerHTML. We use textContent, which shows text as plain
  text and can't run any code hidden inside it.
*/

const DATA_URL = "results.json";
const SHOW = ["Solid", "Just talk"];           // must match SHOW_ON_SITE in judge.py

// Which colour (CSS class) each badge gets
const BADGE_CLASS = { "Solid": "badge-solid", "Just talk": "badge-talk" };

// ---- Small helpers -----------------------------------------------------------

// Make an HTML element: el("p", "my-class", "some text")
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

const money = (n) => "$" + Number(n).toFixed(2);
const signed = (n, digits) => (n >= 0 ? "+" : "") + Number(n).toFixed(digits);

// 1234567 -> "1.2M"
function shortNumber(n) {
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

// "2026-11-04T08:50:35Z" -> "4 Nov, 09:50" in the visitor's own time zone
function localTime(iso) {
  return new Date(iso).toLocaleString(undefined, {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
  });
}

const SESSION_NAMES = { pre: "Pre-market", post: "After-hours" };

// ---- Header -----------------------------------------------------------------

function showStatus(result, shownCount) {
  const status = document.getElementById("status");
  const session = SESSION_NAMES[result.session] || result.session;
  status.textContent = "";
  status.append(
    el("strong", null, session),
    ` · data from ${localTime(result.generated_at_utc)} (${result.data_delay_minutes} min delayed)`,
    ` · ${shownCount} of ${result.count} movers have news`
  );
}

// ---- Chart ------------------------------------------------------------------

// Moving averages drawn on every chart - the same ones as on your
// thinkorswim chart: EMA 10, EMA 20, SMA 50.
// "n" counts candles: on the daily chart EMA 10 = 10 days, on the weekly = 10 weeks.
// The colours live in style.css (--ema10, --ema20, --sma50).
const MAS = [
  { type: "EMA", n: 10, color: "--ema10" },
  { type: "EMA", n: 20, color: "--ema20" },
  { type: "SMA", n: 50, color: "--sma50" },
];

// The daily chart SHOWS the last 6 months, but charts.py downloads 12.
// The 6 hidden months are used only to calculate the averages, so even
// SMA 50 already has 50 days of data at the left edge of the chart
// (and the EMAs have had time to settle).
const DAILY_MONTHS_SHOWN = 6;

// "2026-11-03" minus 6 months -> "2026-05-03"
function monthsBefore(dateText, months) {
  const d = new Date(dateText + "T00:00:00Z");
  d.setUTCMonth(d.getUTCMonth() - months);
  return d.toISOString().slice(0, 10);
}

// Colours for the chart, read from style.css so light/dark mode match
function chartColors() {
  const css = getComputedStyle(document.documentElement);
  const v = (name) => css.getPropertyValue(name).trim();
  return {
    text: v("--muted"), border: v("--border"), up: v("--up"), down: v("--down"), bg: v("--card"),
    ma: MAS.map((ma) => v(ma.color)),                 // one colour per moving average
  };
}

// Simple Moving Average: the average close of the last `n` candles.
// The first n-1 candles have no full window yet, so the line starts at candle n.
//   closes 1,2,3,4,5 with n=3  ->  (1+2+3)/3=2, (2+3+4)/3=3, (3+4+5)/3=4
function sma(candles, n) {
  const points = [];
  let sum = 0;
  for (let i = 0; i < candles.length; i++) {
    sum += candles[i].close;                       // add the newest close ...
    if (i >= n) sum -= candles[i - n].close;       // ... and drop the one that left the window
    if (i >= n - 1) points.push({ time: candles[i].time, value: sum / n });
  }
  return points;
}

// Exponential Moving Average, like thinkorswim's ExpAverage(close, n):
// each value = close * k + previous EMA * (1 - k),
// with k = 2 / (n + 1). It starts at the first close, so it reacts faster
// to new prices than an SMA does.
//   closes 1,2,3,4,5 with n=3 (k=0.5)  ->  1, 1.5, 2.25, 3.125, 4.0625
function ema(candles, n) {
  const k = 2 / (n + 1);
  const points = [];
  let value;
  for (let i = 0; i < candles.length; i++) {
    value = i === 0 ? candles[i].close : candles[i].close * k + value * (1 - k);
    points.push({ time: candles[i].time, value });
  }
  return points;
}

// Calculate one moving average from the MAS list above
const movingAverage = (candles, ma) => (ma.type === "EMA" ? ema(candles, ma.n) : sma(candles, ma.n));

// Every chart on the page, so they can be removed cleanly when new data arrives
const liveCharts = [];

// Draw one chart (candles + volume bars + moving-average lines) inside `container`.
// `nowPrice` is today's pre-market/after-hours price: the candles stop at the
// last regular close, so a label on the price scale shows where the stock is today.
// Returns a function that swaps the data, used by the "6 months / 2 years" buttons.
function makeChart(container, candles, showMonths, nowPrice) {
  const c = chartColors();
  const barUp = c.up;                                 // candle colours
  const barDown = c.down;
  const chart = LightweightCharts.createChart(container, {
    autoSize: true,                                   // follow the container's size
    layout: { background: { type: "solid", color: c.bg }, textColor: c.text },
    grid: { vertLines: { color: c.border }, horzLines: { color: c.border } },
    rightPriceScale: { borderColor: c.border, scaleMargins: { top: 0.1, bottom: 0.25 } },
    timeScale: { borderColor: c.border },
    handleScroll: false,                              // so the page can still scroll on phones
    handleScale: false,
  });
  liveCharts.push(chart);

  const candleSeries = chart.addCandlestickSeries({
    upColor: barUp, downColor: barDown, wickUpColor: barUp, wickDownColor: barDown, borderVisible: false,
    lastValueVisible: false, priceLineVisible: false,   // the "now" line below replaces these
  });
  // Today's price: only a label on the price scale (no line across the chart)
  candleSeries.createPriceLine({
    price: nowPrice, color: c.up, lineVisible: false, axisLabelVisible: true,
  });

  // Volume bars at the bottom 20 % of the chart, on their own hidden scale
  const volumeSeries = chart.addHistogramSeries({
    priceFormat: { type: "volume" }, priceScaleId: "",
    lastValueVisible: false, priceLineVisible: false,   // no volume number on the right edge
  });
  volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

  // One thin line per moving average, on top of the candles
  const maSeries = MAS.map((ma, i) => chart.addLineSeries({
    color: c.ma[i], lineWidth: 1,
    lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
  }));

  // showMonths: only show the last N months (all data is still used for the averages).
  // Leave it out to show everything.
  function setData(data, showMonths) {
    const from = showMonths && data.length ? monthsBefore(data[data.length - 1].time, showMonths) : "";
    const visible = data.filter((d) => d.time >= from);   // dates as text compare correctly

    candleSeries.setData(visible.map((d) => ({ time: d.time, open: d.open, high: d.high, low: d.low, close: d.close })));
    MAS.forEach((ma, i) => maSeries[i].setData(movingAverage(data, ma).filter((p) => p.time >= from)));
    volumeSeries.setData(visible.map((d) => ({
      time: d.time,
      value: d.volume,
      color: (d.close >= d.open ? barUp : barDown) + "66",   // "66" = see-through
    })));
    chart.timeScale().fitContent();
  }

  setData(candles, showMonths);
  return setData;
}

function chartBlock(stock) {
  const box = el("div", "chart-box");
  if (!stock.chart_daily || stock.chart_daily.length === 0) {
    box.append(el("p", "no-chart", "No chart data."));
    return box;
  }

  const tabs = el("div", "chart-tabs");
  const daily = el("button", "active", `${DAILY_MONTHS_SHOWN} months · daily`);
  const weekly = el("button", null, "2 years · weekly");
  daily.type = weekly.type = "button";
  tabs.append(daily, weekly);

  // Colour legend for the moving-average lines, e.g. "— EMA 10  — EMA 20  — SMA 50"
  const legend = el("div", "ma-legend");
  for (const ma of MAS) {
    const item = el("span", "ma", `${ma.type} ${ma.n}`);
    item.style.setProperty("--dash", `var(${ma.color})`);   // colour of the little dash
    legend.append(item);
  }
  tabs.append(legend);

  const area = el("div", "chart");
  box.append(tabs, area);

  // The chart needs to be on the page before it can measure its size,
  // so we draw it a moment later (after the card has been added).
  requestAnimationFrame(() => {
    const setData = makeChart(area, stock.chart_daily, DAILY_MONTHS_SHOWN, stock.price);
    daily.onclick = () => { setData(stock.chart_daily, DAILY_MONTHS_SHOWN); daily.classList.add("active"); weekly.classList.remove("active"); };
    weekly.onclick = () => { setData(stock.chart_weekly); weekly.classList.add("active"); daily.classList.remove("active"); };
  });
  return box;
}

// ---- Headlines --------------------------------------------------------------

function newsBlock(stock) {
  const details = el("details", "news");
  details.append(el("summary", null, `Headlines (${stock.news.length})`));
  const list = el("ul");
  for (const article of stock.news) {
    const li = el("li");
    const link = el("a", null, article.headline);
    link.href = article.url;
    link.target = "_blank";
    link.rel = "noopener";
    li.append(link, el("div", "meta", `${localTime(article.created_at)} · ${article.source}`));
    list.append(li);
  }
  details.append(list);
  return details;
}

// ---- One card ---------------------------------------------------------------

// stock.badges is a list like [{label: "Solid", text: "Q3 earnings beat..."}]
function card(stock) {
  const article = el("article", "card");
  article.dataset.labels = stock.badges.map((b) => b.label).join("|");   // used by the filter buttons

  // Top row: symbol + name on the left, price + change on the right
  const head = el("div", "card-head");
  const left = el("div");
  left.append(el("h2", "symbol", stock.symbol), el("p", "name", stock.name));
  const right = el("div", "price");
  right.append(
    el("div", "now", money(stock.price)),
    el("div", "change" + (stock.change < 0 ? " neg" : ""),
       `${signed(stock.change, 2)} (${signed(stock.change_pct, 2)}%)`),
    el("div", "vol", stock.volumeText)
  );
  head.append(left, right);

  // One line per badge: coloured label + its reason
  article.append(head);
  for (const badge of stock.badges) {
    const reason = el("p", "reason");
    reason.append(el("span", "badge " + BADGE_CLASS[badge.label], badge.label), badge.text);
    article.append(reason);
  }

  article.append(chartBlock(stock));
  if (stock.news && stock.news.length) article.append(newsBlock(stock));
  return article;
}

// ---- Turning the data file into cards -----------------------------------------

// Today's mover -> card data (badge from the AI)
function moverCard(m) {
  return {
    ...m,
    volumeText: `vol ${shortNumber(m.session_volume || 0)}`,
    badges: [{ label: m.ai.label, text: m.ai.reason }],
  };
}

async function fetchJson(url) {
  try {
    // no-store: always get the newest file, never an old copy from the browser cache
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return await response.json();
  } catch (err) {
    console.error(url, err);
    return null;                       // the page still works with the other file
  }
}

// ---- Filter buttons ---------------------------------------------------------

let currentFilter = "all";        // remembered, so a refresh keeps the chosen tab

// Show only the cards that have the chosen label
function applyFilter() {
  document.querySelectorAll(".card").forEach((c) => {
    c.hidden = currentFilter !== "all" && !c.dataset.labels.split("|").includes(currentFilter);
  });
}

function setUpFilters() {
  const nav = document.getElementById("filters");
  nav.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    nav.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === button));
    currentFilter = button.dataset.filter;
    applyFilter();
  });
}

// ---- Drawing the cards --------------------------------------------------------

// Build all cards from results.json (also used again after each refresh)
function render(result) {
  const cards = document.getElementById("cards");

  // Remove the old cards and their charts first
  liveCharts.forEach((chart) => chart.remove());
  liveCharts.length = 0;
  cards.replaceChildren();

  // Today's movers with news
  const all = (result.movers || [])
    .filter((m) => m.ai && SHOW.includes(m.ai.label))
    .sort((a, b) =>
      SHOW.indexOf(a.ai.label) - SHOW.indexOf(b.ai.label)   // Solid before Just talk
      || b.change_pct - a.change_pct)                       // then biggest move first
    .map(moverCard);

  showStatus(result, all.length);

  document.getElementById("filters").hidden = all.length === 0;
  if (all.length === 0) {
    cards.append(el("div", "message",
      "No movers with news right now. Check back during pre-market (10:00–15:30 Swedish time) or after-hours (22:00–02:00)."));
    return;
  }

  for (const stock of all) cards.append(card(stock));
  applyFilter();
}

// ---- Auto refresh -------------------------------------------------------------
//
//   every 2 min ──▶ fetch results.json ──▶ same "generated_at" as before? ──▶ do nothing
//                                       └─▶ new data?                    ──▶ render() again
//
// The data in AWS changes every 20 min (pre-market / after-hours), so checking
// every 2 min means a visitor sees new data at most ~2 min late.
// A hidden browser tab doesn't check; it checks right away when you come back.

const REFRESH_MINUTES = 2;
let shownTime = null;             // generated_at_utc of the data currently on the page

async function loadAndShow() {
  const result = await fetchJson(DATA_URL);

  if (!result) {
    // Network hiccup: keep showing what we have; only complain if the page is empty
    if (!shownTime) document.getElementById("status").textContent = "Could not load the data. Try again in a minute.";
    return;
  }
  if (result.generated_at_utc === shownTime) return;   // nothing new

  shownTime = result.generated_at_utc;
  render(result);
}

function refreshIfVisible() {
  if (!document.hidden) loadAndShow();
}

// ---- Start ------------------------------------------------------------------

setUpFilters();
loadAndShow();
setInterval(refreshIfVisible, REFRESH_MINUTES * 60 * 1000);
document.addEventListener("visibilitychange", refreshIfVisible);
