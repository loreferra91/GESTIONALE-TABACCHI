"""Read-only operational checks; never infer stock or cash from unrelated histories."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json

if __package__:
    from .money import money_decimal
else:
    from money import money_decimal


RECONCILIATION_STATES = {"aperta", "in_verifica", "risolta", "ignorata"}


def _finding_fingerprint(kind, collection, document, values):
    """Identify the same operational finding across repeated read-only scans."""
    reference = str(document.get("id") or document.get("codice") or document.get("_id") or "")
    identity = {
        "tipo": kind,
        "raccolta": collection,
        "riferimento": reference,
        "campo": values.get("campo"),
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def reconcile(database, payment_bucket, detail_limit=100, date_from=None, date_to=None, case_states=None):
    findings = []
    counts = Counter()
    state_counts = Counter()
    scanned = Counter()
    case_states = case_states or {}

    def in_date_range(doc):
        if not date_from and not date_to:
            return True
        raw = doc.get("data") or doc.get("created_at") or doc.get("first_seen_at")
        value = str(raw or "")[:10]
        return (not date_from or value >= str(date_from)) and (not date_to or value <= str(date_to))

    def issue(kind, collection, document, message, **values):
        counts[kind] += 1
        fingerprint = _finding_fingerprint(kind, collection, document, values)
        workflow = case_states.get(fingerprint) or {}
        state = workflow.get("stato")
        if state not in RECONCILIATION_STATES:
            state = "aperta"
        state_counts[state] += 1
        if len(findings) < detail_limit:
            findings.append({
                "tipo": kind, "raccolta": collection,
                "riferimento": str(document.get("id") or document.get("codice") or document.get("_id") or ""),
                "messaggio": message, "fingerprint": fingerprint, "stato": state,
                "nota": str(workflow.get("nota") or ""),
                "aggiornato_il": workflow.get("updated_at"),
                "operatore": workflow.get("operatore"),
                **values,
            })

    def amount(collection, doc, field, signed=False):
        try:
            value = money_decimal(doc.get(field), nonnegative=not signed)
            raw = Decimal(str(doc[field]).strip().replace(",", "."))
            # Ignore harmless binary-double tails, not real fractions of a cent.
            if abs(raw - value) > Decimal("0.000000001"):
                issue("FRAZIONI_CENTESIMO", collection, doc,
                      f"{field}: valore storico con frazioni di centesimo; verificare la fonte.",
                      campo=field, valore=str(doc[field]), normalizzato=float(value))
            return value
        except (ValueError, TypeError):
            issue("IMPORTO_INVALIDO", collection, doc,
                  f"{field}: importo mancante, non numerico, non finito o fuori limite.", campo=field)
            return None

    def stock(collection, doc, field):
        # Missing historical stock is UNKNOWN, not zero. Never fabricate a
        # baseline or silently compare incomplete product/vending records.
        if field not in doc or doc[field] is None:
            issue(
                "GIACENZA_MANCANTE", collection, doc,
                f"{field}: valore assente; impossibile presumere una giacenza zero.",
                campo=field,
            )
            return None
        raw = doc[field]
        try:
            value = Decimal(str(raw))
            if isinstance(raw, bool) or not value.is_finite() or value < 0 or value != value.to_integral_value():
                raise ValueError()
            return int(value)
        except (ValueError, ArithmeticError):
            issue("GIACENZA_INVALIDA", collection, doc, f"{field}: la giacenza deve essere intera e non negativa.", campo=field)
            return None

    products = defaultdict(list)
    async for doc in database.prodotti.find({}):
        scanned["prodotti"] += 1
        amount("prodotti", doc, "prezzo")
        stock("prodotti", doc, "giacenza_negozio")
        value = stock("prodotti", doc, "giacenza_vending")
        products[str(doc.get("codice") or "").strip()].append((doc, value))
    for code, candidates in products.items():
        if len(candidates) > 1:
            issue("CODICE_PRODOTTO_DUPLICATO", "prodotti", candidates[0][0],
                  f"Codice {code} presente in {len(candidates)} prodotti: confronto giacenze non eseguito.")
    columns = defaultdict(list)
    async for doc in database.vending.find({}):
        scanned["vending"] += 1
        value = stock("vending", doc, "giacenza")
        code = str(doc.get("codice") or "").strip()
        if code:
            columns[code].append(value)
            if code not in products:
                issue("COLONNA_ORFANA", "vending", doc, f"Il prodotto {code} associato alla colonna non esiste.")
    compared_products = 0
    for code, values in columns.items():
        candidates = products.get(code, [])
        # Ambiguous or malformed data cannot establish an expected quantity.
        if len(candidates) != 1 or None in values or candidates[0][1] is None:
            continue
        doc, saved = candidates[0]
        expected = sum(values)
        compared_products += 1
        if saved != expected:
            issue("GIACENZA_VENDING", "prodotti", doc,
                  f"{code}: giacenza prodotto {saved}, somma colonne {expected}.",
                  registrato=saved, atteso=expected, differenza=saved - expected)

    events = defaultdict(list)
    async for doc in database.vending_accounting_events.find({}):
        if not in_date_range(doc):
            continue
        scanned["vending_accounting_events"] += 1
        value = amount("vending_accounting_events", doc, "importo")
        for field in ("csv_event_key", "csv_accounting_key"):
            if isinstance(doc.get(field), str) and doc[field]:
                key = (field, doc[field])
                if events[key]:
                    issue("IDENTITA_CSV_DUPLICATA", "vending_accounting_events", doc,
                          f"Identità {field} presente in più eventi: confronto ambiguo.")
                events[key].append((doc, value))
    compared_sales = unmatched_sales = 0
    bulk_sales_by_batch = Counter()
    pos_sales_by_batch = {}
    async for doc in database.vendite.find({}):
        if not in_date_range(doc):
            continue
        scanned["vendite"] += 1
        value = amount("vendite", doc, "importo")
        if not date_from and not date_to and doc.get("sorgente") == "BULK":
            bulk_sales_by_batch[str(doc.get("batch_id") or "")] += 1
        if not date_from and not date_to and doc.get("sorgente") == "POS":
            batch_id = str(doc.get("batch_id") or "")
            aggregate = pos_sales_by_batch.setdefault(batch_id, {
                "righe": 0, "pezzi": 0, "importo": Decimal(0),
                "valido": True, "pagamenti": set(),
            })
            aggregate["righe"] += 1
            aggregate["pagamenti"].add(str(doc.get("pagamento") or "").strip().upper())
            try:
                quantity = Decimal(str(doc.get("quantita")))
                if (not quantity.is_finite() or quantity <= 0
                        or quantity != quantity.to_integral_value()):
                    raise ValueError()
                aggregate["pezzi"] += int(quantity)
            except (TypeError, ValueError, ArithmeticError):
                aggregate["valido"] = False
                issue("POS_QUANTITA_INVALIDA", "vendite", doc,
                      "Riga Cassa Rapida con quantità non intera o non positiva.")
            if value is None:
                aggregate["valido"] = False
            else:
                aggregate["importo"] += value
        if doc.get("sorgente") != "CSV_VENDING":
            continue
        matches = []
        for field in ("csv_event_key", "csv_accounting_key"):
            if isinstance(doc.get(field), str) and events.get((field, doc[field])):
                matches = events[(field, doc[field])]
                break
        if len(matches) != 1:
            unmatched_sales += 1
            continue
        event, expected = matches[0]
        if value is None or expected is None:
            continue
        compared_sales += 1
        if value != expected or payment_bucket(doc.get("pagamento")) != payment_bucket(event.get("pagamento")):
            issue("IMPORTO_PAGAMENTO_CSV", "vendite", doc,
                  "La vendita e lo stesso evento contabile hanno importo o pagamento diversi.",
                  registrato=float(value), atteso=float(expected), differenza=float(value - expected),
                  pagamento_vendita=doc.get("pagamento"), pagamento_registro=event.get("pagamento"))

    # Cross-check live BULK rows against batch metadata. These checks require
    # the complete history; a date-filtered reconciliation skips them rather
    # than reporting false mismatches for partial batches.
    if not date_from and not date_to:
        audited_undos = {}
        # This collection also stores CSV vending import batches:
        # only shop BULK and legacy source-less batches belong here.
        async for batch in database.vendite_bulk_imports.find({
            "$or": [
                {"sorgente": "BULK"},
                {"sorgente": {"$exists": False}},
            ],
        }):
            scanned["vendite_bulk_imports"] += 1
            batch_id = str(batch.get("id") or "")
            actual = bulk_sales_by_batch.pop(batch_id, 0)
            status = str(batch.get("status") or "")
            try:
                expected = int(batch["inseriti"])
                if expected < 0:
                    raise ValueError()
            except (KeyError, TypeError, ValueError):
                issue("BULK_METADATA_INVALIDI", "vendite_bulk_imports", batch,
                      "Numero di vendite registrate nel batch non valido.")
                continue
            if status == "active" and actual != expected:
                issue("BULK_BATCH_INCOERENTE", "vendite_bulk_imports", batch,
                      "Il numero delle vendite BULK attive diverge dal batch.",
                      registrato=actual, atteso=expected, differenza=actual - expected)
            elif status in {"annulled", "annullata"} and actual != 0:
                issue("BULK_ANNULLO_INCOMPLETO", "vendite_bulk_imports", batch,
                      "Un batch annullato contiene ancora vendite attive.",
                      registrato=actual, atteso=0)
            elif status not in {"active", "annulled", "annullata", "processing", "annulling"}:
                issue("BULK_STATO_NON_VALIDO", "vendite_bulk_imports", batch,
                      "Lo stato del batch BULK non è riconosciuto.",
                      stato_batch=status)
            if status in {"annulled", "annullata"} and batch.get("undo_audit_version") == 1:
                audited_undos[batch_id] = (batch, expected)

        for batch_id, count in bulk_sales_by_batch.items():
            issue("BULK_BATCH_ORFANO", "vendite", {"id": batch_id},
                  "Vendite BULK senza un batch valido collegato.",
                  registrato=count)

        if audited_undos:
            audit_counts = Counter()
            async for event in database.audit_events.find({
                "azione": "annulla-vendita-bulk",
                "batch_id": {"$in": list(audited_undos)},
            }):
                scanned["audit_events_bulk"] += 1
                audit_counts[str(event.get("batch_id"))] += 1
            for batch_id, (batch, expected) in audited_undos.items():
                actual = audit_counts[batch_id]
                if actual != expected:
                    issue("BULK_STORNO_AUDIT_INCOMPLETO", "vendite_bulk_imports", batch,
                          "Mancano documenti di audit per un batch annullato.",
                          registrato=actual, atteso=expected)

    pos_cash_by_batch = defaultdict(list)
    for name, field, signed in (
        ("versamenti", "importo", False), ("prelievi_vending", "importo", False),
        ("scontrini_vending", "importo", False), ("cassa", "importo", True),
        ("storico_ordini", "prezzo", False), ("listino_adm", "prezzo", False),
        ("rettifiche_venduti", "importo_contabile", True),
        ("db_storico_vend", "importo", True),
    ):
        async for doc in database[name].find({}):
            if not in_date_range(doc):
                continue
            scanned[name] += 1
            if name == "rettifiche_venduti" and field not in doc:
                continue  # Legacy corrections deliberately have no monetary effect.
            parsed = amount(name, doc, field, signed)
            if name == "cassa" and not date_from and not date_to and doc.get("batch_id"):
                pos_cash_by_batch[str(doc["batch_id"])].append((doc, parsed))

    # Only native POS rows can be compared to their own batch and cash entry.
    # Historic Excel, manual and vending amounts are NOT added to POS revenue.
    if not date_from and not date_to:
        audited_pos_undos = {}
        seen_pos_batch_ids = set()
        async for batch in database.cassa_rapida_batches.find({}):
            scanned["cassa_rapida_batches"] += 1
            batch_id = str(batch.get("id") or "")
            if not batch_id or batch_id in seen_pos_batch_ids:
                issue("POS_BATCH_DUPLICATO_O_INVALIDO", "cassa_rapida_batches", batch,
                      "Identificativo batch Cassa Rapida mancante o duplicato.")
                continue
            seen_pos_batch_ids.add(batch_id)
            rows = pos_sales_by_batch.pop(batch_id, None)
            cash = pos_cash_by_batch.pop(batch_id, [])
            status = str(batch.get("status") or "").strip().lower()

            if status == "active":
                try:
                    expected_pieces = Decimal(str(batch.get("pezzi")))
                    if (not expected_pieces.is_finite() or expected_pieces <= 0
                            or expected_pieces != expected_pieces.to_integral_value()):
                        raise ValueError()
                    expected_total = money_decimal(batch.get("totale"), nonnegative=True)
                except (ValueError, TypeError, ArithmeticError):
                    issue("POS_METADATI_INVALIDI", "cassa_rapida_batches", batch,
                          "Totale o numero di pezzi del batch non valido.")
                    continue
                if (rows is None or not rows["valido"]
                        or rows["pezzi"] != int(expected_pieces)
                        or rows["importo"] != expected_total
                        or rows["pagamenti"] != {
                            str(batch.get("pagamento") or "").strip().upper()
                        }):
                    issue("POS_VENDITE_INCOERENTI", "cassa_rapida_batches", batch,
                          "Le vendite POS non corrispondono a pezzi, incasso o pagamento del batch.")
                if (len(cash) != 1
                        or cash[0][0].get("id") != batch.get("movimento_cassa_id")
                        or cash[0][0].get("tipo") != "ENTRATA"
                        or str(cash[0][0].get("pagamento") or "").strip().upper()
                            != str(batch.get("pagamento") or "").strip().upper()
                        or cash[0][1] != expected_total):
                    issue("POS_CASSA_INCOERENTE", "cassa_rapida_batches", batch,
                          "Il movimento cassa collegato è assente, duplicato o non corrispondente.")
            elif status in {"annullata", "annulled"}:
                if rows is not None or cash:
                    issue("POS_STORNO_INCOMPLETO", "cassa_rapida_batches", batch,
                          "Un batch annullato contiene ancora vendite o movimenti cassa.")
                if batch.get("undo_audit_version") == 1:
                    audited_pos_undos[batch_id] = batch
            else:
                issue("POS_STATO_INVALIDO", "cassa_rapida_batches", batch,
                      "Stato batch Cassa Rapida non riconosciuto.")

        for batch_id, rows in pos_sales_by_batch.items():
            issue("POS_VENDITE_ORFANE", "vendite", {"id": batch_id},
                  "Vendite Cassa Rapida prive di un batch valido.",
                  registrato=rows["righe"])
        for batch_id, cash in pos_cash_by_batch.items():
            issue("POS_CASSA_ORFANA", "cassa", {"id": batch_id},
                  "Movimento Cassa Rapida senza un batch valido.",
                  registrato=len(cash))
        if audited_pos_undos:
            audit_counts = Counter()
            async for event in database.audit_events.find({
                "azione": "annulla-vendita-cassa-rapida",
                "batch_id": {"$in": list(audited_pos_undos)},
            }):
                scanned["audit_events_pos"] += 1
                audit_counts[str(event.get("batch_id"))] += 1
            for batch_id, batch in audited_pos_undos.items():
                if audit_counts[batch_id] != 1:
                    issue("POS_AUDIT_STORNO_INCOERENTE", "cassa_rapida_batches", batch,
                          "Batch annullato senza un unico evento audit di storno.",
                          registrato=audit_counts[batch_id], atteso=1)

    async for doc in database.vending_accounting_state.find({}):
        scanned["vending_accounting_state"] += 1
        for bucket, value in (doc.get("baseline_importi_pagamenti") or {}).items():
            amount("vending_accounting_state", {**doc, "importo": value}, "importo")
    async for doc in database.parametri.find({"nome": "GIACENZA_INIZIALE_CONTANTI_VENDING"}):
        scanned["parametri"] += 1
        amount("parametri", doc, "valore")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "intervallo": {"da": date_from, "a": date_to},
        "esito": "da_verificare" if counts else "nessuna_discrepanza_rilevata",
        "totale_segnalazioni": sum(counts.values()), "conteggi": dict(counts),
        "conteggi_stato": dict(state_counts),
        "totale_da_gestire": state_counts["aperta"] + state_counts["in_verifica"],
        "dettagli": findings, "dettagli_omessi": sum(counts.values()) - len(findings),
        "documenti_esaminati": dict(scanned), "prodotti_confrontati": compared_products,
        "vendite_csv_confrontate": compared_sales, "vendite_csv_non_confrontabili": unmatched_sales,
        "copertura": [
            "Importi di prodotti, listino, vendite, ordini storici, movimenti, rettifiche, storico negozio, baseline contabile e fondo iniziale vending.",
            "Giacenze presenti, non negative e intere; cache vending confrontata con le colonne associate.",
            "Importo e pagamento delle vendite CSV con un evento contabile univoco.",
            "Controllo BULK non filtrato: righe attive, batch orfani e audit degli annullamenti nuovi.",
            "Controllo Cassa Rapida non filtrato: pezzi, importi, pagamento, movimento cassa, orfani e audit degli storni nuovi.",
        ],
        "limiti": [
            "Controllo in sola lettura, non certificazione contabile. Ripetere a operazioni ferme: le letture non sono una fotografia transazionale.",
            "Le giacenze si confrontano solo per prodotti univoci con colonne associate; nessuna formula acquistati meno venduti.",
            "Vendite CSV senza un evento univoco non sono errori: possono appartenere alla baseline storica. Eventi senza vendita sono ammessi dall'allineamento solo contabile.",
            "Non si confronta la cassa tecnica legacy con i saldi: baseline e copertura temporale non sono equivalenti. Non è un conteggio fisico del contante.",
            "Coerenza dei batch BULK verificata soltanto nell'analisi senza filtri data; annullamenti legacy senza versione audit non sono giudicati retroattivamente.",
            "Coerenza Cassa Rapida verificata senza filtri data e solo su registrazioni native POS; nessuna ricostruzione del saldo fisico o del pregresso Excel.",
        ],
    }
