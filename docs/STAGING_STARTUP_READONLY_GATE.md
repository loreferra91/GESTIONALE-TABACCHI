# Staging — startup senza migrazioni implicite

## Problema confermato (19 novembre 2026)

Il vecchio `backend/server.py:on_start()` esegue ad ogni avvio
seed, parametri di default, creazione indici, assegnazione ID ai
prodotti legacy, riconciliazione ADM/vending, ripristino snapshot,
riparazioni Smart Venue e stato contabile vending. Su staging il
precedente `except` registrava i problemi senza necessariamente
bloccare l'avvio: un errore di migrazione poteva lasciare online un
backend con lo schema parzialmente aggiornato.

## Soluzione conservativa

Si introduce il **solo per staging** parametro
`STAGING_STARTUP_MODE`:

- `legacy` (predefinito): stesso comportamento precedente, tranne
  che ora gli errori iniziali bloccano correttamente staging.
- `read_only`: **nessuna chiamata** a `on_start()` durante il normale
  avvio. Esegue esclusivamente un preflight di lettura e blocca
  l'avvio se manca una dipendenza necessaria.

L'applicazione resta operativa: **le API ordinarie possono
continuare a scrivere** durante l'uso al banco. La modalità
`read_only` riguarda soltanto l'inizializzazione all'avvio.

Il preflight verifica:
1. Indici univoci delle chiavi operative di prodotti, vending,
   parametri e delle operazioni idempotenti (vendite, cassa,
   ricezioni merce, prelievi, Smart Venue, audit ecc.).
2. Presenza delle tre migrazioni note; solo la migrazione indici
   usa il campo `completed: true`. Le migrazioni Auto-Order e
   colonne vending usano la **presenza del record**, non
   `completed`, come indicatore.
3. Assenza di colonne H1/H2/H3 in attesa di conversione.
4. Presenza di tutti i parametri applicativi di default.
5. Assenza di prodotti legacy privi di ID.

Non aggiorna dati, indici, baseline né crea backup: se il
preflight fallisce è necessaria una manutenzione separata,
documentata e approvata.

## Prove e attivazione (staging soltanto)

1. CI: `python -m pytest backend/tests/test_staging_readonly_startup.py -q`,
   suite backend, frontend e build.
2. Indici, migrazioni, parametri e ID verificati su Atlas staging
   in sola lettura. In caso di incongruenze **non correggere
   automaticamente**, mantenere il valore `legacy`.
3. Prima del cambio accertarsi che il deploy Git precedente sia
   live e che lo staging sia funzionante. Salvare i riferimenti
   al commit e al precedente valore del flag.
4. In Render **staging** impostare solo
   `STAGING_STARTUP_MODE=read_only`, tramite aggiornamento
   merge delle variabili (mai sostituire tutte le env).
   Non alterare `MONGO_URL`, credenziali o `DB_NAME`.
5. Verificare il riavvio, `GET /api/`, commit esatto,
   DB health, endpoint backup senza autenticazione (401),
   e assenza di errori di preflight nei log.
6. Ripetere la riconciliazione **read-only** senza rettifiche.

**Rollback configurazione:** riportare il SOLO flag a
`STAGING_STARTUP_MODE=legacy`, riavviare lo staging e
verificare nuovamente health, commit e connessione al DB.
Usare questo rollback esclusivamente dopo aver valutato le
scritture legacy che verranno ripristinate all'avvio; se il
preflight ha rilevato dati incoerenti, preferire la
manutenzione esplicita su copia.

## Limiti

- Le operazioni mutanti di manutenzione rimangono nel vecchio
  `on_start()` per compatibilità; questa PR **non** le
  trasforma in migrazioni versionate separate.
- Lo staging può fallire all'avvio dopo un restore incompleto o
  nuove feature che richiedano indici/default aggiuntivi: è
  un comportamento **intenzionalmente fail-closed**.
- Non abilitare in produzione tramite questa PR. La
  promozione richiede test e piano di migrazione indipendenti.
- Non usare `read_only` per simulare un backup consistente;
  non imposta transazioni o un lock delle API.
