import logging
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt

from app_backend.services.service_09_user_authentication.logic import (
    authentication_logout,
    authentication_me,
    authentication_refresh,
)
from app_backend.services.service_09_user_authentication.logic.authentication_login import (
    _hash_refresh_token,
)


JWT_SECRET = "unit-test-secret-with-at-least-32-bytes"
REFRESH_TOKEN = "logout-refresh-token"


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

        if self.engine.fail_on_execute:
            raise Exception("Simulated database failure.")

        if query.strip().startswith("update authentication_sessions"):
            if "revoke_reason = 'user_logout'" in query:
                return self._execute_logout(params)
            return self._execute_refresh_rotation(params)

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

        if (
            "from authentication_sessions" in query
            and "refresh_token_hash = :refresh_token_hash" in query
        ):
            refresh_token_hash = params.get("refresh_token_hash")
            rows = [
                session
                for session in self.engine.sessions
                if session.get("refresh_token_hash") == refresh_token_hash
            ]
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

    def _execute_logout(self, params):
        refresh_token_hash = params.get("refresh_token_hash")
        for session in self.engine.sessions:
            if (
                session.get("refresh_token_hash") == refresh_token_hash
                and session.get("revoked_at") is None
            ):
                session["revoked_at"] = datetime.now()
                session["revoke_reason"] = "USER_LOGOUT"
                self.engine.logout_update_count += 1
                return FakeResult(rowcount=1)

        return FakeResult(rowcount=0)

    def _execute_refresh_rotation(self, params):
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
                self.engine.refresh_update_count += 1
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
        sessions_table_exists=True,
        fail_on_execute=False
    ):
        self.user = user
        self.organization = organization
        self.sessions = sessions or []
        self.roles = roles or []
        self.licenses = licenses or []
        self.sessions_table_exists = sessions_table_exists
        self.fail_on_execute = fail_on_execute
        self.logout_update_count = 0
        self.refresh_update_count = 0

    def begin(self):
        return FakeBegin(self)


def base_user():
    return {
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
        "sessions_table_exists": True,
        "fail_on_execute": False
    }
    values.update(overrides)
    return FakeEngine(**values)


def make_access_token():
    now = datetime.now(timezone.utc)
    claims = {
        "sub": "10",
        "org_id": 1,
        "session_id": 1,
        "user_principal_name": "system_admin",
        "token_type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=30)).timestamp()),
        "jti": "logout-test-jti"
    }
    return jwt.encode(claims, JWT_SECRET, algorithm="HS256")


class AuthenticationLogoutLogicTest(unittest.TestCase):
    def env(self):
        return {
            "JWT_SECRET_KEY": JWT_SECRET,
            "JWT_ALGORITHM": "HS256",
            "ACCESS_TOKEN_EXPIRE_MINUTES": "30",
            "REFRESH_TOKEN_EXPIRE_DAYS": "30"
        }

    def logout(self, engine, refresh_token=REFRESH_TOKEN):
        payload = (
            {}
            if refresh_token is None
            else {"refresh_token": refresh_token}
        )
        with patch.object(authentication_logout, "db_engine", return_value=engine):
            with patch.dict(os.environ, self.env(), clear=True):
                return authentication_logout.logout_user(payload)

    def refresh(self, engine, refresh_token=REFRESH_TOKEN):
        with patch.object(authentication_refresh, "db_engine", return_value=engine):
            with patch.dict(os.environ, self.env(), clear=True):
                return authentication_refresh.refresh_authentication(
                    {"refresh_token": refresh_token}
                )

    def me(self, engine, access_token):
        with patch.object(authentication_me, "db_engine", return_value=engine):
            with patch.dict(os.environ, self.env(), clear=True):
                return authentication_me.get_authenticated_user(access_token)

    def test_successful_logout_revokes_session(self):
        engine = base_engine()

        response = self.logout(engine)

        self.assertEqual(
            response,
            {"success": True, "message": "Logout successful."}
        )
        self.assertIsNotNone(engine.sessions[0]["revoked_at"])
        self.assertEqual(engine.sessions[0]["revoke_reason"], "USER_LOGOUT")

    def test_session_row_remains_after_logout(self):
        engine = base_engine()

        self.logout(engine)

        self.assertEqual(len(engine.sessions), 1)
        self.assertEqual(engine.sessions[0]["auth_session_id_pk"], 1)

    def test_second_logout_attempt_is_safe(self):
        engine = base_engine()

        first_response = self.logout(engine)
        second_response = self.logout(engine)

        self.assertEqual(
            first_response,
            {"success": True, "message": "Logout successful."}
        )
        self.assertEqual(
            second_response,
            {"success": True, "message": "Logout successful."}
        )
        self.assertEqual(engine.logout_update_count, 1)

    def test_refresh_after_logout_fails(self):
        engine = base_engine()
        self.logout(engine)

        response = self.refresh(engine)

        self.assertFalse(response["authenticated"])
        self.assertEqual(response["error_code"], "revoked_session")

    def test_auth_me_for_revoked_session_fails(self):
        engine = base_engine()
        access_token = make_access_token()
        self.logout(engine)

        response = self.me(engine, access_token)

        self.assertFalse(response["authenticated"])
        self.assertEqual(response["error_code"], "revoked_session")

    def test_invalid_refresh_token_fails(self):
        response = self.logout(base_engine(), "unknown-refresh-token")

        self.assertFalse(response["authenticated"])
        self.assertEqual(response["error_code"], "invalid_refresh_token")

    def test_missing_refresh_token_fails(self):
        response = self.logout(base_engine(), None)

        self.assertFalse(response["authenticated"])
        self.assertEqual(response["error_code"], "missing_refresh_token")

    def test_database_failure_is_handled(self):
        response = self.logout(base_engine(fail_on_execute=True))

        self.assertFalse(response["authenticated"])
        self.assertEqual(response["error_code"], "authentication_logout_error")

    def test_raw_token_values_are_not_logged(self):
        engine = base_engine()
        with patch("builtins.print") as mock_print:
            with patch.object(logging.Logger, "_log") as mock_log:
                response = self.logout(engine)

        self.assertEqual(
            response,
            {"success": True, "message": "Logout successful."}
        )
        mock_print.assert_not_called()
        mock_log.assert_not_called()

    def test_plaintext_refresh_token_is_not_persisted(self):
        engine = base_engine()

        self.logout(engine)

        self.assertNotEqual(
            engine.sessions[0]["refresh_token_hash"],
            REFRESH_TOKEN
        )
        self.assertEqual(
            engine.sessions[0]["refresh_token_hash"],
            _hash_refresh_token(REFRESH_TOKEN)
        )

    def test_missing_authentication_sessions_table_returns_development_error(self):
        response = self.logout(base_engine(sessions_table_exists=False))

        self.assertFalse(response["authenticated"])
        self.assertEqual(
            response["error_code"],
            "authentication_development_error"
        )


if __name__ == "__main__":
    unittest.main()
