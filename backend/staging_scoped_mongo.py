"""Non-disruptive Atlas staging credential rotation, with legacy rollback.

Modes:
- legacy: only the original MONGO_URL, unchanged.
- probe: original URI stays active, checks candidate without side effects.
- active: candidate URI serves staging after exact role validation.

All code is staging-scoped. Never returns or logs credential-bearing URIs.
"""
from urllib.parse import urlsplit

from motor.motor_asyncio import AsyncIOMotorClient

STAGING_DB = "gestionale_staging"
VALID_MODES = frozenset({"legacy", "probe", "active"})


def choose_staging_connection(environment, mode, legacy_url, scoped_url):
    """Choose the active URI without touching any credential or data."""
    if str(environment or "").strip().lower() != "staging":
        return legacy_url, "legacy"
    value = str(mode or "legacy").strip().lower()
    if value not in VALID_MODES:
        raise RuntimeError("STAGING_MONGO_ROLE_MODE must be legacy, probe, or active")
    if value == "legacy":
        return legacy_url, value
    # Require a fully authenticated, explicit URI on the isolated cluster.
    # Never include URI details in an exception.
    try:
        parsed = urlsplit(scoped_url or "")
        valid = (
            parsed.scheme == "mongodb+srv"
            and (parsed.hostname or "").lower().startswith("gestionale-staging.")
            and (parsed.hostname or "").lower().endswith(".mongodb.net")
            and bool(parsed.username)
            and bool(parsed.password)
        )
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise RuntimeError("Scoped staging MongoDB URI missing or invalid")
    return (scoped_url if value == "active" else legacy_url), value


def verify_scoped_staging_roles(auth_info):
    """Require exactly readWrite on the single staging database.

    Return sanitized metadata only; don't reveal authenticated username,
    URI or password, even on failure.
    """
    raw = (auth_info or {}).get("authInfo", {}).get("authenticatedUserRoles", [])
    roles = set()
    if not isinstance(raw, list):
        raise RuntimeError("Unrecognized Atlas authenticated role response")
    for entry in raw:
        if not isinstance(entry, dict):
            raise RuntimeError("Unrecognized Atlas authenticated role response")
        roles.add((entry.get("role"), entry.get("db")))
    if roles != {("readWrite", STAGING_DB)}:
        raise RuntimeError("Scoped staging MongoDB credential lacks exact database-only readWrite role")
    return {"verified_role": "readWrite", "database": STAGING_DB}


async def verify_scoped_staging_connection(uri, *, client=None):
    """Read-only validation; does not insert, create indexes, or seed data."""
    owned = client is None
    candidate = client or AsyncIOMotorClient(
        uri, serverSelectionTimeoutMS=6500, connectTimeoutMS=6500,
    )
    try:
        info = await candidate.admin.command("connectionStatus")
        sanitized = verify_scoped_staging_roles(info)
        await candidate[STAGING_DB].command("ping")
        await candidate[STAGING_DB].prodotti.find_one({}, {"_id": 1})
        return {**sanitized, "read_only_probe_passed": True}
    finally:
        if owned:
            candidate.close()
