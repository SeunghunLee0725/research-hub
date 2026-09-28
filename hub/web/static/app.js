(function () {
  const target = document.getElementById("overview");
  async function refresh() {
    try {
      const resp = await fetch("/partials/overview", { credentials: "same-origin" });
      if (resp.status === 401) { window.location.href = "/login"; return; }
      if (resp.ok) target.innerHTML = await resp.text();
    } catch (err) {
      console.warn("overview refresh failed", err);
    }
  }
  setInterval(refresh, 10000);
})();
