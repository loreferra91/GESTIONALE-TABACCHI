import { act } from "react";
import { createRoot } from "react-dom/client";

jest.mock("react-router-dom", () => ({
  Link: ({ children, ...props }) => <a {...props}>{children}</a>,
}));

jest.mock("../components/Layout", () => ({ children }) => <main>{children}</main>);

jest.mock("recharts", () => ({
  ResponsiveContainer: ({ children }) => <div>{children}</div>,
  AreaChart: () => <div />,
  Area: () => null,
  CartesianGrid: () => null,
  Tooltip: () => null,
  XAxis: () => null,
  YAxis: () => null,
}));

jest.mock("../lib/api", () => ({
  api: { get: jest.fn() },
  apiErrorMessage: (_error, fallback) => fallback,
}));

jest.mock("sonner", () => ({ toast: { error: jest.fn() } }));

import Dashboard from "./Dashboard";
import { api } from "../lib/api";

const dashboard = {
  vendite_oggi: { importo: 463.5 },
  andamento_vendite: { totale_ultimo_giorno: 463.5, variazione_pct: -17.4 },
  saldi: { saldoCassaNegozioEVending: 59.95 },
  vending_da_caricare: 0,
  vending_totale: 84,
};

const autoOrder = {
  n_righe: 3,
  n_anomalie_stock: 1,
  riepilogo_stati: { "CONTROLLO MANUALE": 0 },
  parametri: { AGGIO_PCT: 0.1 },
  righe: [{ codice: "A1", descrizione: "Prodotto urgente", stato: "ORDINA ORA", magazzino_reale: 2, magazzino_reale_lordo: 2, giacenza: 2, prezzo: 5 }],
  esclusi: [],
};

const sales = days => ({
  giorni: days,
  totale: 16735.42,
  variazione_pct: 4.8,
  serie: [{ data: "2026-10-20", totale: 463.5 }],
  canali: [
    { nome: "NEGOZIO", importo: 12051.5 },
    { nome: "VENDING", importo: 4683.92 },
  ],
});

let container;
let root;

beforeEach(async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  api.get.mockImplementation(path => {
    if (path === "/dashboard") return Promise.resolve({ data: dashboard });
    if (path === "/anomalie") return Promise.resolve({ data: { summary: { totale: 2 } } });
    if (path === "/auto-order") return Promise.resolve({ data: autoOrder });
    if (path.startsWith("/report/vendite?giorni=")) return Promise.resolve({ data: sales(Number(path.split("=")[1])) });
    return Promise.reject(new Error(`Unexpected GET ${path}`));
  });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => root.render(<Dashboard />));
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  jest.clearAllMocks();
  delete global.IS_REACT_ACT_ENVIRONMENT;
});

test("mostra la dashboard operativa con dati reali e cambia periodo", async () => {
  expect(container.textContent).toContain("Dashboard operativa");
  expect(container.textContent).toContain("Vendite oggi");
  expect(container.textContent).toContain("84/84");
  expect(container.textContent).toContain("Prodotto urgente");
  expect(container.querySelector('[data-testid="dashboard-sales-chart"]')).not.toBeNull();

  await act(async () => container.querySelector('[data-testid="dashboard-period-365"]').click());

  expect(api.get).toHaveBeenCalledWith("/report/vendite?giorni=365");
  expect(container.querySelector('[data-testid="dashboard-period-365"]').getAttribute("aria-pressed")).toBe("true");
});
