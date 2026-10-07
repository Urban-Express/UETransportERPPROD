"""Generation-bound, short-lived downloads for authoritative staged documents."""
from datetime import datetime, timedelta, timezone
import logging
from urllib.parse import quote

from fastapi import HTTPException
from firebase_admin import storage
from google.api_core.exceptions import NotFound, PreconditionFailed

from app_backend.services.service_07_alerts_wf_engine.workflow_document_cleanup import (
    FIREBASE_STORAGE_BUCKET,
    initialize_firebase_app,
)
from app_backend.services.service_07_alerts_wf_engine.logic.workflow_attachment_logic import (
    PUBLIC_ATTACHMENT_FIELDS,
    document_error,
    resolve_attachment,
)

logger = logging.getLogger(__name__)
SIGNED_URL_EXPIRES_IN_SECONDS = 300


def download_workflow_document(instance, attachment_id, version_id):
    metadata, path, bucket_name = resolve_attachment(
        instance, attachment_id, version_id, FIREBASE_STORAGE_BUCKET,
    )
    try:
        initialize_firebase_app()
        bucket = storage.bucket(bucket_name)
        generation = int(metadata["storage_generation"])
        blob = bucket.blob(path, generation=generation)
        blob.reload(if_generation_match=generation)
        expected = {
            "workflow_attachment_id": attachment_id,
            "workflow_version_id": version_id,
            "workflow_document_role": metadata["document_role"],
            "workflow_organization_id": str(instance["organization_id"]),
            "workflow_sha256": metadata["sha256"],
        }
        if (str(blob.generation) != str(generation)
                or blob.size != metadata["size_bytes"]
                or blob.content_type != metadata["content_type"]
                or any((blob.metadata or {}).get(key) != value for key, value in expected.items())):
            document_error("WORKFLOW_DOCUMENT_INTEGRITY_FAILED")
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=SIGNED_URL_EXPIRES_IN_SECONDS)
        download_url = blob.generate_signed_url(
            version="v4", expiration=timedelta(seconds=SIGNED_URL_EXPIRES_IN_SECONDS),
            method="GET", generation=generation,
            response_disposition=f"attachment; filename*=UTF-8''{quote(metadata['file_name'], safe='')}",
        )
        return {
            "workflow_instance_id": instance["workflow_instance_id"],
            **{key: metadata[key] for key in PUBLIC_ATTACHMENT_FIELDS},
            "document_source": "PROPOSED",
            "download_url": download_url,
            "expires_at": expires_at.isoformat(),
            "expires_in_seconds": SIGNED_URL_EXPIRES_IN_SECONDS,
        }
    except HTTPException:
        raise
    except NotFound:
        document_error("WORKFLOW_DOCUMENT_UNAVAILABLE", 410)
    except PreconditionFailed:
        document_error("WORKFLOW_DOCUMENT_INTEGRITY_FAILED")
    except Exception as exc:
        # Storage exceptions can contain object paths and credential details.
        logger.warning("Workflow document download failed (%s), instance=%s",
                       type(exc).__name__, instance["workflow_instance_id"])
        document_error("WORKFLOW_DOCUMENT_STORAGE_UNAVAILABLE", 503)
