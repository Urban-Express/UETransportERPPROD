import logging
from datetime import datetime, timedelta, timezone

from firebase_admin import storage

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.firebase_file_pointer_helpers import safe_file_name
from app_backend.services.service_02_hr_payroll.integrations.firebase_employee_image_upload import (
    FIREBASE_STORAGE_BUCKET,
    initialize_firebase_app,
)
from app_backend.services.service_02_hr_payroll.logic.penalty_master_common import (
    PenaltyBusinessValidationError,
    get_penalty_row,
    penalty_identity,
)


logger = logging.getLogger(__name__)
SIGNED_URL_EXPIRES_IN_SECONDS = 15 * 60


def download_penalty_attachment(payload):
    engine = None
    try:
        params = penalty_identity(payload)
        engine = db_engine()
        with engine.begin() as conn:
            penalty = get_penalty_row(conn, params)
        pointer = penalty["penalty_attachment_path"]
        if not pointer:
            raise PenaltyBusinessValidationError("penalty_attachment_path is empty for this Penalty.")
        prefix = f"gs://{FIREBASE_STORAGE_BUCKET}/"
        blob_path = pointer[len(prefix):] if pointer.startswith(prefix) else pointer
        initialize_firebase_app()
        blob = storage.bucket().blob(blob_path)
        if not blob.exists():
            raise PenaltyBusinessValidationError("File not found in Firebase Storage.")
        blob.reload()
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=SIGNED_URL_EXPIRES_IN_SECONDS)
        return {
            "message": "Successfully generated Penalty attachment download URL.",
            "penalty_id_pk": params["penalty_id"],
            "penalty_attachment_path": pointer,
            "file_name": safe_file_name(blob_path),
            "content_type": blob.content_type or "application/octet-stream",
            "download_url": blob.generate_signed_url(version="v4", expiration=expires_at, method="GET"),
            "expires_at": expires_at.isoformat(),
            "expires_in_seconds": SIGNED_URL_EXPIRES_IN_SECONDS,
        }
    except PenaltyBusinessValidationError as exc:
        return {"error": str(exc)}
    except Exception:
        logger.exception("Failed to download Penalty attachment")
        return {"error": "Failed to download Penalty attachment."}
    finally:
        if engine is not None:
            engine.dispose()
