from typing import Any, Iterable, Optional

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.encoders import jsonable_encoder

from app_backend.services.service_09_user_authentication.logic.authentication_me import (
    get_authenticated_user,
)


bearer_scheme = HTTPBearer(auto_error=False)


def _authentication_error_status(error_code: str, error_message: str) -> int:
    normalized = f"{error_code} {error_message}".lower()
    if (
        "inactive" in normalized
        or "deleted" in normalized
        or "organization_mismatch" in normalized
        or "forbidden" in normalized
    ):
        return 403
    return 401


def get_authenticated_context(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
) -> dict[str, Any]:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=401,
            detail="Authentication bearer token is required.",
        )

    auth_context = get_authenticated_user(credentials.credentials)
    if not auth_context.get("authenticated"):
        error_code = str(auth_context.get("error_code") or "")
        error_message = str(
            auth_context.get("error")
            or auth_context.get("message")
            or "Authentication failed."
        )
        raise HTTPException(
            status_code=_authentication_error_status(error_code, error_message),
            detail=error_message,
        )

    return auth_context


def authenticated_user_id(auth_context: dict[str, Any]) -> int:
    user = auth_context.get("user") or {}
    value = user.get("user_id") or user.get("user_id_pk")
    if value is None or value == "":
        raise HTTPException(
            status_code=401,
            detail="Authenticated user id is missing.",
        )
    return int(value)


def authenticated_principal(auth_context: dict[str, Any]) -> str:
    user = auth_context.get("user") or {}
    value = user.get("user_principal_name")
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(
            status_code=401,
            detail="Authenticated user principal is missing.",
        )
    return value.strip()


def authenticated_organization_id(auth_context: dict[str, Any]) -> int:
    organization = auth_context.get("organization") or {}
    user = auth_context.get("user") or {}
    value = organization.get("org_id") or user.get("user_org_id_fk")
    if value is None or value == "":
        raise HTTPException(
            status_code=401,
            detail="Authenticated organization id is missing.",
        )
    return int(value)


def _is_provided(value: Any) -> bool:
    return value is not None and value != ""


def _assert_same_org(field_name: str, submitted_value: Any, authenticated_org_id: int) -> None:
    if not _is_provided(submitted_value):
        return

    try:
        submitted_org_id = int(submitted_value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} must be a valid organization id.",
        ) from exc

    if submitted_org_id != authenticated_org_id:
        raise HTTPException(
            status_code=403,
            detail=f"{field_name} does not match the authenticated organization.",
        )


def _assert_same_principal(
    field_name: str,
    submitted_value: Any,
    authenticated_user_principal: str,
) -> None:
    if not _is_provided(submitted_value):
        return

    if (
        not isinstance(submitted_value, str)
        or submitted_value.strip().casefold()
        != authenticated_user_principal.casefold()
    ):
        raise HTTPException(
            status_code=403,
            detail=f"{field_name} does not match the authenticated user.",
        )


def _payload_dict(payload: Any) -> dict[str, Any]:
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return dict(payload)
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude_none=False)
    return dict(payload)


def bind_authenticated_payload(
    payload: Any,
    auth_context: dict[str, Any],
    *,
    org_fields: Iterable[str] = (),
    principal_fields: Iterable[str] = (),
    audit_actor: str = "principal",
    set_created_by: bool = False,
    set_updated_by: bool = False,
) -> dict[str, Any]:
    data = _payload_dict(payload)
    org_id = authenticated_organization_id(auth_context)
    user_id = authenticated_user_id(auth_context)
    principal = authenticated_principal(auth_context)

    for field_name in org_fields:
        _assert_same_org(field_name, data.get(field_name), org_id)
        data[field_name] = org_id

    for field_name in principal_fields:
        _assert_same_principal(field_name, data.get(field_name), principal)
        data[field_name] = principal

    audit_value: Any
    if audit_actor == "user_id":
        audit_value = user_id
    elif audit_actor == "principal":
        audit_value = principal
    else:
        raise ValueError("audit_actor must be 'principal' or 'user_id'.")

    if set_created_by:
        data["created_by"] = audit_value
    if set_updated_by:
        data["updated_by"] = audit_value

    data["authenticated_org_id"] = org_id
    data["authenticated_user_id"] = user_id
    data["authenticated_user_principal_name"] = principal
    return data


def logic_error_status(error_message: str) -> int:
    normalized = error_message.lower()
    if "not found" in normalized or "no processed monthly payroll found" in normalized:
        return 404
    if "ambiguous" in normalized:
        return 400
    if "already exists" in normalized or "duplicate" in normalized:
        return 409
    if "blocked by workflow" in normalized:
        return 403
    if (
        "authorization" in normalized
        or "forbidden" in normalized
        or "permission" in normalized
        or "does not have" in normalized
        or "does not match authenticated" in normalized
        or "assigned to another approver" in normalized
        or "lacks" in normalized
    ):
        return 403
    if "authentication" in normalized or "unauthorized" in normalized:
        return 401
    if (
        " is required" in normalized
        or " is empty" in normalized
        or "missing" in normalized
        or "invalid" in normalized
        or "must be" in normalized
        or "does not match" in normalized
        or "exceeds available" in normalized
        or "could not be allocated" in normalized
        or "deductions exceed payable earnings" in normalized
    ):
        return 400
    return 500


def _extract_logic_error(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        if value.get("error"):
            return str(value["error"])
        if value.get("success") is False:
            return str(
                value.get("error")
                or value.get("message")
                or value.get("status")
                or "Logic call failed."
            )
        for key, item in value.items():
            key_text = str(key)
            key_lower = key_text.lower()
            if (
                "error" in key_lower
                or "failed" in key_lower
                or "not found" in key_lower
                or "already exists" in key_lower
            ):
                return f"{key_text}{item}"
            nested_error = _extract_logic_error(item)
            if nested_error:
                return nested_error
    elif isinstance(value, (list, tuple)):
        for item in value:
            nested_error = _extract_logic_error(item)
            if nested_error:
                return nested_error

    return None


def raise_for_logic_error(value: Any) -> None:
    error_message = _extract_logic_error(value)
    if not error_message:
        try:
            error_message = _extract_logic_error(jsonable_encoder(value))
        except Exception:
            error_message = None
    if error_message:
        raise HTTPException(
            status_code=logic_error_status(error_message),
            detail=error_message,
        )
