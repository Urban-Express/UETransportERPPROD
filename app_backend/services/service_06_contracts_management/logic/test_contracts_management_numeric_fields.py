"""Contract numeric-field mapping and JSON/multipart API regression tests."""
from contextlib import ExitStack
import json
import unittest
from decimal import Decimal
from unittest.mock import patch

from fastapi.testclient import TestClient

from app_backend.services.auth_context import get_authenticated_context
from app_backend.services.main import app as consolidated_app
from app_backend.services.service_06_contracts_management.api import main as api
from app_backend.services.service_06_contracts_management.logic import contracts_management_create_data as create
from app_backend.services.service_06_contracts_management.logic import contracts_management_update_data as update
from app_backend.services.service_07_alerts_wf_engine.logic.workflow_attachment_logic import public_instance
from app_backend.services.service_07_alerts_wf_engine.workflow_runtime_engine import _clean_workflow_payload


FIELDS = ("cont_no_of_days", "cont_per_day_rate", "cont_no_of_kms", "cont_per_km_rate")
POPULATED = dict(zip(FIELDS, (22, 1250.50, 4500, 4.75)))
DECIMALS = dict(zip(FIELDS, (22.50, 1250.75, 12345.67, 4.25)))
CASES = {
    "populated": POPULATED,
    "null": dict.fromkeys(FIELDS),
    "omitted": {},
    "zero": dict.fromkeys(FIELDS, 0),
    "decimals": DECIMALS,
}
REQUESTER = "contract.requester@example.invalid"
REVIEWER = "contract.reviewer@example.invalid"


def auth_context(org=77):
    return {
        "authenticated": True,
        "user": {"user_id": 1, "user_principal_name": REQUESTER, "user_org_id_fk": org},
        "organization": {"org_id": org},
    }


def contract_payload(**values):
    return {
        "user_principal_name": REQUESTER,
        "cont_org_id_fk": 77,
        "cont_cust_id_fk": 10,
        "cont_dep_id_fk": 20,
        "cont_contract_number": "CONTRACT-NUMERIC-QA",
        "cont_contract_name": "Contract numeric-field test",
        "cont_start_date": "2026-01-01",
        "cont_end_date": "2026-12-31",
        "cont_revenue_basis": "PER_BUS",
        "cont_big_bus_count_gt_34": 1,
        "cont_big_bus_rate_pm": 1000,
        "cont_no_of_billing_months": 12,
        "cont_no_of_work_days_per_week": 5,
        "cont_no_of_round_trips_per_day": 2,
        "total_contract_value": 12000,
        "cont_notes": "Preserve unrelated contract fields",
        "created_by": REQUESTER,
        "updated_by": REQUESTER,
        **values,
    }


class ContractNumericFieldsTest(unittest.TestCase):
    def test_shared_mapping_preserves_each_value_and_missing_null(self):
        self.assertIs(update.get_contract_insert_params, create.get_contract_insert_params)
        for name, values in CASES.items():
            for field in FIELDS:
                with self.subTest(case=name, field=field):
                    mapped = create.get_contract_insert_params(contract_payload(**values))
                    self.assertIn(field, mapped)
                    self.assertEqual(mapped[field], values.get(field))
                    self.assertEqual(mapped["total_contract_value"], 12000)
        for field in FIELDS:
            with self.subTest(decimal_field=field):
                value = Decimal("1250.75")
                self.assertIs(create.get_contract_insert_params({field: value})[field], value)

    def test_json_and_multipart_accept_each_field_on_both_apps(self):
        # The consolidated app reuses the service's APIRoute objects and provider.
        with ExitStack() as stack:
            for app in (api.app, consolidated_app):
                stack.enter_context(patch.dict(app.dependency_overrides, {get_authenticated_context: auth_context}))
            for app in (api.app, consolidated_app):
                with TestClient(app) as client:
                    for action in ("create", "update"):
                        for multipart in (False, True):
                            for name, values in CASES.items():
                                with self.subTest(app=app.title, action=action, multipart=multipart, case=name):
                                    payload = contract_payload(**values)
                                    if action == "update":
                                        payload["cont_id_pk"] = 9
                                    path = f"/api/v1/contracts/{action}"
                                    with patch.object(api, f"{action}_contract", return_value={"workflow_status": "PENDING_APPROVAL"}) as logic:
                                        if multipart:
                                            response = client.post(path + "-with-document", data={"payload": json.dumps(payload)}, files={"file": ("contract.pdf", b"%PDF-test", "application/pdf")})
                                        else:
                                            response = client.post(path, json=payload)
                                    self.assertEqual(response.status_code, 200, response.text)
                                    accepted = logic.call_args.args[0]
                                    for field in FIELDS:
                                        if action == "update" and field not in values:
                                            self.assertNotIn(field, accepted)
                                        else:
                                            self.assertIn(field, accepted)
                                            self.assertEqual(accepted[field], values.get(field))
                                    self.assertEqual(accepted["total_contract_value"], 12000)
                                    self.assertEqual(accepted["cont_org_id_fk"], 77)

    def test_update_preserves_omission_and_explicit_null_for_each_field(self):
        with ExitStack() as stack:
            for app in (api.app, consolidated_app):
                stack.enter_context(patch.dict(app.dependency_overrides, {get_authenticated_context: auth_context}))
            for app in (api.app, consolidated_app):
                with TestClient(app) as client:
                    for multipart in (False, True):
                        for field in FIELDS:
                            for value in (None, 0, 1.50):
                                with self.subTest(app=app.title, multipart=multipart, field=field, value=value):
                                    payload = contract_payload(cont_id_pk=9, **{field: value})
                                    with patch.object(api, "update_contract", return_value={"workflow_status": "PENDING_APPROVAL"}) as logic:
                                        if multipart:
                                            response = client.post("/api/v1/contracts/update-with-document", files={"payload": (None, json.dumps(payload))})
                                        else:
                                            response = client.post("/api/v1/contracts/update", json=payload)
                                    self.assertEqual(response.status_code, 200, response.text)
                                    accepted = logic.call_args.args[0]
                                    self.assertIn(field, accepted)
                                    self.assertEqual(accepted[field], value)
                                    for omitted in set(FIELDS) - {field}:
                                        self.assertNotIn(omitted, accepted)
                                    self.assertEqual(accepted["cont_extra_trip_charge"], 0)
                                    self.assertEqual(accepted["cont_currency_code"], "AED")

    def test_new_fields_remain_optional_in_create_and_update_schemas(self):
        schemas = api.app.openapi()["components"]["schemas"]
        for model in ("ContractsManagementPayload", "ContractsManagementUpdatePayload"):
            for field in FIELDS:
                with self.subTest(model=model, field=field):
                    self.assertIn(field, schemas[model]["properties"])
                    self.assertNotIn(field, schemas[model]["required"])

    def test_workflow_sanitizer_and_review_payload_preserve_business_values(self):
        for name, values in CASES.items():
            with self.subTest(case=name):
                payload = contract_payload(**values)
                cleaned = _clean_workflow_payload(payload)
                rendered = public_instance({"workflow_code": "CONTRACTS_MANAGEMENT", "workflow_action": "UPDATE", "request_payload": cleaned})["request_payload"]
                for field in FIELDS:
                    self.assertEqual(field in cleaned, field in values)
                    self.assertEqual(field in rendered, field in values)
                    self.assertEqual(cleaned.get(field), values.get(field))
                    self.assertEqual(rendered.get(field), values.get(field))


if __name__ == "__main__":
    unittest.main()
