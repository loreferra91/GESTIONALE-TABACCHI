import { useEffect, useMemo, useState } from "react";
import Layout from "../components/Layout";
import { api } from "../lib/api";
import { Card, KpiCard, Badge, formatEur, formatNum } from "../components/UI";
import { toast } from "sonner";
import { Plus, Trash, Warning, Lightning } from "@phosphor-icons/react";

export default function Carico() {
  const [prodotti, setProdotti] = useState([]);
  const today = new Date().toISOString().slice(0, 10);
  const [data, setData] = useState(today);
  const [sorgente, setSorgente] = useState("carico manuale");
  const [righe, setRighe] = useState([]); // {codice, descrizione, quantita, prezzo}
  const [q, setQ] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.get("/prodotti", { params: { limit: 5000 } }).then(r => setProdotti(r.data));
  }, []);

  const prodMap = useMemo(() => {
    const m = new Map();
    prodotti.forEach(p => m.set(p.codice, p));
    return m;
  }, [prodotti]);

  const addProd = (p) => {
    setRighe(r => {
      const existing = r.find(x => x.codice === p.codice);
      if (existing) return r.map(x => x.codice === p.codice ? { ...x, quantita: (x.quantita || 0) + (p.categoria === "SIGARETTE" ? 10 : p.categoria === "SIGARETTE ELETTRONICHE" ? 5 : 1) } : x);
      const lotto = p.categoria === "SIGARETTE" ? 10 : p.categoria === "SIGARETTE ELETTRONICHE" ? 5 : 1;
      return [...r, { codice: p.codice, descrizione: p.descrizione, quantita: lotto, prezzo: p.prezzo || 0 }];
    });
    setQ("");
  };

  const addEmpty = () => setRighe(r => [...r, { codice: "", descrizione: "", quantita: 0, prezzo: 0 }]);
  const updateRow = (i, field, val) => setRighe(r => r.map((x, idx) => idx === i ? { ...x, [field]: val } : x));
  const removeRow = (i) => setRighe(r => r.filter((_, idx) => idx !== i));

  const onCodiceBlur = (i) => {
    const codice = (righe[i]?.codice || "").trim();
    if (!codice) return;
    const p = prodMap.get(codice);
    if (p) updateRow(i, "descrizione", righe[i].descrizione || p.descrizione), updateRow(i, "prezzo", righe[i].prezzo || p.prezzo);
  };

  const importFromAO = async () => {
    const r = await api.get("/auto-order");
    const nuove = (r.data.righe || []).map(x => ({
      codice: x.codice, descrizione: x.descrizione, quantita: x.qta_da_ordinare, prezzo: x.prezzo,
    }));
    setRighe(nuove);
    setSorgente("da auto-order (modificato)");
    toast.success(`${nuove.length} righe importate dall'Auto-Order — modifica le quantità e conferma`);
  };

  const totale = righe.reduce((s, r) => s + (r.quantita || 0) * (r.prezzo || 0), 0);
  const pezziTot = righe.reduce((s, r) => s + (r.quantita || 0), 0);
  const nonRiconosciute = righe.filter(r => r.codice && !prodMap.has(r.codice.trim())).length;

  const filtered = useMemo(() => {
    if (!q) return [];
    const lc = q.toLowerCase();
    return prodotti.filter(p => p.codice.toLowerCase().includes(lc) || (p.descrizione || "").toLowerCase().includes(lc)).slice(0, 8);
  }, [q, prodotti]);

  const carica = async () => {
    const valide = righe.filter(r => r.codice && r.quantita > 0);
    if (!valide.length) return toast.error("Nessuna riga valida");
    if (!window.confirm(`Confermi il carico di ${valide.length} righe per un totale di € ${totale.toFixed(2)}?\nGiacenze e Storico Ordini verranno aggiornati.`)) return;
    setSaving(true);
    try {
      const r = await api.post("/ordini/bulk", {
        file_sorgente: sorgente, data: new Date(data).toISOString(),
        righe: valide,
      });
      toast.success(`${r.data.caricate} righe caricate · ${r.data.prodotti_nuovi} prodotti nuovi creati`);
      setRighe([]);
    } catch {
      toast.error("Errore durante il carico");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Layout title="Carico Merce" subtitle="registra il ricevuto dal fornitore">
      <Card className="p-5 mb-6 bg-slate-50 border-slate-200">
        <div className="flex items-start gap-3">
          <Warning size={20} className="text-slate-600 mt-0.5" />
          <div className="text-sm text-slate-700 leading-relaxed">
            <strong>Come funziona:</strong> costruisci qui la lista di ciò che hai <b>ricevuto realmente</b> dal fornitore (può essere diverso dall'Auto-Order). Alla conferma:
            <ul className="list-disc list-inside mt-1 space-y-0.5 text-slate-600">
              <li>ogni riga viene registrata in <b>Storico ordini</b> con la data indicata,</li>
              <li>la <b>giacenza negozio</b> e i totali <b>acquistati</b> vengono incrementati,</li>
              <li>codici sconosciuti creano un nuovo prodotto automaticamente.</li>
            </ul>
          </div>
        </div>
      </Card>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <KpiCard label="Righe carico" value={formatNum(righe.length)} />
        <KpiCard label="Pezzi totali" value={formatNum(pezziTot)} />
        <KpiCard label="Codici sconosciuti" value={formatNum(nonRiconosciute)} tone={nonRiconosciute ? "warning" : "default"} />
        <KpiCard label="Totale carico" value={formatEur(totale)} tone="success" />
      </div>

      <Card className="p-4 mb-6">
        <div className="grid grid-cols-1 md:grid-cols-5 gap-3 mb-4">
          <div>
            <label className="text-xs font-bold uppercase tracking-wider text-slate-500 block mb-1">Data carico</label>
            <input data-testid="carico-data" type="date" value={data} onChange={e => setData(e.target.value)} className="border rounded-md px-3 py-2 text-sm w-full" />
          </div>
          <div className="md:col-span-2">
            <label className="text-xs font-bold uppercase tracking-wider text-slate-500 block mb-1">Sorgente / Bolla</label>
            <input data-testid="carico-sorgente" value={sorgente} onChange={e => setSorgente(e.target.value)} placeholder="es. ORDINE TABACCHI 18-02.xlsx / Bolla n.123" className="border rounded-md px-3 py-2 text-sm w-full" />
          </div>
          <div className="md:col-span-2 flex items-end gap-2">
            <button data-testid="carico-import-ao" onClick={importFromAO} className="flex items-center gap-2 bg-white border border-slate-300 text-slate-900 rounded-md px-3 py-2 text-sm hover:bg-slate-50 transition-colors">
              <Lightning size={16} /> Precompila da Auto-Order
            </button>
            <button data-testid="carico-add-empty" onClick={addEmpty} className="flex items-center gap-2 bg-white border border-slate-300 text-slate-900 rounded-md px-3 py-2 text-sm hover:bg-slate-50 transition-colors">
              <Plus size={16} /> Riga vuota
            </button>
          </div>
        </div>

        <div className="relative">
          <label className="text-xs font-bold uppercase tracking-wider text-slate-500 block mb-1">Aggiungi prodotto (cerca per codice o descrizione)</label>
          <input data-testid="carico-search" value={q} onChange={e => setQ(e.target.value)} placeholder="TEREA, MARLBORO, ELFBAR…" className="border rounded-md px-3 py-2 text-sm w-full" />
          {filtered.length > 0 && (
            <div className="absolute left-0 right-0 top-full mt-1 bg-white border border-slate-200 rounded-md shadow-lg z-20 max-h-72 overflow-auto">
              {filtered.map(p => (
                <button key={p.id} data-testid={`carico-add-${p.codice}`} onClick={() => addProd(p)} className="w-full text-left px-3 py-2 hover:bg-slate-50 border-b border-slate-100 last:border-b-0 flex justify-between items-center transition-colors">
                  <div>
                    <div className="text-sm font-medium">{p.descrizione}</div>
                    <div className="font-mono text-xs text-slate-500">{p.codice} · {p.categoria}</div>
                  </div>
                  <span className="font-mono text-sm font-semibold">{formatEur(p.prezzo)}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </Card>

      <Card className="overflow-hidden mb-6">
        <div className="overflow-x-auto">
          <table className="data-table w-full">
            <thead>
              <tr>
                <th>Codice</th><th>Descrizione</th>
                <th className="text-right">Quantità</th>
                <th className="text-right">Prezzo</th>
                <th className="text-right">Totale</th>
                <th>Stato</th><th></th>
              </tr>
            </thead>
            <tbody>
              {righe.length === 0 && <tr><td colSpan={7} className="text-center py-12 text-slate-400">Nessuna riga — cerca un prodotto, usa "Precompila da Auto-Order" o aggiungi una riga vuota.</td></tr>}
              {righe.map((r, i) => {
                const known = r.codice && prodMap.has(r.codice.trim());
                return (
                  <tr key={i} className={!known && r.codice ? "bg-red-50" : ""} data-testid={`carico-row-${i}`}>
                    <td>
                      <input value={r.codice} onChange={e => updateRow(i, "codice", e.target.value)} onBlur={() => onCodiceBlur(i)} className="border rounded-md px-2 py-1 text-sm font-mono w-28" data-testid={`carico-row-${i}-codice`} />
                    </td>
                    <td>
                      <input value={r.descrizione} onChange={e => updateRow(i, "descrizione", e.target.value)} className="border rounded-md px-2 py-1 text-sm w-full" />
                    </td>
                    <td className="text-right">
                      <input type="number" min={0} value={r.quantita} onChange={e => updateRow(i, "quantita", parseInt(e.target.value) || 0)} className="border rounded-md px-2 py-1 text-sm font-mono w-20 text-right" data-testid={`carico-row-${i}-qta`} />
                    </td>
                    <td className="text-right">
                      <input type="number" step="0.01" min={0} value={r.prezzo} onChange={e => updateRow(i, "prezzo", parseFloat(e.target.value) || 0)} className="border rounded-md px-2 py-1 text-sm font-mono w-24 text-right" />
                    </td>
                    <td className="text-right font-mono font-semibold">{formatEur((r.quantita || 0) * (r.prezzo || 0))}</td>
                    <td>
                      {r.codice ? (known
                        ? <Badge tone="ok">OK</Badge>
                        : <Badge tone="error">NUOVO</Badge>)
                       : <span className="text-xs text-slate-400">—</span>}
                    </td>
                    <td className="text-right">
                      <button onClick={() => removeRow(i)} className="text-red-600 hover:text-red-800"><Trash size={14} /></button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="flex justify-end gap-3">
        <button data-testid="carico-clear" onClick={() => setRighe([])} disabled={!righe.length} className="border border-slate-300 bg-white text-slate-900 rounded-md px-4 py-2 text-sm hover:bg-slate-50 transition-colors disabled:opacity-40">
          Svuota
        </button>
        <button data-testid="carico-conferma" onClick={carica} disabled={saving || righe.filter(r => r.codice && r.quantita > 0).length === 0} className="bg-emerald-600 hover:bg-emerald-700 text-white rounded-md px-6 py-2 text-sm font-bold transition-colors disabled:opacity-40">
          {saving ? "Carico in corso…" : `Carica in magazzino (${formatEur(totale)})`}
        </button>
      </div>
    </Layout>
  );
}
