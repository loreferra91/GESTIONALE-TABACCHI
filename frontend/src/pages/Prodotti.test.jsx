import { act } from "react";
import { createRoot } from "react-dom/client";
jest.mock("react-router-dom", () => ({ useSearchParams: () => [new URLSearchParams()] }));
jest.mock("../components/Layout", () => ({ children, title }) => <main><h1>{title}</h1>{children}</main>);
jest.mock("../lib/api", () => ({
  api: { get: jest.fn(), patch: jest.fn(), post: jest.fn(), put: jest.fn(), delete: jest.fn() },
  apiErrorMessage: (_error, fallback) => fallback,
}));
jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));

import Prodotti from "./Prodotti";
import { api } from "../lib/api";

test("gestisce il minimo operativo in Prodotti e mostra la soglia calcolata", async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  let minimum = 0;
  api.get.mockImplementation(url => Promise.resolve({ data: url === "/prodotti"
    ? [{ id: "product-1", codice: "P", descrizione: "Prodotto P", categoria: "SIGARETTE",
      prezzo: 5, giacenza_negozio: 40, giacenza_vending: 2, auto_order_version: minimum ? 1 : 0,
      scorta_minima_negozio_auto_order: minimum },
      { id: "change-1", codice: "0000001", descrizione: "resto non erogato", categoria: "SIGARETTE", prezzo: 1 }]
    : { righe: [], esclusi: [{ codice: "P", punto_riordino: 20, target_scorta: 40 }] } }));
  api.patch.mockImplementation(() => {
    minimum = 50;
    return Promise.resolve({ data: { codice: "P", minimo: 50, version: 1 } });
  });
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => root.render(<Prodotti />));
  expect(container.querySelector('[data-testid="prod-row-P"]').textContent).toContain("20");
  expect(container.querySelector('[data-testid="prod-row-P"]').textContent).toContain("40");
  expect(container.querySelector('[data-testid="prod-minimum-edit-0000001"]')).toBeNull();
  await act(async () => container.querySelector('[data-testid="prod-minimum-edit-P"]').click());
  const input = container.querySelector('[data-testid="prod-minimum-input-P"]');
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    setter.call(input, "50");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => [...container.querySelectorAll("button")].find(button => button.textContent === "Salva").click());
  expect(api.patch).toHaveBeenCalledWith("/auto-order/prodotti/P/scorta-minima", { minimo: 50, version: 0 });
  expect(container.querySelector('[data-testid="prod-minimum-edit-P"]').textContent).toContain("50");
  await act(async () => root.unmount());
  container.remove();
  jest.clearAllMocks();
  delete global.IS_REACT_ACT_ENVIRONMENT;
});
