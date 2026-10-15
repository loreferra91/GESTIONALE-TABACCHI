import { renderToStaticMarkup } from "react-dom/server";
import InventoryPrint from "../components/InventoryPrint";

const rows = [{
  codice: "P1",
  descrizione: "WINSTON BLUE",
  giacenza_negozio: 148,
  giacenza_vending: 300,
  giacenza_totale: 448,
}];

test("la stampa inventario in bianco usa solo Vetrinetta, Armadio e Totale senza quantità", () => {
  const html = renderToStaticMarkup(<InventoryPrint mode="blank" rows={rows} printedAt={new Date()} totalCatalog={227} />);

  expect(html).toContain("INVENTARIO FISICO NEGOZIO");
  expect(html).toContain("VETRINETTA");
  expect(html).toContain("ARMADIO");
  expect(html).not.toContain("GIACENZA VENDING");
  expect((html.match(/<th(?:\s[^>]*)?>/g) || [])).toHaveLength(4);
  expect((html.match(/class="blank-cell"/g) || [])).toHaveLength(3);
  expect(html).not.toContain(">148<");
  expect(html).not.toContain(">300<");
  expect(html).not.toContain(">448<");
});

test("la stampa di controllo usa le quattro giacenze salvate e il totale", () => {
  const html = renderToStaticMarkup(
    <InventoryPrint mode="current" rows={rows} printedAt={new Date("2026-10-15T10:00:00Z")} totalCatalog={227} />,
  );

  expect(html).toContain("CONTROLLO GIACENZE REALI");
  expect(html).toContain("GIACENZA NEGOZIO");
  expect(html).toContain("GIACENZA VENDING");
  expect(html).toContain("GIACENZA TOTALE");
  expect((html.match(/<th(?:\s[^>]*)?>/g) || [])).toHaveLength(4);
  expect(html).toContain(">148<");
  expect(html).toContain(">300<");
  expect(html).toContain(">448<");
});

test("la stampa mostra il conteggio filtrato rispetto al catalogo", () => {
  const html = renderToStaticMarkup(
    <InventoryPrint mode="current" rows={rows} printedAt={new Date()} totalCatalog={227} />,
  );
  expect(html).toContain("1 di 227 prodotti");
});

test("la stampa mostra il conteggio totale quando non filtrato", () => {
  const html = renderToStaticMarkup(
    <InventoryPrint mode="blank" rows={rows} printedAt={new Date()} totalCatalog={1} />,
  );
  expect(html).toContain("1 prodotti");
  expect(html).not.toContain("di 1");
});
