"""Security preflight must not grant rights, touch data or print secrets."""
import pytest

from scripts.staging_rbac_check import (
    assess_effective_roles,
    validate_target,
    select_effective_runtime_uri,
)


def _status(*roles):
    return {"authInfo": {"authenticatedUserRoles": [
        {"role": role, "db": database} for role, database in roles
    ]}}


def test_staging_app_user_requires_scoped_readwrite_role():
    result = assess_effective_roles(_status(("readWrite", "gestionale_staging")))
    assert result["least_privilege"] is True
    assert result["credentials_rotated"] == "not_verified"
    assert result["effective_roles"] == [{
        "role": "readWrite", "database": "gestionale_staging"
    }]


@pytest.mark.parametrize("roles", [
    (("readWriteAnyDatabase", "admin"),),
    (("atlasAdmin", "admin"),),
    (("readWrite", "gestionale"),),
    (("readWrite", "gestionale_staging"), ("read", "admin")),
    (("read", "gestionale_staging"),),
    (),
])
def test_staging_app_user_rejects_excessive_or_insufficient_roles(roles):
    result = assess_effective_roles(_status(*roles))
    assert result["least_privilege"] is False
    assert result["mode"] == "read_only"
    assert "password" not in str(result).lower()
    assert "username" not in str(result).lower()


@pytest.mark.parametrize("environment,database,uri", [
    ("production", "gestionale_staging",
     "mongodb+srv://user:synthetic@gestionale-staging.example.mongodb.net/db"),
    ("staging", "gestionale",
     "mongodb+srv://user:synthetic@gestionale-staging.example.mongodb.net/db"),
    ("staging", "gestionale_staging",
     "mongodb+srv://user:synthetic@production.example.mongodb.net/db"),
    ("staging", "gestionale_staging", "mongodb://localhost:27017"),
    ("staging", "gestionale_staging",
     "mongodb+srv://user@gestionale-staging.example.mongodb.net/db"),
])
def test_staging_preflight_refuses_wrong_target(environment, database, uri):
    with pytest.raises(ValueError):
        validate_target(environment, database, uri)


def test_staging_preflight_accepts_dedicated_cluster_uri():
    validate_target(
        "staging", "gestionale_staging",
        "mongodb+srv://user:synthetic@gestionale-staging.example.mongodb.net/gestionale_staging",
    )


def test_staging_rbac_preflight_audits_active_scoped_uri_not_legacy():
    legacy = "mongodb+srv://legacy:sensitive@gestionale-staging.example.mongodb.net/gestionale_staging"
    scoped = "mongodb+srv://scoped:private@gestionale-staging.example.mongodb.net/gestionale_staging"
    selected = select_effective_runtime_uri({
        "ENVIRONMENT": "staging",
        "DB_NAME": "gestionale_staging",
        "STAGING_MONGO_ROLE_MODE": "active",
        "MONGO_URL": legacy,
        "STAGING_SCOPED_MONGO_URL": scoped,
    })
    assert selected == scoped
    assert selected != legacy


@pytest.mark.parametrize("mode", ["legacy", "probe", "", "invalid"])
def test_staging_rbac_preflight_refuses_audit_if_scoped_user_not_active(mode):
    with pytest.raises((ValueError, RuntimeError)) as err:
        select_effective_runtime_uri({
            "ENVIRONMENT": "staging",
            "DB_NAME": "gestionale_staging",
            "STAGING_MONGO_ROLE_MODE": mode,
            "MONGO_URL": "mongodb+srv://legacy:sensitive@gestionale-staging.example.mongodb.net/test",
            "STAGING_SCOPED_MONGO_URL": "mongodb+srv://scoped:private@gestionale-staging.example.mongodb.net/test",
        })
    assert "sensitive" not in str(err.value)
    assert "private" not in str(err.value)


def test_staging_rbac_preflight_rejects_scoped_uri_pointing_to_production():
    with pytest.raises((ValueError, RuntimeError)):
        select_effective_runtime_uri({
            "ENVIRONMENT": "staging",
            "DB_NAME": "gestionale_staging",
            "STAGING_MONGO_ROLE_MODE": "active",
            "MONGO_URL": "mongodb+srv://legacy:sensitive@gestionale-staging.example.mongodb.net/test",
            "STAGING_SCOPED_MONGO_URL": "mongodb+srv://scoped:private@production.example.mongodb.net/test",
        })


def test_staging_rbac_preflight_refuses_production_even_if_mode_is_active():
    with pytest.raises(ValueError):
        select_effective_runtime_uri({
            "ENVIRONMENT": "production",
            "DB_NAME": "gestionale",
            "STAGING_MONGO_ROLE_MODE": "active",
            "MONGO_URL": "mongodb+srv://legacy:sensitive@gestionale-staging.example.mongodb.net/test",
            "STAGING_SCOPED_MONGO_URL": "mongodb+srv://scoped:private@gestionale-staging.example.mongodb.net/test",
        })
