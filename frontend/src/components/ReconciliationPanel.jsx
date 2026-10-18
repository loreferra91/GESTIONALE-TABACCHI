import { useState } from "react";
import { api, apiErrorMessage } from "../lib/api";
import { Card } from "./UI";

export default function ReconciliationPanel() {
  const [report, setReport] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const run = async () => {
    if (busy) return;
    setBusy(true);
    setError("");
    setReport(null);
    try {
      const response = await api.get("/contabilita/riconciliazione");
      setReport(response.data);
    } catch (failure) {
      setError(apiErrorMessage(failure, "Controllo non riuscito. Riprovare."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="mb-5 p-5">
      <h2 className="font-heading text-lg font-black">Controllo di riconciliazione</h2>
      <p className="my-2 text-sm text-slate-600">
        Verifica importi, giacenze vending e corrispondenze CSV senza modificare i dati.
        Eseguire a operazioni ferme. Non sostituisce il conteggio fisico o una verifica contabile completa.
      </p>
      <button type="button" onClick={run} disabled={busy}
        className="rounded-md border border-slate-300 px-3 py-2 text-sm font-semibold disabled:opacity-50">
        {busy ? "Controllo in corso…" : "Esegui controllo"}
      </button>
      {error && <p role="alert" className="mt-3 text-red-700">{error}</p>}
      {report && <div className="mt-4" aria-live="polite">
        <p className="font-semibold">
          {report.totale_segnalazioni
            ? `${report.totale_segnalazioni} segnalazioni da verificare`
            : "Nessuna discrepanza rilevata nei controlli eseguiti"}
        </p>
        <p className="my-2 text-sm text-slate-600">
          {new Date(report.generated_at).toLocaleString("it-IT")} · {report.prodotti_confrontati} prodotti confrontati · {report.vendite_csv_confrontate} vendite CSV confrontate · {report.vendite_csv_non_confrontabili} non confrontabili (non necessariamente errate).
        </p>
        <ul className="space-y-2 text-sm">
          {report.dettagli.map((item, index) => <li key={index} className="rounded border border-amber-200 bg-amber-50 p-3">
            <b>{item.raccolta} · {item.riferimento}</b>: {item.messaggio}
            {item.tipo === "IMPORTO_PAGAMENTO_CSV" && <div>
              Vendita: {item.registrato} € ({item.pagamento_vendita || "non indicato"}); registro: {item.atteso} € ({item.pagamento_registro || "non indicato"}).
            </div>}
            {item.tipo === "FRAZIONI_CENTESIMO" && <div>Valore: {item.valore}; ai centesimi: {item.normalizzato} €.</div>}
          </li>)}
        </ul>
        {report.dettagli_omessi > 0 && <p className="mt-2 text-sm">Altre {report.dettagli_omessi} segnalazioni non mostrate. Il conteggio comprende tutti i documenti esaminati.</p>}
        <details className="mt-3 text-sm text-slate-600">
          <summary className="cursor-pointer">Copertura e limiti del controllo</summary>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            {[...(report.copertura || []), ...report.limiti].map((text) => <li key={text}>{text}</li>)}
          </ul>
        </details>
      </div>}
    </Card>
  );
}
