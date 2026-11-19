# Staging — provenienza delle vendite BULK precedenti al batch ID

## Risultato (solo lettura)

Nel database Atlas isolato `gestionale_staging` sono presenti **148**
vendite `sorgente: "BULK"` prive del campo `batch_id`. Tutte hanno
`data` fra **2026-10-04** e **2026-10-06**:

| Data vendita | Righe senza batch ID |
|---|---:|
| 2026-10-04 | 48 |
| 2026-10-05 | 54 |
| 2026-10-06 | 46 |
| **Totale** | **148** |

La data `created_at` più recente del gruppo è
`2026-10-06T07:44:21.756870+00:00`.

## Evidenza dal codice GitHub

Il commit
[`66a88139c6c29f35006ab466faea321c0ecc4d30`](https://github.com/loreferra91/GESTIONALE-TABACCHI/commit/66a88139c6c29f35006ab466faea321c0ecc4d30),
datato **2026-10-06 19:26:26 UTC**, ha introdotto per la prima volta
la proprietà `batch_id` nel modello `VenditaGiornaliera`, la
collezione `vendite_bulk_imports` e l'assegnazione del batch
durante `POST /vendite/bulk`.

Le 148 vendite senza `batch_id` riportano quindi timestamp
**precedenti al commit che introduce il tracciamento**: la mancanza
del campo è coerente con il vecchio schema BULK.

**Precisazione:** i timestamp applicativi non costituiscono un audit
crittograficamente immutabile, e la data di commit non prova da sola
il momento esatto del deploy. Questa evidenza giustifica una
classificazione come *storico potenzialmente legacy*, non autorizza
a creare nuovi batch, unire righe o rettificare quantità.

## Modifica al diagnostico

Il controllo `BULK_BATCH_ORFANO` distingue:

- `collegamento: batch_id_assente`: righe prive del campo,
  riassunte con numero e intervallo delle date;
- `collegamento: batch_non_trovato`: righe con un `batch_id`
  che non corrisponde a nessun batch registrato.

Si conserva il tipo di segnalazione
`BULK_BATCH_ORFANO` e il relativo fingerprint per non perdere gli
stati e le note già associati in `reconciliation_cases`.
Il controllo resta **read-only**: nessuna movimentazione, ricostruzione
del magazzino o modifica dello storico.

## Prima di qualsiasi eventuale migrazione

Controllare import/export storici e backup esterni, confermare
l'origine dei file e il momento di messa in servizio del tracciamento
BULK. Evitare di rimuovere la segnalazione per far apparire
automaticamente riconciliato uno storico non tracciato.
