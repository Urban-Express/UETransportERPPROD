from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app_backend.services.service_09_user_authentication.logic.authentication_login import (
    authenticate_user,
)
from app_backend.services.service_09_user_authentication.logic.authentication_logout import (
    logout_user,
)
from app_backend.services.service_09_user_authentication.logic.authentication_me import (
    get_authenticated_user,
)
from app_backend.services.service_09_user_authentication.logic.authentication_refresh import (
    refresh_authentication,
)


app = FastAPI(
    title="UETransportERP Authentication API",
    description="REST API for Urban Express ERP authentication.",
    version="1.0.0",
)

bearer_scheme = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    organization_name: str
    user_principal_name: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


AUTHENTICATION_ERROR_STATUS = {
    "invalid_access_token": 401,
    "expired_access_token": 401,
    "invalid_signature": 401,
    "invalid_token_type": 401,
    "missing_token_claims": 401,
    "missing_session": 401,
    "revoked_session": 401,
    "invalid_refresh_token": 401,
    "expired_refresh_token": 401,
    "inactive_user": 403,
    "deleted_user": 403,
    "organization_mismatch": 403,
    "session_user_mismatch": 403,
    "missing_user": 403,
    "missing_refresh_token": 400,
    "authentication_configuration_error": 500,
    "authentication_development_error": 500,
    "authentication_validation_error": 500,
    "authentication_refresh_error": 500,
    "authentication_logout_error": 500,
}


def success_response(result: Any) -> dict[str, Any]:
    return {"success": True, "data": result}


def _safe_error_message(result: dict[str, Any], fallback: str) -> str:
    error_message = result.get("error")
    if isinstance(error_message, str) and error_message:
        return error_message

    return fallback


def _raise_authentication_failure(
    result: dict[str, Any],
    fallback: str = "Authentication request failed."
) -> None:
    error_code = result.get("error_code")
    status_code = AUTHENTICATION_ERROR_STATUS.get(error_code)

    if status_code is None:
        error_message = result.get("error")
        if error_message == "Invalid organization or credentials.":
            status_code = 401
        elif isinstance(error_message, str) and error_message.endswith(" is required."):
            status_code = 400
        else:
            status_code = 500

    raise HTTPException(
        status_code=status_code,
        detail=_safe_error_message(result, fallback)
    )


def _handle_authentication_result(
    result: dict[str, Any],
    fallback: str = "Authentication request failed."
) -> dict[str, Any]:
    if result.get("authenticated") is True or result.get("success") is True:
        return success_response(result)

    _raise_authentication_failure(result, fallback)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    error_message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "error": error_message},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"success": False, "error": str(exc)},
    )


@app.get("/health", tags=["Health"])
def health_check() -> dict[str, Any]:
    return success_response({"status": "healthy"})


@app.post(
    "/api/v1/auth/login",
    tags=["Authentication"],
    summary="Authenticate ERP user",
    description="Authenticates a LOCAL ERP user by organization, username, and password.",
)
def login_endpoint(payload: LoginRequest) -> dict[str, Any]:
    result = authenticate_user(payload.model_dump(exclude_none=False))
    return _handle_authentication_result(
        result,
        "Invalid organization or credentials."
    )


@app.get(
    "/api/v1/auth/me",
    tags=["Authentication"],
    summary="Get current authenticated user",
    description="Validates the bearer access token and returns current user, organization, role, license, and session context.",
)
def get_authenticated_user_endpoint(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> dict[str, Any]:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Bearer access token is required."
        )

    result = get_authenticated_user(credentials.credentials)
    return _handle_authentication_result(
        result,
        "Access token is invalid."
    )


@app.post(
    "/api/v1/auth/refresh",
    tags=["Authentication"],
    summary="Refresh authentication tokens",
    description="Rotates a refresh token and returns a new access token and refresh token.",
)
def refresh_authentication_endpoint(payload: RefreshRequest) -> dict[str, Any]:
    result = refresh_authentication(payload.model_dump(exclude_none=False))
    return _handle_authentication_result(
        result,
        "Refresh token is invalid."
    )


@app.post(
    "/api/v1/auth/logout",
    tags=["Authentication"],
    summary="Logout ERP user",
    description="Revokes the server-side authentication session associated with the refresh token.",
)
def logout_user_endpoint(payload: LogoutRequest) -> dict[str, Any]:
    result = logout_user(payload.model_dump(exclude_none=False))
    return _handle_authentication_result(
        result,
        "Logout request failed."
    )


if __name__ == "__main__":
    uvicorn.run(
        "app_backend.services.service_09_user_authentication.api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
