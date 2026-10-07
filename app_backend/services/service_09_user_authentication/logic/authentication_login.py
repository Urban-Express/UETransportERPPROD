import hashlib
import os
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import bcrypt
import jwt
from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import (
    db_engine,
)


INVALID_CREDENTIALS_RESPONSE = {
    "authenticated": False,
    "error": "Invalid organization or credentials."
}

AUTHENTICATION_SESSIONS_DDL_PATH = (
    "app_backend/services/service_09_user_authentication/data/"
    "authentication_sessions_ddl.sql"
)


class AuthenticationConfigurationError(Exception):
    pass


class AuthenticationDevelopmentError(Exception):
    pass


def _invalid_credentials_response():
    return INVALID_CREDENTIALS_RESPONSE.copy()


def _required_field_error(field_name: str):
    return {
        "authenticated": False,
        "error": f"{field_name} is required."
    }


def _trimmed_required_string(payload: dict, field_name: str):
    value = payload.get(field_name)
    if not isinstance(value, str):
        return None

    value = value.strip()
    if not value:
        return None

    return value


def _get_required_password(payload: dict):
    password = payload.get("password")
    if not isinstance(password, str) or password == "":
        return None

    return password


def _get_int_env(name: str, default_value: int):
    value = os.getenv(name)
    if value is None or value == "":
        return default_value

    try:
        return int(value)
    except ValueError as exc:
        raise AuthenticationConfigurationError(
            f"{name} must be configured as an integer."
        ) from exc


def get_jwt_configuration():
    jwt_secret_key = os.getenv("JWT_SECRET_KEY")
    if not jwt_secret_key:
        raise AuthenticationConfigurationError(
            "JWT_SECRET_KEY is not configured."
        )

    return {
        "jwt_secret_key": jwt_secret_key,
        "jwt_algorithm": os.getenv("JWT_ALGORITHM") or "HS256",
        "access_token_expire_minutes": _get_int_env(
            "ACCESS_TOKEN_EXPIRE_MINUTES",
            30
        ),
        "refresh_token_expire_days": _get_int_env(
            "REFRESH_TOKEN_EXPIRE_DAYS",
            30
        )
    }


def verify_authentication_sessions_table(auth_engine=None):
    auth_engine = auth_engine or db_engine()
    check_table = text("""
        select to_regclass('public.authentication_sessions') as table_name
    """)

    with auth_engine.begin() as conn:
        table_name = conn.execute(check_table).scalar_one()

    if not table_name:
        raise AuthenticationDevelopmentError(
            "authentication_sessions table does not exist. "
            f"Please review and execute {AUTHENTICATION_SESSIONS_DDL_PATH} "
            "before using authentication."
        )


def _resolve_organization(auth_engine, organization_name: str):
    get_organization = text("""
        select
            org_id_pk,
            org_name
        from organization_master
        where lower(org_name) = lower(:organization_name)
        limit 1
    """)

    with auth_engine.begin() as conn:
        organization = conn.execute(
            get_organization,
            {"organization_name": organization_name}
        ).mappings().fetchone()

    return dict(organization) if organization else None


def _resolve_user(auth_engine, user_principal_name: str, org_id: int):
    get_user = text("""
        select
            user_id_pk,
            auth_provider,
            user_principal_name,
            password_hash,
            display_name,
            email,
            user_org_id_fk,
            user_department_id_fk,
            is_active,
            is_deleted
        from user_master
        where lower(user_principal_name) = lower(:user_principal_name)
        and user_org_id_fk = :org_id
        limit 1
    """)

    with auth_engine.begin() as conn:
        user = conn.execute(
            get_user,
            {
                "user_principal_name": user_principal_name,
                "org_id": org_id
            }
        ).mappings().fetchone()

    return dict(user) if user else None


def _bcrypt_hash_bytes(password_hash):
    if isinstance(password_hash, bytes):
        return password_hash

    if isinstance(password_hash, str):
        return password_hash.encode("utf-8")

    return None


def _verify_password(submitted_password: str, stored_password_hash):
    password_hash_bytes = _bcrypt_hash_bytes(stored_password_hash)
    if not password_hash_bytes:
        return False

    try:
        return bcrypt.checkpw(
            submitted_password.encode("utf-8"),
            password_hash_bytes
        )
    except (TypeError, ValueError):
        return False


def _json_safe_value(value):
    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, Decimal):
        return float(value)

    return value


def _json_safe_records(records):
    return [
        {
            key: _json_safe_value(value)
            for key, value in dict(record).items()
        }
        for record in records
    ]


def _get_role_context(auth_engine, user_id: int):
    get_roles = text("""
        select
            ur.user_role_id_pk,
            ur.user_id_fk,
            ur.role_id_fk,
            r.role_name,
            r.role_description,
            r.is_active,
            ur.created_at
        from user_role_mapping ur
        join role_master r
            on r.role_id_pk = ur.role_id_fk
        where ur.user_id_fk = :user_id
        and coalesce(r.is_active, true) = true
        order by r.role_name
    """)

    with auth_engine.begin() as conn:
        roles = conn.execute(
            get_roles,
            {"user_id": user_id}
        ).mappings().all()

    return _json_safe_records(roles)


def _get_license_context(auth_engine, org_id: int):
    get_licenses = text("""
        select
            org_license_id,
            org_id_fk,
            org_management_module,
            hr_payroll_management_module,
            fleet_management_module,
            maintenance_management_module,
            gps_tracking_module,
            contracts_management_module,
            financial_management_module,
            created_by,
            created_at
        from organization_license_master
        where org_id_fk = :org_id
        order by org_license_id desc
    """)

    with auth_engine.begin() as conn:
        licenses = conn.execute(
            get_licenses,
            {"org_id": org_id}
        ).mappings().all()

    return _json_safe_records(licenses)


def _hash_refresh_token(refresh_token: str):
    return hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()


def _db_timestamp(value: datetime):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _create_authentication_session(
    auth_engine,
    user_id: int,
    org_id: int,
    refresh_token_hash: str,
    access_expires_at: datetime,
    refresh_expires_at: datetime
):
    insert_session = text("""
        insert into authentication_sessions (
            user_id_fk,
            org_id_fk,
            refresh_token_hash,
            access_expires_at,
            refresh_expires_at
        ) values (
            :user_id_fk,
            :org_id_fk,
            :refresh_token_hash,
            :access_expires_at,
            :refresh_expires_at
        )
        returning auth_session_id_pk
    """)

    params_insert = {
        "user_id_fk": user_id,
        "org_id_fk": org_id,
        "refresh_token_hash": refresh_token_hash,
        "access_expires_at": _db_timestamp(access_expires_at),
        "refresh_expires_at": _db_timestamp(refresh_expires_at)
    }

    with auth_engine.begin() as conn:
        return conn.execute(insert_session, params_insert).scalar_one()


def _create_access_token(
    jwt_config: dict,
    user: dict,
    org_id: int,
    session_id: int,
    issued_at: datetime,
    expires_at: datetime
):
    claims = {
        "sub": str(user.get("user_id_pk")),
        "org_id": org_id,
        "session_id": session_id,
        "user_principal_name": user.get("user_principal_name"),
        "token_type": "access",
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": str(uuid.uuid4())
    }

    return jwt.encode(
        claims,
        jwt_config["jwt_secret_key"],
        algorithm=jwt_config["jwt_algorithm"]
    )


def _user_response(user: dict):
    return {
        "user_id": user.get("user_id_pk"),
        "user_principal_name": user.get("user_principal_name"),
        "display_name": user.get("display_name"),
        "email": user.get("email"),
        "user_org_id_fk": user.get("user_org_id_fk"),
        "user_department_id_fk": user.get("user_department_id_fk")
    }


def _organization_response(organization: dict):
    return {
        "org_id": organization.get("org_id_pk"),
        "org_name": organization.get("org_name")
    }


def authenticate_user(payload: dict):
    try:
        payload = payload or {}

        organization_name = _trimmed_required_string(
            payload,
            "organization_name"
        )
        if not organization_name:
            return _required_field_error("organization_name")

        user_principal_name = _trimmed_required_string(
            payload,
            "user_principal_name"
        )
        if not user_principal_name:
            return _required_field_error("user_principal_name")

        password = _get_required_password(payload)
        if password is None:
            return _required_field_error("password")

        auth_engine = db_engine()

        organization = _resolve_organization(
            auth_engine,
            organization_name
        )
        if not organization:
            return _invalid_credentials_response()

        org_id = organization.get("org_id_pk")
        user = _resolve_user(
            auth_engine,
            user_principal_name,
            org_id
        )
        if not user:
            return _invalid_credentials_response()

        if user.get("auth_provider") != "LOCAL":
            return _invalid_credentials_response()

        if user.get("is_active") is not True:
            return _invalid_credentials_response()

        if user.get("is_deleted") is True:
            return _invalid_credentials_response()

        if not _verify_password(password, user.get("password_hash")):
            return _invalid_credentials_response()

        jwt_config = get_jwt_configuration()
        verify_authentication_sessions_table(auth_engine)

        roles = _get_role_context(auth_engine, user.get("user_id_pk"))
        licenses = _get_license_context(auth_engine, org_id)

        now_utc = datetime.now(timezone.utc)
        access_expires_at = now_utc + timedelta(
            minutes=jwt_config["access_token_expire_minutes"]
        )
        refresh_expires_at = now_utc + timedelta(
            days=jwt_config["refresh_token_expire_days"]
        )

        refresh_token = secrets.token_urlsafe(64)
        refresh_token_hash = _hash_refresh_token(refresh_token)

        session_id = _create_authentication_session(
            auth_engine=auth_engine,
            user_id=user.get("user_id_pk"),
            org_id=org_id,
            refresh_token_hash=refresh_token_hash,
            access_expires_at=access_expires_at,
            refresh_expires_at=refresh_expires_at
        )

        access_token = _create_access_token(
            jwt_config=jwt_config,
            user=user,
            org_id=org_id,
            session_id=session_id,
            issued_at=now_utc,
            expires_at=access_expires_at
        )

        return {
            "authenticated": True,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": jwt_config["access_token_expire_minutes"] * 60,
            "user": _user_response(user),
            "organization": _organization_response(organization),
            "roles": roles,
            "licenses": licenses
        }

    except AuthenticationConfigurationError as exc:
        return {
            "authenticated": False,
            "error": str(exc)
        }
    except AuthenticationDevelopmentError as exc:
        return {
            "authenticated": False,
            "error": str(exc)
        }
    except Exception:
        return {
            "authenticated": False,
            "error": "Authentication could not be completed."
        }
