import { act } from "react";
import { createRoot } from "react-dom/client";
import ReconciliationPanel from "./ReconciliationPanel";
import { api } from "../lib/api";

jest.mock("../lib/api", () => ({
  api: { get: jest.fn() },
  apiErrorMessage: (_error, fallback) => fallback,
}));

let container;
let root;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  jest.clearAllMocks();
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  delete global.IS_REACT_ACT_ENVIRONMENT;
});

const report = {
  generated_at: "2026-10-18T10:00:00Z", totale_segnalazioni: 0,
  prodotti_confrontati: 2, vendite_csv_confrontate: 3,
  vendite_csv_non_confrontabili: 5, dettagli: [], dettagli_omessi: 0,
  copertura: ["Importi e giacenze"], limiti: ["Non è una certificazione contabile"],
};

test("controllo esplicito, stato occupato e risultato senza certificare le righe non confrontabili", async () => {
  let complete;
  api.get.mockReturnValue(new Promise(resolve => { complete = resolve; }));
  await act(async () => root.render(<ReconciliationPanel />));
  expect(api.get).not.toHaveBeenCalled();
  await act(async () => container.querySelector("button").click());
  expect(container.querySelector("button").disabled).toBe(true);
  expect(api.get).toHaveBeenCalledWith("/contabilita/riconciliazione", { params: {} });
  await act(async () => complete({ data: report }));
  expect(container.textContent).toContain("Nessuna discrepanza rilevata nei controlli eseguiti");
  expect(container.textContent).toContain("5 non confrontabili (non necessariamente errate)");
  expect(container.textContent).toContain("Non è una certificazione contabile");
  expect(container.querySelector("button").disabled).toBe(false);
});

test("mostra riferimenti, importi, pagamento e dettagli omessi", async () => {
  api.get.mockResolvedValue({ data: { ...report, totale_segnalazioni: 101, dettagli_omessi: 100,
    dettagli: [{ tipo: "IMPORTO_PAGAMENTO_CSV", raccolta: "vendite", riferimento: "sale-1", messaggio: "Importi diversi", registrato: 1.01, atteso: 1, pagamento_vendita: "CARTE", pagamento_registro: "CONTANTI" }],
  } });
  await act(async () => root.render(<ReconciliationPanel />));
  await act(async () => container.querySelector("button").click());
  expect(container.textContent).toContain("101 segnalazioni da verificare");
  expect(container.textContent).toContain("sale-1");
  expect(container.textContent).toContain("1.01 € (CARTE)");
  expect(container.textContent).toContain("Altre 100 segnalazioni non mostrate");
});

test("un errore non lascia visibile un precedente risultato pulito", async () => {
  api.get.mockResolvedValueOnce({ data: report }).mockRejectedValueOnce(new Error("offline"));
  await act(async () => root.render(<ReconciliationPanel />));
  await act(async () => container.querySelector("button").click());
  await act(async () => container.querySelector("button").click());
  expect(container.querySelector('[role="alert"]').textContent).toContain("Controllo non riuscito");
  expect(container.textContent).not.toContain("Nessuna discrepanza rilevata");
  expect(container.querySelector("button").disabled).toBe(false);
});
