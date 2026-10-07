import hashlib
import os
import unittest
from datetime import datetime
from unittest.mock import patch

import bcrypt
import jwt

from app_backend.services.service_09_user_authentication.logic import (
    authentication_login,
)


VALID_PASSWORD = "PlainTextPassword123"
JWT_SECRET = "unit-test-secret-with-at-least-32-bytes"


class FakeResult:
    def __init__(self, rows=None, scalar_value=None):
        self.rows = rows or []
        self.scalar_value = scalar_value

    def mappings(self):
        return self

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows

    def scalar_one(self):
        return self.scalar_value


class FakeConnection:
    def __init__(self, engine):
        self.engine = engine

    def execute(self, statement, params=None):
        params = params or {}
        query = str(statement).lower()

        if "to_regclass('public.authentication_sessions')" in query:
            table_name = (
                "authentication_sessions"
                if self.engine.sessions_table_exists
                else None
            )
            return FakeResult(scalar_value=table_name)

        if "from organization_master" in query:
            organization_name = params.get("organization_name", "")
            organization = self.engine.organization
            if (
                organization
                and organization.get("org_name", "").lower()
                == organization_name.lower()
            ):
                return FakeResult(rows=[organization])
            return FakeResult(rows=[])

        if "from user_master" in query:
            user_principal_name = params.get("user_principal_name", "")
            org_id = params.get("org_id")
            user = self.engine.user
            if (
                user
                and user.get("user_principal_name", "").lower()
                == user_principal_name.lower()
                and user.get("user_org_id_fk") == org_id
            ):
                return FakeResult(rows=[user])
            return FakeResult(rows=[])

        if "from user_role_mapping" in query:
            user_id = params.get("user_id")
            rows = [
                role
                for role in self.engine.roles
                if role.get("user_id_fk") == user_id
            ]
            return FakeResult(rows=rows)

        if "from organization_license_master" in query:
            org_id = params.get("org_id")
            rows = [
                license_row
                for license_row in self.engine.licenses
                if license_row.get("org_id_fk") == org_id
            ]
            return FakeResult(rows=rows)

        if "insert into authentication_sessions" in query:
            session_id = len(self.engine.sessions) + 1
            session = {
                **params,
                "auth_session_id_pk": session_id
            }
            self.engine.sessions.append(session)
            return FakeResult(scalar_value=session_id)

        raise AssertionError(f"Unhandled SQL in fake engine: {query}")


class FakeBegin:
    def __init__(self, engine):
        self.engine = engine

    def __enter__(self):
        return FakeConnection(self.engine)

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeEngine:
    def __init__(
        self,
        organization=None,
        user=None,
        roles=None,
        licenses=None,
        sessions_table_exists=True
    ):
        self.organization = organization
        self.user = user
        self.roles = roles or []
        self.licenses = licenses or []
        self.sessions_table_exists = sessions_table_exists
        self.sessions = []

    def begin(self):
        return FakeBegin(self)


def password_hash(password=VALID_PASSWORD):
    return bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt()
    ).decode("utf-8")


def base_organization():
    return {
        "org_id_pk": 1,
        "org_name": "Urban Express Transport"
    }


def base_user():
    return {
        "user_id_pk": 10,
        "auth_provider": "LOCAL",
        "user_principal_name": "system_admin",
        "password_hash": password_hash(),
        "display_name": "System Administrator",
        "email": "system.admin@ue.local",
        "user_org_id_fk": 1,
        "user_department_id_fk": 2,
        "is_active": True,
        "is_deleted": False
    }


def base_roles():
    return [
        {
            "user_role_id_pk": 100,
            "user_id_fk": 10,
            "role_id_fk": 1,
            "role_name": "System Administrator",
            "role_description": "Full system access",
            "is_active": True,
            "created_at": datetime(2026, 1, 1, 12, 0, 0)
        }
    ]


def base_licenses():
    return [
        {
            "org_license_id": 20,
            "org_id_fk": 1,
            "org_management_module": True,
            "hr_payroll_management_module": True,
            "fleet_management_module": True,
            "maintenance_management_module": True,
            "gps_tracking_module": True,
            "contracts_management_module": True,
            "financial_management_module": True,
            "created_by": "SYSTEM",
            "created_at": datetime(2026, 1, 1, 12, 0, 0)
        }
    ]


def base_payload(password=VALID_PASSWORD):
    return {
        "organization_name": " Urban Express Transport ",
        "user_principal_name": " system_admin ",
        "password": password
    }


def base_engine(**overrides):
    values = {
        "organization": base_organization(),
        "user": base_user(),
        "roles": base_roles(),
        "licenses": base_licenses(),
        "sessions_table_exists": True
    }
    values.update(overrides)
    return FakeEngine(**values)


def contains_key(value, unsafe_keys):
    if isinstance(value, dict):
        return any(
            key in unsafe_keys or contains_key(item, unsafe_keys)
            for key, item in value.items()
        )

    if isinstance(value, list):
        return any(contains_key(item, unsafe_keys) for item in value)

    return False


class AuthenticationLoginLogicTest(unittest.TestCase):
    def authenticate(self, engine, payload=None, env=None):
        if env is None:
            env = {
                "JWT_SECRET_KEY": JWT_SECRET,
                "JWT_ALGORITHM": "HS256",
                "ACCESS_TOKEN_EXPIRE_MINUTES": "30",
                "REFRESH_TOKEN_EXPIRE_DAYS": "30"
            }
        with patch.object(authentication_login, "db_engine", return_value=engine):
            with patch.dict(os.environ, env, clear=True):
                return authentication_login.authenticate_user(
                    payload or base_payload()
                )

    def assert_invalid_credentials(self, response):
        self.assertEqual(
            response,
            {
                "authenticated": False,
                "error": "Invalid organization or credentials."
            }
        )

    def test_successful_authentication_creates_session_and_tokens(self):
        engine = base_engine()

        response = self.authenticate(engine)

        self.assertTrue(response["authenticated"])
        self.assertEqual(response["token_type"], "bearer")
        self.assertEqual(response["expires_in"], 1800)
        self.assertEqual(response["user"]["user_id"], 10)
        self.assertEqual(
            response["organization"]["org_name"],
            "Urban Express Transport"
        )
        self.assertEqual(response["roles"][0]["role_name"], "System Administrator")
        self.assertTrue(response["licenses"][0]["financial_management_module"])
        self.assertEqual(len(engine.sessions), 1)

        session = engine.sessions[0]
        self.assertNotEqual(
            session["refresh_token_hash"],
            response["refresh_token"]
        )
        self.assertEqual(
            session["refresh_token_hash"],
            hashlib.sha256(
                response["refresh_token"].encode("utf-8")
            ).hexdigest()
        )

        decoded = jwt.decode(
            response["access_token"],
            JWT_SECRET,
            algorithms=["HS256"]
        )
        self.assertEqual(decoded["sub"], "10")
        self.assertEqual(decoded["org_id"], 1)
        self.assertEqual(decoded["session_id"], 1)
        self.assertEqual(decoded["user_principal_name"], "system_admin")
        self.assertEqual(decoded["token_type"], "access")
        self.assertIn("jti", decoded)

        self.assertFalse(
            contains_key(
                response,
                {
                    "password",
                    "password_hash",
                    "hashed_password",
                    "refresh_token_hash"
                }
            )
        )

    def test_invalid_organization_returns_generic_failure(self):
        engine = base_engine()
        payload = base_payload()
        payload["organization_name"] = "Other Organization"

        response = self.authenticate(engine, payload)

        self.assert_invalid_credentials(response)
        self.assertEqual(engine.sessions, [])

    def test_invalid_username_returns_generic_failure(self):
        engine = base_engine()
        payload = base_payload()
        payload["user_principal_name"] = "unknown_user"

        response = self.authenticate(engine, payload)

        self.assert_invalid_credentials(response)
        self.assertEqual(engine.sessions, [])

    def test_incorrect_password_returns_generic_failure(self):
        engine = base_engine()

        response = self.authenticate(engine, base_payload("WrongPassword"))

        self.assert_invalid_credentials(response)
        self.assertEqual(engine.sessions, [])

    def test_inactive_user_returns_generic_failure(self):
        user = base_user()
        user["is_active"] = False
        engine = base_engine(user=user)

        response = self.authenticate(engine)

        self.assert_invalid_credentials(response)
        self.assertEqual(engine.sessions, [])

    def test_deleted_user_returns_generic_failure(self):
        user = base_user()
        user["is_deleted"] = True
        engine = base_engine(user=user)

        response = self.authenticate(engine)

        self.assert_invalid_credentials(response)
        self.assertEqual(engine.sessions, [])

    def test_entra_user_cannot_use_local_password_authentication(self):
        user = base_user()
        user["auth_provider"] = "ENTRA"
        user["password_hash"] = None
        engine = base_engine(user=user)

        response = self.authenticate(engine)

        self.assert_invalid_credentials(response)
        self.assertEqual(engine.sessions, [])

    def test_missing_organization_is_rejected(self):
        response = self.authenticate(base_engine(), {"user_principal_name": "u", "password": "p"})

        self.assertEqual(
            response,
            {"authenticated": False, "error": "organization_name is required."}
        )

    def test_missing_username_is_rejected(self):
        response = self.authenticate(base_engine(), {"organization_name": "o", "password": "p"})

        self.assertEqual(
            response,
            {"authenticated": False, "error": "user_principal_name is required."}
        )

    def test_missing_password_is_rejected(self):
        response = self.authenticate(
            base_engine(),
            {"organization_name": "o", "user_principal_name": "u"}
        )

        self.assertEqual(
            response,
            {"authenticated": False, "error": "password is required."}
        )

    def test_missing_jwt_secret_key_returns_configuration_error(self):
        engine = base_engine()

        response = self.authenticate(engine, env={})

        self.assertEqual(
            response,
            {"authenticated": False, "error": "JWT_SECRET_KEY is not configured."}
        )
        self.assertEqual(engine.sessions, [])

    def test_missing_authentication_sessions_table_returns_development_error(self):
        engine = base_engine(sessions_table_exists=False)

        response = self.authenticate(engine)

        self.assertFalse(response["authenticated"])
        self.assertIn("authentication_sessions table does not exist", response["error"])
        self.assertEqual(engine.sessions, [])

    def test_user_must_be_scoped_to_resolved_organization(self):
        user = base_user()
        user["user_org_id_fk"] = 2
        engine = base_engine(user=user)

        response = self.authenticate(engine)

        self.assert_invalid_credentials(response)
        self.assertEqual(engine.sessions, [])

    def test_submitted_password_is_not_trimmed(self):
        user = base_user()
        user["password_hash"] = password_hash(" password-with-spaces ")
        engine = base_engine(user=user)

        response = self.authenticate(
            engine,
            base_payload(" password-with-spaces ")
        )

        self.assertTrue(response["authenticated"])


if __name__ == "__main__":
    unittest.main()
