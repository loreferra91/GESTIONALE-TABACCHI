# Integrità delle vendite BULK — 19 novembre 2026

Il parser e il backend accettano interi positivi, anche scritti come `2.0` o
`2,0`. Rifiutano frazioni, segni, testo e quantità oltre il massimo intero
sicuro JavaScript. Una riga incompleta o con quantità non valida blocca
l'intero invio; i riepiloghi di categoria riconosciuti restano esclusi.
Le date esplicite devono essere giorni ISO validi (`YYYY-MM-DD`). La data
predefinita della pagina e del BULK backend usa Europe/Rome.

## Retry e concorrenza

Il client genera `idempotency_key` per il payload e la conserva dopo un
fallimento fino a successo o modifica del payload. La protezione lato client
impedisce due invii simultanei. Un reload della pagina non conserva la chiave.

Il backend conserva SHA-256 del payload e la risposta nel batch. Una richiesta
ripetuta con la stessa chiave restituisce la risposta originale, anche se lo
stock è ormai insufficiente. Payload diverso con la stessa chiave: HTTP 409.
Replay di un batch annullato: HTTP 409, senza nuovi scarichi. Una nuova
operazione intenzionale richiede una nuova chiave.

Per compatibilità, i client senza chiave vengono deduplicati per impronta esatta
del payload: non possono registrare due import identici senza adottare chiavi
esplicite. Non si deduplicano automaticamente payload identici con chiavi diverse.

La prenotazione del batch precede le scritture di stock nella stessa transazione,
con `_id` deterministico e indice Mongo univoco. Esiste anche un indice univoco
sparso su `idempotency_key`, oltre agli indici `id` e `vendite.batch_id`.
Le compensazioni locali restano best-effort; la garanzia di rollback in produzione
richiede il replica set e le transazioni già richiesti dal server.

## Totali

`GET /api/vendite/totali?giorno=YYYY-MM-DD` aggrega importi, pezzi e numero di
vendite nel database con lo stesso filtro della lista. I totali della pagina
non dipendono più dalle 500 righe visibili. La tabella segnala quando mostra
solo parte delle vendite. Non viene modificato il modello contabile della cassa.

## Verifica e limiti

Test isolati: parser, quantità/date invalide, somma di righe dello stesso prodotto,
replay, conflitto, annullamento, concorrenza con barriera prima della prenotazione,
compensazione dopo errore di scrittura, totale su 501 vendite e retry UI dopo timeout.
La concorrenza è verificata con Mongo simulato: non certifica failover/crash di un
replica set reale. Nessuna riconciliazione o riparazione delle giacenze Atlas.

Il rollback del codice non richiede rimozione degli indici o dei metadata.
Tornare al codice precedente riapre i difetti BULK: sospendere l'uso del BULK
in caso di rollback, senza ripristinare automaticamente dati di magazzino.
