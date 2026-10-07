import hashlib
import logging
import os
from datetime import datetime, timezone
from uuid import uuid4


logger = logging.getLogger(__name__)


WORKFLOW_STAGED_DOCUMENT_CLEANUP_POLICY = (
    "DELETE_STAGED_DOCUMENT_IF_WORKFLOW_REJECTED_OR_CANCELLED"
)
WORKFLOW_DOCUMENT_METADATA_FIELDS = {
    "workflow_document_staging_status",
    "workflow_document_cleanup_policy",
    "workflow_document_staged_blob_path",
    "workflow_document_staged_bucket",
    "workflow_document_attachment",
}


def upload_staged_document(
    blob, file_stream, file_name, content_type, version_id, document_role, organization_id,
) -> dict:
    """Upload once, retaining server-owned identity and byte integrity metadata."""
    digest = hashlib.sha256()
    size = 0
    file_stream.seek(0)
    while chunk := file_stream.read(1024 * 1024):
        digest.update(chunk)
        size += len(chunk)
    file_stream.seek(0)
    attachment = {
        "attachment_id": str(uuid4()),
        "version_id": version_id,
        "document_role": document_role,
        "file_name": safe_file_name(file_name),
        "content_type": content_type,
        "size_bytes": size,
        "sha256": digest.hexdigest(),
    }
    blob.metadata = {
        "workflow_attachment_id": attachment["attachment_id"],
        "workflow_version_id": version_id,
        "workflow_document_role": document_role,
        "workflow_organization_id": str(organization_id),
        "workflow_sha256": attachment["sha256"],
    }
    blob.upload_from_file(
        file_stream, content_type=content_type, rewind=True, if_generation_match=0,
    )
    uploaded_generation = blob.generation
    try:
        if not uploaded_generation or int(uploaded_generation) <= 0:
            raise ValueError("Staged document generation is unavailable.")
        blob.reload(if_generation_match=int(uploaded_generation))
        if str(blob.generation) != str(uploaded_generation) or int(blob.size) != size:
            raise ValueError("Staged document integrity metadata is unavailable.")
    except Exception:
        # A failed metadata read must not leave a newly uploaded, unassociated file.
        # Never delete by name alone: another generation may now occupy the name.
        if uploaded_generation:
            try:
                blob.delete(if_generation_match=int(uploaded_generation))
            except Exception as exc:
                logger.warning("Staged document cleanup failed (%s)", type(exc).__name__)
        raise
    attachment["storage_generation"] = str(blob.generation)
    return attachment


def safe_storage_path_component(value) -> str:
    safe = "".join(
        character if character.isalnum() or character in ("-", "_", ".") else "_"
        for character in str(value or "")
    ).strip("._")
    return safe or "record"


def generate_upload_version_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{timestamp}_{uuid4().hex}"


def safe_file_name(file_name: str | None) -> str | None:
    if not file_name:
        return None
    return os.path.basename(file_name.replace("\\", "/"))


def build_versioned_blob_path(
    storage_folder: str,
    entity_key,
    file_name: str,
    upload_version_id: str | None = None,
) -> tuple[str, str]:
    version_id = upload_version_id or generate_upload_version_id()
    return (
        "/".join(
            [
                storage_folder.rstrip("/"),
                safe_storage_path_component(entity_key),
                "versions",
                safe_storage_path_component(version_id),
                safe_file_name(file_name),
            ]
        ),
        version_id,
    )


def resolve_storage_folder(
    default_storage_folder: str,
    requested_storage_folder: str | None = None,
    *,
    allow_workflow_staging: bool = False,
) -> str:
    default_folder = _normalize_storage_folder(default_storage_folder)
    requested_folder = _normalize_storage_folder(requested_storage_folder)
    if not requested_folder:
        return default_folder

    staging_prefix = f"{default_folder}/_workflow_staging"
    if allow_workflow_staging and (
        requested_folder == staging_prefix
        or requested_folder.startswith(f"{staging_prefix}/")
    ):
        return requested_folder

    return default_folder


def _normalize_storage_folder(storage_folder: str | None) -> str | None:
    if not storage_folder:
        return None
    normalized = storage_folder.replace("\\", "/").strip("/")
    path_parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in path_parts):
        return None
    return normalized


def delete_firebase_blob_if_exists(bucket, blob_path: str | None) -> dict:
    if not bucket or not blob_path:
        return {
            "cleanup_attempted": False,
            "cleanup_completed": False,
            "firebase_blob_path": blob_path,
        }
    try:
        blob = bucket.blob(blob_path)
        if hasattr(blob, "exists") and not blob.exists():
            return {
                "cleanup_attempted": True,
                "cleanup_completed": True,
                "already_absent": True,
                "firebase_blob_path": blob_path,
            }
        blob.delete()
        return {
            "cleanup_attempted": True,
            "cleanup_completed": True,
            "already_absent": False,
            "firebase_blob_path": blob_path,
        }
    except Exception as exc:
        return {
            "cleanup_attempted": True,
            "cleanup_completed": False,
            "firebase_blob_path": blob_path,
            "error": f"Failed to cleanup uncommitted Firebase upload. Error Message: {str(exc)}",
        }


def has_trusted_workflow_document_path(
    payload: dict,
    durable_field_name: str,
    *,
    staged_in_current_request: bool = False,
    trusted_workflow_execution: bool = False,
) -> bool:
    if not payload or not payload.get(durable_field_name):
        return False
    if staged_in_current_request:
        return True
    return (
        trusted_workflow_execution
        and payload.get("workflow_document_staging_status") == "STAGED"
        and payload.get("workflow_document_cleanup_policy")
        == WORKFLOW_STAGED_DOCUMENT_CLEANUP_POLICY
        and bool(payload.get("workflow_document_staged_blob_path"))
    )


def strip_untrusted_file_pointer_fields(
    payload: dict,
    durable_field_name: str,
) -> dict:
    blocked_fields = {durable_field_name, *WORKFLOW_DOCUMENT_METADATA_FIELDS}
    return {
        key: value
        for key, value in (payload or {}).items()
        if key not in blocked_fields
    }
