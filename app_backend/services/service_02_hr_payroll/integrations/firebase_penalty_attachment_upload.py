import logging
import mimetypes

from firebase_admin import storage
from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.firebase_file_pointer_helpers import (
    build_versioned_blob_path,
    delete_firebase_blob_if_exists,
    resolve_storage_folder,
    safe_file_name,
)
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
DEFAULT_PENALTY_ATTACHMENT_STORAGE_FOLDER = "firebase_upload_files/penalty_attachments"


def upload_penalty_attachment(payload, file_stream=None, file_name=None, content_type=None):
    engine = None
    bucket = None
    blob_path = None
    pointer_committed = False
    try:
        params = penalty_identity(payload, require_actor=True)
        file_name = safe_file_name(file_name)
        if file_stream is None:
            raise PenaltyBusinessValidationError("file is required.")
        if not file_name or file_name in (".", ".."):
            raise PenaltyBusinessValidationError("Valid file_name is required.")
        engine = db_engine()
        with engine.begin() as conn:
            # Serialize uploads with updates/deletes and warning-letter changes.
            penalty = get_penalty_row(conn, params, for_update=True)
            if not penalty["warning_letter_issued"]:
                raise PenaltyBusinessValidationError(
                    "Invalid attachment eligibility: warning_letter_issued must be true."
                )
            previous_path = penalty["penalty_attachment_path"]
            storage_folder = resolve_storage_folder(DEFAULT_PENALTY_ATTACHMENT_STORAGE_FOLDER)
            blob_path, version_id = build_versioned_blob_path(
                storage_folder, params["penalty_id"], file_name,
            )
            upload_content_type = (
                content_type or payload.get("content_type")
                or mimetypes.guess_type(file_name)[0] or "application/octet-stream"
            )
            initialize_firebase_app()
            bucket = storage.bucket()
            blob = bucket.blob(blob_path)
            blob.upload_from_file(file_stream, content_type=upload_content_type, rewind=True)
            pointer = conn.execute(text("""
                update penalty_master set penalty_attachment_path = :penalty_attachment_path,
                    updated_by = :actor, updated_at = current_timestamp
                where penalty_id_pk = :penalty_id and penalty_org_id_fk = :authenticated_org_id
                returning penalty_attachment_path
            """), {**params, "penalty_attachment_path": blob_path}).scalar_one_or_none()
            if pointer != blob_path:
                raise RuntimeError("Penalty attachment pointer update affected zero rows.")
        pointer_committed = True
        return {
            "message": "Successfully uploaded Penalty attachment.",
            "penalty_id_pk": params["penalty_id"],
            "penalty_attachment_path": pointer,
            "previous_penalty_attachment_path": previous_path,
            "firebase_blob_path": blob_path,
            "firebase_storage_path": f"gs://{FIREBASE_STORAGE_BUCKET}/{blob_path}",
            "firebase_bucket": FIREBASE_STORAGE_BUCKET,
            "file_name": file_name,
            "content_type": upload_content_type,
            "upload_version_id": version_id,
        }
    except PenaltyBusinessValidationError as exc:
        return {"error": str(exc)}
    except Exception:
        logger.exception("Failed to upload Penalty attachment")
        response = {"error": "Failed to upload Penalty attachment."}
        if bucket is not None and blob_path and not pointer_committed:
            response["firebase_cleanup"] = delete_firebase_blob_if_exists(bucket, blob_path)
        return response
    finally:
        if engine is not None:
            engine.dispose()
