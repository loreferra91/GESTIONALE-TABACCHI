# Gratta e Vinci — magazzino e vendite indipendenti

## Scopo
La voce laterale `Gratta e Vinci` apre `/gratta-e-vinci`, separata da
`Prodotti`, `Vending`, `Smart Venue`, Auto-Order e cassa.

### Magazzino
- Crea un prodotto indicando **codice**, **descrizione**, **prezzo nominale** e
  **quantità iniziale in magazzino** (intero non negativo).
- Un codice è unico per il catalogo Gratta e Vinci; lo stesso codice in
  `prodotti` non crea conflitti, perché le collezioni sono indipendenti.
- È possibile correggere descrizione e prezzo con versione ottimistica.
- Le giacenze esistenti si aumentano esclusivamente con `Carica`;
  non sono mai riscritte silenziosamente durante un cambio prezzo.
- Non è prevista cancellazione di prodotti con storico.

### Registrazione vendite
- Una riga per ciascun prodotto, con codice, descrizione, biglietti venduti,
  totale nominale delle vendite, quantità ancora disponibile e campo
  per registrare una nuova vendita.
- Il riepilogo mostra per impostazione predefinita la giornata corrente
  nel fuso `Europe/Rome`; permette di selezionare un altro giorno o
  tutto lo storico.
- Ogni vendita sottrae la quantità disponibile e memorizza **prezzo unitario
  del momento**, quantità e importo totale in **centesimi interi**.
  Modifiche successive al prezzo non alterano i ricavi storici.
- In caso di stock insufficiente la vendita non viene registrata.
  Quantità frazionarie e negative sono rifiutate.

## Persistenza
- `gratta_vinci_prodotti`: anagrafica, giacenza corrente, giacenza iniziale,
  venduti totali, incasso totale in centesimi e versione.
- `gratta_vinci_movimenti`: carichi e vendite con identificativo e
  chiave idempotente univoca, operatore, timestamp UTC e giorno locale.
- Le due collezioni sono incluse nei backup completi. Sono opzionali
  nel ripristino dei vecchi file che non le contengono.

## API
- `GET /api/gratta-e-vinci/prodotti`
- `POST /api/gratta-e-vinci/prodotti`
- `PUT /api/gratta-e-vinci/prodotti/{id}` (metadati, con `version`)
- `POST /api/gratta-e-vinci/carichi`
- `POST /api/gratta-e-vinci/vendite`
- `GET /api/gratta-e-vinci/riepilogo?giorno=YYYY-MM-DD` (omettendo
  `giorno` si ottiene tutto lo storico)
- `GET /api/gratta-e-vinci/movimenti?codice=GV001`

Vendite e carichi richiedono sempre una `idempotency_key` di 10-160
caratteri. Retry con stessa chiave e stesso payload = nessun secondo
scarico; stessa chiave con payload diverso = 409. In Atlas il movimento
e il saldo vengono modificati nella stessa transazione MongoDB; la
modalità locale senza transazioni usa una compensazione per i test.

## Ambito contabile
`totale_centesimi` rappresenta **valore nominale dei biglietti venduti**,
non aggio, margine, vincite pagate o saldo cassa. Non contabilizzare
automaticamente questi importi come ricavi netti o pagamenti senza
un requisito approvato e una specifica separata per le scritture di cassa.

## Verifiche prima del rilascio
1. Eseguire i test unitari su branch dedicato e la build React.
2. Su staging con database isolato creare un biglietto, fare un carico,
   vendere, ripetere la stessa richiesta e verificare lo storico.
3. Provare due vendite simultanee con disponibilità pari a un pezzo,
   i conflitti di versione, la data locale e il cambio prezzo.
4. Verificare che `prodotti`, `vendite`, `vending`, `cassa` e Auto-Order
   non cambino dopo questi test.
5. Eseguire backup di staging, verifica export e prova di ripristino
   controllata prima di qualsiasi rilascio in produzione.

### Staging integration
Read-only startup is preserved. Additive indexes for the two new collections are ensured before an explicit catalogue creation or stock movement; failures block the operation. No existing collection or index is changed.

## Dedicated navigation and preliminary Auto-Order
Separate sidebar group and routes for Prodotti, Vendite and Auto-Order. Read-only proposals use the greater of 7-day and 30-day average sales from completed local days, after 30 days of catalogue age. Editable provisional coverage, lead time and lot parameters are independent of tobacco. Pending supply orders are not tracked yet: proposals require manual verification and never create orders or change inventory.
