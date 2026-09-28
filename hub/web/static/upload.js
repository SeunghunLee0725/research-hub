(function () {
  const zone = document.getElementById("dropzone");
  if (!zone) return;
  const input = zone.querySelector("input[type=file]");
  const log = zone.querySelector(".upload-log");

  function note(text, cls) {
    const li = document.createElement("li");
    li.textContent = text;
    if (cls) li.className = cls;
    log.appendChild(li);
  }

  async function send(files) {
    if (!files.length) return;
    const body = new FormData();
    for (const f of files) body.append("files", f, f.name);
    note(`${files.length}개 파일 올리는 중…`);
    try {
      const resp = await fetch(zone.dataset.url, {
        method: "POST", body, credentials: "same-origin", headers: { "X-CSRF-Token": zone.dataset.csrf },
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) { note(`실패: ${data.detail || resp.status}`, "bad"); return; }
      data.data.forEach((u) => note(`올림: ${u.path}`, "ok"));
      setTimeout(() => window.location.reload(), 800);
    } catch (err) {
      note(`실패: ${err}`, "bad");
    }
  }

  ["dragenter", "dragover"].forEach((e) => zone.addEventListener(e, (ev) => { ev.preventDefault(); zone.classList.add("over"); }));
  ["dragleave", "drop"].forEach((e) => zone.addEventListener(e, (ev) => { ev.preventDefault(); zone.classList.remove("over"); }));
  zone.addEventListener("drop", (ev) => send([...ev.dataTransfer.files]));
  input.addEventListener("change", () => send([...input.files]));
})();
