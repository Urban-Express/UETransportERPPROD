import hashlib
import logging
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt

from app_backend.services.service_09_user_authentication.logic import (
    authentication_me,
    authentication_refresh,
)
from app_backend.services.service_09_user_authentication.logic.authentication_login import (
    _hash_refresh_token,
)


JWT_SECRET = "unit-test-secret-with-at-least-32-bytes"
REFRESH_TOKEN = "initial-refresh-token"


class FakeResult:
    def __init__(self, rows=None, scalar_value=None, rowcount=0):
        self.rows = rows or []
        self.scalar_value = scalar_value
        self.rowcount = rowcount

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

        if query.strip().startswith("update authentication_sessions"):
            return self._execute_session_rotation(params)

        if "from authentication_sessions s" in query:
            refresh_token_hash = params.get("refresh_token_hash")
            rows = []
            for session in self.engine.sessions:
                if session.get("refresh_token_hash") != refresh_token_hash:
                    continue

                row = dict(session)
                user = self.engine.user
                if user and user.get("user_id_pk") == session.get("user_id_fk"):
                    row.update({
                        "user_id_pk": user.get("user_id_pk"),
                        "user_principal_name": user.get("user_principal_name"),
                        "display_name": user.get("display_name"),
                        "email": user.get("email"),
                        "user_org_id_fk": user.get("user_org_id_fk"),
                        "user_department_id_fk": user.get("user_department_id_fk"),
                        "is_active": user.get("is_active"),
                        "is_deleted": user.get("is_deleted")
                    })
                rows.append(row)
            return FakeResult(rows=rows[:1])

        if "from authentication_sessions" in query:
            session_id = params.get("session_id")
            rows = [
                session
                for session in self.engine.sessions
                if session.get("auth_session_id_pk") == session_id
            ]
            return FakeResult(rows=rows[:1])

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

    def _execute_session_rotation(self, params):
        now_utc = datetime.now(timezone.utc)
        for session in self.engine.sessions:
            expires_at = session.get("refresh_expires_at")
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)

            if (
                session.get("auth_session_id_pk") == params.get("session_id")
                and session.get("refresh_token_hash")
                == params.get("old_refresh_token_hash")
                and session.get("revoked_at") is None
                and expires_at > now_utc
            ):
                session["refresh_token_hash"] = params.get(
                    "new_refresh_token_hash"
                )
                session["last_refreshed_at"] = datetime.now()
                session["access_expires_at"] = params.get("access_expires_at")
                session["refresh_expires_at"] = params.get("refresh_expires_at")
                self.engine.rotation_count += 1
                return FakeResult(rowcount=1)

        return FakeResult(rowcount=0)


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
        self.rotation_count = 0

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


def base_organization():
    return {
        "org_id_pk": 1,
        "org_name": "Urban Express Transport"
    }


def base_session(**overrides):
    now = datetime.now(timezone.utc)
    session = {
        "auth_session_id_pk": 1,
        "user_id_fk": 10,
        "org_id_fk": 1,
        "refresh_token_hash": _hash_refresh_token(REFRESH_TOKEN),
        "created_at": now.replace(tzinfo=None),
        "access_expires_at": (now + timedelta(minutes=30)).replace(tzinfo=None),
        "refresh_expires_at": (now + timedelta(days=30)).replace(tzinfo=None),
        "last_refreshed_at": now.replace(tzinfo=None),
        "revoked_at": None,
        "revoke_reason": None
    }
    session.update(overrides)
    return session


def base_roles():
    return [
        {
            "user_role_id_pk": 100,
            "user_id_fk": 10,
            "role_id_fk": 1,
            "role_name": "System Administrator",
            "role_description": "Current role",
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


def contains_key(value, unsafe_keys):
    if isinstance(value, dict):
        return any(
            key in unsafe_keys or contains_key(item, unsafe_keys)
            for key, item in value.items()
        )

    if isinstance(value, list):
        return any(contains_key(item, unsafe_keys) for item in value)

    return False


class AuthenticationRefreshLogicTest(unittest.TestCase):
    def refresh(self, engine, refresh_token=REFRESH_TOKEN, env=None):
        if env is None:
            env = {
                "JWT_SECRET_KEY": JWT_SECRET,
                "JWT_ALGORITHM": "HS256",
                "ACCESS_TOKEN_EXPIRE_MINUTES": "30",
                "REFRESH_TOKEN_EXPIRE_DAYS": "30"
            }

        payload = (
            {}
            if refresh_token is None
            else {"refresh_token": refresh_token}
        )

        with patch.object(authentication_refresh, "db_engine", return_value=engine):
            with patch.dict(os.environ, env, clear=True):
                return authentication_refresh.refresh_authentication(payload)

    def authenticate_me(self, engine, access_token):
        env = {
            "JWT_SECRET_KEY": JWT_SECRET,
            "JWT_ALGORITHM": "HS256",
            "ACCESS_TOKEN_EXPIRE_MINUTES": "30",
            "REFRESH_TOKEN_EXPIRE_DAYS": "30"
        }
        with patch.object(authentication_me, "db_engine", return_value=engine):
            with patch.dict(os.environ, env, clear=True):
                return authentication_me.get_authenticated_user(access_token)

    def test_successful_refresh_rotates_tokens(self):
        engine = base_engine()
        old_hash = engine.sessions[0]["refresh_token_hash"]

        response = self.refresh(engine)

        self.assertTrue(response["authenticated"])
        self.assertEqual(response["token_type"], "bearer")
        self.assertEqual(response["expires_in"], 1800)
        self.assertNotEqual(response["refresh_token"], REFRESH_TOKEN)
        self.assertEqual(engine.rotation_count, 1)
        self.assertNotEqual(engine.sessions[0]["refresh_token_hash"], old_hash)
        self.assertEqual(
            engine.sessions[0]["refresh_token_hash"],
            _hash_refresh_token(response["refresh_token"])
        )

    def test_new_access_token_works(self):
        engine = base_engine()
        refresh_response = self.refresh(engine)

        me_response = self.authenticate_me(
            engine,
            refresh_response["access_token"]
        )

        self.assertTrue(me_response["authenticated"])
        self.assertEqual(me_response["user"]["user_id"], 10)
        self.assertEqual(me_response["session"]["session_id"], 1)

    def test_new_refresh_token_works(self):
        engine = base_engine()
        first_response = self.refresh(engine)

        second_response = self.refresh(
            engine,
            first_response["refresh_token"]
        )

        self.assertTrue(second_response["authenticated"])
        self.assertEqual(engine.rotation_count, 2)
        self.assertNotEqual(
            first_response["refresh_token"],
            second_response["refresh_token"]
        )

    def test_old_refresh_token_fails_after_rotation(self):
        engine = base_engine()
        first_response = self.refresh(engine)

        second_response = self.refresh(engine, REFRESH_TOKEN)

        self.assertTrue(first_response["authenticated"])
        self.assertFalse(second_response["authenticated"])
        self.assertEqual(second_response["error_code"], "invalid_refresh_token")
        self.assertEqual(engine.rotation_count, 1)

    def test_expired_refresh_token_fails(self):
        expired_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        engine = base_engine(
            sessions=[
                base_session(refresh_expires_at=expired_at.replace(tzinfo=None))
            ]
        )

        response = self.refresh(engine)

        self.assertFalse(response["authenticated"])
        self.assertEqual(response["error_code"], "expired_refresh_token")
        self.assertEqual(engine.rotation_count, 0)

    def test_revoked_session_fails(self):
        engine = base_engine(
            sessions=[
                base_session(revoked_at=datetime.now())
            ]
        )

        response = self.refresh(engine)

        self.assertFalse(response["authenticated"])
        self.assertEqual(response["error_code"], "revoked_session")
        self.assertEqual(engine.rotation_count, 0)

    def test_unknown_refresh_token_fails(self):
        response = self.refresh(base_engine(), "unknown-refresh-token")

        self.assertFalse(response["authenticated"])
        self.assertEqual(response["error_code"], "invalid_refresh_token")

    def test_missing_refresh_token_fails(self):
        response = self.refresh(base_engine(), None)

        self.assertFalse(response["authenticated"])
        self.assertEqual(response["error_code"], "missing_refresh_token")

    def test_inactive_user_fails(self):
        response = self.refresh(
            base_engine(user=base_user(is_active=False))
        )

        self.assertFalse(response["authenticated"])
        self.assertEqual(response["error_code"], "inactive_user")

    def test_deleted_user_fails(self):
        response = self.refresh(
            base_engine(user=base_user(is_deleted=True))
        )

        self.assertFalse(response["authenticated"])
        self.assertEqual(response["error_code"], "deleted_user")

    def test_organization_mismatch_fails(self):
        response = self.refresh(
            base_engine(user=base_user(user_org_id_fk=2))
        )

        self.assertFalse(response["authenticated"])
        self.assertEqual(response["error_code"], "organization_mismatch")

    def test_two_repeated_old_refresh_attempts_do_not_both_succeed(self):
        engine = base_engine()

        first_response = self.refresh(engine, REFRESH_TOKEN)
        second_response = self.refresh(engine, REFRESH_TOKEN)

        self.assertTrue(first_response["authenticated"])
        self.assertFalse(second_response["authenticated"])
        self.assertEqual(second_response["error_code"], "invalid_refresh_token")
        self.assertEqual(engine.rotation_count, 1)

    def test_plaintext_refresh_token_is_never_persisted(self):
        engine = base_engine()
        response = self.refresh(engine)

        persisted_hash = engine.sessions[0]["refresh_token_hash"]

        self.assertNotEqual(persisted_hash, REFRESH_TOKEN)
        self.assertNotEqual(persisted_hash, response["refresh_token"])
        self.assertEqual(
            persisted_hash,
            hashlib.sha256(
                response["refresh_token"].encode("utf-8")
            ).hexdigest()
        )

    def test_token_values_are_not_logged(self):
        engine = base_engine()
        with patch("builtins.print") as mock_print:
            with patch.object(logging.Logger, "_log") as mock_log:
                response = self.refresh(engine)

        self.assertTrue(response["authenticated"])
        mock_print.assert_not_called()
        mock_log.assert_not_called()

    def test_token_hashes_and_secrets_are_not_returned(self):
        response = self.refresh(base_engine())

        self.assertTrue(response["authenticated"])
        self.assertFalse(
            contains_key(
                response,
                {
                    "password",
                    "password_hash",
                    "hashed_password",
                    "refresh_token_hash",
                    "JWT_SECRET_KEY"
                }
            )
        )

    def test_missing_authentication_sessions_table_returns_development_error(self):
        response = self.refresh(base_engine(sessions_table_exists=False))

        self.assertFalse(response["authenticated"])
        self.assertEqual(
            response["error_code"],
            "authentication_development_error"
        )

    def test_missing_jwt_secret_key_returns_configuration_error(self):
        response = self.refresh(base_engine(), env={})

        self.assertFalse(response["authenticated"])
        self.assertEqual(
            response["error_code"],
            "authentication_configuration_error"
        )

    def test_new_access_token_claims_match_login_format(self):
        response = self.refresh(base_engine())

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
        self.assertIn("iat", decoded)
        self.assertIn("exp", decoded)
        self.assertIn("jti", decoded)


if __name__ == "__main__":
    unittest.main()
