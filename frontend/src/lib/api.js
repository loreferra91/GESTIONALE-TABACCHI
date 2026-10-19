import axios from "axios";

const CONFIGURED_BACKEND_URL = (process.env.REACT_APP_BACKEND_URL || "").replace(/\/$/, "");
const localHosts = new Set(["127.0.0.1", "localhost"]);
const servedLocally = typeof window !== "undefined" && localHosts.has(window.location.hostname);
const BACKEND_URL = servedLocally ? "" : CONFIGURED_BACKEND_URL;
export const API = `${BACKEND_URL}/api`;
export const api = axios.create({ baseURL: API });

function detailItemMessage(item) {
  if (typeof item === "string") return item.trim();
  if (!item || typeof item !== "object") return "";

  const location = item.riga != null
    ? `Riga ${item.riga}`
    : (item.codice ? `Codice ${item.codice}` : "");
  const message = item.errore || item.msg || item.message || "";
  return [location, message].filter(Boolean).join(": ");
}

export function apiErrorMessage(err, fallback = "Operazione non riuscita") {
  const detail = err?.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail.map(detailItemMessage).filter(Boolean).join(" · ") || fallback;
  }
  if (detail && typeof detail === "object") {
    const message = typeof detail.message === "string" ? detail.message.trim() : "";
    const errors = Array.isArray(detail.errori)
      ? detail.errori.map(detailItemMessage).filter(Boolean)
      : [];
    return [message, ...errors].filter(Boolean).join(" · ") || fallback;
  }
  if (err?.code === "ECONNABORTED") return "Tempo scaduto: il server sta impiegando troppo.";
  if (!err?.response && err?.message) return `Connessione non riuscita: ${err.message}`;
  return fallback;
}
