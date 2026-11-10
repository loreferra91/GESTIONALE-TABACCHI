# Verifica dell'autonomia operativa della WebApp

## Fonti autorevoli

- Vendite correnti: `vendite`, con `canale=NEGOZIO` o `VENDING`. Il CSV del
  distributore resta un'integrazione attiva e inserisce eventi `CSV_VENDING`
  nella stessa collezione con chiave evento deduplicata.
- Scorte: `prodotti.giacenza_negozio` per il negozio e somma delle
  `vending.giacenza` per codice per i distributori. Il valore cache
  `prodotti.giacenza_vending` non prevale sulle colonne.
- Ordini fornitore: `ordini_fornitore` e `ordini_fornitore_righe`. La conferma
  non carica merce; `ricezioni_merce`, `storico_ordini` e il carico collegato
  aggiornano la giacenza fisica e il residuo.
- `db_storico_vend` resta in MongoDB come archivio legacy. Non partecipa a
  domanda, qualità dati, snapshot o conferma Auto-Order, né ai report delle
  vendite correnti e all'indicatore dell'ultima vendita WebApp.
- Il pivot economico include tutti i prodotti presenti nel database e usa i
  contatori operativi `venduti_negozio`, `venduti_vending` e `acquistati`.
  L'ultimo import Excel non sostituisce più il totale venduto corrente. Una
  base storica già migrata resta disponibile nella composizione degli acquisti
  e dei saldi di cassa, senza richiedere nuovi file Excel.

## Data di migrazione e copertura verificabile

Non esiste nel codice un marker che attesti la data effettiva di passaggio da
Excel alla WebApp. Una data dedotta dal primo documento `vendite`, dal primo
batch `BULK` o dall'ultimo import Excel sarebbe una supposizione. **La data di
migrazione non è verificata**; non viene impostata automaticamente e non si
copiano righe legacy in `vendite` durante questa modifica.

La copia locale del backup applicativo datata **3 novembre 2026** è stata
ispezionata in sola lettura:

| Evidenza nel backup | Risultato |
| --- | --- |
| Documenti `vendite` | 3.955, tutti con `id` e canale; nessun `id` duplicato |
| Prima e ultima data nativa osservata | 18 luglio – 3 novembre 2026 |
| Prime sorgenti `BULK` e `CSV_VENDING` osservate | 4 ottobre e 2 ottobre 2026 |
| Ultima data `db_storico_vend` osservata | 2 ottobre 2026 |
| Giorni con entrambe le collezioni | 29: la somma delle due fonti produrrebbe sovrapposizioni |
| Finestra nativa 5 ottobre – 3 novembre | 1.819 vendite, almeno una vendita in ciascuno dei 30 giorni |
| Copertura per canale nella stessa finestra | Negozio: 29 giorni; vending: 30 giorni |

Questi numeri dimostrano che una finestra recente è rappresentata da vendite
native nel backup. Non provano che ogni operazione del periodo sia presente, né
che la copia del 3 novembre rispecchi lo stato attuale di staging o produzione.
Il giorno senza vendite negozio potrebbe essere legittimo. La certificazione
della completezza storica richiede una riconciliazione con registrazioni
indipendenti e una data di cutover attestata dall'operatore; non si inventano
eventi o quantità per colmare i vuoti.

## Percorso senza Excel

1. Registrare la vendita in negozio nella WebApp o acquisire il CSV del
   distributore. Entrambi aggiornano `vendite` e le rispettive scorte.
2. Aprire Auto-Order: domanda e posizione inventariale sono calcolate dal
   database operativo, con finestra che termina nel giorno corrente. Se il
   controllo fisico richiede una scorta di banco superiore alla previsione,
   impostare il minimo del singolo articolo nella WebApp e ricalcolare.
3. Salvare l'ordine fornitore dalla WebApp. PDF ed Excel sono export facoltativi.
4. In Carico Merce usare **Precompila da Auto-Order**, indicare il riferimento
   ordine e confermare il carico. Il residuo dell'ordine e lo stock negozio si
   aggiornano insieme.

Per gli ordini legacy già consegnati e caricati senza riferimento, Auto-Order
offre una riconciliazione esplicita con il carico esistente. Essa chiude il
residuo senza sommare nuovamente merce al magazzino; bozze mai inviate possono
essere annullate mantenendole nello storico. Nessuna delle due azioni richiede
un file Excel.

La suite `backend/tests/test_auto_order_2.py` verifica il ciclo completo con
Mongo simulato e che assenza, presenza e modifica di `db_storico_vend` non
cambino quantità, canali o `snapshot_key`. I test dell'ingestione CSV vending
restano attivi. I test locali non certificano da soli la completezza del
database reale né il comportamento sotto crash su Atlas.

## Strumenti Excel legacy

`POST /api/import/excel` è dismesso perché scriveva giacenze senza backup.
`POST /api/import/excel-full` e `POST /api/smart-venue/import-excel` sono
disabilitati per impostazione predefinita. Una migrazione eccezionale richiede
che l'amministratore abiliti temporaneamente
`ENABLE_LEGACY_EXCEL_IMPORT=true`, verifichi una copia di sicurezza e invii
`X-Legacy-Import-Confirmed: confermo-import-legacy`. L'interfaccia mostra
questi strumenti in una sezione legacy e chiede conferma prima dell'invio.
Disabilitare nuovamente l'opzione al termine. Nessun import Excel è avviato
automaticamente nel percorso operativo. L'import CSV vending rimane disponibile.
