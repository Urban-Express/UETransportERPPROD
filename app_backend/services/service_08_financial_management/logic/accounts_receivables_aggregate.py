"""AR-specific proposed aggregate and approved persistence; reuse the workflow engine."""
import hashlib
import json
import logging
from decimal import Decimal

from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_07_alerts_wf_engine.accounts_receivables_wf import is_accounts_receivables_workflow_approved
from app_backend.services.service_07_alerts_wf_engine.workflow_adapter_helpers import (
    is_pending_workflow_submission, pending_workflow_submission_response,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_document_cleanup import cleanup_staged_workflow_document
from app_backend.services.service_07_alerts_wf_engine.workflow_security import is_trusted_workflow_execution
from app_backend.services.firebase_file_pointer_helpers import has_trusted_workflow_document_path, WORKFLOW_DOCUMENT_METADATA_FIELDS
from app_backend.services.service_08_financial_management.logic.accounts_receivables_common import get_ar_params, validate_ar_payload
from app_backend.services.service_08_financial_management.logic.accounts_receivables_calculations import (
    ARValidationError, ar_json_safe, calculate_line, calculate_totals, date_value, decimal_value,
)
from app_backend.services.service_08_financial_management.logic.accounts_receivables_configuration import (
    ar_connection, customer_identity, invoice_identity, resolve_tax,
)
from app_backend.services.service_08_financial_management.logic.accounts_receivables_contract_prefill import load_contract, contract_line


LINE_INPUT_FIELDS = {
    "ar_line_id_pk", "ar_line_number", "ar_line_source_contract_id_fk", "ar_line_description", "ar_line_vehicle_description",
    "ar_line_quantity", "ar_line_uom", "ar_line_unit_rate", "ar_line_service_period_start", "ar_line_service_period_end",
    "ar_line_proration_method", "ar_line_tax_code", "ar_line_tax_rate", "ar_line_notes",
}
TAX_FIELDS = ("ar_line_tax_config_id_fk", "ar_line_tax_code", "ar_line_tax_treatment", "ar_line_tax_rate")
CONTRACT_PRICE_FIELDS = ("ar_line_quantity", "ar_line_unit_rate", "ar_line_uom", "ar_line_service_period_start", "ar_line_service_period_end", "ar_line_proration_method")
LINE_FIELDS = tuple(sorted((LINE_INPUT_FIELDS - {"ar_line_id_pk"}) | set(TAX_FIELDS) | {
    "ar_line_source_type", "ar_line_proration_numerator", "ar_line_proration_denominator", "ar_line_base_amount",
    "ar_line_net_amount", "ar_line_tax_amount", "ar_line_total_amount",
}))
HEADER_FIELDS = tuple(get_ar_params({})) + (
    "ar_contract_id_fk", "ar_contract_number_snapshot", "ar_contract_name_snapshot", "ar_billing_period_start", "ar_billing_period_end",
    "ar_subtotal_amount", "ar_invoice_identity_snapshot",
)
SERVER_FIELDS = {"ar_subtotal_amount", "ar_invoice_identity_snapshot", "ar_revision", "ar_base_revision", "ar_base_updated_at",
                 "ar_line_schema_version", "ar_contract_number_snapshot", "ar_contract_name_snapshot"}
logger = logging.getLogger(__name__)


def read_header(conn, ar_id, org_id, *, lock=False):
    row = conn.execute(text("SELECT * FROM accounts_receivables WHERE ar_id_pk=:id AND ar_org_id_fk=:org" + (" FOR UPDATE" if lock else "")),
                       {"id": ar_id, "org": org_id}).mappings().one_or_none()
    if not row:
        raise ARValidationError("AR invoice ID not found.")
    return dict(row)


def read_lines(conn, ar_id):
    return [dict(row) for row in conn.execute(text("SELECT * FROM accounts_receivable_lines WHERE ar_line_ar_id_fk=:id ORDER BY ar_line_number"), {"id": ar_id}).mappings()]


def _validate_header(payload):
    for field in ("ar_org_id_fk", "ar_cust_id_fk", "ar_invoice_number", "ar_currency_code", "user_principal_name"):
        if not payload.get(field):
            raise ARValidationError(f"{field} is required.")
    if payload.get("authenticated_org_id") and payload["authenticated_org_id"] != payload["ar_org_id_fk"]:
        raise ARValidationError("Organization does not match authenticated organization.")
    day = date_value(payload.get("ar_invoice_date"), "ar_invoice_date", required=True)
    due = date_value(payload.get("ar_due_date"), "ar_due_date")
    if due and due < day:
        raise ARValidationError("Invalid due date: cannot precede invoice date.")
    start = date_value(payload.get("ar_billing_period_start"), "ar_billing_period_start")
    end = date_value(payload.get("ar_billing_period_end"), "ar_billing_period_end")
    if bool(start) != bool(end) or (start and end < start):
        raise ARValidationError("Invalid billing period: supply both dates in chronological order.")
    payload.update(ar_invoice_date=day, ar_due_date=due, ar_billing_period_start=start, ar_billing_period_end=end,
                   ar_received_amount=decimal_value(payload.get("ar_received_amount") or 0, "ar_received_amount"))
    return payload


def _contract_price_inputs(raw, suggested):
    for key in CONTRACT_PRICE_FIELDS:
        if raw.get(key) is not None:
            value, expected = raw[key], suggested[key]
            if key in ("ar_line_quantity", "ar_line_unit_rate"):
                value = decimal_value(value, key, scale=4 if key == "ar_line_quantity" else 2)
                expected = decimal_value(expected, key, scale=4 if key == "ar_line_quantity" else 2)
            elif "period_" in key:
                value = date_value(value, key)
                expected = date_value(expected, key)
            if value != expected:
                raise ARValidationError(f"Invalid {key}: contract-derived pricing/service dates must match the validated prefill. Use manual lines for separate charges.")


def prepare_invoice(payload, conn, existing=None):
    trusted = is_trusted_workflow_execution()
    payload = dict(payload)
    if not trusted:
        # Shared API models retain these fields for legacy headers. In line mode,
        # neither proposed approval metadata nor persistence may trust the requester.
        for key in ("ar_approval_status", "ar_approved_by", "ar_approved_at"):
            payload.pop(key, None)
        forbidden = SERVER_FIELDS | WORKFLOW_DOCUMENT_METADATA_FIELDS | {"ar_invoice_file_path", "file_path", "ar_invoice_local_file_path", "storage_folder"}
        if any(payload.get(key) is not None for key in forbidden):
            raise ARValidationError("Invalid server-owned invoice snapshot, document pointer or workflow metadata input.")
    proposal = _validate_header(dict(payload))
    raw_lines = proposal.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        raise ARValidationError("lines must be a nonempty array when explicitly supplied; null/empty is not legacy mode.")
    org, customer, contract_id = proposal["ar_org_id_fk"], proposal["ar_cust_id_fk"], proposal.get("ar_contract_id_fk")
    customer_identity(conn, org, customer)
    old_lines = {line["ar_line_id_pk"]: line for line in read_lines(conn, existing["ar_id_pk"])} if existing else {}
    contract = load_contract(conn, org, customer, contract_id, proposal["ar_currency_code"]) if contract_id else None
    unchanged_contract_period = existing and contract_id == existing.get("ar_contract_id_fk") and all(
        proposal.get(key) == existing.get(key) for key in ("ar_billing_period_start", "ar_billing_period_end"))
    numbers, ids, lines = set(), set(), []
    derived_count = 0
    for index, raw in enumerate(raw_lines, 1):
        if not isinstance(raw, dict):
            raise ARValidationError("Invalid line object.")
        if not trusted and set(raw) - LINE_INPUT_FIELDS:
            raise ARValidationError("Invalid line fields: calculated values, source type and audit/organization fields are server-owned.")
        line_id = raw.get("ar_line_id_pk")
        if line_id is not None and (line_id not in old_lines or line_id in ids):
            raise ARValidationError("Invalid ar_line_id_pk: duplicate or not owned by this invoice.", "AR_LINE_OWNERSHIP_INVALID")
        if line_id is not None:
            ids.add(line_id)
        old = old_lines.get(line_id, {})
        number = raw.get("ar_line_number") if raw.get("ar_line_number") is not None else index
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0 or number in numbers:
            raise ARValidationError("Invalid or duplicate ar_line_number; positive unique integers are required.")
        numbers.add(number)
        description = raw.get("ar_line_description")
        if not isinstance(description, str) or not description.strip():
            raise ARValidationError("ar_line_description is required.")
        source_contract = raw.get("ar_line_source_contract_id_fk", old.get("ar_line_source_contract_id_fk"))
        line = {key: raw.get(key) for key in LINE_INPUT_FIELDS}
        line.update(ar_line_number=number, ar_line_id_pk=line_id, ar_line_source_contract_id_fk=source_contract,
                    ar_line_source_type="CONTRACT_AUTOFILL" if source_contract else "MANUAL")
        if source_contract:
            derived_count += 1
            if source_contract != contract_id or not contract:
                raise ARValidationError("Invalid line source contract: must match the validated header contract.")
            if trusted:
                # Approval checks snapshot calculations, not subsequently amended contract prices.
                suggested = raw
                service_start = date_value(raw.get("ar_line_service_period_start"), "service start", required=True)
                service_end = date_value(raw.get("ar_line_service_period_end"), "service end", required=True)
                if service_start < date_value(contract["cont_start_date"], "contract start") or (contract.get("cont_end_date") and service_end > date_value(contract["cont_end_date"], "contract end")):
                    raise ARValidationError("Invalid service dates: approved proposal is outside the current contract term.")
            elif unchanged_contract_period and old.get("ar_line_source_contract_id_fk") == contract_id:
                suggested = old
            else:
                suggested = contract_line(contract, proposal.get("ar_billing_period_start"), proposal.get("ar_billing_period_end"))
            _contract_price_inputs(raw, suggested)
            line.update({key: suggested[key] for key in CONTRACT_PRICE_FIELDS})
        if trusted:
            line.update({key: raw[key] for key in TAX_FIELDS})
            line.update({key: raw.get(key) for key in ("ar_line_base_amount", "ar_line_net_amount", "ar_line_tax_amount", "ar_line_total_amount",
                                                      "ar_line_proration_numerator", "ar_line_proration_denominator")})
        elif old and (not raw.get("ar_line_tax_code") or raw["ar_line_tax_code"] == old["ar_line_tax_code"]) and (
            raw.get("ar_line_tax_rate") is None or decimal_value(raw["ar_line_tax_rate"], "ar_line_tax_rate", scale=4, precision=9) == old["ar_line_tax_rate"]
        ):
            line.update({key: old[key] for key in TAX_FIELDS})
        else:
            line.update(resolve_tax(conn, org, proposal["ar_invoice_date"], raw.get("ar_line_tax_code") or old.get("ar_line_tax_code")))
        if raw.get("ar_line_tax_rate") is not None and decimal_value(raw["ar_line_tax_rate"], "ar_line_tax_rate", scale=4, precision=9) != Decimal(str(line["ar_line_tax_rate"])):
            raise ARValidationError("Invalid ar_line_tax_rate: it must match the selected configuration/snapshot.")
        lines.append(calculate_line(line))
    if derived_count != (1 if contract else 0):
        raise ARValidationError("A selected contract requires exactly one contract-derived line; other lines must be manual.")
    totals = calculate_totals(lines)
    for key, amount in totals.items():
        if payload.get(key) is not None and decimal_value(payload[key], key) != amount:
            raise ARValidationError(f"Invalid {key}: does not match backend line totals.", "AR_CALCULATED_VALUE_MISMATCH")
    proposal.update(totals)
    proposal["lines"] = lines
    if contract:
        same_contract = existing and contract_id == existing.get("ar_contract_id_fk")
        for key, source in (("ar_contract_number_snapshot", "cont_contract_number"), ("ar_contract_name_snapshot", "cont_contract_name")):
            proposal[key] = payload.get(key) if trusted else existing.get(key) if same_contract else contract.get(source)
        proposal["ar_contract"] = proposal["ar_contract_number_snapshot"]
    else:
        proposal["ar_contract_number_snapshot"] = proposal["ar_contract_name_snapshot"] = None
    if not trusted:
        proposal["ar_invoice_identity_snapshot"] = invoice_identity(conn, org, customer, existing.get("ar_invoice_identity_snapshot") if existing else None)
        proposal["ar_line_schema_version"] = 1
        proposal["ar_base_revision"] = existing["ar_revision"] if existing else None
        proposal["ar_base_updated_at"] = str(existing["updated_at"]) if existing else None
        if existing and payload.get("ar_expected_revision") is not None and payload["ar_expected_revision"] != existing["ar_revision"]:
            raise ARValidationError("Invalid invoice revision: reload the current invoice before updating.", "AR_STALE_INVOICE")
    error = validate_ar_payload(proposal)
    if error:
        raise ARValidationError(error["error"])
    return proposal


def _write_invoice(conn, proposal, existing):
    """Identifiers below are fixed backend field lists, never request-provided SQL."""
    params = {key: proposal.get(key) for key in HEADER_FIELDS}
    params.update(get_ar_params(proposal))
    params.update(ar_approval_status="APPROVED", ar_approved_by=proposal["ar_approved_by"],
                  created_by=proposal.get("created_by") or proposal["user_principal_name"], updated_by=proposal["user_principal_name"])
    params["ar_invoice_identity_snapshot"] = json.dumps(proposal["ar_invoice_identity_snapshot"], default=str)
    expressions = {key: f":{key}" for key in HEADER_FIELDS}
    expressions["ar_invoice_identity_snapshot"] = "CAST(:ar_invoice_identity_snapshot AS jsonb)"
    # Also protects already-pending proposals submitted before this correction.
    expressions["ar_approved_at"] = "CURRENT_TIMESTAMP"
    if existing:
        params["id"] = existing["ar_id_pk"]
        setters = ",".join(f"{key}={expressions[key]}" for key in HEADER_FIELDS if key != "created_by")
        conn.execute(text(f"UPDATE accounts_receivables SET {setters},ar_revision=ar_revision+1 WHERE ar_id_pk=:id AND ar_org_id_fk=:ar_org_id_fk"), params)
        ar_id = existing["ar_id_pk"]
    else:
        ar_id = conn.execute(text(f"INSERT INTO accounts_receivables ({','.join(HEADER_FIELDS)},ar_revision) VALUES ({','.join(expressions.values())},1) RETURNING ar_id_pk"), params).scalar_one()
    conn.execute(text("SET CONSTRAINTS uq_ar_line_number DEFERRED"))
    keep_ids = [line["ar_line_id_pk"] for line in proposal["lines"] if line.get("ar_line_id_pk")]
    conn.execute(text("DELETE FROM accounts_receivable_lines WHERE ar_line_ar_id_fk=:ar AND NOT (ar_line_id_pk=ANY(CAST(:ids AS bigint[])))"), {"ar": ar_id, "ids": keep_ids})
    for line in proposal["lines"]:
        values = {key: line.get(key) for key in LINE_FIELDS}
        values.update(ar=ar_id, actor=proposal["user_principal_name"], id=line.get("ar_line_id_pk"))
        if line.get("ar_line_id_pk"):
            setters = ",".join(f"{key}=:{key}" for key in LINE_FIELDS)
            result = conn.execute(text(f"UPDATE accounts_receivable_lines SET {setters},updated_by=:actor WHERE ar_line_id_pk=:id AND ar_line_ar_id_fk=:ar"), values)
            if result.rowcount != 1:
                raise ARValidationError("Invalid line ownership at execution time.")
        else:
            conn.execute(text(f"INSERT INTO accounts_receivable_lines ({','.join(LINE_FIELDS)},ar_line_ar_id_fk,created_by,updated_by) VALUES ({','.join(':'+key for key in LINE_FIELDS)},:ar,:actor,:actor)"), values)
    conn.execute(text("SET CONSTRAINTS uq_ar_line_number IMMEDIATE"))
    return read_header(conn, ar_id, proposal["ar_org_id_fk"])


def _execute_invoice(payload, conn, action):
    # Roll back ALL aggregate writes even when returning an error to the outer workflow transaction.
    with conn.begin_nested():
        # Preserve case-insensitive invoice uniqueness when different pending proposals
        # are approved concurrently. This lock does not allocate/change invoice numbers.
        conn.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(CAST(:org AS text)||':'||lower(:number), 0))"),
                     {"org": payload["ar_org_id_fk"], "number": payload["ar_invoice_number"]})
        existing = read_header(conn, payload.get("ar_id") or payload.get("ar_id_pk"), payload["ar_org_id_fk"], lock=True) if action == "UPDATE" else None
        duplicate = conn.execute(text("SELECT ar_id_pk FROM accounts_receivables WHERE ar_org_id_fk=:org AND lower(ar_invoice_number)=lower(:number) AND (:id IS NULL OR ar_id_pk<>:id)"),
                                 {"org": payload["ar_org_id_fk"], "number": payload["ar_invoice_number"], "id": existing["ar_id_pk"] if existing else None}).first()
        if duplicate:
            raise ARValidationError("AR invoice number already exists for this organization.")
        if existing and (existing["ar_revision"] != payload.get("ar_base_revision") or str(existing["updated_at"]) != payload.get("ar_base_updated_at")):
            raise ARValidationError("Invalid stale invoice proposal: the current invoice changed after submission.", "AR_STALE_INVOICE")
        if payload.get("ar_line_schema_version") != 1 or not payload.get("ar_invoice_identity_snapshot") or not has_trusted_workflow_document_path(payload, "ar_invoice_file_path", trusted_workflow_execution=True) or not payload.get("workflow_document_attachment"):
            raise ARValidationError("Invalid approved line proposal: generated document and identity metadata are required.")
        if not payload.get("ar_approved_by"):
            raise ARValidationError("Invalid approved line proposal: final workflow approver is required.")
        proposal = prepare_invoice(payload, conn, existing)
        row = _write_invoice(conn, proposal, existing)
    return {"message": f"Successfully {action.lower()}d AR invoice: {row['ar_invoice_number']}", "ar_id_pk": row["ar_id_pk"],
            "ar_invoice_number": row["ar_invoice_number"], "ar_balance_amount": row["ar_balance_amount"], "ar_revision": row["ar_revision"],
            "ar_invoice_file_path": row["ar_invoice_file_path"], "ar_approval_status": "APPROVED", "business_operation_executed": True,
            "workflow_required": True, "legacy_header_only": False, "line_count": len(proposal["lines"]), **calculate_totals(proposal["lines"])}


def process_line_invoice(payload, action, file_stream=None, conn=None):
    staged = None
    pending = False
    trusted = is_trusted_workflow_execution()
    try:
        if file_stream is not None:
            raise ARValidationError("Invalid document combination: line invoices use the generated canonical PDF. Submit without an external file; supporting attachments require a separately approved model.", "AR_EXTERNAL_DOCUMENT_CONFLICT")
        if trusted:
            if conn is not None:
                return ar_json_safe(_execute_invoice(payload, conn, action))
            engine = db_engine()
            try:
                with engine.begin() as connection:
                    return ar_json_safe(_execute_invoice(payload, connection, action))
            finally:
                engine.dispose()
        with ar_connection(conn) as connection:
            existing = read_header(connection, payload.get("ar_id") or payload.get("ar_id_pk"), payload.get("ar_org_id_fk")) if action == "UPDATE" else None
            proposal = prepare_invoice(payload, connection, existing)
            duplicate = connection.execute(text("SELECT ar_id_pk FROM accounts_receivables WHERE ar_org_id_fk=:org AND lower(ar_invoice_number)=lower(:number) AND (:id IS NULL OR ar_id_pk<>:id)"),
                                           {"org": proposal["ar_org_id_fk"], "number": proposal["ar_invoice_number"], "id": existing["ar_id_pk"] if existing else None}).first()
            if duplicate:
                raise ARValidationError("AR invoice number already exists for this organization.")
        from app_backend.services.service_08_financial_management.integrations.accounts_receivables_invoice_document_generator import generate_ar_invoice_document
        from app_backend.services.service_08_financial_management.logic.accounts_receivables_create_data import stage_ar_invoice_document_for_workflow
        # Stable business identity is calculated before versioned document metadata is added.
        if not proposal.get("idempotency_key"):
            proposal["idempotency_key"] = "ar-lines-" + hashlib.sha256(json.dumps(proposal, sort_keys=True, default=str).encode()).hexdigest()
        with generate_ar_invoice_document(proposal) as stream:
            staged, error = stage_ar_invoice_document_for_workflow(proposal, stream, "invoice.pdf", "application/pdf", action == "UPDATE", action)
        if error:
            return error
        approved, workflow = is_accounts_receivables_workflow_approved(staged, action)
        if not is_pending_workflow_submission(workflow):
            cleanup_staged_workflow_document(staged, force=True)
            staged = None
            return {"error": "AR invoice submission blocked by workflow.", "workflow": workflow}
        pending = True
        if workflow.get("idempotent_replay"):
            # The workflow kept its original proposal; do not leak the newly rendered orphan.
            with ar_connection(conn) as connection:
                original = connection.execute(text("SELECT request_payload FROM workflow_instances WHERE workflow_instance_id_pk=:id AND organization_id_fk=:org AND workflow_code='ACCOUNTS_RECEIVABLE'"),
                                              {"id": workflow["workflow_instance_id"], "org": staged["ar_org_id_fk"]}).scalar_one()
            if original.get("ar_invoice_file_path") != staged.get("ar_invoice_file_path"):
                cleanup_staged_workflow_document(staged, force=True)
            proposal = original
        response = pending_workflow_submission_response(workflow, f"Accounts receivable {action.lower()} submitted for approval.", payload.get("ar_id") or payload.get("ar_id_pk"))
        return ar_json_safe({**response, "legacy_header_only": False, "line_count": len(proposal["lines"]),
                **{key: proposal[key] for key in ("ar_subtotal_amount", "ar_tax_amount", "ar_invoice_amount")}})
    except ARValidationError as exc:
        result = exc.response()
    except Exception:
        logger.exception("AR line invoice processing failed (action=%s).", action)
        result = {"error": "Failed to process AR line invoice; the aggregate was not committed.", "error_code": "AR_PROCESSING_FAILED"}
    if staged and not pending:
        result["document_cleanup"] = cleanup_staged_workflow_document(staged, force=True)
    elif trusted and payload.get("workflow_document_staged_blob_path"):
        # Never delete a pointer already associated with an issued invoice, including retries.
        try:
            with ar_connection(conn) as connection:
                associated = connection.execute(text("SELECT 1 FROM accounts_receivables WHERE ar_invoice_file_path=:path LIMIT 1"),
                                                {"path": payload.get("ar_invoice_file_path")}).first()
            if not associated:
                result["document_cleanup"] = cleanup_staged_workflow_document(payload, force=True)
        except Exception:
            result["document_cleanup"] = {"cleanup_attempted": False, "reason": "Pointer association could not be verified."}
    return result
