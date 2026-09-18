import { useEffect, useState, useMemo } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { toast, Toaster } from "sonner";
import { House, Trash, Check, MagnifyingGlass } from "@phosphor-icons/react";
import { formatEur, formatNum } from "../components/UI";

const CAT_STYLE = {
  "SIGARETTE": "bg-slate-900 text-white",
  "SIGARETTE ELETTRONICHE": "bg-amber-600 text-white",
  "ACCESSORI": "bg-emerald-700 text-white",
};

export default function CassaRapida() {
  const [prods, setProds] = useState([]);
  const [top, setTop] = useState([]);
  const [q, setQ] = useState("");
  const [cat, setCat] = useState("ALL");
  const [cart, setCart] = useState([]); // {codice, descrizione, prezzo, qta}
  const [pagamento, setPagamento] = useState("CONTANTI");
  const [canale, setCanale] = useState("NEGOZIO");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.get("/prodotti", { params: { limit: 1000 } }).then(r => setProds(r.data));
    api.get("/prodotti/top", { params: { limit: 40 } }).then(r => setTop(r.data));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const visibili = useMemo(() => {
    const base = q ? prods : (cat === "ALL" ? top : prods);
    return base
      .filter(p => (cat === "ALL" || p.categoria === cat))
      .filter(p => !q || p.codice.toLowerCase().includes(q.toLowerCase()) || (p.descrizione || "").toLowerCase().includes(q.toLowerCase()))
      .slice(0, 60);
  }, [prods, top, q, cat]);

  const add = (p) => {
    setCart(c => {
      const found = c.find(x => x.codice === p.codice);
      if (found) return c.map(x => x.codice === p.codice ? { ...x, qta: x.qta + 1 } : x);
      return [...c, { codice: p.codice, descrizione: p.descrizione, prezzo: p.prezzo || 0, qta: 1 }];
    });
  };
  const inc = (codice, delta) => setCart(c => c.map(x => x.codice === codice ? { ...x, qta: Math.max(0, x.qta + delta) } : x).filter(x => x.qta > 0));
  const remove = (codice) => setCart(c => c.filter(x => x.codice !== codice));
  const svuota = () => setCart([]);

  const totale = cart.reduce((s, x) => s + x.qta * (x.prezzo || 0), 0);
  const pezzi = cart.reduce((s, x) => s + x.qta, 0);

  const conferma = async () => {
    if (!cart.length) return toast.error("Carrello vuoto");
    setSaving(true);
    const nowIso = new Date().toISOString();
    try {
      for (const x of cart) {
        await api.post("/vendite", {
          data: nowIso,
          codice: x.codice,
          descrizione: x.descrizione,
          quantita: x.qta,
          importo: x.qta * x.prezzo,
          canale,
          pagamento,
        });
      }
      // registra anche entrata cassa se contanti/pos
      if (totale > 0) {
        await api.post("/cassa", {
          data: nowIso,
          tipo: "ENTRATA",
          importo: totale,
          descrizione: `Vendita banco (${pezzi} pz · ${pagamento})`,
          operatore: "POS",
        });
      }
      toast.success(`Vendita registrata: ${formatEur(totale)}`);
      setCart([]);
    } catch (e) {
      toast.error("Errore registrazione");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#F1F5F9] flex flex-col">
      <header className="h-16 bg-slate-900 text-white px-6 flex items-center justify-between border-b border-slate-800">
        <div className="flex items-center gap-4">
          <Link to="/" data-testid="pos-home" className="flex items-center gap-2 text-slate-300 hover:text-white transition-colors">
            <House size={18} /> <span className="text-sm">Torna al gestionale</span>
          </Link>
          <div className="h-6 w-px bg-slate-700" />
          <div>
            <div className="text-[10px] uppercase tracking-widest text-slate-400">modalità cassiere</div>
            <div className="font-heading font-black text-lg">Cassa Rapida</div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {["NEGOZIO", "VENDING"].map(c => (
            <button key={c} data-testid={`pos-canale-${c}`} onClick={() => setCanale(c)} className={`px-3 py-1.5 rounded-md text-xs font-bold uppercase tracking-wider ${canale === c ? "bg-brand text-white" : "bg-slate-800 text-slate-300"}`}>{c}</button>
          ))}
        </div>
      </header>

      <div className="flex-1 flex overflow-hidden">
        {/* Product grid */}
        <div className="flex-1 flex flex-col p-4 overflow-hidden">
          <div className="flex gap-2 mb-3">
            <div className="relative flex-1">
              <MagnifyingGlass size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <input data-testid="pos-search" value={q} onChange={e => setQ(e.target.value)} placeholder="Cerca codice o descrizione…" className="w-full border border-slate-300 rounded-md pl-9 pr-3 py-3 text-base bg-white" autoFocus />
            </div>
            {["ALL", "SIGARETTE", "SIGARETTE ELETTRONICHE", "ACCESSORI"].map(c => (
              <button key={c} data-testid={`pos-cat-${c}`} onClick={() => setCat(c)} className={`px-4 py-2 rounded-md text-xs font-bold uppercase tracking-wider transition-colors ${cat === c ? "bg-slate-900 text-white" : "bg-white text-slate-700 border border-slate-200 hover:bg-slate-50"}`}>
                {c === "ALL" ? "Preferiti" : c === "SIGARETTE ELETTRONICHE" ? "Elettr." : c}
              </button>
            ))}
          </div>

          <div className="flex-1 overflow-auto pr-1">
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3">
              {visibili.map(p => (
                <button
                  key={p.id}
                  data-testid={`pos-btn-${p.codice}`}
                  onClick={() => add(p)}
                  className="text-left bg-white border border-slate-200 rounded-md p-3 hover:border-slate-900 hover:shadow-md transition-all group relative overflow-hidden"
                >
                  <div className={`inline-block text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded ${CAT_STYLE[p.categoria] || "bg-slate-100 text-slate-700"}`}>
                    {p.categoria === "SIGARETTE ELETTRONICHE" ? "ELETTR." : (p.categoria || "").slice(0,7)}
                  </div>
                  <div className="font-mono text-[10px] text-slate-400 mt-1">{p.codice}</div>
                  <div className="text-sm font-medium text-slate-900 mt-1 line-clamp-2 min-h-[2.5rem]">{p.descrizione}</div>
                  <div className="flex items-baseline justify-between mt-2">
                    <span className="font-heading font-black text-lg text-slate-900">{formatEur(p.prezzo)}</span>
                    <span className="text-[10px] text-slate-500">Giac. {formatNum((p.giacenza_negozio || 0) + (p.giacenza_vending || 0))}</span>
                  </div>
                </button>
              ))}
              {visibili.length === 0 && <div className="col-span-full text-center py-16 text-slate-400">Nessun prodotto</div>}
            </div>
          </div>
        </div>

        {/* Cart */}
        <div className="w-96 bg-white border-l border-slate-200 flex flex-col">
          <div className="p-4 border-b border-slate-200 flex items-center justify-between">
            <div>
              <div className="text-[10px] uppercase tracking-widest text-slate-500">Carrello</div>
              <div className="font-heading font-black text-lg">{pezzi} pezzi</div>
            </div>
            <button data-testid="pos-clear" onClick={svuota} className="text-slate-400 hover:text-red-600 transition-colors p-2" title="Svuota">
              <Trash size={20} />
            </button>
          </div>

          <div className="flex-1 overflow-auto">
            {cart.length === 0 && <div className="p-6 text-center text-slate-400 text-sm">Tocca un prodotto per aggiungerlo</div>}
            {cart.map(x => (
              <div key={x.codice} data-testid={`cart-row-${x.codice}`} className="px-4 py-3 border-b border-slate-100">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-slate-900 truncate">{x.descrizione}</div>
                    <div className="font-mono text-[10px] text-slate-400">{x.codice} · {formatEur(x.prezzo)}</div>
                  </div>
                  <button data-testid={`cart-del-${x.codice}`} onClick={() => remove(x.codice)} className="text-slate-300 hover:text-red-500"><Trash size={14} /></button>
                </div>
                <div className="flex items-center justify-between mt-2">
                  <div className="flex items-center gap-2">
                    <button data-testid={`cart-dec-${x.codice}`} onClick={() => inc(x.codice, -1)} className="w-8 h-8 rounded-md bg-slate-100 hover:bg-slate-200 text-lg font-bold">−</button>
                    <span className="font-mono font-bold w-8 text-center">{x.qta}</span>
                    <button data-testid={`cart-inc-${x.codice}`} onClick={() => inc(x.codice, 1)} className="w-8 h-8 rounded-md bg-slate-100 hover:bg-slate-200 text-lg font-bold">+</button>
                  </div>
                  <div className="font-mono font-semibold text-sm">{formatEur(x.qta * x.prezzo)}</div>
                </div>
              </div>
            ))}
          </div>

          <div className="p-4 border-t border-slate-200 bg-slate-50">
            <div className="grid grid-cols-2 gap-2 mb-3">
              {["CONTANTI", "POS", "SATISPAY", "ALTRO"].map(p => (
                <button key={p} data-testid={`pos-pag-${p}`} onClick={() => setPagamento(p)} className={`py-2 rounded-md text-xs font-bold uppercase tracking-wider ${pagamento === p ? "bg-slate-900 text-white" : "bg-white text-slate-700 border border-slate-200"}`}>
                  {p}
                </button>
              ))}
            </div>
            <div className="flex items-baseline justify-between mb-3">
              <span className="text-xs uppercase tracking-widest text-slate-500">Totale</span>
              <span className="font-heading font-black text-3xl text-slate-900">{formatEur(totale)}</span>
            </div>
            <button
              data-testid="pos-conferma"
              onClick={conferma}
              disabled={saving || !cart.length}
              className="w-full bg-emerald-600 hover:bg-emerald-700 text-white rounded-md py-4 font-bold text-base flex items-center justify-center gap-2 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <Check size={20} weight="bold" /> {saving ? "Registrazione…" : "Incassa vendita"}
            </button>
          </div>
        </div>
      </div>
      <Toaster position="top-center" richColors />
    </div>
  );
}
