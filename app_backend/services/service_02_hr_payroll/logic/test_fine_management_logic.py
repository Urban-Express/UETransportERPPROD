"""Offline validation/API contract tests. No database or Firebase access."""
from datetime import date
from decimal import Decimal
from pathlib import Path
import unittest

from app_backend.services.service_02_hr_payroll.api import main as hr_api
from app_backend.services.service_02_hr_payroll.logic.fine_master_common import (
    FineBusinessValidationError, fine_identity, validate_fine_fields,
)
from app_backend.services.main import app as consolidated_app


def fine_payload(**changes):
    return {
        "fine_date": "2026-09-29", "fine_on": "DRIVER", "fine_empl_id_fk": 1,
        "fine_accountability": "DRIVER", "payment_authority": "URBAN_EXPRESS",
        "amount_paid": Decimal("1000.00"), "recovery_split": Decimal("50"),
        "authenticated_org_id": 1, "authenticated_user_principal_name": "fine.test@example.invalid",
        **changes,
    }


class FineValidationTests(unittest.TestCase):
    def test_values_are_stored_without_installment_calculation(self):
        for amount, split in (("1000", "50"), ("1000", "30"), ("1000", "100"),
                              ("999.99", "50"), ("0.01", "25")):
            with self.subTest(amount=amount, split=split):
                fields = validate_fine_fields(fine_payload(amount_paid=amount, recovery_split=split))
                self.assertEqual(fields["amount_paid"], Decimal(amount))
                self.assertEqual(fields["recovery_split"], Decimal(split))
                self.assertEqual(fields["fine_date"], date(2026, 9, 29))

    def test_all_original_driver_combinations(self):
        for accountable, payer in (("DRIVER", "DRIVER"), ("URBAN_EXPRESS", "DRIVER"),
                                   ("URBAN_EXPRESS", "URBAN_EXPRESS")):
            with self.subTest(accountable=accountable, payer=payer):
                fields = validate_fine_fields(fine_payload(
                    fine_accountability=accountable, payment_authority=payer,
                    amount_paid=None, recovery_split=None,
                ))
                self.assertIsNone(fields["amount_paid"])
                self.assertIsNone(fields["recovery_split"])

    def test_employee_branch_has_selected_employee(self):
        fields = validate_fine_fields(fine_payload(
            fine_on="EMPLOYEE", fine_accountability=None, payment_authority=None,
            amount_paid=None, recovery_split=None,
        ))
        self.assertEqual(fields["fine_empl_id_fk"], 1)

    def test_missing_fields(self):
        for field in ("fine_date", "fine_empl_id_fk", "fine_accountability", "payment_authority",
                      "amount_paid", "recovery_split"):
            with self.subTest(field=field), self.assertRaises(FineBusinessValidationError):
                validate_fine_fields(fine_payload(**{field: None}))

    def test_invalid_values(self):
        for field, values in {
            "fine_empl_id_fk": [0, -1, True, "1.5", "bad"],
            "fine_on": ["OTHER", "", None],
            "fine_date": ["bad", "2026-02-30"],
            "fine_accountability": ["COMPANY", ""],
            "payment_authority": ["OTHER", ""],
            "amount_paid": ["0", "-0.01", "NaN", "Infinity", "1.001", "10000000000000000"],
            "recovery_split": ["0", "-1", "100.01", "NaN", "Infinity", "25.001"],
        }.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(FineBusinessValidationError):
                    validate_fine_fields(fine_payload(**{field: value}))

    def test_hidden_payment_values_are_rejected(self):
        with self.assertRaises(FineBusinessValidationError):
            validate_fine_fields(fine_payload(payment_authority="DRIVER"))

    def test_employee_branch_rejects_driver_only_fields(self):
        with self.assertRaises(FineBusinessValidationError):
            validate_fine_fields(fine_payload(fine_on="EMPLOYEE"))

    def test_client_organization_is_not_trusted_by_logic(self):
        with self.assertRaisesRegex(FineBusinessValidationError, "authenticated_org_id"):
            fine_identity({"fine_org_id_fk": 1, "fine_id": 1})

    def test_client_audit_actor_is_not_trusted_by_logic(self):
        with self.assertRaisesRegex(FineBusinessValidationError, "authenticated_user_principal_name"):
            fine_identity({"fine_id": 1, "authenticated_org_id": 1, "created_by": "fake"}, require_actor=True)

    def test_update_binding_preserves_omitted_and_explicit_null_fields(self):
        context = {"user": {"user_id": 7, "user_principal_name": "trusted@example.invalid"},
                   "organization": {"org_id": 1}}
        payload = hr_api.FineMasterUpdatePayload(fine_id=1, amount_paid=None, updated_by="spoofed")
        bound = hr_api.bind_fine_payload(payload, context, set_updated_by=True)
        self.assertIsNone(bound["amount_paid"])
        self.assertNotIn("recovery_split", bound)
        self.assertEqual(bound["updated_by"], "trusted@example.invalid")
        self.assertEqual(bound["authenticated_org_id"], 1)

    def test_routes_are_in_consolidated_app_once(self):
        expected = {
            ("POST", "/api/v1/fines/create"), ("POST", "/api/v1/fines/update"),
            ("POST", "/api/v1/fines/delete"), ("GET", "/api/v1/fines"),
            ("POST", "/api/v1/fines/get"), ("POST", "/api/v1/fines/attachments/upload"),
            ("POST", "/api/v1/fines/attachments/download"),
        }
        for app in (hr_api.app, consolidated_app):
            routes = [(method, route.path) for route in app.routes
                      for method in getattr(route, "methods", ())]
            for endpoint in expected:
                self.assertEqual(routes.count(endpoint), 1, endpoint)

    def test_new_fine_programs_do_not_depend_on_payroll_or_fleet(self):
        service = Path(__file__).resolve().parents[1]
        paths = list((service / "logic").glob("fine_*.py"))
        paths += list((service / "integrations").glob("firebase_fine_*.py"))
        paths += [service / "data/20260929_add_fines_module.sql"]
        for path in paths:
            source = path.read_text().lower()
            for forbidden in ("fine_recovery_installment", "payroll_adjustment", "payroll_run",
                              "round_half_up", "fleet_driver_allocation"):
                self.assertNotIn(forbidden, source, str(path))


if __name__ == "__main__":
    unittest.main()
