/* Intentional diagnostic-only UI. The Rust layer has a fixed loopback target. */
const check = document.getElementById("check");
const open = document.getElementById("open");
const start = document.getElementById("start");
const stop = document.getElementById("stop");
let managed = false;
const status = document.getElementById("status");
const message = document.getElementById("message");

function render(ready, detail) {
  status.classList.remove("ok", "bad");
  status.classList.add(ready ? "ok" : "bad");
  open.disabled = !ready;
  message.textContent = detail;
}

async function invoke(cmd) {
  const api = window.__TAURI__?.core;
  if (!api?.invoke) {
    throw new Error("L'interfaccia non è eseguita nel contenitore Tauri.");
  }
  return api.invoke(cmd);
}

check.addEventListener("click", async () => {
  check.disabled = true;
  try {
    const result = await invoke("probe_local_backend");
    render(result.ready === true, result.detail);
  } catch (err) {
    render(false, String(err));
  } finally {
    check.disabled = false;
  }
});

open.addEventListener("click", async () => {
  open.disabled = true;
  try {
    await invoke(managed ? "open_owned_local_backend" : "open_local_backend");
    // A later check re-verifies the backend if the process has exited.
    render(true, "Finestra locale aperta. I dati restano nel runtime avviato esternamente.");
  } catch (err) {
    render(false, String(err));
  }
});

start.addEventListener("click", async () => {
  start.disabled = true;
  try {
    const detail = await invoke("start_experimental_runtime");
    managed = true;
    stop.disabled = false;
    render(false, detail + " Premi «Verifica servizio» quando il database è pronto.");
  } catch (err) {
    managed = false;
    render(false, String(err));
    start.disabled = false;
  }
});

stop.addEventListener("click", async () => {
  stop.disabled = true;
  try {
    const detail = await invoke("stop_experimental_runtime");
    render(false, detail);
  } catch (err) {
    render(false, String(err));
  } finally {
    managed = false;
    start.disabled = false;
    open.disabled = true;
  }
});
