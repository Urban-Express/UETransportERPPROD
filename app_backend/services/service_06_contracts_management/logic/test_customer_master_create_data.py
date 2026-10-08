import types
import unittest
from unittest.mock import patch

from app_backend.services.service_06_contracts_management.logic import (
    customer_master_create_data,
)


def base_payload(actor="system_admin"):
    return {
        "cust_org_id_fk": 1,
        "cust_code": "QA-CUST-001",
        "cust_name": "QA Test Customer",
        "cust_category": "TEST",
        "cust_status": "ACTIVE",
        "portal_system": "QA Portal",
        "procurement_head_name": "procurement head name",
        "procurement_head_phone_primary": "+971500000001",
        "procurement_head_phone_secondary": "+971500000002",
        "procurement_head_email_primary": "procurement.head.email.primary@example.com",
        "procurement_head_email_secondary": "procurement.head.email.secondary@example.com",
        "operation_incharge_name": "operation incharge name",
        "operation_incharge_phone_primary": "+971500000001",
        "operation_incharge_phone_secondary": "+971500000002",
        "operation_incharge_email_primary": "operation.incharge.email.primary@example.com",
        "operation_incharge_email_secondary": "operation.incharge.email.secondary@example.com",
        "operation_head_name": "operation head name",
        "operation_head_phone_primary": "+971500000001",
        "operation_head_phone_secondary": "+971500000002",
        "operation_head_email_primary": "operation.head.email.primary@example.com",
        "operation_head_email_secondary": "operation.head.email.secondary@example.com",
        "finance_incharge_name": "finance incharge name",
        "finance_incharge_phone_primary": "+971500000001",
        "finance_incharge_phone_secondary": "+971500000002",
        "finance_incharge_email_primary": "finance.incharge.email.primary@example.com",
        "finance_incharge_email_secondary": "finance.incharge.email.secondary@example.com",
        "finance_head_name": "finance head name",
        "finance_head_phone_primary": "+971500000001",
        "finance_head_phone_secondary": "+971500000002",
        "finance_head_email_primary": "finance.head.email.primary@example.com",
        "finance_head_email_secondary": "finance.head.email.secondary@example.com",
        "wcr_incharge_name": "wcr incharge name",
        "wcr_incharge_phone_primary": "+971500000001",
        "wcr_incharge_phone_secondary": "+971500000002",
        "wcr_incharge_email_primary": "wcr.incharge.email.primary@example.com",
        "wcr_incharge_email_secondary": "wcr.incharge.email.secondary@example.com",
        "grn_incharge_name": "grn incharge name",
        "grn_incharge_phone_primary": "+971500000001",
        "grn_incharge_phone_secondary": "+971500000002",
        "grn_incharge_email_primary": "grn.incharge.email.primary@example.com",
        "grn_incharge_email_secondary": "grn.incharge.email.secondary@example.com",
        "cust_billing_address": "QA Billing Address",
        "cust_service_address": "QA Service Address",
        "cust_tax_registration_number": "QA-TRN-001",
        "cust_credit_period_days": 30,
        "cust_notes": "QA customer create test",
        "created_by": actor,
        "updated_by": actor
    }


class FakeConnection:
    def __init__(self):
        self.executions = []

    def execute(self, query, params):
        self.executions.append((query, params))


class FakeBegin:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc, traceback):
        return False


class FakeEngine:
    def __init__(self):
        self.connections = []

    def begin(self):
        connection = FakeConnection()
        self.connections.append(connection)
        return FakeBegin(connection)


class CustomerMasterCreateDataTest(unittest.TestCase):
    def create_customer(self, payload):
        engine = FakeEngine()
        with patch.object(
            customer_master_create_data,
            "db_engine",
            return_value=engine
        ):
            with patch.object(
                customer_master_create_data.pd,
                "read_sql",
                return_value=types.SimpleNamespace(empty=True)
            ):
                response = customer_master_create_data.create_customer_master(payload)
        return response, engine

    def inserted_params(self, payload):
        response, engine = self.create_customer(payload)

        self.assertEqual(
            response,
            {"message": f"Successfully created customer: {payload['cust_code']}"}
        )
        self.assertEqual(len(engine.connections), 2)
        self.assertEqual(len(engine.connections[-1].executions), 1)
        return engine.connections[-1].executions[0][1]

    def test_audit_fields_are_passed_to_insert(self):
        params = self.inserted_params(base_payload("system_admin"))

        self.assertEqual(params["created_by"], "system_admin")
        self.assertEqual(params["updated_by"], "system_admin")

    def test_core_customer_fields_remain_mapped(self):
        payload = base_payload("system_admin")

        params = self.inserted_params(payload)

        for field in (
            "cust_org_id_fk",
            "cust_code",
            "cust_name",
            "cust_category",
            "cust_status",
            "portal_system",
            "procurement_head_name",
            "procurement_head_phone_primary",
            "procurement_head_phone_secondary",
            "procurement_head_email_primary",
            "procurement_head_email_secondary",
            "operation_incharge_name",
            "operation_incharge_phone_primary",
            "operation_incharge_phone_secondary",
            "operation_incharge_email_primary",
            "operation_incharge_email_secondary",
            "operation_head_name",
            "operation_head_phone_primary",
            "operation_head_phone_secondary",
            "operation_head_email_primary",
            "operation_head_email_secondary",
            "finance_incharge_name",
            "finance_incharge_phone_primary",
            "finance_incharge_phone_secondary",
            "finance_incharge_email_primary",
            "finance_incharge_email_secondary",
            "finance_head_name",
            "finance_head_phone_primary",
            "finance_head_phone_secondary",
            "finance_head_email_primary",
            "finance_head_email_secondary",
            "wcr_incharge_name",
            "wcr_incharge_phone_primary",
            "wcr_incharge_phone_secondary",
            "wcr_incharge_email_primary",
            "wcr_incharge_email_secondary",
            "grn_incharge_name",
            "grn_incharge_phone_primary",
            "grn_incharge_phone_secondary",
            "grn_incharge_email_primary",
            "grn_incharge_email_secondary",
            "cust_billing_address",
            "cust_service_address",
            "cust_tax_registration_number",
            "cust_credit_period_days",
            "cust_notes"
        ):
            self.assertEqual(params[field], payload[field])

    def test_audit_fields_are_not_hardcoded(self):
        params = self.inserted_params(base_payload("qa_test_actor"))

        self.assertEqual(params["created_by"], "qa_test_actor")
        self.assertEqual(params["updated_by"], "qa_test_actor")

    def test_missing_created_by_returns_application_error_before_insert(self):
        payload = base_payload()
        payload["created_by"] = None

        response, engine = self.create_customer(payload)

        self.assertEqual(response, {"error": "created_by is required."})
        self.assertEqual(len(engine.connections), 1)


if __name__ == "__main__":
    unittest.main()
