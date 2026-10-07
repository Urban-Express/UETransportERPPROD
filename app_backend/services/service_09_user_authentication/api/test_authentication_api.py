import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import bcrypt
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app_backend.services.main import app, health_check
from app_backend.services.service_09_user_authentication.api import main as auth_api
from app_backend.services.service_09_user_authentication.logic import (
    authentication_login,
    authentication_logout,
    authentication_me,
    authentication_refresh,
)


JWT_SECRET = "unit-test-secret-with-at-least-32-bytes"
PASSWORD = "PlainTextPassword123"
ORGANIZATION_NAME = "Urban Express Transport"
USER_PRINCIPAL_NAME = "system_admin"


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
            return FakeResult(scalar_value="authentication_sessions")

        if query.strip().startswith("insert into authentication_sessions"):
            return self._insert_session(params)

        if query.strip().startswith("update authentication_sessions"):
            if "revoke_reason = 'user_logout'" in query:
                return self._revoke_session(params)
            return self._rotate_session(params)

        if "from authentication_sessions s" in query:
            return self._session_with_user_by_refresh_hash(
                params.get("refresh_token_hash")
            )

        if (
            "from authentication_sessions" in query
            and "refresh_token_hash = :refresh_token_hash" in query
        ):
            return self._session_by_refresh_hash(
                params.get("refresh_token_hash")
            )

        if "from authentication_sessions" in query:
            return self._session_by_id(params.get("session_id"))

        if "from organization_master" in query:
            if "organization_name" in params:
                return self._organization_by_name(params.get("organization_name"))
            return self._organization_by_id(params.get("org_id"))

        if "from user_master" in query:
            if "user_principal_name" in params:
                return self._user_by_principal_and_org(
                    params.get("user_principal_name"),
                    params.get("org_id")
                )
            return self._user_by_id(params.get("user_id"))

        if "from user_role_mapping" in query:
            return FakeResult(
                rows=[
                    role
                    for role in self.engine.roles
                    if role.get("user_id_fk") == params.get("user_id")
                ]
            )

        if "from organization_license_master" in query:
            return FakeResult(
                rows=[
                    license_row
                    for license_row in self.engine.licenses
                    if license_row.get("org_id_fk") == params.get("org_id")
                ]
            )

        raise AssertionError(f"Unhandled SQL in fake engine: {query}")

    def _insert_session(self, params):
        session_id = len(self.engine.sessions) + 1
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        self.engine.sessions.append({
            "auth_session_id_pk": session_id,
            "user_id_fk": params.get("user_id_fk"),
            "org_id_fk": params.get("org_id_fk"),
            "refresh_token_hash": params.get("refresh_token_hash"),
            "created_at": now,
            "access_expires_at": params.get("access_expires_at"),
            "refresh_expires_at": params.get("refresh_expires_at"),
            "last_refreshed_at": now,
            "revoked_at": None,
            "revoke_reason": None,
        })
        return FakeResult(scalar_value=session_id)

    def _revoke_session(self, params):
        refresh_token_hash = params.get("refresh_token_hash")
        for session in self.engine.sessions:
            if (
                session.get("refresh_token_hash") == refresh_token_hash
                and session.get("revoked_at") is None
            ):
                session["revoked_at"] = datetime.now()
                session["revoke_reason"] = "USER_LOGOUT"
                return FakeResult(rowcount=1)
        return FakeResult(rowcount=0)

    def _rotate_session(self, params):
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
                return FakeResult(rowcount=1)
        return FakeResult(rowcount=0)

    def _session_with_user_by_refresh_hash(self, refresh_token_hash):
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
                    "is_deleted": user.get("is_deleted"),
                })
            rows.append(row)
        return FakeResult(rows=rows[:1])

    def _session_by_refresh_hash(self, refresh_token_hash):
        return FakeResult(
            rows=[
                session
                for session in self.engine.sessions
                if session.get("refresh_token_hash") == refresh_token_hash
            ][:1]
        )

    def _session_by_id(self, session_id):
        return FakeResult(
            rows=[
                session
                for session in self.engine.sessions
                if session.get("auth_session_id_pk") == session_id
            ][:1]
        )

    def _organization_by_name(self, organization_name):
        organization = self.engine.organization
        if (
            organization
            and organization.get("org_name", "").lower()
            == organization_name.lower()
        ):
            return FakeResult(rows=[organization])
        return FakeResult(rows=[])

    def _organization_by_id(self, org_id):
        organization = self.engine.organization
        if organization and organization.get("org_id_pk") == org_id:
            return FakeResult(rows=[organization])
        return FakeResult(rows=[])

    def _user_by_principal_and_org(self, user_principal_name, org_id):
        user = self.engine.user
        if (
            user
            and user.get("user_principal_name", "").lower()
            == user_principal_name.lower()
            and user.get("user_org_id_fk") == org_id
        ):
            return FakeResult(rows=[user])
        return FakeResult(rows=[])

    def _user_by_id(self, user_id):
        user = self.engine.user
        if user and user.get("user_id_pk") == user_id:
            return FakeResult(rows=[user])
        return FakeResult(rows=[])


class FakeBegin:
    def __init__(self, engine):
        self.engine = engine

    def __enter__(self):
        return FakeConnection(self.engine)

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeEngine:
    def __init__(self):
        self.organization = {
            "org_id_pk": 1,
            "org_name": ORGANIZATION_NAME,
        }
        self.user = {
            "user_id_pk": 10,
            "auth_provider": "LOCAL",
            "user_principal_name": USER_PRINCIPAL_NAME,
            "password_hash": bcrypt.hashpw(
                PASSWORD.encode("utf-8"),
                bcrypt.gensalt()
            ).decode("utf-8"),
            "display_name": "System Administrator",
            "email": "system.admin@ue.local",
            "user_org_id_fk": 1,
            "user_department_id_fk": 2,
            "is_active": True,
            "is_deleted": False,
        }
        self.roles = [
            {
                "user_role_id_pk": 100,
                "user_id_fk": 10,
                "role_id_fk": 1,
                "role_name": "System Administrator",
                "role_description": "Full system access",
                "is_active": True,
                "created_at": datetime(2026, 1, 1, 12, 0, 0),
            }
        ]
        self.licenses = [
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
                "created_at": datetime(2026, 1, 1, 12, 0, 0),
            }
        ]
        self.sessions = []

    def begin(self):
        return FakeBegin(self)


class AuthenticationApiTest(unittest.TestCase):
    def setUp(self):
        self.engine = FakeEngine()
        self.env = {
            "JWT_SECRET_KEY": JWT_SECRET,
            "JWT_ALGORITHM": "HS256",
            "ACCESS_TOKEN_EXPIRE_MINUTES": "30",
            "REFRESH_TOKEN_EXPIRE_DAYS": "30",
        }

    def run_with_patches(self, callback):
        with patch.object(authentication_login, "db_engine", return_value=self.engine):
            with patch.object(authentication_me, "db_engine", return_value=self.engine):
                with patch.object(authentication_refresh, "db_engine", return_value=self.engine):
                    with patch.object(authentication_logout, "db_engine", return_value=self.engine):
                        with patch.dict(os.environ, self.env, clear=True):
                            return callback()

    def test_health_openapi_and_full_authentication_flow(self):
        def scenario():
            self.assertEqual(
                health_check(),
                {"success": True, "data": {"status": "healthy"}}
            )

            openapi = app.openapi()
            self.assertEqual(app.docs_url, "/docs")
            self.assertIn("/api/v1/auth/login", openapi["paths"])
            self.assertIn("/api/v1/auth/me", openapi["paths"])
            self.assertIn("/api/v1/auth/refresh", openapi["paths"])
            self.assertIn("/api/v1/auth/logout", openapi["paths"])
            self.assertEqual(
                openapi["components"]["securitySchemes"]["HTTPBearer"],
                {"type": "http", "scheme": "bearer"}
            )
            self.assertEqual(
                openapi["paths"]["/api/v1/auth/me"]["get"]["security"],
                [{"HTTPBearer": []}]
            )

            login_response = auth_api.login_endpoint(
                auth_api.LoginRequest(
                    organization_name=ORGANIZATION_NAME,
                    user_principal_name=USER_PRINCIPAL_NAME,
                    password=PASSWORD,
                )
            )
            login_data = login_response["data"]
            self.assertTrue(login_data["authenticated"])
            self.assertIn("access_token", login_data)
            self.assertIn("refresh_token", login_data)
            self.assertEqual(login_data["user"]["user_id"], 10)
            self.assertEqual(login_data["organization"]["org_id"], 1)
            self.assertEqual(
                login_data["roles"][0]["role_name"],
                "System Administrator"
            )
            self.assertTrue(
                login_data["licenses"][0]["financial_management_module"]
            )

            access_token = login_data["access_token"]
            refresh_token = login_data["refresh_token"]

            me_response = auth_api.get_authenticated_user_endpoint(
                HTTPAuthorizationCredentials(
                    scheme="Bearer",
                    credentials=access_token,
                )
            )
            self.assertEqual(me_response["data"]["user"]["user_id"], 10)

            refresh_response = auth_api.refresh_authentication_endpoint(
                auth_api.RefreshRequest(refresh_token=refresh_token)
            )
            refresh_data = refresh_response["data"]
            self.assertIn("access_token", refresh_data)
            self.assertIn("refresh_token", refresh_data)
            self.assertNotEqual(refresh_data["refresh_token"], refresh_token)

            with self.assertRaises(HTTPException) as old_refresh_error:
                auth_api.refresh_authentication_endpoint(
                    auth_api.RefreshRequest(refresh_token=refresh_token)
                )
            self.assertEqual(old_refresh_error.exception.status_code, 401)

            new_access_token = refresh_data["access_token"]
            new_refresh_token = refresh_data["refresh_token"]

            new_me_response = auth_api.get_authenticated_user_endpoint(
                HTTPAuthorizationCredentials(
                    scheme="Bearer",
                    credentials=new_access_token,
                )
            )
            self.assertEqual(new_me_response["data"]["user"]["user_id"], 10)

            logout_response = auth_api.logout_user_endpoint(
                auth_api.LogoutRequest(refresh_token=new_refresh_token)
            )
            self.assertTrue(logout_response["data"]["success"])

            with self.assertRaises(HTTPException) as refresh_after_logout_error:
                auth_api.refresh_authentication_endpoint(
                    auth_api.RefreshRequest(refresh_token=new_refresh_token)
                )
            self.assertEqual(
                refresh_after_logout_error.exception.status_code,
                401
            )

            with self.assertRaises(HTTPException) as me_after_logout_error:
                auth_api.get_authenticated_user_endpoint(
                    HTTPAuthorizationCredentials(
                        scheme="Bearer",
                        credentials=new_access_token,
                    )
                )
            self.assertEqual(me_after_logout_error.exception.status_code, 401)

        self.run_with_patches(scenario)

    def test_invalid_login_uses_401(self):
        def scenario():
            with self.assertRaises(HTTPException) as exc_info:
                auth_api.login_endpoint(
                    auth_api.LoginRequest(
                        organization_name=ORGANIZATION_NAME,
                        user_principal_name=USER_PRINCIPAL_NAME,
                        password="wrong-password",
                    )
                )

            self.assertEqual(exc_info.exception.status_code, 401)

        self.run_with_patches(scenario)

    def test_missing_bearer_token_uses_401(self):
        with self.assertRaises(HTTPException) as exc_info:
            auth_api.get_authenticated_user_endpoint(None)

        self.assertEqual(exc_info.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
