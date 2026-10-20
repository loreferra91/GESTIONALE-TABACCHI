import { act } from "react";
import { createRoot } from "react-dom/client";

jest.mock("../components/Layout", () => ({ children, title }) => (
  <main><h1>{title}</h1>{children}</main>
));

jest.mock("../lib/api", () => ({
  api: {
    get: jest.fn(),
    post: jest.fn(),
    put: jest.fn(),
    delete: jest.fn(),
  },
  apiErrorMessage: (error, fallback) => error?.response?.data?.detail || fallback,
}));

jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn() },
}));

import SmartVenue from "./SmartVenue";
import { api } from "../lib/api";

const rows = [
  {
    id: "sv-negative", codice: "NEG", descrizione: "Saldo da aumentare",
    acquistati: 10, giacenza_negozio: 8, giacenza_vending: 2,
    rimanenze: 10, smart_venue: 7, carichi_pendenti: 0,
    differenza: -3, stato: "ENTRATA DA VERIFICARE", barcode: "100",
  },
  {
    id: "sv-positive", codice: "POS", descrizione: "Saldo da diminuire",
    acquistati: 10, giacenza_negozio: 6, giacenza_vending: 1,
    rimanenze: 7, smart_venue: 10, carichi_pendenti: 0,
    differenza: 3, stato: "USCITA DA CONFERMARE", barcode: "200",
  },
  {
    id: "sv-pending", codice: "PEND", descrizione: "Carico pendente",
    acquistati: 10, giacenza_negozio: 8, giacenza_vending: 0,
    rimanenze: 8, smart_venue: 6, carichi_pendenti: 2,
    differenza: -2, stato: "CARICO DA INSERIRE", barcode: "300",
  },
  {
    id: "sv-anomaly", codice: "VEND", descrizione: "Anomalia vending",
    acquistati: 10, giacenza_negozio: 2, giacenza_vending: 3,
    rimanenze: 5, smart_venue: 2, carichi_pendenti: 0,
    differenza: -3, stato: "ANOMALIA VENDING", barcode: "400",
  },
  {
    id: "sv-aligned", codice: "OK", descrizione: "Allineato",
    acquistati: 10, giacenza_negozio: 5, giacenza_vending: 0,
    rimanenze: 5, smart_venue: 5, carichi_pendenti: 0,
    differenza: 0, stato: "ALLINEATO", barcode: "500",
  },
];

let container;
let root;

const buttonWithText = text => Array.from(container.querySelectorAll("button"))
  .filter(button => button.textContent.includes(text))
  .pop();

beforeEach(async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  api.get.mockResolvedValue({ data: rows });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => root.render(<SmartVenue />));
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  jest.clearAllMocks();
  delete global.IS_REACT_ACT_ENVIRONMENT;
});

test("mostra solo le azioni compatibili con lo stato di riconciliazione", () => {
  expect(container.querySelector('[data-testid="smart-venue-entry-NEG"]')).not.toBeNull();
  expect(container.querySelector('[data-testid="smart-venue-confirm-POS"]')).not.toBeNull();
  expect(container.querySelector('[data-testid="smart-venue-insertion-NEG"]')).toBeNull();
  expect(container.querySelector('[data-testid="smart-venue-entry-PEND"]')).toBeNull();
  expect(container.querySelector('[data-testid="smart-venue-entry-VEND"]')).toBeNull();
  expect(container.querySelector('[data-testid="smart-venue-confirm-OK"]')).toBeNull();

  const headers = Array.from(container.querySelectorAll("thead th"))
    .map(header => header.textContent.trim());
  const stateIndex = headers.findIndex(header => header.startsWith("Stato"));
  const differenceIndex = headers.findIndex(header => header.startsWith("Differenza"));
  expect(stateIndex).toBeGreaterThanOrEqual(0);
  expect(differenceIndex).toBeGreaterThanOrEqual(0);
  expect(stateIndex).toBeLessThan(differenceIndex);
});

test("riusa la stessa chiave nel retry dell'entrata e mostra i saldi futuri", async () => {
  api.post
    .mockRejectedValueOnce({ response: { data: { detail: "errore temporaneo" } } })
    .mockResolvedValueOnce({ data: { quantita: 3 } });

  await act(async () => container.querySelector('[data-testid="smart-venue-entry-NEG"]').click());

  expect(container.querySelector('[data-testid="smart-venue-entry-quantity"]').value).toBe("3");
  expect(container.querySelector('[data-testid="smart-venue-entry-reason"]').value).toBe(
    "Riconciliazione saldo Smart Venue",
  );
  expect(container.querySelector('[data-testid="smart-venue-entry-next-balance"]').textContent).toBe("10");
  expect(container.querySelector('[data-testid="smart-venue-entry-next-difference"]').textContent).toBe("0");

  await act(async () => buttonWithText("Conferma entrata").click());
  expect(container.querySelector('[data-testid="smart-venue-entry-quantity"]')).not.toBeNull();

  await act(async () => buttonWithText("Conferma entrata").click());

  const entryCalls = api.post.mock.calls.filter(([path]) => path.includes("conferma-entrata"));
  expect(entryCalls).toHaveLength(2);
  expect(entryCalls[0][0]).toBe("/smart-venue/sv-negative/conferma-entrata");
  expect(entryCalls[0][1]).toEqual(expect.objectContaining({
    codice: "NEG",
    quantita: 3,
    saldo_atteso: 7,
    differenza_attesa: -3,
    idempotency_key: expect.any(String),
  }));
  expect(entryCalls[1][1].idempotency_key).toBe(entryCalls[0][1].idempotency_key);
  expect(api.get).toHaveBeenCalledTimes(2);
  expect(container.querySelector('[data-testid="smart-venue-entry-quantity"]')).toBeNull();
});

test("conferma l'uscita sul nuovo endpoint idempotente con riepilogo", async () => {
  api.post.mockResolvedValueOnce({ data: { quantita: 3 } });

  await act(async () => container.querySelector('[data-testid="smart-venue-confirm-POS"]').click());

  expect(container.querySelector('[data-testid="smart-venue-exit-quantity"]').value).toBe("3");
  expect(container.querySelector('[data-testid="smart-venue-exit-reason"]').value).toBe(
    "Uscita registrata in Smart Venue",
  );
  expect(container.querySelector('[data-testid="smart-venue-exit-next-balance"]').textContent).toBe("7");
  expect(container.querySelector('[data-testid="smart-venue-exit-next-difference"]').textContent).toBe("0");

  await act(async () => buttonWithText("Conferma uscita esterna").click());

  expect(api.post).toHaveBeenCalledWith(
    "/smart-venue/sv-positive/conferma-uscita",
    expect.objectContaining({
      codice: "POS",
      quantita: 3,
      motivazione: "Uscita registrata in Smart Venue",
      saldo_atteso: 10,
      differenza_attesa: 3,
      idempotency_key: expect.any(String),
    }),
  );
  expect(api.get).toHaveBeenCalledTimes(2);
});
