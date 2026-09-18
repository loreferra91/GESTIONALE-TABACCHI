import axios from "axios";

// In production the frontend is served by FastAPI, so API requests can stay
// on the same origin. Local development can still point at a remote backend.
const BACKEND_URL = (process.env.REACT_APP_BACKEND_URL || "").replace(/\/$/, "");
export const API = `${BACKEND_URL}/api`;
export const api = axios.create({ baseURL: API });
