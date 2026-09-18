import { useEffect, useState, useRef } from "react";
import Layout from "../components/Layout";
import { api, API } from "../lib/api";
import { Card } from "../components/UI";
import { toast } from "sonner";

export default function Parametri() {
  const [rows, setRows] = useState([]);
  const [importing, setImporting] = useState(false);
  const fileRef = useRef(null);

  const load = async () => {
    const r = await api.get("/parametri");
    setRows(r.data);
  };
  useEffect(() => { load(); }, []);

  const update = async (nome, valore) => {
    await api.put(`/parametri/${nome}`, { valore: parseFloat(valore) });
    toast.success(`${nome} aggiornato`);
    load();
  };

  const upload = async (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    setImporting(true);
    const fd = new FormData();
    fd.append("file", f);
    try {
      const r = await fetch(`${API}/import/excel`, { method: "POST", body: fd });
      const j = await r.json();
      toast.success(`Import: ${j.inseriti} nuovi, ${j.aggiornati} aggiornati`);
    } catch {
      toast.error("Errore import");
    } finally {
      setImporting(false);
      fileRef.current.value = "";
    }
  };

  return (
    <Layout title="Parametri di sistema" subtitle="configurazione motore auto-order">
      <Card className="p-6 mb-6">
        <h2 className="font-heading font-black text-lg mb-2">Import Excel</h2>
        <p className="text-sm text-slate-600 mb-4">Carica un file .xlsm/.xlsx (foglio RIEP_VENDITA) per aggiornare prodotti e giacenze in massa.</p>
        <input ref={fileRef} data-testid="import-file" type="file" accept=".xlsx,.xlsm" onChange={upload} className="text-sm" />
        {importing && <span className="ml-3 text-sm text-slate-500">Elaborazione…</span>}
      </Card>

      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="data-table w-full">
            <thead><tr><th>Parametro</th><th>Descrizione</th><th className="text-right">Valore</th><th></th></tr></thead>
            <tbody>
              {rows.map(p => (
                <tr key={p.nome} data-testid={`param-row-${p.nome}`}>
                  <td className="font-mono font-semibold">{p.nome}</td>
                  <td className="text-slate-600">{p.descrizione}</td>
                  <td className="font-mono text-right">
                    <input
                      data-testid={`param-input-${p.nome}`}
                      type="number"
                      step="0.01"
                      defaultValue={p.valore}
                      className="border rounded-md px-3 py-1 text-sm font-mono w-32 text-right"
                      onBlur={(e) => parseFloat(e.target.value) !== Number(p.valore) && update(p.nome, e.target.value)}
                    />
                  </td>
                  <td></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </Layout>
  );
}
