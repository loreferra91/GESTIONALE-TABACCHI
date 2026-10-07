import { useEffect, useMemo, useState } from "react";
import Layout from "../components/Layout";
import { api, API, apiErrorMessage } from "../lib/api";
import { Card, Badge, KpiCard } from "../components/UI";
import { useSortSearch, Th, SearchBar } from "../lib/tableHooks";
import { toast } from "sonner";
import { CheckCircle, FloppyDisk, Plus, Printer } from "@phosphor-icons/react";
import ConfirmDialog from "../components/ConfirmDialog";

const EMPTY_VENDING_FORM = {
  colonna: "",
  codice: "",
  capacita_max: "5",
  soglia_minima: "2",
  giacenza_iniziale: "0",
};

export default function Vending() {
  const [allRows, setAllRows] = useState([]);
  const [products, setProducts] = useState([]);
  const [quantitaManuali, setQuantitaManuali] = useState({});
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [bulkLoading, setBulkLoading] = useState(false);
  const [giacenzeManuali, setGiacenzeManuali] = useState({});
  const [savingGiacenza, setSavingGiacenza] = useState(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [productQuery, setProductQuery] = useState("");
  const [newVending, setNewVending] = useState(EMPTY_VENDING_FORM);

  const load = async () => {
    try {
      const [vendingResponse, productsResponse] = await Promise.all([
        api.get("/vending"),
        api.get("/prodotti", { params: { limit: 5000 } }),
      ]);
      setAllRows(vendingResponse.data || []);
      setProducts(productsResponse.data || []);
    } catch (err) {
      toast.error(apiErrorMessage(err, "Impossibile caricare i dati vending"));
    }
  };
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const { rows, sortKey, sortDir, toggle, query, setQuery } = useSortSearch(allRows, {
    initial: "colonna", dir: "asc",
    searchFields: ["colonna", "codice", "descrizione", "esito"],
  });

  const selectedProduct = useMemo(
    () => products.find(product => product.codice === newVending.codice) || null,
    [products, newVending.codice],
  );
  const productMatches = useMemo(() => {
    const normalized = productQuery.trim().toLowerCase();
    if (!normalized) return products.slice(0, 8);
    return products.filter(product =>
      String(product.codice || "").toLowerCase().includes(normalized)
      || String(product.descrizione || "").toLowerCase().includes(normalized)
    ).slice(0, 8);
  }, [productQuery, products]);

  const openCreate = () => {
    setNewVending(EMPTY_VENDING_FORM);
    setProductQuery("");
    setCreateOpen(true);
  };

  const closeCreate = () => {
    if (creating) return;
    setCreateOpen(false);
  };

  const createVendingProduct = async () => {
    const colonna = newVending.colonna.trim().toUpperCase();
    const capacita = Number(newVending.capacita_max);
    const soglia = Number(newVending.soglia_minima);
    const iniziale = Number(newVending.giacenza_iniziale);
    if (!/^[A-Z]\d+$/.test(colonna)) {
      toast.error("Inserisci una colonna valida, ad esempio A01");
      return;
    }
    if (!selectedProduct) {
      toast.error("Seleziona un prodotto del gestionale");
      return;
    }
    if (![capacita, soglia, iniziale].every(Number.isInteger)) {
      toast.error("Capacità, soglia e giacenza iniziale devono essere numeri interi");
      return;
    }
    if (capacita <= 0 || soglia < 0 || soglia > capacita || iniziale < 0 || iniziale > capacita) {
      toast.error("Controlla capacità, soglia e giacenza iniziale");
      return;
    }
    setCreating(true);
    try {
      const response = await api.post("/vending", {
        colonna,
        codice: selectedProduct.codice,
        capacita_max: capacita,
        soglia_minima: soglia,
        giacenza_iniziale: iniziale,
      });
      toast.success(`${response.data.codice} aggiunto alla colonna ${response.data.colonna}`);
      setCreateOpen(false);
      setNewVending(EMPTY_VENDING_FORM);
      setProductQuery("");
      await load();
    } catch (err) {
      toast.error(apiErrorMessage(err, "Impossibile aggiungere il prodotto alla vending"));
    } finally {
      setCreating(false);
    }
  };

  const quantitaEffettiva = (v) => {
    const manuale = quantitaManuali[v.id];
    return manuale === undefined || manuale === "" ? Number(v.proposta || 0) : Number(manuale);
  };

  const setQuantitaManuale = (id, value) => {
    setQuantitaManuali(current => ({ ...current, [id]: value }));
  };

  const salvaGiacenza = async (v) => {
    const value = Number(giacenzeManuali[v.id] ?? v.giacenza);
    if (!Number.isInteger(value) || value < 0 || value > v.capacita_max) {
      toast.error(`Inserisci una giacenza intera tra 0 e ${v.capacita_max} per la colonna ${v.colonna}`);
      return;
    }
    setSavingGiacenza(v.id);
    try {
      const response = await api.put(`/vending/${v.id}/giacenza`, { giacenza: value });
      toast.success(`Colonna ${v.colonna}: giacenza corretta da ${response.data.giacenza_precedente} a ${response.data.giacenza}`);
      setGiacenzeManuali(current => {
        const next = { ...current };
        delete next[v.id];
        return next;
      });
      await load();
    } catch (err) {
      toast.error(apiErrorMessage(err, "Impossibile correggere la giacenza"));
    } finally {
      setSavingGiacenza(null);
    }
  };

  const rica = async (v) => {
    const q = window.prompt(`Ricarica colonna ${v.colonna} — vending ${v.giacenza}/${v.capacita_max}, negozio libero ${v.giacenza_magazzino}, riserva protetta ${v.scorta_minima_negozio}. Quanti pezzi?`, quantitaEffettiva(v));
    if (!q) return;
    const quantita = Number(q);
    if (!Number.isInteger(quantita) || quantita <= 0) {
      toast.error("Inserisci una quantità intera maggiore di zero");
      return;
    }
    try {
      const response = await api.post(`/vending/${v.id}/ricarica`, { quantita });
      toast.success(`Colonna ${v.colonna}: caricati ${response.data.quantita_caricata} pezzi`);
      setQuantitaManuali(current => {
        const next = { ...current };
        delete next[v.id];
        return next;
      });
      load();
    } catch (err) {
      toast.error(apiErrorMessage(err, "Impossibile ricaricare la colonna"));
    }
  };

  const proposteDaCaricare = allRows.filter(r => (r.proposta || 0) > 0);
  const righeDaCaricare = allRows.filter(r => quantitaEffettiva(r) > 0);
  const daCaricare = righeDaCaricare.length;
  const scortaProtetta = allRows.filter(r => r.esito === "SCORTA NEGOZIO").length;
  const piene = allRows.filter(r => r.esito === "PIENO").length;
  const totDaCaricare = righeDaCaricare.reduce((s, r) => s + (quantitaEffettiva(r) || 0), 0);
  const righeSenzaQuantitaManuale = righeDaCaricare.filter(r => quantitaManuali[r.id] === undefined || quantitaManuali[r.id] === "").length;

  const apriConfermaCompleta = () => {
    const nonValida = righeDaCaricare.find(r => {
      const quantita = quantitaEffettiva(r);
      return !Number.isInteger(quantita) || quantita <= 0 || quantita > r.fabbisogno;
    });
    if (nonValida) {
      toast.error(`Controlla la quantità scelta per la colonna ${nonValida.colonna}`);
      return;
    }
    const richiestoPerCodice = righeDaCaricare.reduce((totali, r) => {
      totali[r.codice] = (totali[r.codice] || 0) + quantitaEffettiva(r);
      return totali;
    }, {});
    const stockNonValido = righeDaCaricare.find(r => {
      const caricabile = Math.max(0, Number(r.giacenza_magazzino || 0) - Number(r.scorta_minima_negozio || 0));
      return richiestoPerCodice[r.codice] > caricabile;
    });
    if (stockNonValido) {
      toast.error(`Il carico di ${stockNonValido.codice} supererebbe lo stock disponibile oltre la riserva negozio`);
      return;
    }
    setConfirmOpen(true);
  };

  const caricaTutto = async () => {
    setBulkLoading(true);
    try {
      const response = await api.post("/vending/ricarica-completa", {
        righe: righeDaCaricare.map(r => ({ id: r.id, quantita: quantitaEffettiva(r) })),
      });
      toast.success(`${response.data.colonne_caricate} colonne caricate, ${response.data.pezzi_caricati} pezzi totali`);
      setQuantitaManuali({});
      setConfirmOpen(false);
      setReviewOpen(false);
      await load();
    } catch (err) {
      toast.error(apiErrorMessage(err, "Impossibile completare il caricamento vending"));
    } finally {
      setBulkLoading(false);
    }
  };

  const stampaPdf = () => {
    window.open(`${API}/vending/ricarica-pdf`, "_blank");
  };

  const cellTone = (r) => {
    if (r.esito === "DA CARICARE" || r.esito === "CARICO PARZIALE") return "bg-red-50 border-red-200";
    if (r.esito === "SCORTA NEGOZIO") return "bg-amber-50 border-amber-300";
    if (r.esito === "PIENO") return "bg-emerald-50 border-emerald-200";
    if (r.esito === "OLTRE CAPACITA") return "bg-amber-50 border-amber-200";
    return "bg-slate-50 border-slate-200";
  };

  return (
    <Layout title="Distributore Vending" subtitle={`${allRows.length} colonne mappate`} actions={
      <>
        <button data-testid="vending-pdf" aria-label={`Stampa ${proposteDaCaricare.length} colonne proposte`} title="Stampa proposte" onClick={stampaPdf} disabled={!proposteDaCaricare.length || bulkLoading} className="flex items-center gap-1.5 border border-slate-300 bg-white text-slate-900 rounded-md px-3 sm:px-4 py-2 text-sm hover:bg-slate-50 transition-colors disabled:opacity-40">
          <Printer size={16} weight="bold" /> <span className="hidden sm:inline">Stampa</span> ({proposteDaCaricare.length})
        </button>
        <button data-testid="vending-bulk-open" aria-label={`Carica tutte le ${daCaricare} colonne`} title="Carica tutto" onClick={apriConfermaCompleta} disabled={!daCaricare || bulkLoading} className="flex items-center gap-1.5 bg-emerald-600 text-white rounded-md px-3 sm:px-4 py-2 text-sm font-bold hover:bg-emerald-700 transition-colors disabled:opacity-40">
          <CheckCircle size={16} weight="bold" /> <span className="hidden sm:inline">Carica tutto</span> ({daCaricare})
        </button>
      </>
    }>
      <Card className="mb-6 flex flex-col gap-4 border-blue-200 bg-blue-50/60 p-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="font-heading text-base font-black text-slate-900">Nuovo prodotto nella vending</div>
          <p className="mt-1 text-sm text-slate-600">Associa un prodotto esistente a una nuova colonna e, se necessario, registra il carico iniziale senza consumare la riserva del negozio.</p>
        </div>
        <button data-testid="vending-create-open" onClick={openCreate} className="inline-flex shrink-0 items-center justify-center gap-2 rounded-md bg-blue-600 px-4 py-2 text-sm font-bold text-white transition-colors hover:bg-blue-700">
          <Plus size={16} weight="bold" /> Aggiungi prodotto
        </button>
      </Card>
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-6">
        <KpiCard label="Colonne totali" value={allRows.length} />
        <KpiCard label="Da caricare" value={daCaricare} tone={daCaricare ? "danger" : "default"} />
        <KpiCard label="Scorta negozio protetta" value={scortaProtetta} tone={scortaProtetta ? "warning" : "default"} />
        <KpiCard label="Piene" value={piene} tone="success" />
        <KpiCard label="Pezzi da caricare" value={totDaCaricare} />
      </div>

      <div className="grid grid-cols-4 md:grid-cols-6 lg:grid-cols-8 2xl:grid-cols-12 gap-2 mb-8">
        {allRows.map(r => (
          <button
            key={r.colonna}
            data-testid={`vending-cell-${r.colonna}`}
            onClick={() => rica(r)}
            disabled={!r.proposta}
            className={`aspect-square flex flex-col items-center justify-center border rounded-md p-1 transition-transform enabled:hover:scale-[1.03] disabled:cursor-default ${cellTone(r)}`}
            title={`${r.codice} · ${r.descrizione}\nVending ${r.giacenza}/${r.capacita_max} · magazzino ${r.giacenza_magazzino}`}
          >
            <div className="font-mono font-bold text-xs">{r.colonna}</div>
            <div className="font-mono text-[10px] mt-1">{r.giacenza}/{r.capacita_max}</div>
            {r.proposta > 0 && <div className="text-[9px] text-red-600 font-bold mt-1">+{r.proposta}</div>}
          </button>
        ))}
      </div>

      <div className="flex gap-3 mb-4">
        <SearchBar data-testid="vending-search" value={query} onChange={setQuery} placeholder="Cerca colonna, codice o descrizione…" className="flex-1 max-w-md" />
        <span className="text-sm text-slate-500 self-center">{rows.length} risultati</span>
      </div>

      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="data-table w-full">
            <thead>
              <tr>
                <Th sortKey="colonna" currentKey={sortKey} dir={sortDir} onClick={toggle}>Colonna</Th>
                <Th sortKey="codice" currentKey={sortKey} dir={sortDir} onClick={toggle}>Codice</Th>
                <Th sortKey="descrizione" currentKey={sortKey} dir={sortDir} onClick={toggle}>Descrizione</Th>
                <Th sortKey="giacenza" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Giacenza reale</Th>
                <Th sortKey="giacenza_magazzino" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Negozio libero</Th>
                <Th sortKey="scorta_minima_negozio" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Riserva</Th>
                <Th sortKey="giacenza_caricabile" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Caricabile</Th>
                <Th sortKey="capacita_max" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Capacità</Th>
                <Th sortKey="soglia_minima" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Soglia</Th>
                <Th sortKey="proposta" currentKey={sortKey} dir={sortDir} onClick={toggle} align="right">Proposta</Th>
                <th className="text-right">Quantità scelta</th>
                <Th sortKey="esito" currentKey={sortKey} dir={sortDir} onClick={toggle}>Stato</Th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.colonna} data-testid={`vending-row-${r.colonna}`}>
                  <td className="font-mono font-bold">{r.colonna}</td>
                  <td className="font-mono">{r.codice}</td>
                  <td className="max-w-sm truncate">{r.descrizione}</td>
                  <td className="text-right">
                    <div className="inline-flex items-center gap-1">
                      <input
                        data-testid={`vending-stock-${r.colonna}`}
                        aria-label={`Giacenza reale colonna ${r.colonna}`}
                        type="number"
                        inputMode="numeric"
                        min="0"
                        max={r.capacita_max}
                        step="1"
                        value={giacenzeManuali[r.id] ?? r.giacenza}
                        onChange={e => setGiacenzeManuali(current => ({ ...current, [r.id]: e.target.value }))}
                        disabled={savingGiacenza === r.id || bulkLoading}
                        className="w-16 rounded-md border border-slate-300 px-2 py-1 text-right font-mono text-sm focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-200 disabled:bg-slate-100"
                      />
                      {Number(giacenzeManuali[r.id]) !== Number(r.giacenza) && giacenzeManuali[r.id] !== undefined && (
                        <button
                          data-testid={`vending-stock-save-${r.colonna}`}
                          onClick={() => salvaGiacenza(r)}
                          disabled={savingGiacenza === r.id}
                          className="rounded-md bg-blue-600 p-1.5 text-white hover:bg-blue-700 disabled:opacity-40"
                          title={`Salva giacenza reale colonna ${r.colonna}`}
                          aria-label={`Salva giacenza reale colonna ${r.colonna}`}
                        >
                          <FloppyDisk size={14} weight="bold" />
                        </button>
                      )}
                    </div>
                  </td>
                  <td className={`font-mono text-right ${r.giacenza_magazzino <= 0 ? "text-red-600 font-bold" : ""}`}>{r.giacenza_magazzino}</td>
                  <td className="font-mono text-right text-amber-700">{r.scorta_minima_negozio}</td>
                  <td className="font-mono text-right font-semibold">{r.giacenza_caricabile}</td>
                  <td className="font-mono text-right">{r.capacita_max}</td>
                  <td className="font-mono text-right">{r.soglia_minima}</td>
                  <td className="font-mono text-right font-semibold">{r.proposta || "—"}</td>
                  <td className="text-right">
                    <input
                      data-testid={`vending-custom-${r.colonna}`}
                      aria-label={`Quantità scelta colonna ${r.colonna}`}
                      type="number"
                      inputMode="numeric"
                      min="0"
                      max={Math.max(0, Math.min(r.fabbisogno, r.giacenza_caricabile))}
                      step="1"
                      value={quantitaManuali[r.id] ?? ""}
                      onChange={e => setQuantitaManuale(r.id, e.target.value)}
                      placeholder={String(r.proposta || 0)}
                      disabled={bulkLoading}
                      className="w-24 rounded-md border border-slate-300 px-2 py-1 text-right font-mono text-sm focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-200 disabled:bg-slate-100"
                    />
                  </td>
                  <td><Badge tone={r.esito === "PIENO" ? "ok" : r.esito === "DA CARICARE" || r.esito === "CARICO PARZIALE" ? "error" : "warning"}>{r.esito}</Badge></td>
                  <td className="text-right">
                    {quantitaEffettiva(r) > 0 && (
                      <button data-testid={`vending-rica-${r.colonna}`} onClick={() => rica(r)} disabled={bulkLoading} className="bg-slate-900 text-white rounded-md px-3 py-1 text-xs hover:bg-slate-800 transition-colors disabled:opacity-40">Ricarica</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <ConfirmDialog
        open={createOpen}
        title="Aggiungi prodotto alla vending"
        confirmLabel="Crea colonna"
        loading={creating}
        onCancel={closeCreate}
        onConfirm={createVendingProduct}
      >
        <div className="space-y-4" data-testid="vending-create-dialog">
          <div>
            <label className="mb-1 block text-xs font-bold uppercase tracking-wider text-slate-500">Colonna</label>
            <input
              data-testid="vending-create-column"
              value={newVending.colonna}
              onChange={event => setNewVending(current => ({ ...current, colonna: event.target.value.toUpperCase() }))}
              placeholder="A85"
              className="w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-sm"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-bold uppercase tracking-wider text-slate-500">Cerca prodotto</label>
            <input
              data-testid="vending-create-product-search"
              value={productQuery}
              onChange={event => setProductQuery(event.target.value)}
              placeholder="Codice o descrizione"
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
            <div className="mt-2 max-h-36 overflow-y-auto rounded-md border border-slate-200 bg-white">
              {productMatches.map(product => (
                <button
                  type="button"
                  key={product.id || product.codice}
                  data-testid={`vending-create-product-${product.codice}`}
                  onClick={() => {
                    setNewVending(current => ({ ...current, codice: product.codice }));
                    setProductQuery(`${product.codice} · ${product.descrizione}`);
                  }}
                  className={`block w-full border-b border-slate-100 px-3 py-2 text-left text-xs last:border-b-0 hover:bg-slate-50 ${newVending.codice === product.codice ? "bg-blue-50" : ""}`}
                >
                  <span className="font-mono font-bold">{product.codice}</span> · {product.descrizione}
                  <span className="ml-2 text-slate-400">Negozio: {product.giacenza_negozio || 0}</span>
                </button>
              ))}
              {!productMatches.length && <div className="px-3 py-3 text-xs text-slate-400">Nessun prodotto trovato</div>}
            </div>
          </div>
          {selectedProduct && (
            <div data-testid="vending-create-selected" className="rounded-md bg-emerald-50 px-3 py-2 text-xs text-emerald-900">
              Selezionato <b>{selectedProduct.codice}</b> · {selectedProduct.descrizione} — disponibili in negozio: <b>{selectedProduct.giacenza_negozio || 0}</b>
            </div>
          )}
          <div className="grid grid-cols-3 gap-3">
            <label className="text-xs font-bold uppercase tracking-wider text-slate-500">Capacità
              <input data-testid="vending-create-capacity" type="number" min="1" step="1" value={newVending.capacita_max} onChange={event => setNewVending(current => ({ ...current, capacita_max: event.target.value }))} className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2 font-mono text-sm" />
            </label>
            <label className="text-xs font-bold uppercase tracking-wider text-slate-500">Soglia
              <input data-testid="vending-create-threshold" type="number" min="0" step="1" value={newVending.soglia_minima} onChange={event => setNewVending(current => ({ ...current, soglia_minima: event.target.value }))} className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2 font-mono text-sm" />
            </label>
            <label className="text-xs font-bold uppercase tracking-wider text-slate-500">Carico iniziale
              <input data-testid="vending-create-initial" type="number" min="0" step="1" value={newVending.giacenza_iniziale} onChange={event => setNewVending(current => ({ ...current, giacenza_iniziale: event.target.value }))} className="mt-1 w-full rounded-md border border-slate-300 px-2 py-2 font-mono text-sm" />
            </label>
          </div>
          <p className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-900">Il carico iniziale viene sottratto dal negozio e aggiunto alla vending. La scorta minima configurata resta sempre protetta.</p>
        </div>
      </ConfirmDialog>
      <ConfirmDialog
        open={confirmOpen}
        title="Confermare il caricamento completo?"
        confirmLabel="Continua"
        loading={bulkLoading}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={() => {
          setConfirmOpen(false);
          setReviewOpen(true);
        }}
      >
        <p>Verranno caricate <b>{daCaricare} colonne</b>, per un totale di <b>{totDaCaricare} pezzi</b>.</p>
        {righeSenzaQuantitaManuale > 0 && (
          <p data-testid="vending-default-confirmation" className="mt-2 rounded-md bg-amber-50 px-3 py-2 text-amber-900">
            Per {righeSenzaQuantitaManuale} {righeSenzaQuantitaManuale === 1 ? "colonna lasciata vuota" : "colonne lasciate vuote"} verrà usata automaticamente la quantità proposta.
          </p>
        )}
        <p className="mt-2 text-slate-500">Le quantità inserite manualmente sostituiscono le proposte corrispondenti.</p>
      </ConfirmDialog>
      <ConfirmDialog
        open={reviewOpen}
        title="Controlla le quantità da caricare"
        confirmLabel="Conferma e carica"
        cancelLabel="Indietro"
        loading={bulkLoading}
        onCancel={() => {
          setReviewOpen(false);
          setConfirmOpen(true);
        }}
        onConfirm={caricaTutto}
      >
        <p className="mb-3">Il caricamento non è ancora stato eseguito. Controlla l'elenco prima della conferma finale.</p>
        <div data-testid="vending-review-list" className="max-h-64 overflow-y-auto rounded-md border border-slate-200">
          {righeDaCaricare.map(r => (
            <div key={r.id} data-testid={`vending-review-${r.colonna}`} className="flex items-center justify-between gap-3 border-b border-slate-100 px-3 py-2 last:border-b-0">
              <div className="min-w-0">
                <div className="font-mono text-xs font-bold text-slate-900">{r.colonna}</div>
                <div className="truncate text-xs text-slate-500">{r.descrizione}</div>
              </div>
              <div className="shrink-0 font-mono font-bold text-slate-900">{quantitaEffettiva(r)} pz</div>
            </div>
          ))}
        </div>
      </ConfirmDialog>
    </Layout>
  );
}
