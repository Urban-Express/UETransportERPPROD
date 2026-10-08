"""Customer schema compatibility without changing AR snapshots or calculations."""
from copy import deepcopy
from io import BytesIO
import os
from pathlib import Path
import unittest

from pypdf import PdfReader
from app_backend.services.service_08_financial_management.integrations.accounts_receivables_invoice_document_generator import generate_ar_invoice_document
from app_backend.services.service_08_financial_management.logic.accounts_receivables_configuration import customer_identity, invoice_identity
from app_backend.services.service_08_financial_management.logic.accounts_receivables_calculations import ARValidationError
from app_backend.services.service_08_financial_management.tests.ar_postgres_fixture import ARPostgresCase


def pdf_text(content):
    return "\n".join(page.extract_text() for page in PdfReader(BytesIO(content)).pages)


def example_invoice(phones):
    return {
        "ar_invoice_number": "CUSTOMER-COMPAT-TEST", "ar_invoice_date": "2026-10-08",
        "ar_currency_code": "AED", "ar_subtotal_amount": "20.00",
        "ar_tax_amount": "1.00", "ar_invoice_amount": "21.00",
        "ar_invoice_identity_snapshot": {
            "organization": {"org_name": "Invoice test organization"},
            "customer": {"cust_id_pk": 10, "cust_name": "Snapshot customer", **phones},
            "invoice_configuration": {
                "organization_trn": "TEST-TRN", "bank_name": "Test bank",
                "bank_account_name": "Test account", "bank_account_number": "123",
                "iban": "TEST-IBAN", "signatory_name": "Test signatory",
                "signatory_designation": "Manager",
            },
        },
        "lines": [{
            "ar_line_number": 1, "ar_line_description": "Transport service",
            "ar_line_quantity": "2", "ar_line_unit_rate": "10.00",
            "ar_line_net_amount": "20.00", "ar_line_tax_code": "STANDARD_VAT",
            "ar_line_tax_rate": "5", "ar_line_tax_amount": "1.00",
            "ar_line_total_amount": "21.00",
        }],
    }


class ARCustomerSnapshotDocumentTests(unittest.TestCase):
    def render(self, phones):
        invoice = example_invoice(phones)
        before = deepcopy(invoice)
        with generate_ar_invoice_document(invoice) as stream:
            content = stream.read()
        self.assertTrue(content.startswith(b"%PDF-"))
        self.assertEqual(invoice, before, "Rendering must not mutate persisted identity.")
        rendered = pdf_text(content)
        self.assertIn("AED 21.00", rendered)
        self.assertIn("TWENTY ONE DIRHAMS AND ZERO FILS ONLY", rendered)
        return rendered

    def test_historical_snapshot_phones_render_unchanged(self):
        rendered = self.render({"cust_phone_primary": "+971501111111", "cust_phone_secondary": "+971502222222"})
        self.assertIn("Telephone: +971501111111 / +971502222222", rendered)

    def test_revised_snapshot_phones_render_when_historical_keys_are_absent(self):
        rendered = self.render({"procurement_head_phone_primary": "+971503333333", "procurement_head_phone_secondary": "+971504444444"})
        self.assertIn("Telephone: +971503333333 / +971504444444", rendered)

    def test_saved_historical_values_and_explicit_blanks_are_authoritative(self):
        for primary in ("+971501111111", None, ""):
            with self.subTest(primary=primary):
                rendered = self.render({
                    "cust_phone_primary": primary, "cust_phone_secondary": None,
                    "procurement_head_phone_primary": "+971509999999",
                    "procurement_head_phone_secondary": "+971508888888",
                })
                self.assertNotIn("+971509999999", rendered)
                self.assertNotIn("+971508888888", rendered)
                if primary:
                    self.assertIn("Telephone: " + primary, rendered)

    def test_secondary_only_missing_and_null_phone_fields(self):
        for prefix in ("cust", "procurement_head"):
            with self.subTest(prefix=prefix):
                rendered = self.render({f"{prefix}_phone_primary": None, f"{prefix}_phone_secondary": "+971502222222"})
                self.assertIn("Telephone: +971502222222", rendered)
                self.assertNotIn(" / +971502222222", rendered)
                rendered = self.render({f"{prefix}_phone_primary": None, f"{prefix}_phone_secondary": None})
                self.assertIn("Telephone:", rendered)
                self.assertNotIn("None", rendered)
        self.assertIn("Telephone:", self.render({}))


class ARCustomerCompatibilityPostgresTests(ARPostgresCase):
    def set_phone_values(self, primary="+971501234567", secondary="+971507654321", customer=10):
        self.execute("""
            UPDATE customer_master SET procurement_head_phone_primary=:primary,
                procurement_head_phone_secondary=:secondary
            WHERE cust_id_pk=:customer AND cust_org_id_fk=:org
        """, {"primary": primary, "secondary": secondary, "customer": customer, "org": 77})

    def test_customer_identity_reads_revised_columns_and_preserves_ar_contract(self):
        self.set_phone_values()
        with self.engine.connect() as conn:
            identity = customer_identity(conn, 77, 10)
            self.assertEqual(set(identity), {"cust_id_pk", "cust_name", "cust_billing_address",
                                             "cust_phone_primary", "cust_phone_secondary", "cust_tax_registration_number"})
            self.assertEqual(identity["cust_phone_primary"], "+971501234567")
            self.assertEqual(identity["cust_phone_secondary"], "+971507654321")
            with self.assertRaises(ARValidationError):
                customer_identity(conn, 88, 10)
            with self.assertRaises(ARValidationError):
                customer_identity(conn, 77, 999999)
        self.set_phone_values(None, None)
        with self.engine.connect() as conn:
            identity = customer_identity(conn, 77, 10)
        self.assertIsNone(identity["cust_phone_primary"])
        self.assertIsNone(identity["cust_phone_secondary"])

    def test_new_invoice_create_retrieve_and_document_use_procurement_phones(self):
        self.set_phone_values()
        ar_id, pending = self.create()
        detail = self.detail(ar_id)
        customer = detail["ar_invoice_identity_snapshot"]["customer"]
        self.assertEqual(customer["cust_phone_primary"], "+971501234567")
        self.assertEqual(customer["cust_phone_secondary"], "+971507654321")
        self.assertEqual([detail[key] for key in ("ar_subtotal_amount", "ar_tax_amount", "ar_invoice_amount")],
                         ["2032.26", "101.61", "2133.87"])
        self.assertEqual(detail["line_count"], 1)
        proposal = self.proposal(pending)
        content = self.pdf_bytes(proposal)
        self.assertIn("Telephone: +971501234567 / +971507654321", pdf_text(content))
        response = self.client.post("/api/v1/accounts-receivables/documents/download", json={"ar_id_pk": ar_id})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.committed_download_bytes(response.json()["data"]["download_url"]), content)
        if os.getenv("AR_REDESIGN_ARTIFACT_DIR"):
            directory = Path(os.environ["AR_REDESIGN_ARTIFACT_DIR"])
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "procurement_phone_invoice.pdf").write_bytes(content)

    def test_historical_snapshot_and_generated_document_survive_master_phone_change(self):
        self.set_phone_values()
        ar_id, pending = self.create()
        before = self.detail(ar_id)
        old_pdf = self.pdf_bytes(self.proposal(pending))
        self.set_phone_values("+971509999999", "+971508888888")
        self.assertEqual(self.detail(ar_id), before)
        download = self.client.post("/api/v1/accounts-receivables/documents/download", json={"ar_id_pk": ar_id})
        self.assertEqual(download.status_code, 200, download.text)
        self.assertEqual(self.committed_download_bytes(download.json()["data"]["download_url"]), old_pdf)
        payload = self.update_payload(ar_id)
        payload["ar_notes"] = "Edit without adopting current master phone values"
        response = self.submit(payload, "update")
        self.assertEqual(response.status_code, 200, response.text)
        revised_pending = response.json()["data"]
        revised_proposal = self.proposal(revised_pending)
        self.assertEqual(revised_proposal["ar_invoice_identity_snapshot"], before["ar_invoice_identity_snapshot"])
        rendered = pdf_text(self.pdf_bytes(revised_proposal))
        self.assertIn("Telephone: +971501234567 / +971507654321", rendered)
        self.assertNotIn("+971509999999", rendered)
        self.assertNotIn("+971508888888", rendered)
        self.assertEqual(self.approve(revised_pending)["workflow_status"], "EXECUTED")
        after = self.detail(ar_id)
        self.assertEqual(after["ar_invoice_identity_snapshot"], before["ar_invoice_identity_snapshot"])
        self.assertEqual(after["ar_invoice_amount"], before["ar_invoice_amount"])
        if os.getenv("AR_REDESIGN_ARTIFACT_DIR"):
            directory = Path(os.environ["AR_REDESIGN_ARTIFACT_DIR"])
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "historical_phone_invoice.pdf").write_bytes(self.pdf_bytes(revised_proposal))

    def test_customer_change_refreshes_only_customer_identity(self):
        self.set_phone_values()
        with self.engine.connect() as conn:
            original = invoice_identity(conn, 77, 10)
        before = deepcopy(original)
        self.set_phone_values("+971505555555", "+971506666666", customer=12)
        with self.engine.connect() as conn:
            changed = invoice_identity(conn, 77, 12, existing=original)
        self.assertEqual(changed["customer"]["cust_id_pk"], 12)
        self.assertEqual(changed["customer"]["cust_phone_primary"], "+971505555555")
        self.assertEqual(changed["customer"]["cust_phone_secondary"], "+971506666666")
        self.assertEqual(changed["organization"], original["organization"])
        self.assertEqual(changed["invoice_configuration"], original["invoice_configuration"])
        self.assertEqual(original, before)


if __name__ == "__main__":
    unittest.main()
