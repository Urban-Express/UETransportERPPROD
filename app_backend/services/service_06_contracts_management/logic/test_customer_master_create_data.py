import sys
import types
import unittest
from unittest.mock import patch


fake_sqlalchemy = types.ModuleType("sqlalchemy")
fake_sqlalchemy.create_engine = lambda *args, **kwargs: None
fake_sqlalchemy.text = lambda query: query
sys.modules.setdefault("sqlalchemy", fake_sqlalchemy)

fake_dotenv = types.ModuleType("dotenv")
fake_dotenv.load_dotenv = lambda *args, **kwargs: None
sys.modules.setdefault("dotenv", fake_dotenv)

fake_pandas = types.ModuleType("pandas")
fake_pandas.read_sql = lambda *args, **kwargs: None
sys.modules.setdefault("pandas", fake_pandas)

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
        "cust_contact_person_name": "QA Contact",
        "cust_contact_person_designation": "QA Manager",
        "cust_phone_primary": "+971500000001",
        "cust_phone_secondary": "+971500000002",
        "cust_email_primary": "qa.customer@example.com",
        "cust_email_secondary": "qa.customer.secondary@example.com",
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
            "cust_contact_person_name",
            "cust_contact_person_designation",
            "cust_phone_primary",
            "cust_phone_secondary",
            "cust_email_primary",
            "cust_email_secondary",
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
