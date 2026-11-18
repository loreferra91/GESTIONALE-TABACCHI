"""Gate 4B: isolated authenticated MongoDB bootstrap contract.

Only FIRST-BOOT initialization is implemented. It is not enabled by the
existing desktop launcher. MongoDB must be started independently with
--replSet, --keyFile and --auth, with the keyfile restricted to the owner.
Never use against an existing production installation.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
import stat
import time
from urllib.parse import quote

from pymongo import MongoClient
from pymongo.errors import OperationFailure, PyMongoError


class MongoSecurityError(RuntimeError):
    pass


_ALLOWED = {
    ("rsDesktopAuthCert", "desktop_auth_cert_test"),
    ("rsGestionaleDesktopSecure", "gestionale_desktop"),
}
_ADMIN_USER = "desktop_admin"
_APP_USER = "desktop_api"


def create_or_validate_keyfile(path: Path) -> Path:
    """Never overwrite or rotate a replica-set authentication key implicitly."""
    path = Path(path)
    if path.is_symlink() or not path.parent.is_dir() or path.parent.is_symlink():
        raise MongoSecurityError("Keyfile directory must exist and not be a symlink")
    if path.exists():
        mode = path.stat().st_mode
        if not stat.S_ISREG(mode):
            raise MongoSecurityError("Existing Mongo keyfile must be a regular file")
        if os.name != "nt" and stat.S_IMODE(mode) & 0o077:
            raise MongoSecurityError("Existing Mongo keyfile is readable by another user")
        raw = path.read_bytes()
        if not 6 <= len(raw.strip()) <= 1024:
            raise MongoSecurityError("Existing Mongo keyfile is invalid")
        return path
    # 512 random bytes encoded as base64: below Mongo's maximum key length.
    value = base64.b64encode(os.urandom(512)) + b"\n"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(value)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def _validate_target(client: MongoClient, *, replica_set: str, database: str) -> None:
    if (replica_set, database) not in _ALLOWED:
        raise MongoSecurityError("Unsupported authenticated MongoDB provisioning target")
    address = client.address
    if not address or address[0] not in {"127.0.0.1", "localhost"}:
        raise MongoSecurityError("Authentication bootstrap requires an explicit loopback connection")
    if not client.options.direct_connection:
        raise MongoSecurityError("Authentication bootstrap requires directConnection=true")


def authenticated_uri(port: int, database: str, password: str,
                      replica_set: str) -> str:
    if (replica_set, database) not in _ALLOWED or not 1024 <= port <= 65535:
        raise MongoSecurityError("Invalid authenticated MongoDB target")
    if not isinstance(password, str) or len(password) < 32:
        raise MongoSecurityError("Database secret must be at least 32 characters")
    return (f"mongodb://{_APP_USER}:{quote(password, safe='')}@127.0.0.1:{port}/"
            f"?authSource=admin&directConnection=true&replicaSet={replica_set}")


def bootstrap_first_users(anonymous: MongoClient, *, replica_set: str,
                          database: str, admin_password: str,
                          app_password: str, member_host: str) -> str:
    """Authorize exactly one new keyfile-protected replica set.

    Create a recovery administrator first through the localhost exception,
    then use its authenticated session to grant limited readWrite to the app.
    A partial bootstrap is NOT retried or repaired automatically.
    """
    _validate_target(anonymous, replica_set=replica_set, database=database)
    if not isinstance(admin_password, str) or len(admin_password) < 32:
        raise MongoSecurityError("Administrator secret too short")
    if not isinstance(app_password, str) or len(app_password) < 32:
        raise MongoSecurityError("Application secret too short")
    host, port_text = member_host.rsplit(":", 1)
    if host not in {"127.0.0.1", "localhost"} or not port_text.isdigit():
        raise MongoSecurityError("Replica member must be a loopback hostname and port")
    port = int(port_text)
    if not 1024 <= port <= 65535:
        raise MongoSecurityError("Invalid replica set member port")
    if port != anonymous.address[1]:
        # This also prevents accidentally initializing a different service.
        raise MongoSecurityError("Replica member port does not match client endpoint")
    hello = anonymous.admin.command("hello")
    if hello.get("setName") and hello["setName"] != replica_set:
        raise MongoSecurityError("Unexpected replica set identity")
    if not hello.get("isWritablePrimary"):
        try:
            anonymous.admin.command("replSetInitiate", {
                "_id": replica_set,
                "members": [{"_id": 0, "host": member_host}],
            })
        except OperationFailure as exc:
            # Do not silently recover another person's initialized cluster.
            raise MongoSecurityError("First boot replica-set initiation failed") from exc
        for _ in range(150):
            hello = anonymous.admin.command("hello")
            if hello.get("isWritablePrimary") and hello.get("setName") == replica_set:
                break
            time.sleep(0.2)
        else:
            raise MongoSecurityError("Replica set did not elect a primary")
    if hello.get("setName") != replica_set:
        raise MongoSecurityError("Unexpected MongoDB replica set")
    # localhost exception closes after first user is created.
    try:
        anonymous.admin.command("createUser", _ADMIN_USER,
                                pwd=admin_password, roles=[{"role": "root", "db": "admin"}])
    except OperationFailure as exc:
        raise MongoSecurityError("Admin bootstrap blocked: do not reset database") from exc

    admin_client = MongoClient(
        host="127.0.0.1", port=port, directConnection=True,
        username=_ADMIN_USER, password=admin_password,
        authSource="admin", serverSelectionTimeoutMS=3000)
    try:
        admin_client.admin.command(
            "createUser", _APP_USER, pwd=app_password,
            roles=[{"role": "readWrite", "db": database}])
    except PyMongoError as exc:
        raise MongoSecurityError("App user creation failed; manual admin recovery required") from exc
    finally:
        admin_client.close()
    uri = authenticated_uri(port, database, app_password, replica_set)
    verify = MongoClient(uri, serverSelectionTimeoutMS=3000)
    try:
        hello = verify.admin.command("hello")
        if hello.get("setName") != replica_set or not hello.get("isWritablePrimary"):
            raise MongoSecurityError("Authenticated replica set is not a primary")
        verify[database].command("ping")
    finally:
        verify.close()
    return uri


def verify_authenticated_access(uri: str, *, expected_replica: str,
                                database: str) -> None:
    if (expected_replica, database) not in _ALLOWED:
        raise MongoSecurityError("Invalid validation target")
    client = MongoClient(uri, serverSelectionTimeoutMS=3000)
    try:
        hello = client.admin.command("hello")
        if hello.get("setName") != expected_replica or not hello.get("isWritablePrimary"):
            raise MongoSecurityError("Expected authenticated replica set unavailable")
        # Read/write scope will be exercised by disposable CI transaction tests.
        client[database].list_collection_names()
    finally:
        client.close()
