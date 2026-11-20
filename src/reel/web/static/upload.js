// Reel: resumable upload of a lesson's files (T-807). Plain JavaScript, no libraries.
// Each file goes in pieces (PUT .../files/<n>?offset=...). After a dropped connection the
// script asks the server how much arrived and continues from there. If the page was
// closed, choosing the same files again resumes the same upload (remembered in this
// browser's localStorage).
(() => {
  "use strict";
  const form = document.getElementById("upload-form");
  if (!form) return;
  const csrf = form.querySelector("[name=csrf_token]").value;
  const chunk = Number(form.dataset.chunk) || 8 * 1024 * 1024;
  const input = document.getElementById("files");
  const list = document.getElementById("file-list");
  const status = document.getElementById("upload-status");
  const total = document.getElementById("upload-total");
  const start = document.getElementById("start");
  const stop = document.getElementById("stop");
  let files = [];
  let bars = [];
  let stopped = false;

  const size = (bytes) => {
    const gb = bytes / 1024 ** 3;
    return gb >= 1 ? `${gb.toFixed(2)} ԳԲ` : `${(bytes / 1024 ** 2).toFixed(1)} ՄԲ`;
  };
  const say = (text) => { status.textContent = text; };
  const wait = (ms) => new Promise((done) => setTimeout(done, ms));

  function show() {
    list.textContent = "";
    bars = files.map((file, index) => {
      const item = document.createElement("li");
      const name = document.createElement("span");
      name.textContent = `${file.name} · ${size(file.size)} · ${new Date(file.lastModified).toLocaleString()}`;
      const up = document.createElement("button");
      up.type = "button";
      up.className = "small-button";
      up.textContent = "↑";
      up.disabled = index === 0;
      up.addEventListener("click", () => move(index, -1));
      const down = document.createElement("button");
      down.type = "button";
      down.className = "small-button";
      down.textContent = "↓";
      down.disabled = index === files.length - 1;
      down.addEventListener("click", () => move(index, 1));
      const bar = document.createElement("progress");
      bar.max = file.size;
      bar.value = 0;
      item.append(name, up, down, bar);
      list.append(item);
      return bar;
    });
  }

  function move(index, step) {
    const other = index + step;
    [files[index], files[other]] = [files[other], files[index]];
    show();
  }

  input.addEventListener("change", () => {
    files = Array.from(input.files).sort((a, b) => a.lastModified - b.lastModified);
    show();
  });

  async function call(method, url, body, type = "application/json") {
    const response = await fetch(url, {
      method,
      headers: { "X-CSRF-Token": csrf, ...(body !== undefined ? { "Content-Type": type } : {}) },
      body: body === undefined || type !== "application/json" ? body : JSON.stringify(body),
      credentials: "same-origin",
    });
    let data = null;
    try { data = await response.json(); } catch (error) { data = null; }
    return { ok: response.ok, status: response.status, data };
  }

  function progress(state) {
    let sent = 0;
    state.files.forEach((file, index) => { bars[index].value = file.received; sent += file.received; });
    const all = state.files.reduce((sum, file) => sum + file.size, 0);
    total.hidden = false;
    total.max = all;
    total.value = sent;
    say(`Վերբեռնված է ${size(sent)} ${size(all)}-ից (${Math.floor((100 * sent) / all)}%)`);
  }

  async function begin() {
    const key = "reel-upload:" + files.map((f) => `${f.name}|${f.size}|${f.lastModified}`).join("/");
    let id = null;
    try { id = localStorage.getItem(key); } catch (error) { id = null; }
    if (id) {
      const found = await call("GET", `/uploads/${encodeURIComponent(id)}`);
      if (found.ok && !found.data.finished) return { key, state: found.data };
    }
    const fields = new FormData(form);
    const created = await call("POST", "/uploads", {
      group: fields.get("group"),
      lesson: fields.get("lesson"),
      date: fields.get("date"),
      note: fields.get("note"),
      files: files.map((f) => ({ name: f.name, size: f.size, modified: f.lastModified })),
    });
    if (!created.ok) throw new Error(created.data?.error || `HTTP ${created.status}`);
    try { localStorage.setItem(key, created.data.id); } catch (error) { /* private mode */ }
    return { key, state: created.data };
  }

  async function send(id, state) {
    for (let index = 0; index < files.length; index++) {
      const file = files[index];
      let offset = state.files[index].received;
      let failures = 0;
      while (offset < file.size) {
        if (stopped) throw new Error("Վերբեռնումը դադարեցվեց։ Սեղմեք «Վերբեռնել»՝ շարունակելու համար։");
        try {
          const piece = file.slice(offset, Math.min(offset + chunk, file.size));
          const answer = await call("PUT",
            `/uploads/${encodeURIComponent(id)}/files/${index}?offset=${offset}`,
            piece, "application/octet-stream");
          if (answer.ok || (answer.status === 409 && answer.data?.error === "offset")) {
            offset = answer.data.received;
            state.files[index].received = offset;
            failures = 0;
            progress(state);
            continue;
          }
          if (answer.status >= 400 && answer.status < 500 && answer.status !== 408 && answer.status !== 429) {
            throw new Error(answer.data?.error || `HTTP ${answer.status}`);
          }
          throw new Error("retry");
        } catch (error) {
          if (error.message !== "retry" && !(error instanceof TypeError)) throw error;
          failures += 1;
          const delay = Math.min(30, 2 ** Math.min(failures, 5));
          say(`Կապը խզվեց։ Նորից կփորձեմ ${delay} վայրկյանից…`);
          await wait(delay * 1000);
          const now = await call("GET", `/uploads/${encodeURIComponent(id)}`).catch(() => null);
          if (now?.ok) {
            Object.assign(state, now.data);
            offset = state.files[index].received;
          }
        }
      }
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!files.length) { say("Ընտրեք ֆայլերը։"); return; }
    stopped = false;
    start.disabled = true;
    stop.hidden = false;
    input.disabled = true;
    try {
      const { key, state } = await begin();
      progress(state);
      await send(state.id, state);
      say("Ֆայլերը վերբեռնված են։ Ստեղծվում է job-ը…");
      const done = await call("POST", `/uploads/${encodeURIComponent(state.id)}/finish`, {});
      if (!done.ok) throw new Error(done.data?.error || `HTTP ${done.status}`);
      try { localStorage.removeItem(key); } catch (error) { /* private mode */ }
      window.location.assign(done.data.url);
    } catch (error) {
      say(error.message);
      start.disabled = false;
      input.disabled = false;
      stop.hidden = true;
    }
  });

  stop.addEventListener("click", () => { stopped = true; });
})();
