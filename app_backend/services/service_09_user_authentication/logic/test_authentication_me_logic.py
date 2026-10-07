import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt

from app_backend.services.service_09_user_authentication.logic import (
    authentication_me,
)


JWT_SECRET = "unit-test-secret-with-at-least-32-bytes"
OTHER_JWT_SECRET = "different-unit-test-secret-32-bytes"


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

        if "from authentication_sessions" in query:
            session_id = params.get("session_id")
            rows = [
                session
                for session in self.engine.sessions
                if session.get("auth_session_id_pk") == session_id
            ]
            return FakeResult(rows=rows)

        if "from user_master" in query:
            user_id = params.get("user_id")
            user = self.engine.user
            if user and user.get("user_id_pk") == user_id:
                return FakeResult(rows=[user])
            return FakeResult(rows=[])

        if "from organization_master" in query:
            org_id = params.get("org_id")
            organization = self.engine.organization
            if organization and organization.get("org_id_pk") == org_id:
                return FakeResult(rows=[organization])
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
        user=None,
        organization=None,
        sessions=None,
        roles=None,
        licenses=None,
        sessions_table_exists=True
    ):
        self.user = user
        self.organization = organization
        self.sessions = sessions or []
        self.roles = roles or []
        self.licenses = licenses or []
        self.sessions_table_exists = sessions_table_exists

    def begin(self):
        return FakeBegin(self)


def base_user(**overrides):
    user = {
        "user_id_pk": 10,
        "user_principal_name": "system_admin",
        "display_name": "System Administrator",
        "email": "system.admin@ue.local",
        "user_org_id_fk": 1,
        "user_department_id_fk": 2,
        "is_active": True,
        "is_deleted": False,
        "password_hash": "must-not-leak"
    }
    user.update(overrides)
    return user


def base_organization(**overrides):
    organization = {
        "org_id_pk": 1,
        "org_name": "Urban Express Transport"
    }
    organization.update(overrides)
    return organization


def base_session(**overrides):
    now = datetime(2026, 1, 1, 12, 0, 0)
    session = {
        "auth_session_id_pk": 1,
        "user_id_fk": 10,
        "org_id_fk": 1,
        "created_at": now,
        "access_expires_at": now + timedelta(minutes=30),
        "refresh_expires_at": now + timedelta(days=30),
        "last_refreshed_at": now,
        "revoked_at": None,
        "revoke_reason": None,
        "refresh_token_hash": "must-not-leak"
    }
    session.update(overrides)
    return session


def base_roles(role_name="System Administrator"):
    return [
        {
            "user_role_id_pk": 100,
            "user_id_fk": 10,
            "role_id_fk": 1,
            "role_name": role_name,
            "role_description": "Current role",
            "is_active": True,
            "created_at": datetime(2026, 1, 1, 12, 0, 0)
        }
    ]


def base_licenses(financial_management_module=True):
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
            "financial_management_module": financial_management_module,
            "created_by": "SYSTEM",
            "created_at": datetime(2026, 1, 1, 12, 0, 0)
        }
    ]


def base_engine(**overrides):
    values = {
        "user": base_user(),
        "organization": base_organization(),
        "sessions": [base_session()],
        "roles": base_roles(),
        "licenses": base_licenses(),
        "sessions_table_exists": True
    }
    values.update(overrides)
    return FakeEngine(**values)


def make_token(
    user_id=10,
    org_id=1,
    session_id=1,
    token_type="access",
    secret=JWT_SECRET,
    expires_delta=timedelta(minutes=30),
    include_sub=True,
    include_org_id=True,
    include_session_id=True
):
    now = datetime.now(timezone.utc)
    claims = {
        "token_type": token_type,
        "user_principal_name": "system_admin",
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "jti": "unit-test-jti"
    }

    if include_sub:
        claims["sub"] = str(user_id)

    if include_org_id:
        claims["org_id"] = org_id

    if include_session_id:
        claims["session_id"] = session_id

    return jwt.encode(claims, secret, algorithm="HS256")


def tamper_token(access_token):
    replacement = "a" if access_token[-1] != "a" else "b"
    return access_token[:-1] + replacement


def contains_key(value, unsafe_keys):
    if isinstance(value, dict):
        return any(
            key in unsafe_keys or contains_key(item, unsafe_keys)
            for key, item in value.items()
        )

    if isinstance(value, list):
        return any(contains_key(item, unsafe_keys) for item in value)

    return False


class AuthenticationMeLogicTest(unittest.TestCase):
    def authenticate_me(self, engine, access_token=None, env=None):
        if access_token is None:
            access_token = make_token()

        if env is None:
            env = {
                "JWT_SECRET_KEY": JWT_SECRET,
                "JWT_ALGORITHM": "HS256",
                "ACCESS_TOKEN_EXPIRE_MINUTES": "30",
                "REFRESH_TOKEN_EXPIRE_DAYS": "30"
            }

        with patch.object(authentication_me, "db_engine", return_value=engine):
            with patch.dict(os.environ, env, clear=True):
                return authentication_me.get_authenticated_user(access_token)

    def test_valid_access_token_returns_current_authenticated_user(self):
        response = self.authenticate_me(base_engine())

        self.assertTrue(response["authenticated"])
        self.assertEqual(response["user"]["user_id"], 10)
        self.assertEqual(
            response["organization"]["org_name"],
            "Urban Express Transport"
        )
        self.assertEqual(response["roles"][0]["role_name"], "System Administrator")
        self.assertTrue(response["licenses"][0]["financial_management_module"])
        self.assertEqual(response["session"]["session_id"], 1)

    def test_bearer_prefix_is_accepted(self):
        response = self.authenticate_me(
            base_engine(),
            f"Bearer {make_token()}"
        )

        self.assertTrue(response["authenticated"])

    def test_expired_access_token_is_rejected(self):
        response = self.authenticate_me(
            base_engine(),
            make_token(expires_delta=timedelta(minutes=-1))
        )

        self.assertFalse(response["authenticated"])
        self.assertEqual(response["error_code"], "expired_access_token")

    def test_tampered_token_is_rejected(self):
        response = self.authenticate_me(
            base_engine(),
            tamper_token(make_token())
        )

        self.assertFalse(response["authenticated"])
        self.assertIn(
            response["error_code"],
            {"invalid_signature", "invalid_access_token"}
        )

    def test_invalid_signature_is_rejected(self):
        response = self.authenticate_me(
            base_engine(),
            make_token(secret=OTHER_JWT_SECRET)
        )

        self.assertFalse(response["authenticated"])
        self.assertEqual(response["error_code"], "invalid_signature")

    def test_refresh_token_type_is_rejected(self):
        response = self.authenticate_me(
            base_engine(),
            make_token(token_type="refresh")
        )

        self.assertFalse(response["authenticated"])
        self.assertEqual(response["error_code"], "invalid_token_type")

    def test_missing_required_claim_is_rejected(self):
        response = self.authenticate_me(
            base_engine(),
            make_token(include_session_id=False)
        )

        self.assertFalse(response["authenticated"])
        self.assertEqual(response["error_code"], "missing_token_claims")

    def test_revoked_session_is_rejected(self):
        engine = base_engine(
            sessions=[
                base_session(
                    revoked_at=datetime(2026, 1, 1, 13, 0, 0),
                    revoke_reason="logout"
                )
            ]
        )

        response = self.authenticate_me(engine)

        self.assertFalse(response["authenticated"])
        self.assertEqual(response["error_code"], "revoked_session")

    def test_missing_session_is_rejected(self):
        response = self.authenticate_me(base_engine(sessions=[]))

        self.assertFalse(response["authenticated"])
        self.assertEqual(response["error_code"], "missing_session")

    def test_disabled_user_is_rejected(self):
        response = self.authenticate_me(
            base_engine(user=base_user(is_active=False))
        )

        self.assertFalse(response["authenticated"])
        self.assertEqual(response["error_code"], "inactive_user")

    def test_deleted_user_is_rejected(self):
        response = self.authenticate_me(
            base_engine(user=base_user(is_deleted=True))
        )

        self.assertFalse(response["authenticated"])
        self.assertEqual(response["error_code"], "deleted_user")

    def test_session_organization_mismatch_is_rejected(self):
        response = self.authenticate_me(
            base_engine(sessions=[base_session(org_id_fk=2)])
        )

        self.assertFalse(response["authenticated"])
        self.assertEqual(response["error_code"], "organization_mismatch")

    def test_current_user_organization_mismatch_is_rejected(self):
        response = self.authenticate_me(
            base_engine(user=base_user(user_org_id_fk=2))
        )

        self.assertFalse(response["authenticated"])
        self.assertEqual(response["error_code"], "organization_mismatch")

    def test_role_changed_after_login_is_reflected(self):
        response = self.authenticate_me(
            base_engine(roles=base_roles("Finance Management - Admin"))
        )

        self.assertTrue(response["authenticated"])
        self.assertEqual(
            response["roles"][0]["role_name"],
            "Finance Management - Admin"
        )

    def test_license_changed_after_login_is_reflected(self):
        response = self.authenticate_me(
            base_engine(licenses=base_licenses(financial_management_module=False))
        )

        self.assertTrue(response["authenticated"])
        self.assertFalse(response["licenses"][0]["financial_management_module"])

    def test_current_role_and_license_values_are_returned(self):
        response = self.authenticate_me(
            base_engine(
                roles=base_roles("Contracts Management - User"),
                licenses=base_licenses(financial_management_module=False)
            )
        )

        self.assertTrue(response["authenticated"])
        self.assertEqual(
            response["roles"][0]["role_name"],
            "Contracts Management - User"
        )
        self.assertFalse(response["licenses"][0]["financial_management_module"])

    def test_password_hash_and_token_hash_fields_never_appear(self):
        response = self.authenticate_me(base_engine())

        self.assertTrue(response["authenticated"])
        self.assertFalse(
            contains_key(
                response,
                {
                    "password",
                    "password_hash",
                    "hashed_password",
                    "refresh_token",
                    "refresh_token_hash",
                    "JWT_SECRET_KEY"
                }
            )
        )

    def test_missing_authentication_sessions_table_returns_development_error(self):
        response = self.authenticate_me(
            base_engine(sessions_table_exists=False)
        )

        self.assertFalse(response["authenticated"])
        self.assertEqual(
            response["error_code"],
            "authentication_development_error"
        )


if __name__ == "__main__":
    unittest.main()
