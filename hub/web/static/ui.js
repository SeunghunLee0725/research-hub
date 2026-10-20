(function () {
  const progress = document.querySelector(".progress");
  const tip = document.createElement("div");
  tip.className = "tip";
  tip.hidden = true;
  document.body.appendChild(tip);

  function busy(on) { if (progress) progress.classList.toggle("on", on); }

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
    } catch (err) { /* a hint only */ }
  }

  // Filter buttons swap the page body in place: the click shows at once, no white flash, back button works.
  async function navigate(url, push) {
    busy(true);
    try {
      const resp = await fetch(url, { credentials: "same-origin" });
      if (resp.redirected && new URL(resp.url).pathname === "/login") { window.location.href = "/login"; return; }
      if (!resp.ok) { window.location.href = url; return; }
      const doc = new DOMParser().parseFromString(await resp.text(), "text/html");
      const next = doc.querySelector("main");
      if (!next) { window.location.href = url; return; }
      document.querySelector("main").replaceWith(next);
      if (push) history.pushState({ live: true }, "", url);
      document.dispatchEvent(new CustomEvent("live:replaced"));
    } catch (err) {
      window.location.href = url;
    } finally {
      busy(false);
    }
  }

  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[data-live-nav]");
    if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    a.closest(".seg-group")?.querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("on", b === a));
    navigate(a.href, true);
  });
  window.addEventListener("popstate", () => navigate(location.href, false));

  // Hover card for bars and step segments.
  document.addEventListener("mouseover", (e) => {
    const el = e.target.closest("[data-tip]");
    if (!el) return;
    tip.textContent = el.dataset.tip;
    tip.hidden = false;
  });
  document.addEventListener("mousemove", (e) => {
    if (tip.hidden) return;
    const pad = 14;
    const x = Math.min(e.clientX + pad, window.innerWidth - tip.offsetWidth - 8);
    const y = e.clientY + pad + tip.offsetHeight > window.innerHeight ? e.clientY - tip.offsetHeight - pad : e.clientY + pad;
    tip.style.transform = `translate(${x}px, ${y}px)`;
  });
  document.addEventListener("mouseout", (e) => {
    if (e.target.closest("[data-tip]") && !e.relatedTarget?.closest?.("[data-tip]")) tip.hidden = true;
  });

  // Keyboard: 1 현황, 2 간트, 3 보드, r 새로고침.
  document.addEventListener("keydown", (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey || e.target.closest("input, textarea, select, [contenteditable]")) return;
    const link = document.querySelector(`.menu a[data-key="${e.key}"]`);
    if (link) { window.location.href = link.href; return; }
    if (e.key === "r") navigate(location.href, false);
  });

  badges();
  setInterval(badges, 15000);
})();
