"""Fail-closed, READ-ONLY startup preflight for an already migrated staging DB.

This module deliberately has no write path. Legacy data corrections, seeding
and index creation must be performed via a separate, explicitly approved
maintenance procedure, never hidden inside a routine read-only startup.
"""
if __package__:
    from .migration_preflight import inspect_startup_migrations
    from .index_migrations import MIGRATION_ID
else:
    from migration_preflight import inspect_startup_migrations
    from index_migrations import MIGRATION_ID


# Critical exactly-once operations rely on these unique business keys.
# If a new operation adds another unique-key dependency, extend this guard
# before enabling it on the staging runtime.
REQUIRED_UNIQUE_INDEXES = (
    ("vendite", "idempotency_key"),
    ("vendite", "csv_event_key"),
    ("vendite_bulk_imports", "id"),
    ("vendite_bulk_imports", "idempotency_key"),
    ("cassa_rapida_batches", "idempotency_key"),
    ("cassa", "idempotency_key"),
    ("ricezioni_merce", "idempotency_key"),
    ("vending_operations", "idempotency_key"),
    ("storico_ordini", "idempotency_key"),
    ("smart_venue_movimenti", "idempotency_key"),
    ("vending_accounting_events", "csv_event_key"),
    ("vending_accounting_events", "csv_accounting_key"),
    ("versamenti", "idempotency_key"),
    ("prelievi_vending", "idempotency_key"),
    ("scontrini_vending", "idempotency_key"),
    ("reconciliation_cases", "fingerprint"),
    ("app_migrations", "id"),
)


async def verify_readonly_staging_startup(database, default_parameter_names):
    """Inspect schema, migrations and operation keys without mutating records.

    Block the deployment if anything would require a startup migration.
    Report only collection/field names, not values or credentials.
    """
    report = await inspect_startup_migrations(database)
    problems = []

    for item in report["business_indexes"]:
        if item["status"] != "unique_index_present":
            problems.append(f"indice obbligatorio: {item['collection']}.{item['field']}")

    columns = report["vending_column_zero_padding"]
    if columns["pending"] or columns["collisions"]:
        problems.append("rinomina vending H01-H03 non conclusa")

    for record in report["migration_records"]:
        if not record["recorded"]:
            problems.append(f"migrazione non registrata: {record['id']}")
        elif record["id"] == MIGRATION_ID and record["completed"] is not True:
            problems.append(f"migrazione indici non completata: {record['id']}")
        # The two other existing migrations intentionally DO NOT write a
        # 'completed' property: record presence is their completion marker.

    expected_params = frozenset(str(x) for x in default_parameter_names)
    existing_params = {
        str(row.get("nome") or "")
        async for row in database.parametri.find(
            {"nome": {"$in": sorted(expected_params)}}, {"_id": 0, "nome": 1}
        )
    }
    for name in sorted(expected_params - existing_params):
        problems.append(f"parametro di default mancante: {name}")

    missing_id_count = await database.prodotti.count_documents({
        "$or": [
            {"id": {"$exists": False}}, {"id": None}, {"id": ""},
        ],
    })
    if missing_id_count:
        problems.append("prodotti legacy senza id")

    for collection_name, field in REQUIRED_UNIQUE_INDEXES:
        info = await database[collection_name].index_information()
        if not any(
            details.get("unique") is True
            and list(details.get("key") or []) == [(field, 1)]
            for details in info.values()
        ):
            problems.append(f"indice di idempotenza mancante: {collection_name}.{field}")

    if problems:
        raise RuntimeError(
            "Avvio staging in sola lettura bloccato; manutenzione esplicita "
            "necessaria: " + "; ".join(problems)
        )
    return {
        "mode": "read_only",
        "verified_unique_business_indexes": len(report["business_indexes"]),
        "verified_operation_indexes": len(REQUIRED_UNIQUE_INDEXES),
        "verified_migration_records": len(report["migration_records"]),
        "verified_default_parameters": len(expected_params),
        "startup_database_writes": 0,
    }
