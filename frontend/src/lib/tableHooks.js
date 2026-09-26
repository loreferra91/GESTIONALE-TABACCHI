import { useState, useMemo } from "react";
import { CaretUp, CaretDown, MagnifyingGlass } from "@phosphor-icons/react";

/**
 * Hook riutilizzabile: fornisce ordinamento e ricerca su una lista di righe.
 *  - `initial`: campo ordinamento di default
 *  - `dir`: 'asc' | 'desc'
 *  - `searchFields`: array di chiavi da confrontare con la ricerca full-text
 */
export function useSortSearch(rows, { initial = null, dir = "asc", searchFields = [] } = {}) {
  const [sortKey, setSortKey] = useState(initial);
  const [sortDir, setSortDir] = useState(dir);
  const [query, setQuery] = useState("");

  const toggle = (key) => {
    if (sortKey === key) setSortDir(d => d === "asc" ? "desc" : "asc");
    else { setSortKey(key); setSortDir("asc"); }
  };

  const processed = useMemo(() => {
    let out = rows || [];
    const q = query.trim().toLowerCase();
    if (q && searchFields.length) {
      out = out.filter(r => searchFields.some(k => String(r?.[k] ?? "").toLowerCase().includes(q)));
    }
    if (sortKey) {
      const copy = [...out];
      copy.sort((a, b) => {
        const av = a?.[sortKey], bv = b?.[sortKey];
        if (av == null && bv == null) return 0;
        if (av == null) return 1;
        if (bv == null) return -1;
        const an = typeof av === "number" ? av : parseFloat(av);
        const bn = typeof bv === "number" ? bv : parseFloat(bv);
        let cmp;
        if (!Number.isNaN(an) && !Number.isNaN(bn) && typeof av !== "string" && typeof bv !== "string") {
          cmp = an - bn;
        } else if (!Number.isNaN(an) && !Number.isNaN(bn) && String(an) === String(av) && String(bn) === String(bv)) {
          cmp = an - bn;
        } else {
          cmp = String(av).localeCompare(String(bv), "it", { numeric: true });
        }
        return sortDir === "asc" ? cmp : -cmp;
      });
      out = copy;
    }
    return out;
  }, [rows, sortKey, sortDir, query, searchFields]);

  return { rows: processed, sortKey, sortDir, toggle, query, setQuery };
}

/** Header cliccabile con freccia direzione */
export function Th({ children, sortKey, currentKey, dir, onClick, className = "", align = "left" }) {
  const active = sortKey === currentKey;
  const alignCls = { left: "text-left", right: "text-right", center: "text-center" }[align];
  return (
    <th
      onClick={() => onClick(sortKey)}
      className={`cursor-pointer select-none hover:bg-slate-100 ${alignCls} ${className}`}
      data-testid={`th-${sortKey}`}
      title="Clicca per ordinare"
    >
      <span className={`inline-flex items-center gap-1 ${align === "right" ? "flex-row-reverse" : ""}`}>
        {children}
        {active ? (
          dir === "asc" ? <CaretUp size={12} weight="bold" /> : <CaretDown size={12} weight="bold" />
        ) : (
          <span className="text-slate-300 text-[10px]">↕</span>
        )}
      </span>
    </th>
  );
}

/** Barra di ricerca con lente */
export function SearchBar({ value, onChange, placeholder = "Cerca…", className = "", "data-testid": testId }) {
  return (
    <div className={`relative ${className}`}>
      <MagnifyingGlass size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
      <input
        data-testid={testId}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="border border-slate-300 rounded-md pl-8 pr-3 py-2 text-sm w-full bg-white focus:outline-none focus:ring-2 focus:ring-slate-900/10"
      />
    </div>
  );
}
