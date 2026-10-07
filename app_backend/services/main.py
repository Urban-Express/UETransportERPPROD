import logging
import os
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app_backend.services.service_01_organization_management.api.main import (
    app as organization_management_app,
)
from app_backend.services.service_02_hr_payroll.api.main import (
    app as hr_payroll_app,
)
from app_backend.services.service_03_fleet_management.api.main import (
    app as fleet_management_app,
)
from app_backend.services.service_04_maintenance_management.api.main import (
    app as maintenance_management_app,
)
from app_backend.services.service_06_contracts_management.api.main import (
    app as contracts_management_app,
)
from app_backend.services.service_07_alerts_wf_engine.api.main import (
    app as alerts_workflow_app,
)
from app_backend.services.service_08_financial_management.api.main import (
    app as financial_management_app,
)
from app_backend.services.service_09_user_authentication.api.main import (
    app as user_authentication_app,
)


logger = logging.getLogger(__name__)
INTERNAL_SERVER_ERROR = "INTERNAL_SERVER_ERROR"

app = FastAPI(
    title="UETransportERP Services API",
    description="Consolidated REST API for all UETransportERP services.",
    version="1.0.0",
)

def get_cors_allowed_origins() -> list[str]:
    raw_origins = (
        os.getenv("CORS_ALLOWED_ORIGINS")
        or os.getenv("ALLOWED_ORIGINS")
        or ""
    )
    return [
        origin.strip()
        for origin in raw_origins.split(",")
        if origin.strip()
    ]


cors_allowed_origins = get_cors_allowed_origins()
cors_allowed_origin_regex = os.getenv("CORS_ALLOW_ORIGIN_REGEX")


def success_response(result: Any) -> dict[str, Any]:
    return {"success": True, "data": result}


class ControlledUnexpectedExceptionMiddleware:
    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        except HTTPException:
            raise
        except RequestValidationError:
            raise
        except Exception:
            logger.exception("Unhandled consolidated API request failed.")
            response = JSONResponse(
                status_code=500,
                content={"success": False, "error": INTERNAL_SERVER_ERROR},
            )
            await response(scope, receive, send)


app.add_middleware(ControlledUnexpectedExceptionMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allowed_origins,
    allow_origin_regex=cors_allowed_origin_regex,
    allow_credentials=(
        bool(cors_allowed_origins or cors_allowed_origin_regex)
        and "*" not in cors_allowed_origins
    ),
    allow_methods=["*"],
    allow_headers=["*"],
)


def include_service_routes(service_app: FastAPI) -> None:
    for route in service_app.routes:
        included_router = getattr(route, "original_router", None)
        if included_router is not None:
            for included_route in included_router.routes:
                include_service_route(included_route)
            continue
        include_service_route(route)


def include_service_route(route: Any) -> None:
    if not isinstance(route, APIRoute):
        return

    # Keep one consolidated health endpoint in this top-level app.
    if route.path == "/health":
        return

    app.router.routes.append(route)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict):
        return JSONResponse(
            status_code=exc.status_code,
            content={"success": False, **exc.detail},
        )
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


# service_01_organization_management API routes
include_service_routes(organization_management_app)

# service_02_hr_payroll API routes
include_service_routes(hr_payroll_app)

# service_03_fleet_management API routes
include_service_routes(fleet_management_app)

# service_04_maintenance_management API routes
include_service_routes(maintenance_management_app)

# service_06_contracts_management API routes
include_service_routes(contracts_management_app)

# service_07_alerts_wf_engine API routes
include_service_routes(alerts_workflow_app)

# service_08_financial_management API routes
include_service_routes(financial_management_app)

# service_09_user_authentication API routes
include_service_routes(user_authentication_app)


if __name__ == "__main__":
    uvicorn.run(
        "app_backend.services.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
