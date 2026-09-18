import { useEffect, useState, useRef } from "react";
import Layout from "../components/Layout";
import { api, API } from "../lib/api";
import { Card, Badge, formatEur, formatNum } from "../components/UI";
import { toast } from "sonner";

export default function Vendite() {
  const [rows, setRows] = useState([]);
  const [giorno, setGiorno] = useState(new Date().toISOString().slice(0, 10));
  const today = new Date().toISOString().slice(0, 10);
  const [form, setForm] = useState({ data: today, codice: "", descrizione: "", quantita: 1, importo: 0, canale: "NEGOZIO", pagamento: "CONTANTI" });
  const [tab, setTab] = useState("manuale"); // manuale | bulk | csv
  const [bulkText, setBulkText] = useState("");
  const [bulkCanale, setBulkCanale] = useState("NEGOZIO");
  const [bulkPagamento, setBulkPagamento] = useState("CONTANTI");
  const [bulkData, setBulkData] = useState(today);
  const [bulkResult, setBulkResult] = useState(null);
  const csvRef = useRef(null);
  const [csvResult, setCsvResult] = useState(null);
  const [csvPag, setCsvPag] = useState("CONTANTI");

  const load = async () => {
    const r = await api.get("/vendite", { params: { giorno } });
    setRows(r.data);
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [giorno]);

  const save = async () => {
    if (!form.codice || !form.importo) { toast.error("Compila codice e importo"); return; }
    await api.post("/vendite", form);
    toast.success("Vendita registrata");
    setForm({ ...form, codice: "", descrizione: "", quantita: 1, importo: 0 });
    load();
  };

  const del = async (id) => {
    if (!window.confirm("Eliminare?")) return;
    await api.delete(`/vendite/${id}`);
    toast.success("Eliminata");
    load();
  };

  const lookupPrezzo = async () => {
    if (!form.codice) return;
    try {
      const r = await api.get("/prodotti", { params: { q: form.codice } });
      const p = (r.data || []).find(x => x.codice === form.codice);
      if (p) setForm(f => ({ ...f, descrizione: p.descrizione, importo: p.prezzo * (f.quantita || 1) }));
    } catch {}
  };

  const tot = rows.reduce((s, r) => s + (r.importo || 0), 0);
  const pezzi = rows.reduce((s, r) => s + (r.quantita || 0), 0);

  const parseBulk = (text, defaultData) => {
    // TSV/CSV from Excel: data \t codice \t descrizione \t qta \t importo (o senza data)
    return text.split(/\r?\n/).map(l => l.trim()).filter(Boolean).map(line => {
      const parts = line.split(/\t|;|,/).map(x => x.trim());
      // heuristica: 5 col con data | 4 col (codice, desc, qta, importo)
      let data, codice, descrizione, qta, importo;
      if (parts.length >= 5) [data, codice, descrizione, qta, importo] = parts;
      else if (parts.length === 4) { [codice, descrizione, qta, importo] = parts; data = defaultData; }
      else if (parts.length === 3) { [codice, qta, importo] = parts; descrizione = ""; data = defaultData; }
      else return null;
      return {
        data: data || defaultData,
        codice, descrizione: descrizione || "",
        quantita: parseInt((qta || "0").replace(/[^\d-]/g, "")) || 0,
        importo: parseFloat((importo || "0").replace(",", ".").replace(/[^\d.-]/g, "")) || 0,
      };
    }).filter(Boolean);
  };

  const submitBulk = async () => {
    const righe = parseBulk(bulkText, bulkData);
    if (!righe.length) return toast.error("Nessuna riga valida");
    try {
      const r = await api.post("/vendite/bulk", { canale: bulkCanale, pagamento: bulkPagamento, righe });
      setBulkResult(r.data);
      toast.success(`Bulk: ${r.data.inseriti} inserite, ${r.data.saltati} saltate`);
      setBulkText("");
      load();
    } catch (e) {
      toast.error("Errore bulk");
    }
  };

  const uploadCsv = async (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    try {
      const r = await fetch(`${API}/vendite/import-csv-vending?pagamento=${csvPag}`, { method: "POST", body: fd });
      const j = await r.json();
      setCsvResult(j);
      toast.success(`CSV vending: ${j.inseriti} righe importate`);
      load();
    } catch {
      toast.error("Errore import CSV");
    } finally {
      csvRef.current.value = "";
    }
  };

  return (
    <Layout title="Vendite giornaliere" subtitle={`Registrazioni del ${giorno}`}>
      {/* Tab switcher */}
      <div className="flex gap-1 border-b border-slate-200 mb-6">
        {[
          { k: "manuale", l: "Inserimento manuale" },
          { k: "bulk", l: "Bulk paste da Excel" },
          { k: "csv", l: "Importa CSV Vending" },
        ].map(t => (
          <button
            key={t.k}
            data-testid={`vend-tab-${t.k}`}
            onClick={() => setTab(t.k)}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px ${
              tab === t.k ? "border-slate-900 text-slate-900" : "border-transparent text-slate-500 hover:text-slate-800"
            }`}
          >
            {t.l}
          </button>
        ))}
      </div>

      {tab === "manuale" && (
      <Card className="p-4 mb-6">
        <div className="grid grid-cols-1 md:grid-cols-7 gap-3">
          <input data-testid="vend-data" type="date" value={form.data} onChange={e => setForm({...form, data: e.target.value})} className="border rounded-md px-3 py-2 text-sm" />
          <input data-testid="vend-codice" placeholder="Codice" value={form.codice} onChange={e => setForm({...form, codice: e.target.value})} onBlur={lookupPrezzo} className="border rounded-md px-3 py-2 text-sm font-mono" />
          <input data-testid="vend-desc" placeholder="Descrizione" value={form.descrizione} onChange={e => setForm({...form, descrizione: e.target.value})} className="border rounded-md px-3 py-2 text-sm md:col-span-2" />
          <input data-testid="vend-qta" type="number" placeholder="Qta" value={form.quantita} onChange={e => setForm({...form, quantita: parseInt(e.target.value) || 1})} className="border rounded-md px-3 py-2 text-sm font-mono" />
          <input data-testid="vend-importo" type="number" step="0.01" placeholder="Importo €" value={form.importo} onChange={e => setForm({...form, importo: parseFloat(e.target.value) || 0})} className="border rounded-md px-3 py-2 text-sm font-mono" />
          <button data-testid="vend-save-btn" onClick={save} className="bg-slate-900 text-white rounded-md px-4 py-2 text-sm hover:bg-slate-800 transition-colors">Registra</button>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3">
          <select data-testid="vend-canale" value={form.canale} onChange={e => setForm({...form, canale: e.target.value})} className="border rounded-md px-3 py-2 text-sm">
            <option>NEGOZIO</option><option>VENDING</option>
          </select>
          <select data-testid="vend-pagamento" value={form.pagamento} onChange={e => setForm({...form, pagamento: e.target.value})} className="border rounded-md px-3 py-2 text-sm">
            <option>CONTANTI</option><option>POS</option><option>SATISPAY</option><option>ALTRO</option>
          </select>
        </div>
      </Card>
      )}

      {tab === "bulk" && (
      <Card className="p-6 mb-6">
        <h2 className="font-heading font-black text-lg mb-1">Bulk paste da Excel/CSV</h2>
        <p className="text-sm text-slate-600 mb-4">
          Incolla righe copiate da Excel — formato colonne (TAB / <code>;</code> / <code>,</code>):<br/>
          <code className="text-xs bg-slate-100 px-1.5 py-0.5 rounded">data · codice · descrizione · qtà · importo</code>
          &nbsp;oppure&nbsp;
          <code className="text-xs bg-slate-100 px-1.5 py-0.5 rounded">codice · descrizione · qtà · importo</code> (usa la data di default).
        </p>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3 mb-3">
          <input data-testid="bulk-data" type="date" value={bulkData} onChange={e => setBulkData(e.target.value)} className="border rounded-md px-3 py-2 text-sm" />
          <select data-testid="bulk-canale" value={bulkCanale} onChange={e => setBulkCanale(e.target.value)} className="border rounded-md px-3 py-2 text-sm">
            <option>NEGOZIO</option><option>VENDING</option>
          </select>
          <select data-testid="bulk-pagamento" value={bulkPagamento} onChange={e => setBulkPagamento(e.target.value)} className="border rounded-md px-3 py-2 text-sm">
            <option>CONTANTI</option><option>POS</option><option>SATISPAY</option><option>ALTRO</option>
          </select>
          <button data-testid="bulk-submit" onClick={submitBulk} disabled={!bulkText.trim()} className="bg-slate-900 text-white rounded-md px-4 py-2 text-sm hover:bg-slate-800 transition-colors disabled:opacity-40">
            Aggiorna vendite
          </button>
        </div>
        <textarea
          data-testid="bulk-text"
          value={bulkText}
          onChange={e => setBulkText(e.target.value)}
          rows={10}
          placeholder="AMMS338	WINSTON BLUE*AST20	5	29.00&#10;AMMS20659	TEREA AZURE	2	11.00&#10;..."
          className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm font-mono"
        />
        {bulkResult && (
          <div className="mt-3 text-sm bg-slate-50 border border-slate-200 rounded-md p-3">
            <span className="font-bold text-emerald-700">{bulkResult.inseriti}</span> inserite · <span className="text-slate-600">{bulkResult.saltati}</span> saltate
            {bulkResult.errori?.length > 0 && (
              <details className="mt-2"><summary className="text-red-600 cursor-pointer">Errori ({bulkResult.errori.length})</summary>
                <ul className="text-xs mt-2">{bulkResult.errori.slice(0, 20).map((e, i) => <li key={i}>Riga {e.riga}: {e.errore}</li>)}</ul>
              </details>
            )}
          </div>
        )}
      </Card>
      )}

      {tab === "csv" && (
      <Card className="p-6 mb-6">
        <h2 className="font-heading font-black text-lg mb-1">Importa CSV distributore vending</h2>
        <p className="text-sm text-slate-600 mb-4">
          Carica il file CSV esportato dal distributore. Colonne riconosciute (case-insensitive, separatore <code>,</code> <code>;</code> o TAB):<br/>
          <code className="text-xs bg-slate-100 px-1.5 py-0.5 rounded">data · nome prodotto · prezzo · colonna · codice AAMS · categoria · pagamento</code>
        </p>
        <div className="flex items-center gap-3">
          <select data-testid="csv-pag" value={csvPag} onChange={e => setCsvPag(e.target.value)} className="border rounded-md px-3 py-2 text-sm">
            <option>CONTANTI</option><option>POS</option><option>SATISPAY</option><option>ALTRO</option>
          </select>
          <input ref={csvRef} data-testid="csv-file" type="file" accept=".csv,text/csv" onChange={uploadCsv} className="text-sm" />
        </div>
        {csvResult && (
          <div className="mt-3 text-sm bg-slate-50 border border-slate-200 rounded-md p-3">
            <span className="font-bold text-emerald-700">{csvResult.inseriti}</span> righe · saltate {csvResult.saltati} · delimitatore <code>{csvResult.delimitatore}</code>
            {csvResult.errori?.length > 0 && (
              <details className="mt-2"><summary className="text-red-600 cursor-pointer">Errori ({csvResult.errori.length})</summary>
                <ul className="text-xs mt-2">{csvResult.errori.slice(0, 20).map((e, i) => <li key={i}>Riga {e.riga}: {e.errore}</li>)}</ul>
              </details>
            )}
          </div>
        )}
      </Card>
      )}

      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <label className="text-sm text-slate-600">Filtra giorno:</label>
          <input data-testid="vend-giorno-filter" type="date" value={giorno} onChange={e => setGiorno(e.target.value)} className="border rounded-md px-3 py-2 text-sm" />
        </div>
        <div className="flex gap-2 items-baseline">
          <Badge tone="info">{formatNum(pezzi)} pz</Badge>
          <span className="font-heading font-black text-xl text-slate-900">{formatEur(tot)}</span>
        </div>
      </div>

      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="data-table w-full">
            <thead>
              <tr><th>Ora</th><th>Codice</th><th>Descrizione</th><th className="text-right">Qta</th><th className="text-right">Importo</th><th>Canale</th><th>Pagamento</th><th></th></tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.id} data-testid={`vend-row-${r.id}`}>
                  <td className="font-mono text-xs text-slate-500">{r.data.slice(11, 16) || "—"}</td>
                  <td className="font-mono">{r.codice}</td>
                  <td className="max-w-sm truncate">{r.descrizione}</td>
                  <td className="font-mono text-right">{r.quantita}</td>
                  <td className="font-mono text-right font-semibold">{formatEur(r.importo)}</td>
                  <td><Badge tone={r.canale === "VENDING" ? "warning" : "info"}>{r.canale}</Badge></td>
                  <td className="text-xs">{r.pagamento}</td>
                  <td className="text-right"><button onClick={() => del(r.id)} className="text-red-600 hover:text-red-800 text-xs">✕</button></td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td colSpan={8} className="text-center py-8 text-slate-400">Nessuna vendita per {giorno}</td></tr>}
            </tbody>
          </table>
        </div>
      </Card>
    </Layout>
  );
}
