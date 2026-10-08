"""Task196 request validation, transport parity and early rejection regressions."""
from contextlib import ExitStack
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from .test_contracts_management_numeric_fields import (
    api, auth_context, consolidated_app, contract_payload, create, update,
    get_authenticated_context,
)
from .contracts_management_validation import (
    CONTRACT_COUNT_FIELDS, CONTRACT_DAY_KM_FIELDS, CONTRACT_RATE_FIELDS,
    CONTRACT_REVENUE_BASES, validate_contract_commercial_values,
)


def priced_payload(basis, **changes):
    values = dict.fromkeys((*CONTRACT_COUNT_FIELDS, *CONTRACT_RATE_FIELDS, *CONTRACT_DAY_KM_FIELDS), 0)
    if basis in ("PER_BUS", "PER_PASSENGER_AND_PER_BUS"):
        values.update(cont_big_bus_count_gt_34=1, cont_big_bus_rate_pm=1000)
    if basis in ("PER_PASSENGER", "PER_PASSENGER_AND_PER_BUS"):
        values.update(cont_no_of_passengers=20, cont_per_passenger_rate_pm=50)
    if basis == "PER_DAY":
        values.update(cont_no_of_days=22.50, cont_per_day_rate=1250.75)
    if basis == "PER_KILOMETER":
        values.update(cont_no_of_kms=1200.50, cont_per_km_rate=3.75)
    return contract_payload(**{**values, "cont_status": "ACTIVE", "cont_revenue_basis": basis, **changes})


class ContractTask196ApiTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for app in (api.app, consolidated_app):
            self.stack.enter_context(patch.dict(app.dependency_overrides, {get_authenticated_context: auth_context}))
        self.clients = [self.stack.enter_context(TestClient(app)) for app in (api.app, consolidated_app)]

    def send(self, client, payload, action, multipart):
        path = f"/api/v1/contracts/{action}"
        if multipart:
            return client.post(path + "-with-document", files={"payload": (None, json.dumps(payload)), "file": ("contract.pdf", b"test", "application/pdf")})
        return client.post(path, json=payload)

    def assert_rejected(self, changes):
        for client in self.clients:
            for action in ("create", "update"):
                for multipart in (False, True):
                    with self.subTest(action=action, multipart=multipart, changes=changes), patch.object(api, f"{action}_contract") as logic:
                        result = self.send(client, priced_payload("PER_DAY", cont_id_pk=9, **changes), action, multipart)
                        self.assertEqual(result.status_code, 422, result.text)
                        logic.assert_not_called()

    def test_openapi_lists_exact_canonical_identifiers(self):
        for app in (api.app, consolidated_app):
            schemas = app.openapi()["components"]["schemas"]
            for name in ("ContractsManagementPayload", "ContractsManagementUpdatePayload"):
                self.assertEqual(schemas[name]["properties"]["cont_revenue_basis"]["enum"], list(CONTRACT_REVENUE_BASES))

    def test_unsupported_and_null_basis_rejected_before_dispatch(self):
        for basis in ("PER_KM", "OTHER", "per_day", None):
            self.assert_rejected({"cont_revenue_basis": basis})

    def test_null_negative_fractional_counts_rejected_before_dispatch(self):
        for field in CONTRACT_COUNT_FIELDS:
            for value in (None, -1, 1.5):
                self.assert_rejected({field: value})

    def test_negative_quantities_and_rates_rejected_before_dispatch(self):
        for field in (*CONTRACT_DAY_KM_FIELDS, *CONTRACT_RATE_FIELDS):
            self.assert_rejected({field: -0.01})

    def test_incomplete_non_draft_day_km_rejected_before_dispatch(self):
        for basis, quantity, rate in (("PER_DAY", *CONTRACT_DAY_KM_FIELDS[:2]), ("PER_KILOMETER", *CONTRACT_DAY_KM_FIELDS[2:])):
            for changes in ({quantity: None, rate: 1}, {quantity: 0, rate: 1}, {quantity: 1, rate: None}):
                self.assert_rejected({"cont_revenue_basis": basis, **changes})

    def test_update_defers_only_omitted_new_fields(self):
        payload = priced_payload("PER_DAY", cont_id_pk=9)
        for field in CONTRACT_DAY_KM_FIELDS:
            payload.pop(field)
        for client in self.clients:
            for multipart in (False, True):
                with patch.object(api, "update_contract", return_value={"workflow_status": "PENDING_APPROVAL"}) as logic:
                    result = self.send(client, payload, "update", multipart)
                    self.assertEqual(result.status_code, 200, result.text)
                    self.assertTrue(set(CONTRACT_DAY_KM_FIELDS).isdisjoint(logic.call_args.args[0]))
                with patch.object(api, "create_contract") as logic:
                    result = self.send(client, payload, "create", multipart)
                    self.assertEqual(result.status_code, 422, result.text)
                    logic.assert_not_called()

    def test_invalid_direct_crud_does_not_connect_stage_or_submit(self):
        for module, action in ((create, create.create_contract), (update, update.update_contract)):
            for changes in ({"cont_revenue_basis": "PER_KM"}, {"cont_no_of_days": None}, {"cont_per_day_rate": -1}, {"cont_no_of_passengers": None}):
                with self.subTest(module=module.__name__, changes=changes), ExitStack() as stack:
                    mocks = [stack.enter_context(patch.object(module, name)) for name in ("db_engine", "stage_contract_document_for_workflow", "contract_number_exists", "is_contracts_management_workflow_approved")]
                    result = action(priced_payload("PER_DAY", cont_id_pk=9, **changes))
                    self.assertIn("error", result)
                    for mock in mocks:
                        mock.assert_not_called()

    def test_counts_omitted_default_zero_and_finite_numeric_validation(self):
        payload = priced_payload("PER_DAY")
        for field in CONTRACT_COUNT_FIELDS:
            payload.pop(field)
        model = api.ContractsManagementPayload(**payload)
        self.assertTrue(all(getattr(model, field) == 0 for field in CONTRACT_COUNT_FIELDS))
        for value in (float("nan"), float("inf"), "not-numeric"):
            self.assertIn("Invalid cont_per_day_rate", validate_contract_commercial_values({**payload, "cont_per_day_rate": value}))


def _transport_case(basis, action, multipart):
    def test(self):
        payload = priced_payload(basis, cont_id_pk=9)
        for client in self.clients:
            with patch.object(api, f"{action}_contract", return_value={"workflow_status": "PENDING_APPROVAL"}) as logic:
                result = self.send(client, payload, action, multipart)
                self.assertEqual(result.status_code, 200, result.text)
                accepted = logic.call_args.args[0]
                self.assertEqual(accepted["cont_revenue_basis"], basis)
                for field in CONTRACT_DAY_KM_FIELDS:
                    self.assertEqual(accepted[field], payload[field])
    return test


for _basis in CONTRACT_REVENUE_BASES:
    for _action in ("create", "update"):
        for _multipart in (False, True):
            setattr(ContractTask196ApiTest, f"test_{_action}_{_basis}_{'multipart' if _multipart else 'json'}", _transport_case(_basis, _action, _multipart))


if __name__ == "__main__":
    unittest.main()
