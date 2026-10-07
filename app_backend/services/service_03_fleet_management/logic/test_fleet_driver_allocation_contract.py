import os
import inspect
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import text

from app_backend.services.service_01_organization_management.data.db_connect_engine import (
    db_engine,
)
from app_backend.services.service_03_fleet_management.api import main as fleet_api
from app_backend.services.service_03_fleet_management.logic import (
    fleet_driver_allocation_create_data as create_module,
    fleet_driver_allocation_delete_data as delete_module,
    fleet_driver_allocation_get_data as get_module,
    fleet_driver_allocation_update_data as update_module,
)
from app_backend.services.service_03_fleet_management.logic.fleet_driver_allocation_common import (
    DRIVER_ALLOCATION_COLUMNS,
    normalize_optional_fk,
)


DELETED_DRIVER_ALLOCATION_FIELDS = {
    "is_primary_driver",
    "allocation_type",
    "client_id_fk",
    "allocation_remarks",
    "deallocation_remarks",
    "field_flex_field_1",
    "field_flex_field_2",
    "field_flex_field_3",
    "field_flex_field_4",
}


def _iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat()


def _auth_context(org_id: int, user_id: int = 1) -> dict:
    return {
        "authenticated": True,
        "user": {
            "user_id": user_id,
            "user_principal_name": "fleet_test_user",
            "user_org_id_fk": org_id,
        },
        "organization": {"org_id": org_id},
    }


class FailingEngine:
    def begin(self):
        raise RuntimeError("psycopg2.errors.UndefinedColumn: SQL statement leak")

    def connect(self):
        raise RuntimeError("sqlalchemy.exc.OperationalError: SQL statement leak")

    def dispose(self):
        pass


class DriverAllocationContractTests(unittest.TestCase):
    def test_optional_route_and_shift_zero_values_normalize_to_null(self):
        for value in (0, "0", "", None):
            normalized, error = normalize_optional_fk(value, "route_id_fk")
            self.assertIsNone(error)
            self.assertIsNone(normalized)

    def test_api_schemas_do_not_advertise_deleted_driver_allocation_fields(self):
        openapi = fleet_api.app.openapi()
        schemas = openapi["components"]["schemas"]
        for schema_name in (
            "FleetDriverAllocationCreatePayload",
            "FleetDriverAllocationUpdatePayload",
            "FleetDriverAllocationDeletePayload",
        ):
            properties = set(schemas[schema_name]["properties"])
            self.assertFalse(DELETED_DRIVER_ALLOCATION_FIELDS & properties)

    def test_legacy_extra_fields_are_ignored_by_create_payload_model(self):
        payload = fleet_api.FleetDriverAllocationCreatePayload(
            fleet_org_id_fk=1,
            fleet_vehicle_id_fk=15,
            fleet_driver_empl_id_fk=56,
            allocation_start_datetime="2026-09-07T00:00:00+00:00",
            allocation_type="primary",
            is_primary_driver=True,
            client_id_fk=100,
            allocation_remarks="legacy",
            deallocation_remarks="legacy",
            field_flex_field_1="legacy",
        )
        payload_dict = payload.model_dump()
        self.assertFalse(DELETED_DRIVER_ALLOCATION_FIELDS & set(payload_dict))

    def test_update_and_delete_require_allocation_primary_key(self):
        update_result = update_module.update_fleet_driver_allocation(
            {
                "fleet_org_id_fk": 1,
                "fleet_vehicle_id_fk": 15,
                "updated_by": 1,
                "allocation_reason": "should not identify by vehicle",
            }
        )
        delete_result = delete_module.delete_fleet_driver_allocation(
            {"fleet_org_id_fk": 1, "fleet_vehicle_id_fk": 15}
        )
        self.assertIn("fleet_driver_allocation_id_pk is required", update_result["error"])
        self.assertIn("fleet_driver_allocation_id_pk is required", delete_result["error"])

    def test_database_failures_return_safe_client_messages(self):
        valid_payload = {
            "fleet_org_id_fk": 1,
            "fleet_vehicle_id_fk": 15,
            "fleet_driver_empl_id_fk": 56,
            "allocation_start_datetime": "2026-09-07T00:00:00+00:00",
            "allocation_status": "scheduled",
            "created_by": 1,
            "updated_by": 1,
        }
        with patch.object(create_module, "db_engine", return_value=FailingEngine()):
            create_result = create_module.create_fleet_driver_allocation(valid_payload)
        with patch.object(delete_module, "db_engine", return_value=FailingEngine()):
            delete_result = delete_module.delete_fleet_driver_allocation(
                {
                    "fleet_org_id_fk": 1,
                    "fleet_driver_allocation_id_pk": 1,
                }
            )
        with patch.object(get_module, "db_engine", return_value=FailingEngine()):
            get_result = get_module.get_fleet_driver_allocation({"fleet_org_id_fk": 1})
        with patch.object(update_module, "db_engine", return_value=FailingEngine()):
            update_result = update_module.update_fleet_driver_allocation(
                {
                    "fleet_org_id_fk": 1,
                    "fleet_driver_allocation_id_pk": 1,
                    "allocation_reason": "safe error",
                    "updated_by": 1,
                }
            )

        self.assertEqual(
            create_result["error"],
            "Failed to create fleet driver allocation.",
        )
        self.assertEqual(
            update_result["error"],
            "Failed to update fleet driver allocation.",
        )
        self.assertEqual(
            delete_result["error"],
            "Failed to delete fleet driver allocation.",
        )
        self.assertEqual(
            get_result[1]["error"],
            "Failed to retrieve fleet driver allocations.",
        )
        self.assertNotIn("psycopg2", str(create_result))
        self.assertNotIn("psycopg2", str(update_result))
        self.assertNotIn("sqlalchemy", str(get_result))

    def test_driver_allocation_logic_source_has_no_deleted_columns(self):
        modules = (create_module, update_module, delete_module, get_module)
        for module in modules:
            source = inspect.getsource(module)
            for field_name in DELETED_DRIVER_ALLOCATION_FIELDS:
                self.assertNotIn(field_name, source, module.__name__)

        update_source = inspect.getsource(update_module)
        delete_source = inspect.getsource(delete_module)
        self.assertNotIn("fleet_driver_allocation_id or fleet_vehicle_id", update_source)
        self.assertNotIn("fleet_driver_allocation_id or fleet_vehicle_id", delete_source)
        self.assertNotIn("fleet_vehicle_id_fk = :fleet_vehicle_id", update_source)
        self.assertNotIn("fleet_vehicle_id_fk = :fleet_vehicle_id", delete_source)


@unittest.skipUnless(
    os.getenv("UE_FLEET_LIVE_TESTS") == "1",
    "Set UE_FLEET_LIVE_TESTS=1 to run live PostgreSQL Driver Allocation tests.",
)
class LiveDriverAllocationApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = db_engine()
        self.client = TestClient(fleet_api.app)
        self.test_prefix = f"FDA_LIVE_{int(time.time())}"
        self.org_id, self.vehicle_id, self.driver_id = self._find_same_org_refs()
        self.alternate_driver_id = self._find_alternate_driver()
        self.other_org_id, self.other_vehicle_id, self.other_driver_id = (
            self._find_other_org_refs()
        )
        fleet_api.app.dependency_overrides[
            fleet_api.get_authenticated_context
        ] = lambda: _auth_context(self.org_id)
        self._cleanup(self.org_id)

    def tearDown(self):
        try:
            self._cleanup(self.org_id)
            if self.other_org_id:
                self._cleanup(self.other_org_id)
        finally:
            fleet_api.app.dependency_overrides.clear()
            self.engine.dispose()

    def _find_same_org_refs(self):
        with self.engine.connect() as conn:
            row = conn.execute(
                text("""
                    select
                        fm.fleet_org_id_fk as org_id,
                        fm.fleet_vehicle_id_pk as vehicle_id,
                        em.empl_id_pk as driver_id
                    from fleet_master fm
                    join employee_master em
                      on em.empl_org_id_fk = fm.fleet_org_id_fk
                    order by fm.fleet_org_id_fk, fm.fleet_vehicle_id_pk, em.empl_id_pk
                    limit 1
                """)
            ).mappings().first()
        if not row:
            self.fail("Live database has no same-organization vehicle/driver pair.")
        return int(row["org_id"]), int(row["vehicle_id"]), int(row["driver_id"])

    def _find_alternate_driver(self):
        with self.engine.connect() as conn:
            row = conn.execute(
                text("""
                    select empl_id_pk
                    from employee_master
                    where empl_org_id_fk = :org_id
                      and empl_id_pk <> :driver_id
                    order by empl_id_pk
                    limit 1
                """),
                {"org_id": self.org_id, "driver_id": self.driver_id},
            ).mappings().first()
        return int(row["empl_id_pk"]) if row else None

    def _find_other_org_refs(self):
        with self.engine.connect() as conn:
            row = conn.execute(
                text("""
                    select
                        fm.fleet_org_id_fk as org_id,
                        fm.fleet_vehicle_id_pk as vehicle_id,
                        em.empl_id_pk as driver_id
                    from fleet_master fm
                    left join employee_master em
                      on em.empl_org_id_fk = fm.fleet_org_id_fk
                    where fm.fleet_org_id_fk <> :org_id
                    order by fm.fleet_org_id_fk, fm.fleet_vehicle_id_pk, em.empl_id_pk
                    limit 1
                """),
                {"org_id": self.org_id},
            ).mappings().first()
        if not row:
            return None, 999999999, 999999999
        return (
            int(row["org_id"]),
            int(row["vehicle_id"]),
            int(row["driver_id"] or 999999999),
        )

    def _cleanup(self, org_id: int):
        with self.engine.begin() as conn:
            conn.execute(
                text("""
                    delete from fleet_driver_allocation
                    where fleet_org_id_fk = :org_id
                      and allocation_reason like :reason_pattern
                """),
                {"org_id": org_id, "reason_pattern": f"{self.test_prefix}%"},
            )

    def _create_payload(self, suffix: str, **overrides) -> dict:
        start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=30)
        start = start + timedelta(days=len(suffix))
        payload = {
            "fleet_org_id_fk": self.org_id,
            "fleet_vehicle_id_fk": self.vehicle_id,
            "fleet_driver_empl_id_fk": self.driver_id,
            "allocation_start_datetime": _iso(start),
            "allocation_end_datetime": _iso(start + timedelta(hours=8)),
            "allocation_status": "scheduled",
            "route_id_fk": 0,
            "shift_id_fk": "0",
            "allocation_reason": f"{self.test_prefix}_{suffix}",
            "deallocation_reason": None,
            "allocation_type": "primary",
            "is_primary_driver": True,
            "client_id_fk": 12345,
            "allocation_remarks": "legacy field",
            "deallocation_remarks": "legacy field",
            "field_flex_field_1": "legacy field",
            "field_flex_field_2": "legacy field",
            "field_flex_field_3": "legacy field",
            "field_flex_field_4": "legacy field",
        }
        payload.update(overrides)
        return payload

    def _post_create(self, suffix: str, **overrides) -> int:
        response = self.client.post(
            "/api/v1/fleet-driver-allocations/create",
            json=self._create_payload(suffix, **overrides),
        )
        self.assertEqual(response.status_code, 200, response.text)
        allocation_id = response.json()["data"]["fleet_driver_allocation_id_pk"]
        self.assertIsInstance(allocation_id, int)
        return allocation_id

    def _fetch_row(self, allocation_id: int) -> dict | None:
        with self.engine.connect() as conn:
            return conn.execute(
                text(f"""
                    select {", ".join(DRIVER_ALLOCATION_COLUMNS)}
                    from fleet_driver_allocation
                    where fleet_driver_allocation_id_pk = :allocation_id
                """),
                {"allocation_id": allocation_id},
            ).mappings().first()

    def test_live_create_get_update_delete_driver_allocation_contract(self):
        with self.engine.connect() as conn:
            live_columns = {
                row[0]
                for row in conn.execute(
                    text("""
                        select column_name
                        from information_schema.columns
                        where table_schema = 'public'
                          and table_name = 'fleet_driver_allocation'
                    """)
                ).all()
            }
        self.assertFalse(DELETED_DRIVER_ALLOCATION_FIELDS & live_columns)

        missing_vehicle_payload = self._create_payload("missing_vehicle")
        missing_vehicle_payload.pop("fleet_vehicle_id_fk")
        missing_vehicle = self.client.post(
            "/api/v1/fleet-driver-allocations/create",
            json=missing_vehicle_payload,
        )
        self.assertEqual(missing_vehicle.status_code, 422)

        missing_driver_payload = self._create_payload("missing_driver")
        missing_driver_payload.pop("fleet_driver_empl_id_fk")
        missing_driver = self.client.post(
            "/api/v1/fleet-driver-allocations/create",
            json=missing_driver_payload,
        )
        self.assertEqual(missing_driver.status_code, 422)

        allocation_id_1 = self._post_create("one")
        row_1 = self._fetch_row(allocation_id_1)
        self.assertIsNotNone(row_1)
        self.assertEqual(row_1["route_id_fk"], None)
        self.assertEqual(row_1["shift_id_fk"], None)
        self.assertEqual(row_1["allocated_by"], 1)
        self.assertEqual(row_1["created_by"], 1)
        self.assertEqual(row_1["updated_by"], 1)
        self.assertIsNotNone(row_1["allocated_at"])
        self.assertIsNotNone(row_1["created_at"])
        self.assertIsNotNone(row_1["updated_at"])
        self.assertIsNone(row_1["deallocated_by"])
        self.assertIsNone(row_1["deallocated_at"])

        allocation_id_2 = self._post_create("two")
        row_2 = self._fetch_row(allocation_id_2)
        self.assertEqual(row_2["fleet_vehicle_id_fk"], self.vehicle_id)

        missing_id_update = self.client.post(
            "/api/v1/fleet-driver-allocations/update",
            json={
                "fleet_vehicle_id_fk": self.vehicle_id,
                "allocation_reason": f"{self.test_prefix}_vehicle_fallback_rejected",
            },
        )
        self.assertEqual(missing_id_update.status_code, 400)
        self.assertIn("fleet_driver_allocation_id_pk is required", missing_id_update.text)

        invalid_date_update = self.client.post(
            "/api/v1/fleet-driver-allocations/update",
            json={
                "fleet_driver_allocation_id_pk": allocation_id_1,
                "allocation_start_datetime": "2026-09-10T00:00:00+00:00",
                "allocation_end_datetime": "2026-09-09T00:00:00+00:00",
            },
        )
        self.assertEqual(invalid_date_update.status_code, 400)

        before_update = self._fetch_row(allocation_id_1)
        time.sleep(1)
        patch_update = self.client.post(
            "/api/v1/fleet-driver-allocations/update",
            json={
                "fleet_driver_allocation_id_pk": allocation_id_1,
                "allocation_reason": f"{self.test_prefix}_one_updated",
            },
        )
        self.assertEqual(patch_update.status_code, 200, patch_update.text)
        after_update = self._fetch_row(allocation_id_1)
        untouched_other = self._fetch_row(allocation_id_2)
        self.assertEqual(after_update["allocation_reason"], f"{self.test_prefix}_one_updated")
        self.assertEqual(untouched_other["allocation_reason"], f"{self.test_prefix}_two")
        self.assertEqual(after_update["created_by"], before_update["created_by"])
        self.assertEqual(after_update["created_at"], before_update["created_at"])
        self.assertEqual(after_update["fleet_driver_empl_id_fk"], self.driver_id)
        self.assertEqual(after_update["updated_by"], 1)
        self.assertIsNone(after_update["deallocated_by"])
        self.assertIsNone(after_update["deallocated_at"])

        driver_update_value = self.alternate_driver_id or self.driver_id
        same_org_driver_update = self.client.post(
            "/api/v1/fleet-driver-allocations/update",
            json={
                "fleet_driver_allocation_id_pk": allocation_id_1,
                "fleet_driver_empl_id_fk": driver_update_value,
            },
        )
        self.assertEqual(same_org_driver_update.status_code, 200, same_org_driver_update.text)
        self.assertEqual(
            self._fetch_row(allocation_id_1)["fleet_driver_empl_id_fk"],
            driver_update_value,
        )

        cross_vehicle_update = self.client.post(
            "/api/v1/fleet-driver-allocations/update",
            json={
                "fleet_driver_allocation_id_pk": allocation_id_1,
                "fleet_vehicle_id_fk": self.other_vehicle_id,
            },
        )
        self.assertEqual(cross_vehicle_update.status_code, 404)

        cross_driver_update = self.client.post(
            "/api/v1/fleet-driver-allocations/update",
            json={
                "fleet_driver_allocation_id_pk": allocation_id_1,
                "fleet_driver_empl_id_fk": self.other_driver_id,
            },
        )
        self.assertEqual(cross_driver_update.status_code, 404)

        completed_end = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=60)
        completed_update = self.client.post(
            "/api/v1/fleet-driver-allocations/update",
            json={
                "fleet_driver_allocation_id_pk": allocation_id_2,
                "allocation_status": "completed",
                "allocation_end_datetime": _iso(completed_end),
                "deallocation_reason": f"{self.test_prefix}_completed",
            },
        )
        self.assertEqual(completed_update.status_code, 200, completed_update.text)
        completed_row = self._fetch_row(allocation_id_2)
        self.assertEqual(completed_row["deallocated_by"], 1)
        self.assertIsNotNone(completed_row["deallocated_at"])

        get_response = self.client.get("/api/v1/fleet-driver-allocations")
        self.assertEqual(get_response.status_code, 200, get_response.text)
        records = get_response.json()["data"]
        self.assertTrue(all(record["fleet_org_id_fk"] == self.org_id for record in records))
        self.assertTrue(
            any(record["fleet_driver_allocation_id_pk"] == allocation_id_1 for record in records)
        )
        for record in records:
            self.assertFalse(DELETED_DRIVER_ALLOCATION_FIELDS & set(record))

        fleet_api.app.dependency_overrides[
            fleet_api.get_authenticated_context
        ] = lambda: _auth_context(999999999)
        empty_get = self.client.get("/api/v1/fleet-driver-allocations")
        self.assertEqual(empty_get.status_code, 200, empty_get.text)
        self.assertEqual(empty_get.json()["data"], [])
        fleet_api.app.dependency_overrides[
            fleet_api.get_authenticated_context
        ] = lambda: _auth_context(self.org_id)

        if self.other_org_id:
            fleet_api.app.dependency_overrides[
                fleet_api.get_authenticated_context
            ] = lambda: _auth_context(self.other_org_id)
            cross_org_get = self.client.get("/api/v1/fleet-driver-allocations")
            self.assertEqual(cross_org_get.status_code, 200, cross_org_get.text)
            self.assertFalse(
                any(
                    record["fleet_driver_allocation_id_pk"] == allocation_id_1
                    for record in cross_org_get.json()["data"]
                )
            )

            cross_org_update = self.client.post(
                "/api/v1/fleet-driver-allocations/update",
                json={
                    "fleet_driver_allocation_id_pk": allocation_id_1,
                    "allocation_reason": f"{self.test_prefix}_cross_org",
                },
            )
            self.assertEqual(cross_org_update.status_code, 404)

            cross_org_delete = self.client.post(
                "/api/v1/fleet-driver-allocations/delete",
                json={"fleet_driver_allocation_id_pk": allocation_id_1},
            )
            self.assertEqual(cross_org_delete.status_code, 404)
            fleet_api.app.dependency_overrides[
                fleet_api.get_authenticated_context
            ] = lambda: _auth_context(self.org_id)

        cross_vehicle_create = self.client.post(
            "/api/v1/fleet-driver-allocations/create",
            json=self._create_payload("cross_vehicle", fleet_vehicle_id_fk=self.other_vehicle_id),
        )
        self.assertEqual(cross_vehicle_create.status_code, 404)

        cross_driver_create = self.client.post(
            "/api/v1/fleet-driver-allocations/create",
            json=self._create_payload("cross_driver", fleet_driver_empl_id_fk=self.other_driver_id),
        )
        self.assertEqual(cross_driver_create.status_code, 404)

        active_with_end = self.client.post(
            "/api/v1/fleet-driver-allocations/create",
            json=self._create_payload(
                "active_with_end",
                allocation_status="active",
            ),
        )
        self.assertEqual(active_with_end.status_code, 400)

        delete_by_vehicle_only = self.client.post(
            "/api/v1/fleet-driver-allocations/delete",
            json={"fleet_vehicle_id_fk": self.vehicle_id},
        )
        self.assertEqual(delete_by_vehicle_only.status_code, 400)

        invalid_delete = self.client.post(
            "/api/v1/fleet-driver-allocations/delete",
            json={"fleet_driver_allocation_id_pk": 999999999},
        )
        self.assertEqual(invalid_delete.status_code, 404)

        delete_response = self.client.post(
            "/api/v1/fleet-driver-allocations/delete",
            json={"fleet_driver_allocation_id_pk": allocation_id_1},
        )
        self.assertEqual(delete_response.status_code, 200, delete_response.text)
        self.assertIsNone(self._fetch_row(allocation_id_1))
        self.assertIsNotNone(self._fetch_row(allocation_id_2))

        delete_response_2 = self.client.post(
            "/api/v1/fleet-driver-allocations/delete",
            json={"fleet_driver_allocation_id_pk": allocation_id_2},
        )
        self.assertEqual(delete_response_2.status_code, 200, delete_response_2.text)


if __name__ == "__main__":
    unittest.main()
