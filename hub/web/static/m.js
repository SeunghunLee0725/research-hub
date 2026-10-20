(function () {
  const live = document.getElementById("live");
  const sync = document.querySelector(".sync");
  const KEY = (name) => "m.chip." + name;

  function store(key, value) { try { sessionStorage.setItem(key, value); } catch (err) { /* private mode */ } }
  function recall(key) { try { return sessionStorage.getItem(key); } catch (err) { return null; } }

  // Chips switch panes in place: no reload, the choice survives auto-refresh.
  function applyChips(root) {
    root.querySelectorAll("[data-chips]").forEach((bar) => {
      const name = bar.dataset.chips;
      const panes = root.querySelectorAll(".pane");
      const show = (value) => {
        bar.querySelectorAll("[data-chip]").forEach((b) => b.classList.toggle("on", b.dataset.chip === value));
        panes.forEach((p) => { p.hidden = p.dataset.pane !== value; });
        store(KEY(name), value);
      };
      bar.querySelectorAll("[data-chip]").forEach((b) => b.addEventListener("click", () => {
        show(b.dataset.chip);
        b.scrollIntoView({ inline: "center", block: "nearest", behavior: "smooth" });
      }));
      const saved = recall(KEY(name));
      show(saved && bar.querySelector(`[data-chip="${saved}"]`) ? saved : bar.dataset.default);
    });
  }

  async function refresh() {
    if (!live || document.hidden) return;
    sync && sync.classList.add("busy");
    try {
      const resp = await fetch(live.dataset.src, { credentials: "same-origin" });
      if (resp.status === 401) { window.location.href = "/login"; return; }
      if (!resp.ok) throw new Error(resp.status);
      live.innerHTML = await resp.text();
      applyChips(live);
      sync && sync.classList.remove("err");
    } catch (err) {
      sync && sync.classList.add("err");
    } finally {
      sync && sync.classList.remove("busy");
    }
    badges();
  }

  async function badges() {
    try {
      const resp = await fetch("/partials/badges", { credentials: "same-origin" });
      if (!resp.ok) return;
      const counts = (await resp.json()).data || {};
      document.querySelectorAll("[data-badge]").forEach((el) => {
        const n = counts[el.dataset.badge] || 0;
        el.textContent = n > 99 ? "99+" : String(n);
        el.hidden = n === 0;
      });
    } catch (err) { /* badges are a hint only */ }
  }

  // Pull down at the top of the page to refresh, like a native app.
  const ptr = document.querySelector(".ptr");
  let startY = null;
  window.addEventListener("touchstart", (e) => { startY = window.scrollY === 0 ? e.touches[0].clientY : null; }, { passive: true });
  window.addEventListener("touchmove", (e) => {
    if (startY === null || !ptr) return;
    const pull = Math.min(90, e.touches[0].clientY - startY);
    ptr.style.opacity = pull > 20 ? "1" : "0";
    ptr.style.transform = `translateY(${pull - 40}px)`;
    ptr.textContent = pull > 70 ? "놓으면 새로고침" : "당겨서 새로고침";
  }, { passive: true });
  window.addEventListener("touchend", (e) => {
    if (startY === null || !ptr) return;
    const pull = e.changedTouches[0].clientY - startY;
    ptr.style.opacity = "0"; ptr.style.transform = "translateY(-40px)";
    startY = null;
    if (pull > 70) refresh();
  });

  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  applyChips(document);
  badges();
  if (live) setInterval(refresh, parseInt(live.dataset.every || "15000", 10));
})();
