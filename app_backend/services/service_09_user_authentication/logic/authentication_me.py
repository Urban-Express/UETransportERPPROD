from datetime import date, datetime
from decimal import Decimal

import jwt
from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import (
    db_engine,
)
from app_backend.services.service_09_user_authentication.logic.authentication_login import (
    AuthenticationConfigurationError,
    AuthenticationDevelopmentError,
    _get_license_context,
    _get_role_context,
    _organization_response,
    _user_response,
    get_jwt_configuration,
    verify_authentication_sessions_table,
)


def _authentication_failure(error_code: str, error_message: str):
    return {
        "authenticated": False,
        "error_code": error_code,
        "error": error_message
    }


def _safe_value(value):
    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, Decimal):
        return float(value)

    return value


def _safe_record(record):
    if not record:
        return None

    return {
        key: _safe_value(value)
        for key, value in dict(record).items()
    }


def _normalize_access_token(access_token: str):
    if not isinstance(access_token, str):
        return None

    access_token = access_token.strip()
    if not access_token:
        return None

    if access_token.lower().startswith("bearer "):
        access_token = access_token[7:].strip()

    return access_token or None


def _required_int_claim(claims: dict, claim_name: str):
    value = claims.get(claim_name)
    if value is None or value == "":
        raise ValueError(f"Missing required claim: {claim_name}")

    return int(value)


def _decode_and_validate_access_token(access_token: str):
    normalized_token = _normalize_access_token(access_token)
    if not normalized_token:
        return None, _authentication_failure(
            "invalid_access_token",
            "Invalid access token."
        )

    jwt_config = get_jwt_configuration()

    try:
        claims = jwt.decode(
            normalized_token,
            jwt_config["jwt_secret_key"],
            algorithms=[jwt_config["jwt_algorithm"]]
        )
    except jwt.ExpiredSignatureError:
        return None, _authentication_failure(
            "expired_access_token",
            "Access token has expired."
        )
    except jwt.InvalidSignatureError:
        return None, _authentication_failure(
            "invalid_signature",
            "Access token signature is invalid."
        )
    except jwt.InvalidTokenError:
        return None, _authentication_failure(
            "invalid_access_token",
            "Invalid access token."
        )

    if claims.get("token_type") != "access":
        return None, _authentication_failure(
            "invalid_token_type",
            "Invalid token type. Access token is required."
        )

    try:
        token_context = {
            "user_id": _required_int_claim(claims, "sub"),
            "org_id": _required_int_claim(claims, "org_id"),
            "session_id": _required_int_claim(claims, "session_id"),
            "user_principal_name": claims.get("user_principal_name"),
            "claims": claims
        }
    except (TypeError, ValueError):
        return None, _authentication_failure(
            "missing_token_claims",
            "Access token is missing required authentication claims."
        )

    return token_context, None


def _get_authentication_session(auth_engine, session_id: int):
    get_session = text("""
        select
            auth_session_id_pk,
            user_id_fk,
            org_id_fk,
            created_at,
            access_expires_at,
            refresh_expires_at,
            last_refreshed_at,
            revoked_at,
            revoke_reason
        from authentication_sessions
        where auth_session_id_pk = :session_id
        limit 1
    """)

    with auth_engine.begin() as conn:
        session = conn.execute(
            get_session,
            {"session_id": session_id}
        ).mappings().fetchone()

    return _safe_record(session)


def _validate_session(session: dict | None, token_context: dict):
    if not session:
        return _authentication_failure(
            "missing_session",
            "Authentication session was not found."
        )

    if session.get("revoked_at") is not None:
        return _authentication_failure(
            "revoked_session",
            "Authentication session has been revoked."
        )

    if int(session.get("user_id_fk")) != token_context["user_id"]:
        return _authentication_failure(
            "session_user_mismatch",
            "Authentication session does not match token user."
        )

    if int(session.get("org_id_fk")) != token_context["org_id"]:
        return _authentication_failure(
            "organization_mismatch",
            "Authentication session does not match token organization."
        )

    return None


def _get_current_user(auth_engine, user_id: int):
    get_user = text("""
        select
            user_id_pk,
            user_principal_name,
            display_name,
            email,
            user_org_id_fk,
            user_department_id_fk,
            is_active,
            is_deleted
        from user_master
        where user_id_pk = :user_id
        limit 1
    """)

    with auth_engine.begin() as conn:
        user = conn.execute(
            get_user,
            {"user_id": user_id}
        ).mappings().fetchone()

    return _safe_record(user)


def _validate_current_user(user: dict | None, token_context: dict):
    if not user:
        return _authentication_failure(
            "missing_user",
            "Authenticated user was not found."
        )

    if user.get("is_active") is not True:
        return _authentication_failure(
            "inactive_user",
            "Authenticated user is inactive."
        )

    if user.get("is_deleted") is True:
        return _authentication_failure(
            "deleted_user",
            "Authenticated user is deleted."
        )

    if int(user.get("user_org_id_fk")) != token_context["org_id"]:
        return _authentication_failure(
            "organization_mismatch",
            "Authenticated user does not belong to the token organization."
        )

    return None


def _get_current_organization(auth_engine, org_id: int):
    get_organization = text("""
        select
            org_id_pk,
            org_name
        from organization_master
        where org_id_pk = :org_id
        limit 1
    """)

    with auth_engine.begin() as conn:
        organization = conn.execute(
            get_organization,
            {"org_id": org_id}
        ).mappings().fetchone()

    return _safe_record(organization)


def _session_response(session: dict):
    return {
        "session_id": session.get("auth_session_id_pk"),
        "access_expires_at": session.get("access_expires_at")
    }


def get_authenticated_user(access_token: str):
    try:
        token_context, token_error = _decode_and_validate_access_token(
            access_token
        )
        if token_error:
            return token_error

        auth_engine = db_engine()
        verify_authentication_sessions_table(auth_engine)

        session = _get_authentication_session(
            auth_engine,
            token_context["session_id"]
        )
        session_error = _validate_session(session, token_context)
        if session_error:
            return session_error

        user = _get_current_user(
            auth_engine,
            token_context["user_id"]
        )
        user_error = _validate_current_user(user, token_context)
        if user_error:
            return user_error

        organization = _get_current_organization(
            auth_engine,
            token_context["org_id"]
        )
        if not organization:
            return _authentication_failure(
                "organization_mismatch",
                "Token organization was not found."
            )

        roles = _get_role_context(
            auth_engine,
            token_context["user_id"]
        )
        licenses = _get_license_context(
            auth_engine,
            token_context["org_id"]
        )

        return {
            "authenticated": True,
            "user": _user_response(user),
            "organization": _organization_response(organization),
            "roles": roles,
            "licenses": licenses,
            "session": _session_response(session)
        }

    except AuthenticationConfigurationError as exc:
        return _authentication_failure(
            "authentication_configuration_error",
            str(exc)
        )
    except AuthenticationDevelopmentError as exc:
        return _authentication_failure(
            "authentication_development_error",
            str(exc)
        )
    except Exception:
        return _authentication_failure(
            "authentication_validation_error",
            "Authenticated user could not be validated."
        )
