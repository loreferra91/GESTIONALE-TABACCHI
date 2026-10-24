# MEMORY.md - Durable Project Decisions

## Gestionale Tabacchi

- Dal 2026-10-24 la contabilità vending resta invariata e costituisce la fonte
  autorevole del contante fisico: `giacenza iniziale + vendite in contanti -
  prelievi lordi`. Una migrazione idempotente riallinea soltanto
  `cassa_vending_stato`, con backup e audit preventivi; gli scontrini continuano
  a incidere solo sul prelievo contabile netto.
- Dal 2026-10-10 il **Prelievo da vending** contabile è netto degli scontrini:
  `prelievo netto = totale prelievi registrati - totale scontrini vending`.
  Esempio di accettazione: `3.527,05 € - 327,90 € = 3.199,15 €`.
- La giacenza fisica della vending continua a sottrarre il prelievo lordo
  registrato; il saldo combinato di negozio e vending usa invece il prelievo
  netto. Questa decisione sostituisce la regola annotata il 2026-10-07 che
  lasciava gli scontrini separati anche dal prelievo e dal saldo combinato.
- Dal 2026-10-14 ogni carico merce incrementa anche il saldo Smart Venue dei
  prodotti gia presenti in Smart Venue, oltre a `giacenza_negozio` e
  `acquistati`. Il carico Auto-Order del 12/10/2026 è stato riallineato con una
  migrazione idempotente di 27 righe/810 pezzi e backup preventivo.
- Dal 2026-10-15 i carichi merce bulk sono idempotenti tramite
  `ricezioni_merce.idempotency_key`; ogni riga Smart Venue conserva nello
  storico ordini una conferma esterna separata, che non modifica le scorte.
  La pagina **GIACENZE REALI** usa `prodotti.giacenza_negozio` e la somma delle
  colonne `vending`, registra le correzioni in `rettifiche_inventario` senza
  creare vendite, e offre le stampe A4 inventario in bianco e giacenze attuali.
- Dal 2026-10-15 la contabilità CSV vending è un registro incrementale: ogni
  export mobile aggiunge soltanto gli eventi nuovi tramite `csv_event_key` e
  non elimina più le vendite uscite dalla finestra delle ultime 1.000 righe.
  I backup MongoDB interni hanno retention di 3 copie e gli import Excel usano
  snapshot parziali delle sole collezioni modificate, per restare entro Atlas M0.
- Dal 2026-10-16 nella pagina **Prodotti** il totale Venduti apre un editor in
  cui è modificabile soltanto `venduti_negozio`; `venduti_vending` resta in sola
  lettura e il totale è calcolato. Le correzioni sono protette da confronto col
  valore atteso, non cambiano le giacenze e vengono registrate in
  `rettifiche_venduti`.
- Dal 2026-10-18 l'integrità vendite è fail-closed: le vendite manuali usano una
  chiave idempotente e non possono portare la scorta sotto zero; bulk e CSV
  rifiutano l'intero caricamento se una riga, un prodotto o una colonna non è
  valido; gli annullamenti sono vincolati alla sorgente e transazionali su
  MongoDB. I backup non hanno più il limite di 25.000 documenti, includono
  checksum e lock di restore. In produzione autenticazione completa e CORS non
  wildcard sono requisiti di avvio.
- Dal 2026-10-19 la Cassa Rapida registra il metodo di pagamento e mantiene
  separati il contante fisico da POS, Satispay e altri elettronici. L'annullo
  dell'ultima vendita ripristina atomicamente scorte, vendite e movimento di
  cassa. Gli indici business su codice prodotto, colonna vending e nome
  parametro vengono migrati a univoci con controllo duplicati fail-safe.
- Dal 2026-10-20 la vecchia pagina **Magazzino / Pivot** confluisce in
  **Giacenze reali**, organizzata nelle tab **Inventario** e **Analisi scorte**.
  L'analisi mostra separatamente giacenza negozio, vending e totale; usa la
  giacenza negozio per copertura/riordino e il totale per la valorizzazione.
  Il vecchio URL `/magazzino` reindirizza alla nuova analisi e l'import Excel
  non viene più evidenziato nella barra di stato dell'interfaccia.
- Dal 2026-10-21 l'aggiornamento del listino FIT è fail-closed: errori o conteggi
  di scrittura incompleti non possono essere registrati come sincronizzazioni
  riuscite. L'anteprima Listino mostra il confronto campo per campo prima/dopo
  per righe nuove e aggiornate, inclusi i riempimenti di codice ADM, categoria
  e fonte, e resta visibile come riepilogo dopo l'applicazione.
- Dal 2026-10-21, revisione completa: `backend/requirements.txt` contiene SDK
  mai importati nel codice (litellm, openai, google-generativeai, stripe,
  boto3, passlib, bcrypt, python-jose, oauthlib) ereditati dal template
  Emergent; `backend/requirements-prod.txt` resta il set minimale reale di
  produzione (14 pacchetti) e non li include. `emergentintegrations==0.2.0`
  non è pubblicato su PyPI e non è importato: va escluso quando si
  risincronizza un venv locale da `requirements.txt`. Aggiornati con patch
  compatibili (commit 883a140): certifi 2026.10.19, multidict 6.9.1,
  pillow 13.0.0, PyJWT 2.15.0; verificato con 266 test backend e 33 frontend.
  Il venv locale di sviluppo risaliva al 23/09 e girava su librerie vecchie
  (starlette 0.37.2, pymongo 4.6.3, fastapi 0.110.1) nonostante i pin corretti
  nel repo: risincronizzato. Rimasti senza fix compatibile non rischioso
  (SDK non usati o major breaking): litellm, python-jose, oauthlib, ecdsa,
  aiohttp (dipendenza di litellm), starlette 1.x (richiede major bump non
  valutato). Branch locali stale rimossi (0 commit avanti a main):
  allinea-contabilita-test, applica-tutto-venduti-giacenza,
  codex/fix-adm-sync-timeout, codex/mongo-storage-csv-ledger,
  codex/webapp-category-updates, ft/fix-prelievi-resti, main-merged.
  `codex/auto-order-optimization` è rimasto (worktree attivo in
  `/Users/Lorenzo/.codex/worktrees/`), non eliminabile senza rimuoverlo prima.
- Dal 2026-10-21, bug critico nella sync listino: il pulsante di fallback
  "PDF ADM" (`/adm/sync` in `adm_router.py`, invocato da `syncAdmFallback`
  nella pagina Listino) aggiornava i prodotti con un filtro che includeva il
  match sul codice ADM numerico nudo (es. `"938"`), oltre al codice locale
  con prefisso `AMMS`. Un prodotto/accessorio non-tabacco che usa quello
  stesso numero come proprio `codice` (es. REX BRAVO CARTINE CORTE, codice
  `938`, categoria ACCESSORI) veniva quindi sovrascritto con categoria e
  prezzo del prodotto da fumo omonimo. Il percorso nuovo `/fit/apply`
  (sync listino FIT introdotta lo stesso giorno) non ha questo bug: usa
  `_catalogue_adm_code`, che richiede sempre il prefisso `AMMS` esplicito.
  Fix (commit 83e63a1): `_adm_product_update_operations` ora matcha solo sul
  codice locale `AMMS{n}`, mai sul numero nudo; aggiunti 2 test di
  regressione in `test_fit_tariff.py` sul caso REX BRAVO/938. **Non risolto:**
  i prodotti già corrotti in produzione dalla sync legacy precedente a questo
  fix non sono stati riparati — l'endpoint non crea un backup preventivo e
  serve accesso diretto al MongoDB Atlas di produzione (credenziali non
  disponibili in questo workspace) per enumerare e ripristinare i prodotti
  non-AMMS il cui codice numerico coincide con un codice ADM/FIT ufficiale.
  <!-- project: github.com/loreferra91/GESTIONALE-TABACCHI -->
