"""Offline validation and API contract tests; no database or Firebase access."""
from datetime import date
from decimal import Decimal
import unittest

from fastapi import HTTPException
from app_backend.services.main import app as consolidated_app
from app_backend.services.service_02_hr_payroll.api import main as hr_api
from app_backend.services.service_02_hr_payroll.logic.penalty_master_common import (
    PenaltyBusinessValidationError, penalty_identity, validate_penalty_fields,
)


def penalty_payload(**changes):
    return {
        "penalty_empl_id_fk": 1, "penalty_date": "2026-09-30",
        "penalty_reason": "Repeated late attendance",
        "warning_letter_issued": True, "warning_letter_date": "2026-09-30",
        "warning_letter_accepted": False, "financial_implication": True,
        "penalty_amount": "1000.00", "recovery_split_percentage": "50",
        "authenticated_org_id": 1,
        "authenticated_user_principal_name": "penalty.test@example.invalid",
        **changes,
    }


class PenaltyValidationTests(unittest.TestCase):
    def test_dates_and_decimal_values(self):
        for split in ("0", "33.33", "50", "100"):
            with self.subTest(split=split):
                fields = validate_penalty_fields(penalty_payload(recovery_split_percentage=split))
                self.assertEqual(fields["penalty_date"], date(2026, 9, 30))
                self.assertEqual(fields["penalty_amount"], Decimal("1000.00"))
                self.assertEqual(fields["recovery_split_percentage"], Decimal(split))
                self.assertIs(fields["warning_letter_accepted"], False)

    def test_disabled_branches_clear_dependent_values(self):
        fields = validate_penalty_fields(penalty_payload(
            warning_letter_issued=False, financial_implication=False,
        ))
        for key in ("warning_letter_date", "warning_letter_accepted", "penalty_amount", "recovery_split_percentage"):
            self.assertIsNone(fields[key])

    def test_warning_and_financial_branches_are_independent(self):
        for warning in (True, False):
            for financial in (True, False):
                fields = validate_penalty_fields(penalty_payload(
                    warning_letter_issued=warning, financial_implication=financial,
                ))
                self.assertEqual(fields["warning_letter_date"] is not None, warning)
                self.assertEqual(fields["penalty_amount"] is not None, financial)

    def test_missing_required_fields(self):
        for field in ("penalty_empl_id_fk", "penalty_date", "penalty_reason", "warning_letter_date",
                      "warning_letter_accepted", "penalty_amount", "recovery_split_percentage"):
            with self.subTest(field=field), self.assertRaises(PenaltyBusinessValidationError):
                validate_penalty_fields(penalty_payload(**{field: None}))

    def test_invalid_values(self):
        for field, values in {
            "penalty_empl_id_fk": [0, -1, True, "1.5", "bad"],
            "penalty_date": ["bad", "2026-02-30"],
            "penalty_reason": ["", " \t\n", 123],
            "warning_letter_date": ["bad", "2026-02-30"],
            "warning_letter_issued": [None, 1, "false"],
            "warning_letter_accepted": [None, 0, "true"],
            "financial_implication": [None, 0, "true"],
            "penalty_amount": ["0", "-0.01", "NaN", "Infinity", "1.001", "10000000000000000"],
            "recovery_split_percentage": ["-0.01", "100.01", "NaN", "Infinity", "33.333"],
        }.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(PenaltyBusinessValidationError):
                    validate_penalty_fields(penalty_payload(**{field: value}))

    def test_decimal_storage_boundaries(self):
        for amount in ("0.01", "9999999999999999.99", "1.0000"):
            fields = validate_penalty_fields(penalty_payload(penalty_amount=amount))
            self.assertEqual(fields["penalty_amount"], Decimal(amount))

    def test_untrusted_organization_and_actor_are_not_used(self):
        with self.assertRaisesRegex(PenaltyBusinessValidationError, "authenticated_org_id"):
            penalty_identity({"penalty_org_id_fk": 1, "penalty_id": 1})
        with self.assertRaisesRegex(PenaltyBusinessValidationError, "authenticated_user_principal_name"):
            penalty_identity({"authenticated_org_id": 1, "penalty_id": 1, "created_by": "spoofed"}, require_actor=True)

    def test_both_id_aliases(self):
        for alias in ("penalty_id", "penalty_id_pk"):
            self.assertEqual(penalty_identity({alias: 42, "authenticated_org_id": 1})["penalty_id"], 42)

    def test_update_binding_preserves_omitted_fields_and_explicit_nulls(self):
        context = {"user": {"user_id": 7, "user_principal_name": "trusted@example.invalid"},
                   "organization": {"org_id": 1}}
        payload = hr_api.PenaltyMasterUpdatePayload(penalty_id=1, penalty_amount=None, updated_by="spoofed")
        bound = hr_api.bind_penalty_payload(payload, context, set_updated_by=True)
        self.assertIsNone(bound["penalty_amount"])
        self.assertNotIn("recovery_split_percentage", bound)
        self.assertEqual(bound["updated_by"], "trusted@example.invalid")
        self.assertEqual(bound["authenticated_org_id"], 1)

    def test_binding_rejects_foreign_organization(self):
        context = {"user": {"user_id": 7, "user_principal_name": "trusted@example.invalid"},
                   "organization": {"org_id": 1}}
        with self.assertRaises(HTTPException) as error:
            hr_api.bind_penalty_payload({"penalty_org_id_fk": 2}, context)
        self.assertEqual(error.exception.status_code, 403)

    def test_models_do_not_accept_attachment_pointer_as_business_data(self):
        created = hr_api.PenaltyMasterPayload(**penalty_payload(penalty_attachment_path="forged/path"))
        updated = hr_api.PenaltyMasterUpdatePayload(penalty_id=1, penalty_attachment_path="forged/path")
        for model in (created, updated):
            self.assertNotIn("penalty_attachment_path", model.model_dump())

    def test_routes_and_openapi_are_exposed_once_in_both_apps(self):
        expected = {("GET", "/api/v1/penalties")} | {
            ("POST", f"/api/v1/penalties/{suffix}")
            for suffix in ("get", "create", "update", "delete", "attachments/upload", "attachments/download")
        }
        for app in (hr_api.app, consolidated_app):
            routes = [(method, route.path) for route in app.routes
                      for method in getattr(route, "methods", ()) if route.path.startswith("/api/v1/penalties")]
            self.assertCountEqual(routes, expected)
            schema = app.openapi()
            for method, path in expected:
                self.assertIn(method.lower(), schema["paths"][path])
                self.assertTrue(schema["paths"][path][method.lower()]["security"])
            upload = schema["paths"]["/api/v1/penalties/attachments/upload"]["post"]
            self.assertIn("multipart/form-data", upload["requestBody"]["content"])
            for model in ("PenaltyMasterPayload", "PenaltyMasterUpdatePayload"):
                self.assertNotIn("penalty_attachment_path", schema["components"]["schemas"][model]["properties"])


if __name__ == "__main__":
    unittest.main()
