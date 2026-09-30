import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { MagnifyingGlass } from "@phosphor-icons/react";
import { api } from "../lib/api";
import { formatEur, formatNum } from "./UI";

export default function GlobalSearch() {
  const [q, setQ] = useState("");
  const [items, setItems] = useState([]);
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const timer = useRef(null);

  useEffect(() => {
    clearTimeout(timer.current);
    if (q.trim().length < 2) {
      setItems([]);
      setOpen(false);
      return undefined;
    }
    timer.current = setTimeout(() => {
      api.get("/global-search", { params: { q } }).then(r => {
        setItems(r.data.items || []);
        setOpen(true);
      }).catch(() => setItems([]));
    }, 180);
    return () => clearTimeout(timer.current);
  }, [q]);

  const choose = (item) => {
    setOpen(false);
    setQ("");
    navigate(`/prodotti?search=${encodeURIComponent(item.codice || "")}`);
  };

  return (
    <div className="relative">
      <MagnifyingGlass size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
      <input
        value={q}
        onChange={e => setQ(e.target.value)}
        onFocus={() => items.length && setOpen(true)}
        placeholder="Cerca prodotto per codice o descrizione..."
        className="w-full rounded-md border border-slate-300 bg-white py-2 pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-slate-900/10"
        data-testid="global-search"
      />
      {open && items.length > 0 && (
        <div className="absolute left-0 right-0 top-full z-[70] mt-1 max-h-80 overflow-auto rounded-md border border-slate-200 bg-white shadow-xl">
          {items.map(item => (
            <button key={item.codice} onClick={() => choose(item)} className="w-full border-b border-slate-100 px-3 py-2 text-left last:border-b-0 hover:bg-slate-50">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="font-mono text-xs font-bold text-slate-500">{item.codice}</div>
                  <div className="truncate text-sm font-semibold text-slate-900">{item.descrizione}</div>
                </div>
                <div className="text-right text-xs text-slate-500">
                  <div>{formatEur(item.prezzo)}</div>
                  <div>Neg. {formatNum(item.giacenza_negozio)} · Vend. {formatNum(item.giacenza_vending)}</div>
                </div>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
