(function () {
  const STATE_KEY = "gantt.open.v2";
  const live = () => document.getElementById("live");

  // The server decides the default (open when busy or short); a project the person toggled keeps their choice.
  function readState() {
    try { return JSON.parse(localStorage.getItem(STATE_KEY) || "{}"); } catch (err) { return {}; }
  }
  function writeState(state) {
    try { localStorage.setItem(STATE_KEY, JSON.stringify(state)); } catch (err) { /* this view still works */ }
  }
  function applyCollapse(root) {
    const state = readState();
    root.querySelectorAll("details.g-project[data-key]").forEach((el) => {
      if (el.dataset.key in state) el.open = state[el.dataset.key];
      el.addEventListener("toggle", () => writeState({ ...readState(), [el.dataset.key]: el.open }));
    });
  }

  // Open where the work is: at "now" when recent work fits in view, else at the latest bar.
  function scrollToWork(root, previous) {
    const wrap = root.querySelector(".gantt-wrap");
    if (!wrap) return;
    if (previous !== null) { wrap.scrollLeft = previous; return; }
    const chart = wrap.querySelector(".g-head .g-chart");
    if (!chart) return;
    const toX = (pct) => chart.offsetLeft + chart.offsetWidth * pct / 100;
    const nowPct = parseFloat(wrap.dataset.scrollNow);
    let lastPct = -1;
    wrap.querySelectorAll(".tbar, .seg, .rollup").forEach((el) => {
      const right = parseFloat(el.style.left) + parseFloat(el.style.width);
      if (right > lastPct) lastPct = right;
    });
    const visible = wrap.clientWidth - chart.offsetLeft;
    let target;
    if (lastPct < 0) target = Number.isNaN(nowPct) ? 0 : toX(nowPct) - chart.offsetLeft - visible * 0.85;
    else if (!Number.isNaN(nowPct) && toX(nowPct) - toX(lastPct) < visible * 0.8) target = toX(nowPct) - chart.offsetLeft - visible * 0.9;
    else target = toX(lastPct) - chart.offsetLeft - visible * 0.7;
    wrap.scrollLeft = Math.max(0, target);
  }

  async function refresh() {
    const el = live();
    if (!el || document.hidden) return;
    const wrap = el.querySelector(".gantt-wrap");
    const previous = wrap ? wrap.scrollLeft : null;
    try {
      const resp = await fetch(el.dataset.src, { credentials: "same-origin" });
      if (resp.status === 401) { window.location.href = "/login"; return; }
      if (!resp.ok) return;
      el.innerHTML = await resp.text();
      applyCollapse(el);
      scrollToWork(el, previous);
    } catch (err) {
      console.warn("refresh failed", err);
    }
  }

  let timer = null;
  function start() {
    const el = live();
    if (!el) return;
    applyCollapse(el);
    scrollToWork(el, null);
    clearInterval(timer);
    timer = setInterval(refresh, parseInt(el.dataset.every || "30000", 10));
  }
  document.addEventListener("live:replaced", start);
  start();
})();
