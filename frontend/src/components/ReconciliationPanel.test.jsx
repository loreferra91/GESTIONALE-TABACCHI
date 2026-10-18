import { act } from "react";
import { createRoot } from "react-dom/client";
import ReconciliationPanel from "./ReconciliationPanel";
import { api } from "../lib/api";

jest.mock("../lib/api", () => ({
  api: { get: jest.fn(), put: jest.fn() },
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
    conteggi_stato: { aperta: 101 }, totale_da_gestire: 101,
    dettagli: [{ fingerprint: "a".repeat(64), stato: "aperta", tipo: "IMPORTO_PAGAMENTO_CSV", raccolta: "vendite", riferimento: "sale-1", messaggio: "Importi diversi", registrato: 1.01, atteso: 1, pagamento_vendita: "CARTE", pagamento_registro: "CONTANTI" }],
  } });
  await act(async () => root.render(<ReconciliationPanel />));
  await act(async () => container.querySelector("button").click());
  expect(container.textContent).toContain("101 segnalazioni da verificare");
  expect(container.textContent).toContain("sale-1");
  expect(container.textContent).toContain("1.01 € (CARTE)");
  expect(container.textContent).toContain("Altre 100 segnalazioni non mostrate");
});

test("prende in carico una segnalazione senza rilanciare il controllo", async () => {
  const finding = { fingerprint: "b".repeat(64), stato: "aperta", tipo: "IMPORTO_INVALIDO", raccolta: "versamenti", riferimento: "movement-1", messaggio: "Importo non valido" };
  api.get.mockResolvedValue({ data: { ...report, totale_segnalazioni: 1, totale_da_gestire: 1, conteggi_stato: { aperta: 1 }, dettagli: [finding] } });
  api.put.mockResolvedValue({ data: { ...finding, stato: "in_verifica", nota: "Verificare ricevuta", updated_at: "2026-10-18T11:00:00Z", operatore: "lorenzo" } });
  await act(async () => root.render(<ReconciliationPanel />));
  await act(async () => container.querySelector("button").click());
  const select = container.querySelector('select[aria-label="Stato movement-1"]');
  const note = container.querySelector('input[aria-label="Nota movement-1"]');
  await act(async () => {
    select.value = "in_verifica";
    select.dispatchEvent(new Event("change", { bubbles: true }));
    const setInputValue = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    setInputValue.call(note, "Verificare ricevuta");
    note.dispatchEvent(new Event("input", { bubbles: true }));
  });
  const save = [...container.querySelectorAll("button")].find(button => button.textContent === "Salva stato");
  await act(async () => save.click());
  expect(api.put).toHaveBeenCalledWith(`/contabilita/riconciliazione/segnalazioni/${finding.fingerprint}`, {
    stato: "in_verifica", nota: "Verificare ricevuta", tipo: "IMPORTO_INVALIDO", raccolta: "versamenti", riferimento: "movement-1",
  });
  expect(api.get).toHaveBeenCalledTimes(1);
  expect(container.textContent).toContain("In verifica: 1");
  expect(container.querySelector('input[aria-label="Nota movement-1"]').value).toBe("Verificare ricevuta");
  expect(container.textContent).toContain("lorenzo");
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
