(function () {
  const live = document.getElementById("live");
  if (!live) return;
  const STATE_KEY = "gantt.open.v2";

  // The server decides the default (open when busy or short); a project the person toggled keeps their choice.
  function readState() {
    try { return JSON.parse(localStorage.getItem(STATE_KEY) || "{}"); } catch (err) { return {}; }
  }
  function writeState(state) {
    try { localStorage.setItem(STATE_KEY, JSON.stringify(state)); } catch (err) { /* this view still works */ }
  }
  function applyCollapse() {
    const state = readState();
    live.querySelectorAll("details.g-project[data-key]").forEach((el) => {
      if (el.dataset.key in state) el.open = state[el.dataset.key];
      el.addEventListener("toggle", () => writeState({ ...readState(), [el.dataset.key]: el.open }));
    });
  }
  // Open where the work is: at "now" when recent work fits in view, else at the latest bar.
  function scrollToNow(previous) {
    const wrap = live.querySelector(".gantt-wrap");
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
    const wrap = live.querySelector(".gantt-wrap");
    const previous = wrap ? wrap.scrollLeft : null;
    try {
      const resp = await fetch(live.dataset.src, { credentials: "same-origin" });
      if (resp.status === 401) { window.location.href = "/login"; return; }
      if (!resp.ok) return;
      live.innerHTML = await resp.text();
      applyCollapse();
      scrollToNow(previous);
    } catch (err) {
      console.warn("refresh failed", err);
    }
  }
  applyCollapse();
  scrollToNow(null);
  setInterval(refresh, parseInt(live.dataset.every || "30000", 10));
})();
