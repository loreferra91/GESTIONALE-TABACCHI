# Auto-Order 2.0 — algoritmo e procedure operative

## Flusso e fonti

`vendite` è la sola fonte delle vendite per Auto-Order. Le righe
`sorgente=CSV_VENDING` sono l'integrazione attiva del distributore e rimangono
nella stessa collezione, con `canale=VENDING`; le altre vendite native distinguono
`NEGOZIO` e `VENDING`. I giorni commerciali usano `Europe/Rome`. La finestra
breve e quella lunga terminano nel giorno corrente, anche se non ci sono state
vendite oggi. Una vendita con canale assente o sconosciuto rende la riga
parziale; registrazioni vending CSV e manuali nello stesso giorno/prodotto
richiedono verifica per possibile sovrapposizione. L'ingestione CSV usa chiavi
evento stabili per evitare la duplicazione dello stesso evento.

`db_storico_vend` è uno storico legacy già conservato in MongoDB: Auto-Order non
lo legge, non gli attribuisce precedenza e non lo somma alle vendite native.
Non viene richiesta alcuna cartella di lavoro per fare una proposta o ricevere
un ordine. La data effettiva di passaggio a WebApp non è attestata da un marker
di migrazione: vedere la verifica documentale in `WEBAPP_AUTONOMIA.md`. Finché
non c'è una prova esterna, non si inventa una data di cutover e non si migra
automaticamente lo storico legacy nella collezione `vendite`.

Le scorte fisiche provengono da `prodotti.giacenza_negozio` e dalla somma delle
colonne `vending.giacenza`. Una ricarica vending resta un trasferimento
`-negozio/+vending`; non crea una vendita. `storico_ordini` è lo storico dei
carichi effettivi. `ordini_fornitore` e `ordini_fornitore_righe` sono invece
impegni verso il fornitore e non caricano il magazzino.
Le voci contabili denominate `resto non erogato` o `resti non erogati` restano
nel database ma sono escluse dalle proposte e dalla selezione manuale
Auto-Order: non sono merce acquistabile. Un prodotto può inoltre essere
escluso esplicitamente con `ordinabile_auto_order=false`.

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
La domanda è ripartita tra negozio e vending in base ai canali delle vendite
native. `copertura_negozio_gg` e `copertura_vending_gg` usano ciascuna la
propria domanda e giacenza; `copertura_gg` è la minore copertura disponibile.
Lo stock vending non diventa disponibile al banco: se è il solo stock rimasto
e il negozio è sotto soglia, occorre controllo manuale. Se la vending è sotto
soglia ma il negozio ha stock trasferibile, si verifica una ricarica prima di
ordinare dal fornitore. La proiezione giornaliera delle rotture usa solo lo
stock fisico fino alla data prevista di arrivo. Le forniture senza
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

La qualità della proposta dipende dai dati nativi e dalle anomalie delle
forniture. La presenza, assenza, data o stato di un import Excel non cambia la
domanda e non invalida lo snapshot. Un canale vendita sconosciuto, una possibile
sovrapposizione vending o l'assenza di vendite native recenti espone
`DATI_PARZIALI`; residui ordine incoerenti espongono `DATI_INCOERENTI` e
bloccano la conferma. `AUTO_ORDER_MAX_ETA_IMPORT_GG` è un parametro legacy
ignorato da Auto-Order. Una giornata senza vendite non viene interpretata da
sola come un errore di sincronizzazione.

## Esportazione e compatibilità

`GET /api/auto-order/pdf` e `/excel` accettano `snapshot_key` per una bozza e
`batch_key` per un ordine confermato. Lo snapshot cambiato dà 409; con
`batch_key` le quantità sono lette dalle righe persistite. Le prime quattro
colonne dell'Excel restano `CODICE`, `ARTICOLO`, `QTA`, `TOTALE` nella riga 4,
compatibili con `/api/ordini/import-auto-order/preview`. I metadati sono in un
foglio separato e tutte le celle testuali da input sono protette da formula
injection.

L'export PDF/Excel è facoltativo. In Carico Merce si può usare direttamente
**Precompila da Auto-Order** oppure inserire righe manualmente; l'import del
file Excel dell'ordine resta solo una comodità legacy, con anteprima e
conferma esplicita.

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
- La data effettiva della migrazione e la completezza di tutti i dati storici
  non sono certificate dalla sola presenza di righe nel database. Lo storico
  legacy resta separato; la riconciliazione legacy delle ricezioni necessita
  di prove esterne e non è automatizzata.
- La sicurezza si basa su impronte, CAS e transazioni Mongo. La suite locale
  usa Mongo simulato; la verifica di conflitti su Atlas reale e di import
  interrotti da crash richiede uno staging isolato.
- Il rischio di stockout non viene corretto statisticamente senza uno storico
  affidabile delle indisponibilità. La scorta di sicurezza resta il fattore
  1,15 configurabile.
