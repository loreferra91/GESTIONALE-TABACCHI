import { act } from "react";
import { createRoot } from "react-dom/client";

jest.mock("../components/Layout", () => ({ children, title, subtitle, actions }) => (
  <main><h1>{title}</h1><p>{subtitle}</p>{actions}{children}</main>
));

jest.mock("recharts", () => ({
  ResponsiveContainer: ({ children }) => <div>{children}</div>,
  BarChart: ({ children }) => <div>{children}</div>,
  Bar: () => null,
  CartesianGrid: () => null,
  Legend: () => null,
  Tooltip: () => null,
  XAxis: () => null,
  YAxis: () => null,
}));

jest.mock("../lib/api", () => ({
  api: { get: jest.fn() },
  apiErrorMessage: (_error, fallback) => fallback,
}));

jest.mock("sonner", () => ({ toast: { error: jest.fn() } }));

import Report from "./Report";
import { api } from "../lib/api";

const daily = {
  dashboard: {
    kpi: { da_riordinare: 2 },
    vendite_oggi: { importo: 20, pezzi: 4 },
    saldo_cassa: 100,
    vending_da_caricare: 1,
    vending_totale: 8,
  },
  auto_order: { totale: 50, righe: 3, da_controllare: 1 },
  anomalie: { alta: 0, media: 1 },
  status: { latest_sales_day: "2026-10-19", sales_data_delay_days: 0, counts: {} },
};

function period(giorni) {
  return {
    giorni,
    data_da: giorni === 7 ? "2026-10-13" : "2026-09-20",
    data_a: "2026-10-19",
    totale: 120,
    totale_precedente: 100,
    variazione_pct: 20,
    pezzi: 24,
    media_giornaliera: 120 / giorni,
    giorni_con_vendite: 5,
    serie: [{ data: "2026-10-19", negozio: 80, vending: 40, totale: 120 }],
    pagamenti: [{ nome: "POS", importo: 80, pezzi: 16 }],
    prodotti: [{ codice: "A1", descrizione: "Prodotto A", importo: 120, pezzi: 24 }],
  };
}

let container;
let root;

beforeEach(async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  api.get.mockImplementation((path) => {
    if (path === "/report/giornaliero") return Promise.resolve({ data: daily });
    if (path === "/report/vendite?giorni=7") return Promise.resolve({ data: period(7) });
    if (path === "/report/vendite?giorni=30") return Promise.resolve({ data: period(30) });
    return Promise.reject(new Error(`Unexpected GET ${path}`));
  });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => root.render(<Report />));
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  jest.clearAllMocks();
  delete global.IS_REACT_ACT_ENVIRONMENT;
});

test("mostra il report settimanale e passa al periodo mensile", async () => {
  expect(container.textContent).toContain("Report vendite");
  expect(container.textContent).toContain("Prodotto A");
  expect(container.textContent).toContain("POS");
  expect(container.textContent).toContain("+20%");
  expect(container.querySelector('[data-testid="report-sales-chart"]')).not.toBeNull();

  await act(async () => container.querySelector('[data-testid="report-period-30"]').click());

  expect(api.get).toHaveBeenCalledWith("/report/vendite?giorni=30");
  expect(container.querySelector('[data-testid="report-period-30"]').getAttribute("aria-pressed")).toBe("true");
});

test("esporta la serie visualizzata in CSV", async () => {
  const createObjectURL = jest.fn(() => "blob:report");
  const revokeObjectURL = jest.fn();
  const click = jest.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  URL.createObjectURL = createObjectURL;
  URL.revokeObjectURL = revokeObjectURL;

  await act(async () => container.querySelector('[data-testid="report-export-csv"]').click());

  expect(createObjectURL).toHaveBeenCalledTimes(1);
  expect(click).toHaveBeenCalledTimes(1);
  expect(revokeObjectURL).toHaveBeenCalledWith("blob:report");
  click.mockRestore();
});
