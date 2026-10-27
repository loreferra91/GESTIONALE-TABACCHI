import { act } from "react";
import { createRoot } from "react-dom/client";

jest.mock("react-router-dom", () => ({
  Link: ({ children, ...props }) => <a {...props}>{children}</a>,
}));

jest.mock("../lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn() },
}));

jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn() },
  Toaster: () => null,
}));

import CassaRapida from "./CassaRapida";
import { api } from "../lib/api";

const product = {
  id: "product-1",
  codice: "AMMS001",
  descrizione: "Prodotto test",
  categoria: "SIGARETTE",
  prezzo: 5.5,
  giacenza_negozio: 10,
  giacenza_vending: 0,
};

let container;
let root;

beforeEach(async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  api.get.mockImplementation((path) => {
    if (path === "/prodotti") return Promise.resolve({ data: [product] });
    if (path === "/prodotti/top") return Promise.resolve({ data: [product] });
    return Promise.reject(new Error(`Unexpected GET ${path}`));
  });
  api.post.mockResolvedValue({ data: { inseriti: 1 } });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => root.render(<CassaRapida />));
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  jest.clearAllMocks();
  delete global.IS_REACT_ACT_ENVIRONMENT;
});

test("crea un checkout atomico con quantità aggregata, pagamento e chiave di idempotenza", async () => {
  const productButton = container.querySelector('[data-testid="pos-btn-AMMS001"]');
  await act(async () => {
    productButton.click();
    productButton.click();
  });

  expect(container.querySelector('[data-testid="cart-row-AMMS001"]')).not.toBeNull();
  expect(container.textContent).toContain("2 pezzi");
  expect(container.textContent).toContain("11,00");

  await act(async () => container.querySelector('[data-testid="pos-pag-POS"]').click());
  await act(async () => container.querySelector('[data-testid="pos-conferma"]').click());

  expect(api.post).toHaveBeenCalledTimes(1);
  const [path, payload] = api.post.mock.calls[0];
  expect(path).toBe("/vendite/cassa-rapida");
  expect(payload).toEqual(expect.objectContaining({
    pagamento: "POS",
    righe: [{
      codice: "AMMS001",
      descrizione: "Prodotto test",
      quantita: 2,
      prezzo: 5.5,
    }],
  }));
  expect(payload.data).toEqual(expect.any(String));
  expect(payload.idempotency_key).toEqual(expect.any(String));
  expect(payload.idempotency_key.length).toBeGreaterThanOrEqual(10);
  expect(container.querySelector('[data-testid="cart-row-AMMS001"]')).toBeNull();
});

test("mantiene il carrello quando il checkout viene rifiutato", async () => {
  api.post.mockRejectedValueOnce(new Error("offline"));
  await act(async () => container.querySelector('[data-testid="pos-btn-AMMS001"]').click());
  await act(async () => container.querySelector('[data-testid="pos-conferma"]').click());

  expect(api.post).toHaveBeenCalledWith(
    "/vendite/cassa-rapida",
    expect.objectContaining({ righe: [expect.objectContaining({ codice: "AMMS001", quantita: 1 })] }),
  );
  expect(container.querySelector('[data-testid="cart-row-AMMS001"]')).not.toBeNull();
  expect(container.textContent).toContain("1 pezzi");
});

test("annulla l'ultima vendita confermata", async () => {
  api.post.mockResolvedValueOnce({ data: { id: "batch-1", totale: 5.5 } });
  window.confirm = jest.fn(() => true);
  await act(async () => container.querySelector('[data-testid="pos-btn-AMMS001"]').click());
  await act(async () => container.querySelector('[data-testid="pos-conferma"]').click());

  const undo = container.querySelector('[data-testid="pos-undo-last"]');
  expect(undo).not.toBeNull();
  await act(async () => undo.click());

  expect(window.confirm).toHaveBeenCalled();
  expect(api.post).toHaveBeenLastCalledWith("/vendite/cassa-rapida/batch-1/annulla");
  expect(container.querySelector('[data-testid="pos-undo-last"]')).toBeNull();
});
