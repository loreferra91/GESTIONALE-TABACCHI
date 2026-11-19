# Ripartenza audit — GESTIONALE TABACCHI
**Data:** 19 novembre 2026  
**Baseline:** `main` @ `28c53c77dec714e822b003b46df724ee42cf2e19`  
**Branch di sviluppo:** `codex/restart-integrity-hardening-20261119`

## Obiettivo e confini

Questa implementazione riparte dalla baseline pubblicata, senza includere gli
interventi rimasti nell'ambiente temporaneo di una precedente sessione Codex.
Non viene modificato il ramo `main`, non vengono avviati deploy Render e non
viene eseguita alcuna scrittura su Atlas di produzione.

I risultati di GitHub Actions provano compilazione e test automatizzati sui
commit del branch, **non** la corretta esecuzione del codice su Render/Atlas.

## Implementato nel branch

1. **Storno BULK atomico.** La transizione `active → annulling → annulled`
   è interna alla transazione che ripristina stock, contatori e cancella
   le vendite. Uno stop tra prenotazione e aggiornamento non lascia un batch
   esternamente bloccato. Ogni vendita stornata crea un evento append-only
   nella collezione già esistente `audit_events`. L'annullamento verifica
   il numero di vendite e i contatori prima di ripristinare la scorta.
2. **Retry BULK dopo reload.** `sessionStorage` contiene la chiave generata e
   un'impronta locale compatta del payload: non conserva password, token né
   l'elenco prodotti. La chiave viene eliminata dopo una risposta positiva.
   Un risultato incerto deve essere verificato prima di abbandonare la
   richiesta: il comando «Svuota tutto» chiede una conferma esplicita.
3. **Anteprima giacenze BULK.** Le quantità sono aggregate per codice anche
   quando il prodotto appare in più righe; la schermata indica scorta
   negozio, quantità prevista dopo l'import e stato insufficiente. Blocca
   anomalie note prima dell'invio, ma è una **fotografia non autoritativa**:
   il backend decide in transazione usando lo stock aggiornato.
4. **Parser Excel verticale.** Conserva `2,0` e `6,40` come quantità e importo
   separati, senza interpretare la virgola decimale come separatore CSV.
5. **Vendite manuali.** La chiave idempotente deve rappresentare una sola
   richiesta: cambiare quantità, importo, canale, colonna o altri dati genera
   HTTP 409. Un record legacy privo di fingerprint viene confrontato
   conservativamente sui campi effettivamente salvati.
6. **Snapshot interni.** Sono comprese `cassa_rapida_batches`,
   `vending_operations` e `accounting_baselines`. Sono opzionali quando
   viene letto un archivio storico creato prima della loro introduzione.
7. **Riconciliazione BULK di sola lettura.** Individua batch attivi con righe
   incoerenti, vendite BULK orfane, annullamenti con righe ancora vive
   e audit incompleti per i soli storni registrati con la nuova versione.
   Con filtro data questi controlli sono esclusi per non inventare
   discrepanze su lotti visualizzati parzialmente.
8. **Report.** Il report periodico usa due passaggi streaming, mantiene
   il supporto alle date legacy ed elabora soltanto vendite correnti e
   precedenti all'interno della finestra. Anche trend e calcoli cash
   supplementari evitano il precedente caricamento integrale in memoria.
9. **Date commerciali.** La Dashboard usa Europe/Rome; la
   riconciliazione rifiuta date ISO formalmente corrette ma inesistenti.
10. **Backup cifrato indipendente, opt-in.** Lo script
   `scripts/encrypted_backup.py` prevede `mongodump --archive --gzip`
   diretto a GnuPG, senza una copia in chiaro, su un supporto esterno
   persistente. `scripts/backup_health.py` controlla presenza, età e hash.
   Non esiste alcuna schedulazione attiva.

## Evidenza, test e limiti

- Test automatici backend e frontend: eseguiti mediante GitHub Actions
  a ogni commit, con stato consultabile nella pagina Actions del branch.
  Riportare in release **solo** il risultato della CI dell'ultimo commit.
- Test GPG: gli scenari automatizzati usano un archivio sintetico e una
  chiave usa-e-getta. Confermano il percorso crittografico, **non** una
  certificazione di backup MongoDB recuperabile.
- Test transazioni: la suite comune usa Mongo simulato. Le certificazioni
  su replica set usa-e-getta e failover multi-nodo non sono state eseguite
  durante questa ripartenza.
- Dati reali Atlas, log di Render e commit effettivamente servito dalla
  webapp: **NON VERIFICATI**.
- L'annullamento preesistente delle vendite manuali, POS e CSV rimane da
  analizzare separatamente per uniformare la conservazione dello storico.
- L'impronta client è soltanto un identificatore locale; il backend
  verifica il fingerprint crittografico SHA-256.
- La deduplicazione legacy senza chiave esplicita su payload identici
  deve essere ridisegnata prima di consentire due operazioni identiche
  legittime da vecchi client.
- Le letture streaming non garantiscono da sole una fotografia coerente
  in caso di scritture concorrenti: benchmark e certificazione DB reale
  restano necessari.
- Il backup in locale non garantisce indipendenza se eseguito sul
  filesystem effimero di Render. È necessario un supporto indipendente,
  un processo schedulato autorizzato e un restore di prova.

## Lavori ancora aperti

| Priorità | Intervento | Stato |
|---|---|---|
| P0 | Certificazione delle operazioni BULK su replica set usa-e-getta | Non eseguita in questa sessione |
| P0 | Confronto read-only giacenze e movimenti su Atlas | Accesso operativo non disponibile |
| P1 | Backup indipendente su destinazione persistente con restore certificato | Script disponibile, infrastruttura non attivata |
| P1 | Riconciliazione economica completa vendite/cassa/baseline | Analisi e modello da completare |
| P1 | Migrazioni di avvio esplicite e versionate | Da implementare in PR separata |
| P2 | Autenticazione individuale e permessi backend | Da progettare e migrare |
| P2 | Indici e report su grandi volumi con `explain()` reale | Streaming implementato, benchmark da eseguire |
| P2 | Revisione dipendenze frontend e sicurezza deploy | Da certificare |
| P3 | CRA/CRACO e modernizzazione frontend | PR distinta |
| P3 | Desktop Tauri offline e conflitti multi-postazione | Branch sperimentale separato |

## Rilascio controllato

1. Attendere CI verde per **l'ultimo commit**.
2. Eseguire review di tutta la diff, inclusi error handling e cambiamenti
   a report/cassa/magazzino.
3. Eseguire test transazionali su MongoDB usa-e-getta con replica set.
4. Preparare staging isolato con DB di test.
5. Verificare idempotenza, annullamenti, audit e report con dati sintetici.
6. Esaminare le anomalie in sola lettura; mai correggerle automaticamente.
7. Solo dopo autorizzazione, pianificare l'integrazione e il deploy.
8. Verificare il commit **realmente** eseguito su Render e smoke test.
9. In caso di regressione, rollback del codice valutato sull'effettivo
   stato dati; non eseguire un restore operativo alla cieca.

## Fonti

- [Commit di partenza](https://github.com/loreferra91/GESTIONALE-TABACCHI/commit/28c53c77dec714e822b003b46df724ee42cf2e19)
- [Documentazione BULK di base](BULK_INTEGRITY.md)
- [Runbook backup indipendente](ENCRYPTED_BACKUP_RUNBOOK.md)
