import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import (
    db_engine,
)
from app_backend.services.service_09_user_authentication.logic.authentication_login import (
    AuthenticationConfigurationError,
    AuthenticationDevelopmentError,
    _create_access_token,
    _db_timestamp,
    _hash_refresh_token,
    get_jwt_configuration,
    verify_authentication_sessions_table,
)
from app_backend.services.service_09_user_authentication.logic.authentication_me import (
    _authentication_failure,
)


def _get_required_refresh_token(payload: dict):
    refresh_token = payload.get("refresh_token")
    if not isinstance(refresh_token, str) or refresh_token == "":
        return None

    return refresh_token


def _get_session_for_refresh(conn, refresh_token_hash: str):
    get_session = text("""
        select
            s.auth_session_id_pk,
            s.user_id_fk,
            s.org_id_fk,
            s.refresh_expires_at,
            s.revoked_at,
            u.user_id_pk,
            u.user_principal_name,
            u.display_name,
            u.email,
            u.user_org_id_fk,
            u.user_department_id_fk,
            u.is_active,
            u.is_deleted
        from authentication_sessions s
        left join user_master u
            on u.user_id_pk = s.user_id_fk
        where s.refresh_token_hash = :refresh_token_hash
        limit 1
    """)

    session = conn.execute(
        get_session,
        {"refresh_token_hash": refresh_token_hash}
    ).mappings().fetchone()

    return dict(session) if session else None


def _normalize_db_datetime(value):
    if value is None:
        return None

    if isinstance(value, str):
        return datetime.fromisoformat(value).astimezone(timezone.utc)

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def _refresh_failure(error_code: str, error_message: str):
    return _authentication_failure(error_code, error_message)


def _validate_refresh_session(session: dict | None, now_utc: datetime):
    if not session:
        return _refresh_failure(
            "invalid_refresh_token",
            "Invalid refresh token."
        )

    if session.get("revoked_at") is not None:
        return _refresh_failure(
            "revoked_session",
            "Authentication session has been revoked."
        )

    refresh_expires_at = _normalize_db_datetime(
        session.get("refresh_expires_at")
    )
    if not refresh_expires_at or refresh_expires_at <= now_utc:
        return _refresh_failure(
            "expired_refresh_token",
            "Refresh token has expired."
        )

    if not session.get("user_id_pk"):
        return _refresh_failure(
            "missing_user",
            "Authenticated user was not found."
        )

    if session.get("is_active") is not True:
        return _refresh_failure(
            "inactive_user",
            "Authenticated user is inactive."
        )

    if session.get("is_deleted") is True:
        return _refresh_failure(
            "deleted_user",
            "Authenticated user is deleted."
        )

    if int(session.get("user_org_id_fk")) != int(session.get("org_id_fk")):
        return _refresh_failure(
            "organization_mismatch",
            "Authenticated user does not belong to the session organization."
        )

    return None


def _rotate_refresh_token(
    conn,
    session_id: int,
    old_refresh_token_hash: str,
    new_refresh_token_hash: str,
    access_expires_at: datetime,
    refresh_expires_at: datetime
):
    update_session = text("""
        update authentication_sessions
        set
            refresh_token_hash = :new_refresh_token_hash,
            last_refreshed_at = current_timestamp,
            access_expires_at = :access_expires_at,
            refresh_expires_at = :refresh_expires_at
        where auth_session_id_pk = :session_id
        and refresh_token_hash = :old_refresh_token_hash
        and revoked_at is null
        and refresh_expires_at > current_timestamp
    """)

    params_update = {
        "session_id": session_id,
        "old_refresh_token_hash": old_refresh_token_hash,
        "new_refresh_token_hash": new_refresh_token_hash,
        "access_expires_at": _db_timestamp(access_expires_at),
        "refresh_expires_at": _db_timestamp(refresh_expires_at)
    }

    result = conn.execute(update_session, params_update)

    return result.rowcount == 1


def _session_user_for_token(session: dict):
    return {
        "user_id_pk": session.get("user_id_pk"),
        "user_principal_name": session.get("user_principal_name"),
        "display_name": session.get("display_name"),
        "email": session.get("email"),
        "user_org_id_fk": session.get("user_org_id_fk"),
        "user_department_id_fk": session.get("user_department_id_fk")
    }


def refresh_authentication(payload: dict):
    try:
        payload = payload or {}
        refresh_token = _get_required_refresh_token(payload)
        if not refresh_token:
            return _refresh_failure(
                "missing_refresh_token",
                "refresh_token is required."
            )

        old_refresh_token_hash = _hash_refresh_token(refresh_token)
        auth_engine = db_engine()

        verify_authentication_sessions_table(auth_engine)

        jwt_config = get_jwt_configuration()
        now_utc = datetime.now(timezone.utc)
        access_expires_at = now_utc + timedelta(
            minutes=jwt_config["access_token_expire_minutes"]
        )
        refresh_expires_at = now_utc + timedelta(
            days=jwt_config["refresh_token_expire_days"]
        )

        new_refresh_token = secrets.token_urlsafe(64)
        new_refresh_token_hash = _hash_refresh_token(new_refresh_token)

        with auth_engine.begin() as conn:
            session = _get_session_for_refresh(
                conn,
                old_refresh_token_hash
            )
            session_error = _validate_refresh_session(session, now_utc)
            if session_error:
                return session_error

            rotation_succeeded = _rotate_refresh_token(
                conn=conn,
                session_id=session.get("auth_session_id_pk"),
                old_refresh_token_hash=old_refresh_token_hash,
                new_refresh_token_hash=new_refresh_token_hash,
                access_expires_at=access_expires_at,
                refresh_expires_at=refresh_expires_at
            )

        if not rotation_succeeded:
            return _refresh_failure(
                "invalid_refresh_token",
                "Invalid refresh token."
            )

        access_token = _create_access_token(
            jwt_config=jwt_config,
            user=_session_user_for_token(session),
            org_id=session.get("org_id_fk"),
            session_id=session.get("auth_session_id_pk"),
            issued_at=now_utc,
            expires_at=access_expires_at
        )

        return {
            "authenticated": True,
            "access_token": access_token,
            "refresh_token": new_refresh_token,
            "token_type": "bearer",
            "expires_in": jwt_config["access_token_expire_minutes"] * 60
        }

    except AuthenticationConfigurationError as exc:
        return _refresh_failure(
            "authentication_configuration_error",
            str(exc)
        )
    except AuthenticationDevelopmentError as exc:
        return _refresh_failure(
            "authentication_development_error",
            str(exc)
        )
    except Exception:
        return _refresh_failure(
            "authentication_refresh_error",
            "Authentication refresh could not be completed."
        )
