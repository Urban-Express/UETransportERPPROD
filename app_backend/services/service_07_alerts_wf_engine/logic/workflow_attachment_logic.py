"""Resolve proposed attachments solely from authorized workflow request metadata."""
import re
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import HTTPException

from app_backend.services.firebase_file_pointer_helpers import (
    WORKFLOW_STAGED_DOCUMENT_CLEANUP_POLICY,
    safe_file_name,
)

DOMAIN_DOCUMENTS = {
    "CONTRACTS_MANAGEMENT": ("firebase_upload_files/contracts", "cont_link_path", "CONTRACT_DOCUMENT", "cont_org_id_fk"),
    "ACCOUNTS_PAYABLE": ("firebase_upload_files/ap_invoices", "ap_invoice_file_path", "AP_INVOICE", "ap_org_id_fk"),
    "ACCOUNTS_RECEIVABLE": ("firebase_upload_files/ar_invoices", "ar_invoice_file_path", "AR_INVOICE", "ar_org_id_fk"),
}
PUBLIC_ATTACHMENT_FIELDS = (
    "attachment_id", "version_id", "document_role", "file_name", "content_type",
    "size_bytes", "sha256",
)
PRIVATE_DOCUMENT_FIELDS = {
    "workflow_document_attachment", "workflow_document_staged_blob_path",
    "workflow_document_staged_bucket", "cont_link_path", "ap_invoice_file_path",
    "ar_invoice_file_path", "previous_cont_link_path", "previous_ap_invoice_file_path",
    "previous_ar_invoice_file_path", "firebase_blob_path", "firebase_storage_path",
    "firebase_bucket", "storage_folder", "download_url",
}


def document_error(code, status=409):
    raise HTTPException(status_code=status, detail=code)


def _legacy_attachment(instance, payload, role):
    # Stable discovery only. Old payloads have no upload-time generation/hash proof.
    path = str(payload.get("workflow_document_staged_blob_path") or "")
    version = path.rsplit("/versions/", 1)[-1].split("/")[0] if "/versions/" in path else None
    return {
        "attachment_id": str(uuid5(NAMESPACE_URL, f"ue-workflow:{instance['workflow_instance_id']}:{path}")),
        "version_id": version,
        "document_role": role,
        "file_name": safe_file_name(path),
        "content_type": None, "size_bytes": None, "sha256": None,
        "document_source": "PROPOSED", "availability": "UNVERIFIED_LEGACY",
    }


def attachment_summaries(instance):
    domain = DOMAIN_DOCUMENTS.get(instance.get("workflow_code"))
    if domain is None or instance.get("workflow_action") not in {"CREATE", "UPDATE"}:
        return []
    payload = instance.get("request_payload") or {}
    metadata = payload.get("workflow_document_attachment")
    if not metadata:
        if payload.get("workflow_document_staged_blob_path"):
            return [_legacy_attachment(instance, payload, domain[2])]
        return []
    if not isinstance(metadata, dict):
        return []
    result = {key: metadata.get(key) for key in PUBLIC_ATTACHMENT_FIELDS}
    result["document_source"] = "PROPOSED"
    # Discovery does not contact storage and is not an existence guarantee.
    result["availability"] = ("PENDING_REVIEW" if instance.get("workflow_status") == "PENDING_APPROVAL"
                              else "WORKFLOW_NOT_PENDING")
    return [result]


def public_instance(instance):
    """Preserve business context while removing server-held storage references."""
    payload = instance.get("request_payload") or {}
    references = [payload.get("workflow_document_staged_blob_path"),
                  payload.get("workflow_document_staged_bucket")]
    references += [payload.get(domain[1]) for domain in DOMAIN_DOCUMENTS.values()]
    references = sorted((str(value) for value in references if value), key=len, reverse=True)

    def sanitize(value):
        if isinstance(value, dict):
            return {key: sanitize(item) for key, item in value.items()
                    if key not in PRIVATE_DOCUMENT_FIELDS}
        if isinstance(value, (list, tuple)):
            return [sanitize(item) for item in value]
        if isinstance(value, str):
            for reference in references:
                value = value.replace(reference, "[redacted]")
        return value

    return {**sanitize(instance), "attachments": attachment_summaries(instance)}


def resolve_attachment(instance, attachment_id, version_id, configured_bucket):
    domain = DOMAIN_DOCUMENTS.get(instance.get("workflow_code"))
    if domain is None or instance.get("workflow_action") not in {"CREATE", "UPDATE"}:
        document_error("WORKFLOW_DOCUMENT_UNSUPPORTED", 409)
    if instance.get("workflow_status") != "PENDING_APPROVAL":
        document_error("WORKFLOW_DOCUMENT_NOT_PENDING", 409)
    payload = instance.get("request_payload") or {}
    metadata = payload.get("workflow_document_attachment")
    if not metadata:
        summaries = attachment_summaries(instance)
        if summaries and summaries[0]["attachment_id"] == attachment_id:
            document_error("WORKFLOW_DOCUMENT_LEGACY_UNVERIFIED")
        document_error("WORKFLOW_ATTACHMENT_NOT_FOUND", 404)
    if not isinstance(metadata, dict):
        document_error("WORKFLOW_DOCUMENT_INTEGRITY_FAILED")
    if metadata.get("attachment_id") != attachment_id:
        document_error("WORKFLOW_ATTACHMENT_NOT_FOUND", 404)
    if metadata.get("version_id") != version_id:
        document_error("WORKFLOW_ATTACHMENT_VERSION_NOT_FOUND", 404)

    base, pointer, role, org_field = domain
    organization_id = instance.get("organization_id")
    path = payload.get("workflow_document_staged_blob_path")
    bucket = payload.get("workflow_document_staged_bucket")
    prefix = f"{base}/_workflow_staging/{organization_id}/{instance['workflow_code']}/{instance['workflow_action']}/"
    try:
        UUID(attachment_id)
        generation = str(metadata["storage_generation"])
        size = metadata["size_bytes"]
        if (not configured_bucket or bucket != configured_bucket
                or payload.get("workflow_document_staging_status") != "STAGED"
                or payload.get("workflow_document_cleanup_policy") != WORKFLOW_STAGED_DOCUMENT_CLEANUP_POLICY
                or int(payload[org_field]) != int(organization_id)
                or (payload.get("authenticated_org_id") is not None
                    and int(payload["authenticated_org_id"]) != int(organization_id))
                or not isinstance(path, str) or not path.startswith(prefix)
                or "\\" in path or any(part in {"", ".", ".."} for part in path.split("/"))
                or metadata.get("document_role") != role
                or not re.fullmatch(r"[0-9]+", generation) or int(generation) <= 0
                or type(size) is not int or size < 0
                or not re.fullmatch(r"[a-f0-9]{64}", metadata.get("sha256", ""))
                or not isinstance(metadata.get("content_type"), str) or not metadata["content_type"]
                or any(ord(c) < 32 for c in metadata["content_type"])):
            raise ValueError("Invalid provenance")
        suffix = path[len(prefix):].split("/")
        if (len(suffix) != 5 or suffix[2] != "versions" or suffix[3] != version_id
                or suffix[4] != metadata.get("file_name")
                or safe_file_name(metadata["file_name"]) != metadata["file_name"]
                or payload.get(pointer) not in {path, f"gs://{bucket}/{path}"}):
            raise ValueError("Invalid document version")
    except (KeyError, TypeError, ValueError, AttributeError):
        document_error("WORKFLOW_DOCUMENT_INTEGRITY_FAILED")
    return dict(metadata), path, bucket
