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
  function scrollToNow(previous) {
    const wrap = live.querySelector(".gantt-wrap");
    if (!wrap) return;
    if (previous !== null) { wrap.scrollLeft = previous; return; }
    const pct = parseFloat(wrap.dataset.scrollNow);
    const chart = wrap.querySelector(".g-head .g-chart");
    if (Number.isNaN(pct) || !chart) return;
    const x = chart.offsetLeft + chart.offsetWidth * pct / 100;
    wrap.scrollLeft = Math.max(0, x - wrap.clientWidth * 0.8);
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
