from sqlalchemy import text
import pandas as pd
from app_backend.services.service_08_financial_management.logic.accounts_receivables_calculations import ar_json_safe

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine


def get_accounts_receivables(payload: dict | None = None):
    try:
        payload = payload or {}
        ar_org_id_fk = payload.get("ar_org_id_fk") or payload.get("authenticated_org_id")
        if not ar_org_id_fk:
            return {"error": "ar_org_id_fk is required."}

        params = {
            "ar_id": payload.get("ar_id") or payload.get("ar_id_pk"),
            "ar_org_id_fk": ar_org_id_fk,
            "ar_cust_id_fk": payload.get("ar_cust_id_fk"),
            "ar_approval_status": payload.get("ar_approval_status"),
            "ar_collection_status": payload.get("ar_collection_status"),
            "ar_contract_id_fk": payload.get("ar_contract_id_fk"),
        }

        ar_engine = db_engine()
        try:
            with ar_engine.connect() as conn:
                # Preserve reads during staged rollout even before additive DDL exists.
                line_schema = conn.execute(text("SELECT to_regclass('public.accounts_receivable_lines') IS NOT NULL")).scalar_one()
                projection = "coalesce(c.line_count,0) AS line_count,h.ar_subtotal_amount IS NULL AS legacy_header_only" if line_schema else "0 AS line_count,TRUE AS legacy_header_only"
                join = "LEFT JOIN (SELECT ar_line_ar_id_fk,count(*) AS line_count FROM accounts_receivable_lines GROUP BY ar_line_ar_id_fk) c ON c.ar_line_ar_id_fk=h.ar_id_pk" if line_schema else ""
                contract_filter = "AND (:ar_contract_id_fk IS NULL OR h.ar_contract_id_fk=:ar_contract_id_fk)" if line_schema else "AND :ar_contract_id_fk IS NULL"
                query = text(f"""
                    SELECT h.*,{projection} FROM accounts_receivables h {join}
                    WHERE (:ar_id IS NULL OR h.ar_id_pk=:ar_id) AND h.ar_org_id_fk=:ar_org_id_fk
                    AND (:ar_cust_id_fk IS NULL OR h.ar_cust_id_fk=:ar_cust_id_fk)
                    AND (:ar_approval_status IS NULL OR h.ar_approval_status=:ar_approval_status)
                    AND (:ar_collection_status IS NULL OR h.ar_collection_status=:ar_collection_status)
                    {contract_filter} ORDER BY h.ar_id_pk DESC
                """)
                rows = [dict(row) for row in conn.execute(query, params).mappings()]
                include_lines = bool(params["ar_id"] or payload.get("include_lines"))
                grouped = {}
                if line_schema and rows and include_lines:
                    for line in conn.execute(text("""
                        SELECT l.* FROM accounts_receivable_lines l JOIN accounts_receivables h ON h.ar_id_pk=l.ar_line_ar_id_fk
                        WHERE h.ar_org_id_fk=:org AND l.ar_line_ar_id_fk=ANY(CAST(:ids AS integer[]))
                        ORDER BY l.ar_line_ar_id_fk,l.ar_line_number
                    """), {"org": ar_org_id_fk, "ids": [row["ar_id_pk"] for row in rows]}).mappings():
                        grouped.setdefault(line["ar_line_ar_id_fk"], []).append(ar_json_safe(dict(line)))
                for row in rows:
                    if not row["legacy_header_only"]:
                        # Exact decimal strings for new line invoices; legacy numeric responses remain compatible.
                        for key in ("ar_invoice_amount", "ar_subtotal_amount", "ar_tax_amount", "ar_received_amount", "ar_balance_amount"):
                            row[key] = str(row[key]) if row.get(key) is not None else None
                    else:
                        # Match the old read_sql(coerce_float=True) legacy JSON representation.
                        for key in ("ar_invoice_amount", "ar_tax_amount", "ar_received_amount", "ar_balance_amount"):
                            if row.get(key) is not None:
                                row[key] = float(row[key])
                    if include_lines:
                        row["lines"] = grouped.get(row["ar_id_pk"], [])
                    # Convert before pandas inference: new nullable dates must remain JSON null,
                    # and billing dates must round-trip as ISO dates, never epoch milliseconds.
                    for key in ("ar_invoice_date", "ar_due_date", "ar_approved_at", "created_at", "updated_at",
                                "ar_billing_period_start", "ar_billing_period_end"):
                        if key in row:
                            value = row[key]
                            row[key] = str(value) if value is not None or (row["legacy_header_only"] and key not in ("ar_billing_period_start", "ar_billing_period_end")) else None
                df_ar = pd.DataFrame(rows)
        finally:
            ar_engine.dispose()

        return df_ar, df_ar.to_json(orient="records")
    except Exception:
        return {"error": "Failed to get accounts receivables data.", "error_code": "AR_READ_FAILED"}


def get_accounts_receivable(payload: dict | None = None):
    return get_accounts_receivables(payload)
