"""Read-only preflight of the effective MongoDB user in the staging runtime.

No Atlas user changes, no password rotation and no database writes. Use as a
manual gate before/after a separately approved staging credential cutover.
"""
import asyncio
import json
import os
from urllib.parse import urlsplit

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import PyMongoError

from backend.staging_scoped_mongo import choose_staging_connection


STAGING_DATABASE = "gestionale_staging"


def validate_target(environment: str, database: str, uri: str) -> None:
    if environment.strip().lower() != "staging" or database != STAGING_DATABASE:
        raise ValueError("Only the dedicated staging runtime may be audited")
    try:
        parsed = urlsplit(uri)
        host = (parsed.hostname or "").lower()
    except ValueError as exc:
        raise ValueError("Invalid staging URI") from exc
    if not (
        parsed.scheme == "mongodb+srv"
        and host.startswith("gestionale-staging.")
        and host.endswith(".mongodb.net")
        and parsed.username
        and parsed.password
    ):
        raise ValueError("Only the authenticated, dedicated staging Atlas cluster is allowed")


def assess_effective_roles(connection_status: dict) -> dict:
    """Accept only readWrite scoped to gestionale_staging, no inherited admin roles.

    Role names and database names are not secrets, and no credentials or
    authenticated usernames are included in this report.
    """
    raw = (connection_status.get("authInfo") or {}).get("authenticatedUserRoles")
    normalized = sorted({
        (str(role.get("role") or ""), str(role.get("db") or ""))
        for role in raw if isinstance(role, dict)
    }) if isinstance(raw, list) else []
    allowed = [("readWrite", STAGING_DATABASE)]
    least_privilege = normalized == allowed
    return {
        "mode": "read_only",
        "database": STAGING_DATABASE,
        "effective_roles": [{"role": role, "database": db} for role, db in normalized],
        "least_privilege": least_privilege,
        "reason": (
            "Only readWrite on the dedicated staging database is permitted"
            if not least_privilege else "Role scope verified"
        ),
        "credentials_rotated": "not_verified",
    }


def select_effective_runtime_uri(environ) -> str:
    """Inspect the actual active URI, not the obsolete rollback credential.

    Refuse probe and legacy modes: the scoped user must really serve requests.
    No URI or secret content appears in exceptions, reports or logs.
    """
    environment = environ.get("ENVIRONMENT") or ""
    database = environ.get("DB_NAME") or ""
    mode = str(environ.get("STAGING_MONGO_ROLE_MODE") or "legacy").lower().strip()
    if environment.lower().strip() != "staging" or database != STAGING_DATABASE:
        raise ValueError("Refusing non-staging RBAC preflight")
    if mode != "active":
        raise ValueError("Staging scoped credential is not the active runtime")
    uri, effective_mode = choose_staging_connection(
        environment, mode,
        environ.get("MONGO_URL") or "",
        environ.get("STAGING_SCOPED_MONGO_URL") or "",
    )
    if effective_mode != "active":
        raise ValueError("Scoped staging mode not active")
    validate_target(environment, database, uri)
    return uri


async def audit_runtime() -> dict:
    uri = select_effective_runtime_uri(os.environ)
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=6000)
    try:
        status = await client.admin.command("connectionStatus")
        return assess_effective_roles(status)
    finally:
        client.close()


def main() -> None:
    try:
        outcome = asyncio.run(audit_runtime())
        print(json.dumps(outcome, ensure_ascii=False, sort_keys=True))
        raise SystemExit(0 if outcome["least_privilege"] else 2)
    except (ValueError, OSError, RuntimeError, PyMongoError):
        # Never print a possibly credential-bearing MongoDB URI or traceback.
        print(json.dumps({"mode": "read_only", "least_privilege": False,
                          "error": "Staging Atlas connection or configuration not verified"}))
        raise SystemExit(3)


if __name__ == "__main__":
    main()
