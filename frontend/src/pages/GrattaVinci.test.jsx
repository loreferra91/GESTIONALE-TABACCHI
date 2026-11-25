import { act } from "react";
import { createRoot } from "react-dom/client";

jest.mock("../components/Layout", () => ({ title, children }) => <main><h1>{title}</h1>{children}</main>);
jest.mock("../lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), put: jest.fn() },
  apiErrorMessage: (_error, fallback) => fallback,
}));
jest.mock("../lib/idempotency", () => ({
  idempotencyKeyFor: jest.fn(() => "test-idempotency-key-1234"),
  clearIdempotencyKey: jest.fn(),
}));
jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));

import GrattaVinci from "./GrattaVinci";
import { api } from "../lib/api";
import { clearIdempotencyKey, idempotencyKeyFor } from "../lib/idempotency";

test("Gratta e Vinci shows separate stock and records sales with an idempotency key", async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  idempotencyKeyFor.mockReturnValue("test-idempotency-key-1234");
  const rows = [{
    id: "gv-1", codice: "GV001", descrizione: "Biglietto 5 euro",
    prezzo_centesimi: 500, quantita_magazzino: 12,
    venduti: 3, totale_centesimi: 1500, version: 0,
  }];
  api.get.mockResolvedValue({
    data: { righe: rows, totale_biglietti: 3, totale_centesimi: 1500 },
  });
  api.post.mockResolvedValue({ data: { gia_registrato: false } });

  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(<GrattaVinci />));
    expect(container.querySelector("h1").textContent).toBe("Gratta e Vinci");
    expect(container.querySelector('[data-testid="gv-row-GV001"]').textContent).toContain("12");

    await act(async () => {
      [...container.querySelectorAll('[role="tab"]')]
        .find(button => button.textContent === "Registrazione vendite").click();
    });
    expect(container.querySelector('[data-testid="gv-sold-total"]').textContent).toBe("3");
    expect(container.querySelector('[data-testid="gv-revenue-total"]').textContent).toContain("15");

    await act(async () => container.querySelector('[data-testid="gv-action-vendita:GV001"]').click());
    expect(api.post).toHaveBeenCalledWith("/gratta-e-vinci/vendite", {
      codice: "GV001", quantita: 1, idempotency_key: "test-idempotency-key-1234",
    });
    expect(clearIdempotencyKey).toHaveBeenCalledWith("gratta-vinci:vendita:GV001");
  } finally {
    await act(async () => root.unmount());
    container.remove();
    jest.clearAllMocks();
    delete global.IS_REACT_ACT_ENVIRONMENT;
  }
});

test("product columns toggle ascending and descending without mutating stock or sales", async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const rows = [
    { id: "1", codice: "GV10", descrizione: "Alfa", prezzo_centesimi: 1000, quantita_magazzino: 2 },
    { id: "2", codice: "GV2", descrizione: "Zeta", prezzo_centesimi: 200, quantita_magazzino: 10 },
    { id: "3", codice: "GV1", descrizione: "Beta", prezzo_centesimi: 500, quantita_magazzino: 0 },
  ];
  api.get.mockResolvedValue({ data: { righe: rows } });
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  const order = () => [...container.querySelectorAll('tbody tr[data-testid]')].map(row => row.firstElementChild.textContent);
  const click = async label => act(async () => [...container.querySelectorAll("th button")].find(b => b.textContent.startsWith(label)).click());
  try {
    await act(async () => root.render(<GrattaVinci section="magazzino" />));
    expect(order()).toEqual(["GV1", "GV2", "GV10"]);
    await click("Codice");
    expect(order()).toEqual(["GV10", "GV2", "GV1"]);
    await click("Descrizione");
    expect(order()).toEqual(["GV10", "GV1", "GV2"]);
    await click("Descrizione");
    expect(order()).toEqual(["GV2", "GV1", "GV10"]);
    await click("Prezzo");
    expect(order()).toEqual(["GV2", "GV1", "GV10"]);
    await click("Prezzo");
    expect(order()).toEqual(["GV10", "GV1", "GV2"]);
    await click("Quantità disponibile");
    expect(order()).toEqual(["GV1", "GV10", "GV2"]);
    await click("Quantità disponibile");
    expect(order()).toEqual(["GV2", "GV10", "GV1"]);
    expect(container.querySelector('th[aria-sort="descending"]').textContent).toContain("Quantità disponibile");
    expect(rows.map(row => row.codice)).toEqual(["GV10", "GV2", "GV1"]);
    expect(api.post).not.toHaveBeenCalled();
    expect(api.put).not.toHaveBeenCalled();
  } finally {
    await act(async () => root.unmount());
    container.remove();
    jest.clearAllMocks();
    delete global.IS_REACT_ACT_ENVIRONMENT;
  }
});
