import axios from "axios";

const CONFIGURED_BACKEND_URL = (process.env.REACT_APP_BACKEND_URL || "").replace(/\/$/, "");
const localHosts = new Set(["127.0.0.1", "localhost"]);
const servedLocally = typeof window !== "undefined" && localHosts.has(window.location.hostname);
const BACKEND_URL = servedLocally ? "" : CONFIGURED_BACKEND_URL;
export const API = `${BACKEND_URL}/api`;
export const api = axios.create({ baseURL: API });

export function apiErrorMessage(err, fallback = "Operazione non riuscita") {
  const detail = err?.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail.map(d => d?.msg || d?.message || JSON.stringify(d)).join(" · ");
  }
  if (err?.code === "ECONNABORTED") return "Tempo scaduto: il server sta impiegando troppo.";
  if (!err?.response && err?.message) return `Connessione non riuscita: ${err.message}`;
  return fallback;
}
