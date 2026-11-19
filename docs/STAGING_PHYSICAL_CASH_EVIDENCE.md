# Staging — conteggio fisico della cassa come evidenza separata

## Scopo

**Non ricostruire saldi né inventare un'apertura di cassa.** Nello staging
iniziale non erano presenti documenti nativi `cassa` o batch POS.
Un controllo dei documenti non può certificare il contante fisico.

La schermata Contabilità → Riconciliazione permette ora di
registrare manualmente un conteggio osservato di banconote e monete,
**solo nello staging**. La persona che conta deve conservare
separatamente il verbale firmato.

## Sicurezza e audit

- La somma viene calcolata dal backend con **centesimi interi**.
  Non è possibile inviare un totale arbitrario: soltanto quantità
  intere e non negative per 15 denominazioni EUR riconosciute.
- Orario obbligatoriamente timezone-aware UTC, entro 48 ore
  precedenti o 5 minuti successivi alla richiesta; il backend non
  ricrea conteggi storici.
- Riferimento al verbale (min. 4 caratteri), nota facoltativa e
  operatore derivato da Basic Auth, non dal client.
- La chiave idempotente è persistente per il retry del browser.
  Inserimento in `cash_count_evidence` e audit append-only
  in `audit_events` nella **stessa transazione MongoDB**;
  la chiave primaria `_id` protegge duplicati concorrenti.
- Retry identico restituisce l'evidenza precedente, nuovo payload
  con la stessa chiave risponde 409.
- Le API ordinarie di vendita, cassa e stock NON sono invocate.
- La collection è inclusa fra le opzioni del backup JSON staging.
- Un fallimento di rete dopo la conferma non libera gli input;
  l'utente può ripetere soltanto lo stesso payload, preservando
  la chiave.

Endpoint staging: `GET /api/contabilita/conteggi-fisici` e
`POST /api/contabilita/conteggi-fisici`. Non montati in produzione.

## Qualificazione finanziaria

Ogni documento contiene esplicitamente
`is_financial_baseline: false` e `changes_booked_cash: false`.

Il conteggio è **una fotografia osservata, non una certificazione**
del saldo e non genera un incasso, un prelievo o una rettifica.
Per approvare la riconciliazione reale serviranno:
- fondo di apertura fisico documentato con data e operatore;
- periodo di competenza coerente con tutti i movimenti nativi;
- estratti POS/bancari e ricevute dei prelievi;
- conteggio fisico di chiusura, preferibilmente con
  controverifica di una seconda persona;
- revisione dei movimenti mancanti/anomali senza sovrascritture.

Fino ad allora la risposta
`copertura_finanziaria.saldo_cassa_certificato`
**rimane false** anche se sono stati inseriti conteggi fisici.

## Limiti dei test

I test unitari Mongo simulano idempotenza e audit, ma non la
durabilità transazionale su un vero replica set durante SIGKILL.
Il backend staging è già verificato su Atlas replica set
con transazioni. La certificazione funzionale reale del conteggio
richiederà inserimenti **solo su dati sintetici isolati**, non
conteggi fittizi nel database operativo.
