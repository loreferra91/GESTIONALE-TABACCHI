# Staging — copertura della riconciliazione di cassa

La riconciliazione esistente verifica importi monetari, coerenza
fra vendita POS/Cassa Rapida e relativo movimento `cassa`,
oltre a pagamenti CSV, stock e audit. Non verifica il
conteggio fisico di contante né un fondo di apertura certificato.

## Dato reale rilevato nello staging (19 novembre 2026)

Il database `gestionale_staging` non conteneva alcun documento
nelle collezioni `cassa` e `cassa_rapida_batches` durante
la verifica iniziale. Ciò **non** significa che la cassa
fisica fosse pari a zero, né che gli incassi storici fossero
tutti riconciliati.

## Soluzione

L'endpoint esistente `GET /api/contabilita/riconciliazione`
aggiunge un oggetto `copertura_finanziaria`:

- `movimenti_cassa_esaminati`: numero di movimenti contabili
  nativi effettivamente sottoposti al controllo (eventualmente
  filtrati per data);
- `batch_cassa_rapida_esaminati`: numero batch controllati
  quando il periodo NON è filtrato; altrimenti `null`;
- `confronto_batch_pos_eseguito`: conferma che la verifica
  globale dei batch è stata svolta;
- `baseline_cassa_fisica_verificata: false` e
  `saldo_cassa_certificato: false`, espliciti e non inferiti;
- `avviso`: avverte se mancano movimenti cassa oppure se
  è stata eseguita soltanto una verifica documentale.

Il pannello `ReconciliationPanel` presenta il limite
accanto alle segnalazioni. Il campo `esito` esistente
non cambia, per preservare compatibilità con i client.
Non vengono create baseline, vendite, pagamenti o movimenti.

## Prove

- Suite backend: nessun movimento cassa significa
  **copertura assente**, non saldo zero.
- Con movimenti presenti, il saldo fisico rimane
  **non certificato**.
- Il filtro data non presenta i controlli POS globali
  come se fossero eseguiti.
- Suite frontend: avviso e conteggi visibili e
  accessibili nella schermata contabilità.
- I test verificano che nessuna collezione venga scritta.

## Condizione per una futura certificazione fisica

Una futura riconciliazione reale dovrà disporre di
un fondo di apertura **documentato**, un conteggio
fisico di chiusura, documenti bancari/POS e un
intervallo di competenza definito. È proibito
inventare questi dati o sommare automaticamente
lo storico Excel alle vendite native per quadratura.

**Questa modifica è informativa e solo per staging,
non autorizza rettifiche o cancellazioni.**
