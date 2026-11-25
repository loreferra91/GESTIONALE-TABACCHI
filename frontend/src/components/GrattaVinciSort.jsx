const productCollator = new Intl.Collator("it-IT", { numeric: true, sensitivity: "base" });

export function SortHeader({ field, label, sort, onSort, numeric = false }) {
  const active = sort.field === field;
  return (
    <th scope="col" aria-sort={active ? (sort.direction === "asc" ? "ascending" : "descending") : "none"}
      className={`p-3 ${numeric ? "text-right" : "text-left"}`}>
      <button type="button" onClick={() => onSort(field)}
        className={`inline-flex items-center gap-1.5 rounded px-1 py-1 hover:bg-slate-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-slate-600 ${active ? "text-slate-900" : ""}`}>
        {label}<span aria-hidden="true">{active ? (sort.direction === "asc" ? "↑" : "↓") : "↕"}</span>
      </button>
    </th>
  );
}

export function sortScratchCardRows(rows, sort, numericFields) {
  const numeric = numericFields.includes(sort.field);
    return [...rows].sort((a, b) => {
      const av = a[sort.field], bv = b[sort.field];
      if (numeric) {
        const aValid = typeof av === "number" && Number.isFinite(av);
        const bValid = typeof bv === "number" && Number.isFinite(bv);
        if (aValid !== bValid) return aValid ? -1 : 1;
        if (!aValid) return productCollator.compare(a.codice, b.codice);
      }
      const cmp = numeric ? av - bv : productCollator.compare(String(av ?? ""), String(bv ?? ""));
      return cmp * (sort.direction === "asc" ? 1 : -1) || productCollator.compare(a.codice, b.codice);
    });

}
export function nextSort(previous, field) {
  return { field, direction: previous.field === field && previous.direction === "asc" ? "desc" : "asc" };
}
