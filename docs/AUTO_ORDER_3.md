# Auto-Order 3.0 — confronto controllato

Stato: **motore sperimentale in sola lettura**. `GET /api/auto-order/confronto`
calcola 2.0 e 3.0 sugli stessi prodotti e sulle vendite native `vendite`, ma
`POST /api/auto-order/conferma`, PDF ed Excel continuano a usare 2.0. Il nuovo
motore non è un interruttore di produzione e non scrive in MongoDB.

## Fonti e qualità

- Le vendite sono lette da `vendite`, divise per `NEGOZIO` e `VENDING`. Le
  ricariche vending restano trasferimenti interni. I documenti con lo stesso
  `id` o `csv_event_key` esplicito vengono contati una volta; chiavi in
  conflitto, CSV senza chiave e possibili sovrapposizioni APP/CSV sospendono la
  proposta 3.0 del prodotto. Due vendite simili senza ID non sono unite per
  supposizione.
- Il periodo osservabile deriva dalla prima vendita nativa nella finestra di
  90 giorni. Non prova che tutti i giorni siano stati registrati: una giornata
  senza vendite può essere legittima. Un prodotto senza vendite non riceve una
  domanda artificiale. La qualità è esposta per riga.
- Le giacenze vengono da `prodotti.giacenza_negozio` e dalla giacenza vending
  operativa; gli arrivi da ordini fornitore attivi, con residuo e data. Un
  ordine senza data affidabile produce un avviso e sospende la proposta 3.0.
  Una fornitura futura entra nella simulazione solo il giorno di consegna,
  prima del consumo di quel giorno. La merce vending non è disponibile al banco.

## Previsione e scelta riproducibile

Baseline per canale: `0,70 × (venduto ultimi 10 giorni / 10) + 0,30 ×
(venduto ultimi 30 giorni / 30)`. I giorni a zero restano nella serie. In
assenza di 60 giorni osservabili si usa la baseline e si segnala l'incertezza.
Le alternative sono media mobile 14 giorni, smoothing esponenziale con
`alpha=0,30` e Croston SBA con `alpha=0,20` per domanda intermittente.

Il backtest è rolling origin a un giorno, sugli ultimi 28 punti con almeno
30 giorni di training: ogni previsione usa solo i giorni precedenti. Per
ciascun modello si restituiscono MAE, WAPE quando il denominatore è positivo,
bias e frequenza di sottostima. Un'alternativa sostituisce la baseline soltanto
con almeno 10% di MAE in meno, bias assoluto non peggiore di 0,05 e frequenza
di sottostima non peggiore di 0,05. Occorrono inoltre almeno 10 pezzi negli
ultimi 28 giorni. Altrimenti resta la baseline. Le classi FAST, NORMAL, SLOW,
INTERMITTENTE, NUOVO e SENZA DOMANDA descrivono la domanda osservata, non
impongono vendite ipotetiche.

**Limite di validazione:** i test usano serie sintetiche isolate. Questa
versione non contiene un backtest quantitativo su vendite reali di staging:
senza accesso verificato a un dataset isolato non esistono MAE/WAPE aziendali
da dichiarare. L'API mostra i punteggi soltanto dove lo storico nativo è
sufficiente e rimane in sola lettura.

Con un lead time configurato, il confronto simula separatamente le quantità
2.0 e 3.0 sullo stesso inventario iniziale, con il nuovo ordine disponibile
solo alla data teorica di consegna. Riporta rotture e giacenza finale positiva
media, oltre al MAE medio per classe di rotazione. Senza lead time non
confronta date di arrivo inventate.

## Inventario e riordino

Per la revisione periodica si usa `P = lead_time + review_period`; senza lead
time configurato si usa in modo conservativo il target giorni di 2.0 e non
si attribuisce una data di arrivo al nuovo ordine. I parametri commerciali
restano quelli configurati: non sono stati inventati giorni settimanali di
consegna né livelli di servizio.

Per ogni giorno si aggiungono gli arrivi datati, si effettua al massimo un
trasferimento interno iniziale dal negozio alla vending preservando il minimo
negozio, poi si sottrae la domanda prevista dei due canali. Si espongono la
prima data di esaurimento, il rischio prima del lead time, la prossima
consegna e la disponibilità prevista. Un nuovo ordine non è presentato come
soluzione se lo stock finisce prima del lead time.

La posizione inventariale è `stock negozio + stock vending + residui degli
ordini datati entro P`. Il fabbisogno è il maggiore tra il deficit negozio
(`domanda negozio × P + minimo negozio + trasferimento - stock negozio -
arrivi`) e il deficit complessivo (`obiettivo + minimo negozio - stock
fisico totale - arrivi`), mai inferiore a zero. La scorta di sicurezza è
`ceil(domanda totale × P × (fattore_sicurezza - 1))`, una sola volta;
attualmente il fattore è quello configurato, normalmente 1,15. Punto di
riordino: `ceil(domanda totale × lead_time) + sicurezza`. Obiettivo:
`ceil(domanda totale × P) + sicurezza`. La quantità è
`ceil(fabbisogno / lotto) × lotto` per lotto intero positivo.

La variabilità della domanda e del lead time non è ancora convertita in un
livello di servizio statistico: mancano un livello aziendale approvato e una
serie verificata dei tempi effettivi di consegna. In questa versione la
scorta usa quindi una sola maggiorazione 1,15, evitando margini sovrapposti.
I costi di acquisto sono mostrati solo se presenti; il prezzo di listino non
viene usato come costo. Nessun vincolo di budget elimina articoli critici.

## Difetto 2.0 e protezioni

La conferma 2.0 ora ammette una fornitura datata già pendente quando la riga
è `ORDINA ORA` e il fabbisogno netto arrotondato al lotto è positivo. La
quantità selezionata non può superare quel fabbisogno. Una fornitura senza
data affidabile o sufficiente a coprire il fabbisogno resta bloccante. La
selezione viene ricalcolata dal backend al momento della conferma: snapshot,
versione prodotto, giacenza, prezzo, ordini pendenti e idempotenza seguono le
protezioni di 2.0. PDF ed Excel usano la medesima selezione 2.0.

Per `DATI_PARZIALI` la conferma richiede ora una scelta esplicita e un motivo
di almeno 10 caratteri. Motivo, stato e watermarks sono salvati nel batch e
in `audit_events` nella stessa transazione, o rimossi dal rollback compensato
nei database senza transazioni. Le anomalie bloccanti continuano a bloccare.
Un periodo senza vendite da solo non dimostra perdita di dati.

## Baseline contabile e cutover

`dashboard_router.py` legge facoltativamente `accounting_baselines` con
`id=webapp-opening`, `effective_at` UTC attestato, `validated_at`,
`shop_cash_sales_opening`, `vending_payments_opening` per tipo pagamento,
`source_reference` e note di riconciliazione. Il codice **non crea** questa
baseline, non migra e non cancella dati. Quando esiste, il saldo di apertura
resta stabile e vendite native, prelievi e rettifiche successive sono
calcolati dal database; modifiche successive dello storico Excel non
variano questi saldi. Quando manca, il percorso storico resta disponibile
con stato `LEGACY_DA_RICONCILIARE`, per non azzerare saldi validi e non
inventare il cutover. Le KPI di acquisto ancora legate alla ricostruzione
storica richiedono una baseline distinta prima dell'eliminazione del fallback.

La data effettiva di migrazione e la completezza dei flussi contabili non
sono attestate dal codice. Prima di inserire una baseline su staging servono
riconciliazione documentata, verifica dei movimenti al confine e backup. In
questa modifica non è stata scritta alcuna baseline sul database reale.

## CI e staging

`.github/workflows/ci.yml` verifica push su `main`, branch staging,
`feature/**`, `codex/**` e tutte le PR: compile Python, pytest backend,
test frontend e build. La regression suite locale è l'intera
`backend/tests` più i test frontend. `render.yaml` descrive solo il servizio
di produzione. `render.staging.example.yaml` è un modello non applicato:
servizio `gestionale-tabacchi-staging`, `DB_NAME=gestionale_staging`,
credenziali dedicate non sincronizzate, `ENVIRONMENT=staging`, dominio CORS
separato e `autoDeploy=false`. Non prova da solo che il servizio Render
esistente usi un cluster/database isolato. Prima di eseguire prove con dati
di staging occorre verificare in Render e Atlas i valori effettivi senza
esporre segreti, oltre a backup e procedura di rollback. Nessun deploy o
migrazione sono parte di questo branch.


## Hardening 3.1 (branch feature/auto-order-3-1-hardening)

**Stato: ancora shadow / sola lettura.** Queste correzioni non attivano 3.1
per la conferma degli ordini, non modificano le giacenze e non eseguono
migrazioni contabili.

### Fotografia temporale

Lo stock ottenuto da MongoDB è lo stock fisico **già aggiornato** dalle
vendite registrate durante la giornata. La simulazione non sottrae una
seconda giornata intera di consumi oggi: il primo giorno commerciale
completo previsto è **domani** (`forecast_first_full_day`).

La domanda residua di oggi non può essere inferita senza dati infragiornalieri
validati. Per questo `intraday_demand_not_modelled` espone il limite.
Le consegne datate sono trattate come disponibili all'inizio della loro data
nella simulazione; l'ora effettiva non è nota. Una stima di stockout non è
una garanzia di consegna né una prova di disponibilità intragiornaliera.

Dopo una rottura simulata, la disponibilità viene riportata a zero:
la domanda inevasa **non** diventa inventario fisico negativo né un carico
da recuperare automaticamente nella fornitura successiva.

### Trasferimenti interni vending

Le ricariche interne possono essere simulate in **più giorni** e dopo
una consegna fornitori. `vending_transfer_schedule` contiene le quantità
e le date consigliate. Si tutela `minimum_shop` e la domanda del banco
fino alla successiva ricezione nota, senza doppia allocazione.

Il confronto recupera `capacita_max` dalle colonne operative `vending` e
somma le capacità per codice; non assume una capacità fisica arbitraria.
Se una vending ha domanda ma non è verificabile la capacità, la proposta
3.1 è sospesa (`CAPACITA_VENDING_NON_VERIFICATA`). Lo stesso vale quando
la capacità dichiarata è inferiore alla giacenza fisica
(`CAPACITA_VENDING_INCOERENTE`). Nessuna ricarica simulata viene eseguita.

### Storico nativo: lettura incrementale

L'endpoint `/api/auto-order/confronto` non tronca più la richiesta
a 50.000 eventi. Utilizza un cursore MongoDB e
`NativeSalesAccumulator` per aggregare la domanda a grana
prodotto/giorno/canale senza creare una lista completa delle transazioni.

La memoria **non è costante**: la deduplicazione conserva gli identificativi
espliciti degli eventi; con centinaia di migliaia di chiavi distinte
il consumo cresce. Anche la query sui formati data legacy richiede ancora
una strategia di normalizzazione e indicizzazione prima di certificare
prestazioni su dataset molto grandi.

I benchmark di carico reali, Atlas e l'isolamento di Render staging
non sono attestati da queste modifiche. La suite sintetica verifica
correttezza e assenza del precedente limite, non rappresenta un
benchmark aziendale o la certificazione delle vendite reali.

### Contabilità e autonomia dalla migrazione Excel

La baseline Mongo validata rimane il percorso autorevole per saldi
operativi dopo il cutover. In assenza della baseline, il fallback legacy
**non è stato rimosso**: farlo senza un saldo iniziale verificato
rischierebbe di alterare la cassa. Lo stato contabile va riconciliato
su uno staging isolato con evidenza documentale prima del taglio definitivo.

### Criteri residui prima dell'abilitazione degli acquisti 3.1

- Eseguire backtest temporali su dati reali di staging, autorizzati e verificati.
- Verificare capacità, scorte e colonne reali della vending.
- Confrontare la pianificazione con gli orari effettivi delle consegne.
- Verificare prestazioni MongoDB su almeno 100.000 e 500.000 eventi.
- Riesaminare gli skip della CI e testare concorrenza su Atlas isolato.
- Registrare e validare le baseline contabili senza supposizioni.

In mancanza di tali verifiche: **PASS CON RISERVE, non pronto per produzione**.
