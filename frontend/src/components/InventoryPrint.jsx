export default function InventoryPrint({ mode, rows, printedAt }) {
  const current = mode === "current";
  const headers = current
    ? ["DESCRIZIONE PRODOTTO", "GIACENZA NEGOZIO", "GIACENZA VENDING", "GIACENZA TOTALE"]
    : ["DESCRIZIONE PRODOTTO", "VETRINETTA", "ARMADIO", "TOTALE"];

  return (
    <section className="inventory-print-root" aria-label={current ? "Controllo giacenze reali" : "Inventario fisico negozio"}>
      <h1>{current ? "CONTROLLO GIACENZE REALI" : "INVENTARIO FISICO NEGOZIO"}</h1>
      <div className="inventory-print-meta">
        {current ? `Fotografia del ${printedAt?.toLocaleString("it-IT")}` : "Data conteggio: ____________________"}
      </div>
      <table>
        <thead><tr>{headers.map(label => <th key={label}>{label}</th>)}</tr></thead>
        <tbody>
          {rows.map(row => <tr key={row.codice}>
            <td>{row.descrizione}</td>
            {current
              ? <><td>{row.giacenza_negozio}</td><td>{row.giacenza_vending}</td><td>{row.giacenza_totale}</td></>
              : <><td className="blank-cell">&nbsp;</td><td className="blank-cell">&nbsp;</td><td className="blank-cell">&nbsp;</td></>}
          </tr>)}
        </tbody>
      </table>
    </section>
  );
}
