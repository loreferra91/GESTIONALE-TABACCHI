# Auto-Order 2.0 — algoritmo e procedure operative

## Flusso e fonti

`DB_STORICO_VEND` contiene vendite Excel aggregate senza ID transazione e senza
canale affidabile. `vendite` contiene vendite app; le righe con
`sorgente=CSV_VENDING` sono il flusso CSV vending. Le date sono lette come
giorni commerciali `Europe/Rome`. La riconciliazione raggruppa per prodotto,
giorno, canale e sorgente. Nei giorni con storico Excel, il valore Excel ha
precedenza e la possibile sovrapposizione con l'app viene segnalata; le vendite
app dei giorni successivi restano incluse. Un giorno sovrapposto non viene mai
sommato due volte. Due registrazioni vending CSV/app potenzialmente sovrapposte
sono segnalate e non danno luogo a una proposta automatica. Lo storico Excel non
consente la ripartizione negozio/vending: i relativi campi sono `null`, mentre
il totale rimane disponibile con qualità `DATI_PARZIALI`.

Le scorte fisiche provengono da `prodotti.giacenza_negozio` e dalla somma delle
colonne `vending.giacenza`. Una ricarica vending resta un trasferimento
`-negozio/+vending`; non crea una vendita. `storico_ordini` è lo storico dei
carichi effettivi. `ordini_fornitore` e `ordini_fornitore_righe` sono invece
impegni verso il fornitore e non caricano il magazzino.

## Formule e decisioni

Per default: finestra breve 10 giorni, lunga 30 giorni, peso breve 0,70,
soglia minima 7 giorni, target 14 giorni, fattore di sicurezza 1,15. I valori
restano configurabili in Parametri.

```text
domanda_breve = venduto_breve / giorni_breve
domanda_lunga = venduto_lungo / giorni_lungo
domanda_giornaliera = peso_breve × domanda_breve
                   + (1 - peso_breve) × domanda_lunga
```

Se il peso breve è 100% ma esistono solo vendite nella finestra lunga, si usa
la media lunga per non azzerare una domanda osservata. La classe FAST/SLOW usa
il venduto lungo normalizzato a 30 giorni e le soglie già presenti. Un aumento
recente almeno doppio rispetto alla parte precedente della finestra, con
differenza di almeno 2 pezzi/giorno, viene segnalato senza cambiare la formula.
I giorni senza vendita non sono convertiti in giorni senza dati. In assenza di
storico di stockout non viene inventata domanda persa.

Con `AUTO_ORDER_LEAD_TIME_GG=0` resta la modalità `LEGACY`, con orizzonte target
configurato. Se il lead time è positivo, l'orizzonte di protezione è
`lead time + review period`. Non viene presunto alcun calendario settimanale
del fornitore. La data prevista di ciascun ordine è registrata esplicitamente.

```text
residuo ordine = max(0, quantità ordinata - quantità ricevuta)
posizione inventariale utilizzabile = stock negozio + stock vending
                                  + residui con consegna futura entro l'orizzonte
target = ceil(domanda_giornaliera × giorni_protezione × fattore_sicurezza)
       + riserva negozio per vending, se il prodotto è nel vending
punto di riordino (lead time configurato) = target
scorta di sicurezza = target - riserva - ceil(domanda × giorni_protezione)
fabbisogno netto = max(0, target - posizione utilizzabile)
quantità proposta = ceil(fabbisogno netto / lotto) × lotto
```

Con lead time configurato si ordina quando la posizione utilizzabile è sotto
il punto di riordino; in modalità legacy resta la soglia di copertura 7 giorni.
La copertura immediata (`copertura_gg`) usa lo stock negozio, senza trattare
la vending come disponibile al banco. La proiezione giornaliera delle rotture
usa solo lo stock fisico fino alla data prevista di arrivo. Le forniture senza
data futura affidabile, incluse quelle scadute, non entrano nella posizione
utilizzabile: un possibile acquisto duplicato passa a `CONTROLLO MANUALE`.
Una fornitura che arriva dopo la data prevista di esaurimento viene segnalata.
Stock negativo o residui ordine incoerenti sono `ANOMALIA` e bloccano la
proposta automatica. Un articolo senza vendite non riceve domanda inventata.
Le righe `CONTROLLO MANUALE` e `ORDINE IN ARRIVO` non possono essere aggiunte
alla bozza tramite selezione manuale: occorre prima chiarire la fornitura e
ricalcolare. Le altre righe senza anomalie possono essere aggiunte con un lotto
valido dall'operatore.

`prezzo` e `totale` restano il **valore di listino legacy**, anche in PDF/Excel
e nell'import Carico Merce. Non sono il costo di acquisto. Se un prodotto ha un
`costo_acquisto` verificato, la proposta espone separatamente il costo stimato;
in assenza del campo mostra costo non disponibile. Nessun aggio viene assunto
come costo certo.

## Stato ordine e ricezioni

I nuovi ordini nascono `CONFERMATO`, con protocollo tecnico
`PROCESSING → COMPLETED`. `CREATO` legacy è letto come confermato, senza
riscrivere il documento. `BOZZA` e `ANNULLATO` non sono pendenti;
`PARZIALMENTE_RICEVUTO` contribuisce solo per il residuo; `RICEVUTO` non
contribuisce. `PATCH /api/auto-order/forniture/{batch_key}` salva la data
prevista o la transizione a `INVIATO` con versione ottimistica. La pagina
Auto-Order espone le forniture attive e consente queste operazioni.

`POST /api/ordini/bulk` accetta l'opzionale `supplier_order_batch_key`. Il
riferimento può essere inserito in Carico Merce o importato dal foglio
`Metadati Auto-Order` dell'Excel di un ordine confermato. Senza riferimento il
carico resta non riconciliato; il sistema non associa merce per codice o data
presunti. Con riferimento, ogni quantità deve essere entro il residuo; lo
stesso carico idempotente non ricarica le scorte due volte. L'annullamento
restituisce il residuo solo se la ricezione collegata è l'ultima del batch e le
compensazioni fisiche sono ancora lecite.

### Riconciliazione legacy controllata

Per una ricezione storica priva di riferimento, conservare il movimento
originale. Confrontare documenti del fornitore, ordine confermato e
`storico_ordini.id`; verificare prodotto, quantità, data e assenza di
annullamenti o associazioni precedenti. Annotare la corrispondenza e la prova
in un registro operativo prima di qualsiasi correzione. Questa versione **non
applica automaticamente** quantità ricevute ai batch legacy ambigui. Per
quelle righe l'Auto-Order richiede controllo manuale, evitando di attribuire
carichi non dimostrati. Un'eventuale migrazione manuale successiva deve usare
un backup, ID movimento espliciti, una transazione e un audit verificabile;
non deve incrementare nuovamente lo stock.

## Snapshot, idempotenza e qualità

`GET /api/auto-order` restituisce `snapshot_key`, `created_at`,
`source_watermarks`, `inventory_version`, `parameters_version` e
`orders_version`. Lo snapshot include tutte le righe, comprese quelle escluse,
le scorte, le vendite riconciliate, i parametri rilevanti e i residui.
`POST /api/auto-order/conferma` richiede `snapshot_key` per una selezione e
risponde 409 se è cambiato. Un CAS su `prodotti.auto_order_version` protegge le
righe selezionate durante il salvataggio; la transazione Mongo è usata quando
disponibile. La chiave idempotente con contenuto diverso dà 409; un batch
`PROCESSING` non è mai restituito come completato. In ambiente locale senza
transazioni, la compensazione elimina solo testata/righe possedute dal batch.

L'import Excel completo marca `auto_order_import_state` come `PROCESSING` prima
delle modifiche; se il processo si interrompe, la conferma resta bloccata. Un
rollback riuscito marca `ROLLED_BACK`, un import riuscito `COMPLETED`.
`AUTO_ORDER_MAX_ETA_IMPORT_GG=0` disabilita il limite temporale finché
l'azienda non ne definisce uno. Una giornata senza vendite non è da sola una
prova di dati obsoleti. I livelli esposti sono `DATI_AFFIDABILI`,
`DATI_PARZIALI`, `DATI_OBSOLETI` e `DATI_INCOERENTI`; gli ultimi due bloccano
la conferma.

## Esportazione e compatibilità

`GET /api/auto-order/pdf` e `/excel` accettano `snapshot_key` per una bozza e
`batch_key` per un ordine confermato. Lo snapshot cambiato dà 409; con
`batch_key` le quantità sono lette dalle righe persistite. Le prime quattro
colonne dell'Excel restano `CODICE`, `ARTICOLO`, `QTA`, `TOTALE` nella riga 4,
compatibili con `/api/ordini/import-auto-order/preview`. I metadati sono in un
foglio separato e tutte le celle testuali da input sono protette da formula
injection.

## Migrazione e rollback

`auto-order-2-schema-v1` crea indici additivi per batch attivi e righe
pendenti e registra la versione in `app_migrations`. È idempotente, non
riscrive ordini legacy e non inventa ricezioni. I nuovi campi sono opzionali
alla lettura; i documenti vecchi restano leggibili. In rollback applicativo
si può tornare al codice precedente lasciando i campi e gli indici: sono
ignorati dalla versione vecchia. Non eliminare documenti o modificare
quantità storiche. Prima di qualsiasi rollback dati, creare un backup e
valutare i carichi avvenuti dopo il deploy.

## Limiti verificati

- Nessuna data di consegna viene inventata; non c'è un calendario per giorno
  della settimana o per fornitore, né un'ottimizzazione del capitale disponibile.
- Il canale dello storico Excel non è ricostruibile; i giorni sovrapposti
  richiedono revisione manuale. La riconciliazione legacy delle ricezioni
  necessita di prove esterne e non è automatizzata.
- La sicurezza si basa su impronte, CAS e transazioni Mongo. La suite locale
  usa Mongo simulato; la verifica di conflitti su Atlas reale e di import
  interrotti da crash richiede uno staging isolato.
- Il rischio di stockout non viene corretto statisticamente senza uno storico
  affidabile delle indisponibilità. La scorta di sicurezza resta il fattore
  1,15 configurabile.
