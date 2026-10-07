from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import (
    db_engine,
)
from app_backend.services.service_09_user_authentication.logic.authentication_login import (
    AuthenticationDevelopmentError,
    _hash_refresh_token,
    verify_authentication_sessions_table,
)
from app_backend.services.service_09_user_authentication.logic.authentication_me import (
    _authentication_failure,
)


LOGOUT_SUCCESS_RESPONSE = {
    "success": True,
    "message": "Logout successful."
}


def _logout_failure(error_code: str, error_message: str):
    return _authentication_failure(error_code, error_message)


def _get_required_refresh_token(payload: dict):
    refresh_token = payload.get("refresh_token")
    if not isinstance(refresh_token, str) or refresh_token == "":
        return None

    return refresh_token


def _get_session_for_logout(conn, refresh_token_hash: str):
    get_session = text("""
        select
            auth_session_id_pk,
            revoked_at,
            revoke_reason
        from authentication_sessions
        where refresh_token_hash = :refresh_token_hash
        limit 1
    """)

    session = conn.execute(
        get_session,
        {"refresh_token_hash": refresh_token_hash}
    ).mappings().fetchone()

    return dict(session) if session else None


def _revoke_session(conn, refresh_token_hash: str):
    revoke_session = text("""
        update authentication_sessions
        set
            revoked_at = current_timestamp,
            revoke_reason = 'USER_LOGOUT'
        where refresh_token_hash = :refresh_token_hash
        and revoked_at is null
    """)

    return conn.execute(
        revoke_session,
        {"refresh_token_hash": refresh_token_hash}
    ).rowcount


def logout_user(payload: dict):
    try:
        payload = payload or {}
        refresh_token = _get_required_refresh_token(payload)
        if not refresh_token:
            return _logout_failure(
                "missing_refresh_token",
                "refresh_token is required."
            )

        refresh_token_hash = _hash_refresh_token(refresh_token)
        auth_engine = db_engine()

        verify_authentication_sessions_table(auth_engine)

        with auth_engine.begin() as conn:
            session = _get_session_for_logout(conn, refresh_token_hash)
            if not session:
                return _logout_failure(
                    "invalid_refresh_token",
                    "Invalid refresh token."
                )

            if session.get("revoked_at") is None:
                _revoke_session(conn, refresh_token_hash)

        return LOGOUT_SUCCESS_RESPONSE.copy()

    except AuthenticationDevelopmentError as exc:
        return _logout_failure(
            "authentication_development_error",
            str(exc)
        )
    except Exception:
        return _logout_failure(
            "authentication_logout_error",
            "Logout could not be completed."
        )
