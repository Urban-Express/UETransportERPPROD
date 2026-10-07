from __future__ import annotations

import asyncio
from datetime import date, datetime
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import TypeAdapter, ValidationError
from starlette.responses import JSONResponse

from app_backend.services.service_07_alerts_wf_engine.api import workflow_runtime_api
from app_backend.services.service_07_alerts_wf_engine.logic import workflow_runtime_logic


AUTH_CONTEXT = {
    "authenticated": True,
    "user": {
        "user_principal_name": "approver_x",
        "user_org_id_fk": 1,
    },
    "organization": {
        "org_id": 1,
    },
}


def _dt(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute)


INSTANCES = [
    {
        "workflow_instance_id_pk": 1,
        "workflow_definition_id_fk": 10,
        "workflow_version_id_fk": 100,
        "workflow_code": "PAYROLL",
        "organization_id_fk": 1,
        "workflow_action": "APPROVAL",
        "requester_user_principal_name": "requester_a",
        "current_node_id_fk": 101,
        "workflow_status": "PENDING_APPROVAL",
        "request_payload": {"payroll_run_id": 11},
        "execution_result": None,
        "started_at": _dt(1, 10),
        "completed_at": None,
        "routing_source": "CONFIGURABLE",
        "route_snapshot": {},
        "legacy_request_table": "payroll_workflow_requests",
        "legacy_workflow_request_id": 501,
    },
    {
        "workflow_instance_id_pk": 2,
        "workflow_definition_id_fk": 20,
        "workflow_version_id_fk": 200,
        "workflow_code": "ACCOUNTS_PAYABLE",
        "organization_id_fk": 1,
        "workflow_action": "CREATE",
        "requester_user_principal_name": "requester_b",
        "current_node_id_fk": None,
        "workflow_status": "EXECUTED",
        "request_payload": {"ap_id": 22},
        "execution_result": {"ok": True},
        "started_at": _dt(2, 8),
        "completed_at": _dt(2, 9),
        "routing_source": "CONFIGURABLE",
        "route_snapshot": {},
        "legacy_request_table": "accounts_payables_workflow_requests",
        "legacy_workflow_request_id": 502,
    },
    {
        "workflow_instance_id_pk": 3,
        "workflow_definition_id_fk": 30,
        "workflow_version_id_fk": 300,
        "workflow_code": "FLEET_MANAGEMENT",
        "organization_id_fk": 1,
        "workflow_action": "UPDATE",
        "requester_user_principal_name": "requester_c",
        "current_node_id_fk": None,
        "workflow_status": "REJECTED",
        "request_payload": {"fleet_id": 33},
        "execution_result": None,
        "started_at": datetime(2026, 8, 31, 23, 0),
        "completed_at": _dt(1, 7),
        "routing_source": "CONFIGURABLE",
        "route_snapshot": {},
        "legacy_request_table": "fleet_management_workflow_requests",
        "legacy_workflow_request_id": 503,
    },
    {
        "workflow_instance_id_pk": 4,
        "workflow_definition_id_fk": 40,
        "workflow_version_id_fk": 400,
        "workflow_code": "PAYROLL",
        "organization_id_fk": 1,
        "workflow_action": "APPROVAL",
        "requester_user_principal_name": "Mixed.Case@Example.com",
        "current_node_id_fk": 201,
        "workflow_status": "PENDING_APPROVAL",
        "request_payload": {"payroll_run_id": 44},
        "execution_result": None,
        "started_at": _dt(1, 23, 59),
        "completed_at": None,
        "routing_source": "CONFIGURABLE",
        "route_snapshot": {},
        "legacy_request_table": "payroll_workflow_requests",
        "legacy_workflow_request_id": 504,
    },
    {
        "workflow_instance_id_pk": 5,
        "workflow_definition_id_fk": 50,
        "workflow_version_id_fk": 500,
        "workflow_code": "CONTRACTS_MANAGEMENT",
        "organization_id_fk": 2,
        "workflow_action": "DELETE",
        "requester_user_principal_name": "other_org_requester",
        "current_node_id_fk": 301,
        "workflow_status": "PENDING_APPROVAL",
        "request_payload": {"contract_id": 55},
        "execution_result": None,
        "started_at": _dt(1, 12),
        "completed_at": None,
        "routing_source": "CONFIGURABLE",
        "route_snapshot": {},
        "legacy_request_table": "contracts_management_workflow_requests",
        "legacy_workflow_request_id": 505,
    },
]


STEPS = [
    {
        "workflow_instance_step_id_pk": 1002,
        "workflow_instance_id_fk": 1,
        "workflow_node_id_fk": 102,
        "step_sequence": 2,
        "assigned_approver_user_principal_name": "approver_later",
        "step_status": "NOT_STARTED",
        "acted_by_user_principal_name": None,
        "approval_comments": None,
        "rejection_comments": None,
        "assigned_at": _dt(1, 11),
        "acted_at": None,
    },
    {
        "workflow_instance_step_id_pk": 1001,
        "workflow_instance_id_fk": 1,
        "workflow_node_id_fk": 101,
        "step_sequence": 1,
        "assigned_approver_user_principal_name": "approver_x",
        "step_status": "PENDING",
        "acted_by_user_principal_name": None,
        "approval_comments": None,
        "rejection_comments": None,
        "assigned_at": _dt(1, 10),
        "acted_at": None,
    },
    {
        "workflow_instance_step_id_pk": 2001,
        "workflow_instance_id_fk": 2,
        "workflow_node_id_fk": 120,
        "step_sequence": 1,
        "assigned_approver_user_principal_name": "approver_x",
        "step_status": "APPROVED",
        "acted_by_user_principal_name": "approver_x",
        "approval_comments": "approved",
        "rejection_comments": None,
        "assigned_at": _dt(2, 8),
        "acted_at": _dt(2, 9),
    },
    {
        "workflow_instance_step_id_pk": 3001,
        "workflow_instance_id_fk": 3,
        "workflow_node_id_fk": 130,
        "step_sequence": 1,
        "assigned_approver_user_principal_name": "fleet_approver",
        "step_status": "REJECTED",
        "acted_by_user_principal_name": "historical_actor",
        "approval_comments": None,
        "rejection_comments": "rejected",
        "assigned_at": datetime(2026, 8, 31, 23, 10),
        "acted_at": _dt(1, 7),
    },
    {
        "workflow_instance_step_id_pk": 4001,
        "workflow_instance_id_fk": 4,
        "workflow_node_id_fk": 201,
        "step_sequence": 1,
        "assigned_approver_user_principal_name": "approver_y",
        "step_status": "PENDING",
        "acted_by_user_principal_name": None,
        "approval_comments": None,
        "rejection_comments": None,
        "assigned_at": _dt(1, 23, 59),
        "acted_at": None,
    },
    {
        "workflow_instance_step_id_pk": 4002,
        "workflow_instance_id_fk": 4,
        "workflow_node_id_fk": 999,
        "step_sequence": 99,
        "assigned_approver_user_principal_name": "wrong_pending_approver",
        "step_status": "PENDING",
        "acted_by_user_principal_name": None,
        "approval_comments": None,
        "rejection_comments": None,
        "assigned_at": _dt(1, 23, 59),
        "acted_at": None,
    },
    {
        "workflow_instance_step_id_pk": 5001,
        "workflow_instance_id_fk": 5,
        "workflow_node_id_fk": 301,
        "step_sequence": 1,
        "assigned_approver_user_principal_name": "approver_x",
        "step_status": "PENDING",
        "acted_by_user_principal_name": None,
        "approval_comments": None,
        "rejection_comments": None,
        "assigned_at": _dt(1, 12),
        "acted_at": None,
    },
]


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None


class FakeConnection:
    def __init__(self):
        self.statements = []
        self.params = []

    def execute(self, statement, params=None):
        sql = str(statement)
        params = dict(params or {})
        self.statements.append(sql)
        self.params.append(params)
        if "order by wi.started_at desc" in sql:
            return FakeResult(self._query_instances(params))
        if "where wi.workflow_instance_id_pk = :workflow_instance_id" in sql:
            return FakeResult(self._query_instance(params))
        if "from workflow_instance_steps" in sql:
            return FakeResult(self._query_steps(params))
        return FakeResult([])

    def _query_instances(self, params):
        rows = [
            self._instance_projection(instance)
            for instance in INSTANCES
            if self._visible(instance, params) and self._matches_filters(instance, params)
        ]
        return sorted(
            rows,
            key=lambda row: (row["started_at"], row["workflow_instance_id"]),
            reverse=True,
        )

    def _query_instance(self, params):
        workflow_instance_id = params["workflow_instance_id"]
        return [
            self._instance_projection(instance, include_detail=True)
            for instance in INSTANCES
            if instance["workflow_instance_id_pk"] == workflow_instance_id
            and self._visible(instance, params)
        ][:1]

    def _query_steps(self, params):
        workflow_instance_id = params["workflow_instance_id"]
        rows = [
            {
                "workflow_instance_step_id": step["workflow_instance_step_id_pk"],
                "workflow_instance_id": step["workflow_instance_id_fk"],
                "workflow_node_id": step["workflow_node_id_fk"],
                "step_sequence": step["step_sequence"],
                "assigned_approver_user_principal_name": step[
                    "assigned_approver_user_principal_name"
                ],
                "step_status": step["step_status"],
                "acted_by_user_principal_name": step["acted_by_user_principal_name"],
                "approval_comments": step["approval_comments"],
                "rejection_comments": step["rejection_comments"],
                "assigned_at": step["assigned_at"],
                "acted_at": step["acted_at"],
            }
            for step in STEPS
            if step["workflow_instance_id_fk"] == workflow_instance_id
        ]
        return sorted(
            rows,
            key=lambda row: (
                row["step_sequence"],
                row["workflow_instance_step_id"],
            ),
        )

    def _matches_filters(self, instance, params):
        if "workflow_code" in params and instance["workflow_code"] != params["workflow_code"]:
            return False
        if (
            "organization_id" in params
            and instance["organization_id_fk"] != params["organization_id"]
        ):
            return False
        if (
            "workflow_action" in params
            and instance["workflow_action"] != params["workflow_action"]
        ):
            return False
        if (
            "workflow_status" in params
            and instance["workflow_status"] != params["workflow_status"]
        ):
            return False
        if "requester_user_principal_name" in params:
            requester = params["requester_user_principal_name"]
            if instance["requester_user_principal_name"].lower() != requester.lower():
                return False
        if "date_from" in params and instance["started_at"].date() < params["date_from"]:
            return False
        if (
            "date_to_exclusive" in params
            and instance["started_at"].date() >= params["date_to_exclusive"]
        ):
            return False
        return True

    def _visible(self, instance, params):
        if instance["organization_id_fk"] != params["principal_organization_id"]:
            return False
        if params.get("is_admin"):
            return True
        principal = params["principal"].lower()
        if instance["requester_user_principal_name"].lower() == principal:
            return True
        return any(
            step["workflow_instance_id_fk"] == instance["workflow_instance_id_pk"]
            and (
                step["assigned_approver_user_principal_name"].lower() == principal
                or (step["acted_by_user_principal_name"] or "").lower() == principal
            )
            for step in STEPS
        )

    def _current_approver(self, instance):
        return next(
            (
                step["assigned_approver_user_principal_name"]
                for step in STEPS
                if step["workflow_instance_id_fk"] == instance["workflow_instance_id_pk"]
                and step["workflow_node_id_fk"] == instance["current_node_id_fk"]
                and step["step_status"] == "PENDING"
            ),
            None,
        )

    def _instance_projection(self, instance, include_detail=False):
        row = {
            "workflow_instance_id": instance["workflow_instance_id_pk"],
            "workflow_definition_id": instance["workflow_definition_id_fk"],
            "workflow_version_id": instance["workflow_version_id_fk"],
            "workflow_code": instance["workflow_code"],
            "organization_id": instance["organization_id_fk"],
            "workflow_action": instance["workflow_action"],
            "requester_user_principal_name": instance["requester_user_principal_name"],
            "current_node_id": instance["current_node_id_fk"],
            "current_approver_user_principal_name": self._current_approver(instance),
            "workflow_status": instance["workflow_status"],
            "started_at": instance["started_at"],
            "completed_at": instance["completed_at"],
            "routing_source": instance["routing_source"],
            "legacy_request_table": instance["legacy_request_table"],
            "legacy_workflow_request_id": instance["legacy_workflow_request_id"],
        }
        if include_detail:
            row.update(
                {
                    "request_payload": instance["request_payload"],
                    "execution_result": instance["execution_result"],
                    "route_snapshot": instance["route_snapshot"],
                }
            )
        return row


class FakeEngine:
    def __init__(self):
        self.conn = FakeConnection()
        self.disposed = False

    def begin(self):
        return self

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, traceback):
        return False

    def dispose(self):
        self.disposed = True


def _call_get_instances(
    principal="admin_user",
    organization_id=1,
    is_admin=True,
    filters=None,
):
    engine = FakeEngine()
    with patch.object(workflow_runtime_logic, "db_engine", return_value=engine), patch.object(
        workflow_runtime_logic,
        "ensure_workflow_engine_schema",
    ):
        result = workflow_runtime_logic.get_instances(
            principal=principal,
            principal_organization_id=organization_id,
            is_admin=is_admin,
            filters=filters or {},
        )
    return result, engine


def _ids(rows):
    return [row["workflow_instance_id"] for row in rows]


async def _asgi_request(
    app,
    path: str,
    *,
    method: str = "GET",
    query_string: str = "",
    headers: dict[str, str] | None = None,
) -> dict:
    request_sent = False
    messages = []
    raw_headers = [(b"host", b"testserver")]
    for key, value in (headers or {}).items():
        raw_headers.append((key.lower().encode(), value.encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query_string.encode(),
        "headers": raw_headers,
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "root_path": "",
    }

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    response_headers = {
        key.decode().lower(): value.decode()
        for key, value in start.get("headers", [])
    }
    return {
        "status_code": start["status"],
        "headers": response_headers,
        "text": body.decode(),
        "json": json.loads(body.decode()) if body else None,
    }


def _get_asgi(app, path: str, *, query_string: str = "", headers=None) -> dict:
    return asyncio.run(
        _asgi_request(app, path, query_string=query_string, headers=headers)
    )


class WorkflowRuntimeInstanceQueryTests(unittest.TestCase):
    def test_base_request_omits_nullable_optional_predicates(self):
        result, engine = _call_get_instances()

        self.assertEqual(_ids(result), [2, 4, 1, 3])
        sql = engine.conn.statements[-1].lower()
        self.assertNotIn("date_to + interval", sql)
        self.assertNotIn(":date_to + interval", sql)
        self.assertNotIn(" is null or ", sql)
        self.assertNotIn("date_from", engine.conn.params[-1])
        self.assertNotIn("date_to", engine.conn.params[-1])
        self.assertNotIn("date_to_exclusive", engine.conn.params[-1])
        self.assertTrue(engine.disposed)

    def test_workflow_code_filter(self):
        result, engine = _call_get_instances(filters={"workflow_code": "payroll"})

        self.assertEqual(_ids(result), [4, 1])
        self.assertEqual(engine.conn.params[-1]["workflow_code"], "PAYROLL")

    def test_workflow_action_filter(self):
        result, engine = _call_get_instances(filters={"workflow_action": "create"})

        self.assertEqual(_ids(result), [2])
        self.assertEqual(engine.conn.params[-1]["workflow_action"], "CREATE")

    def test_workflow_status_filter(self):
        expectations = {
            "PENDING_APPROVAL": [4, 1],
            "EXECUTED": [2],
            "REJECTED": [3],
        }
        for status, expected_ids in expectations.items():
            with self.subTest(status=status):
                result, _engine = _call_get_instances(
                    filters={"workflow_status": status.lower()}
                )
                self.assertEqual(_ids(result), expected_ids)

    def test_requester_filter_is_case_insensitive(self):
        result, _engine = _call_get_instances(
            filters={"requester_user_principal_name": "mixed.case@example.com"}
        )

        self.assertEqual(_ids(result), [4])

    def test_date_from_only_uses_inclusive_lower_boundary(self):
        result, engine = _call_get_instances(filters={"date_from": date(2026, 9, 1)})

        self.assertEqual(_ids(result), [2, 4, 1])
        sql = engine.conn.statements[-1]
        self.assertIn("wi.started_at >= :date_from", sql)
        self.assertNotIn("date_to_exclusive", engine.conn.params[-1])

    def test_date_to_only_uses_exclusive_next_day_boundary(self):
        result, engine = _call_get_instances(filters={"date_to": date(2026, 9, 1)})

        self.assertEqual(_ids(result), [4, 1, 3])
        sql = engine.conn.statements[-1]
        self.assertIn("wi.started_at < :date_to_exclusive", sql)
        self.assertNotIn("interval '1 day'", sql)
        self.assertEqual(engine.conn.params[-1]["date_to_exclusive"], date(2026, 9, 2))

    def test_date_from_and_date_to_form_half_open_calendar_range(self):
        result, _engine = _call_get_instances(
            filters={"date_from": date(2026, 9, 1), "date_to": date(2026, 9, 2)}
        )

        self.assertEqual(_ids(result), [2, 4, 1])

    def test_same_day_range_includes_full_calendar_date(self):
        result, engine = _call_get_instances(
            filters={"date_from": date(2026, 9, 1), "date_to": date(2026, 9, 1)}
        )

        self.assertEqual(_ids(result), [4, 1])
        self.assertEqual(engine.conn.params[-1]["date_to_exclusive"], date(2026, 9, 2))

    def test_empty_result_returns_empty_list(self):
        result, _engine = _call_get_instances(filters={"date_from": date(2026, 9, 3)})

        self.assertEqual(result, [])

    def test_non_admin_visibility_keeps_requester_assigned_and_historical_rules(self):
        requester_rows, _ = _call_get_instances(
            principal="requester_a",
            is_admin=False,
        )
        approver_rows, _ = _call_get_instances(
            principal="approver_x",
            is_admin=False,
        )
        historical_rows, _ = _call_get_instances(
            principal="historical_actor",
            is_admin=False,
        )

        self.assertEqual(_ids(requester_rows), [1])
        self.assertEqual(_ids(approver_rows), [2, 1])
        self.assertEqual(_ids(historical_rows), [3])

    def test_admin_visibility_is_organization_wide_only(self):
        result, _engine = _call_get_instances(principal="admin_user", is_admin=True)

        self.assertEqual(_ids(result), [2, 4, 1, 3])
        self.assertNotIn(5, _ids(result))

    def test_current_approver_join_is_bound_to_current_node(self):
        result, engine = _call_get_instances()
        by_id = {row["workflow_instance_id"]: row for row in result}

        self.assertEqual(by_id[4]["current_approver_user_principal_name"], "approver_y")
        self.assertEqual(_ids(result).count(4), 1)
        self.assertIn(
            "current_step.workflow_node_id_fk = wi.current_node_id_fk",
            engine.conn.statements[-1],
        )


class WorkflowRuntimeDetailAndStepsTests(unittest.TestCase):
    def _call_logic(self, func, *args, **kwargs):
        engine = FakeEngine()
        with patch.object(workflow_runtime_logic, "db_engine", return_value=engine), patch.object(
            workflow_runtime_logic,
            "ensure_workflow_engine_schema",
        ):
            result = func(*args, **kwargs)
        return result, engine

    def test_instance_detail_retains_visibility_and_current_node_join(self):
        visible, engine = self._call_logic(
            workflow_runtime_logic.get_instance,
            workflow_instance_id=1,
            principal="approver_x",
            principal_organization_id=1,
            is_admin=False,
        )
        inaccessible, _ = self._call_logic(
            workflow_runtime_logic.get_instance,
            workflow_instance_id=4,
            principal="approver_x",
            principal_organization_id=1,
            is_admin=False,
        )
        cross_org, _ = self._call_logic(
            workflow_runtime_logic.get_instance,
            workflow_instance_id=5,
            principal="admin_user",
            principal_organization_id=1,
            is_admin=True,
        )

        self.assertEqual(visible["workflow_instance_id"], 1)
        self.assertEqual(visible["current_approver_user_principal_name"], "approver_x")
        self.assertIsNone(inaccessible)
        self.assertIsNone(cross_org)
        self.assertIn(
            "current_step.workflow_node_id_fk = wi.current_node_id_fk",
            engine.conn.statements[-1],
        )

    def test_steps_are_ordered_and_inaccessible_instances_return_none(self):
        visible_steps, _ = self._call_logic(
            workflow_runtime_logic.get_steps,
            workflow_instance_id=1,
            principal="approver_x",
            principal_organization_id=1,
            is_admin=False,
        )
        inaccessible_steps, _ = self._call_logic(
            workflow_runtime_logic.get_steps,
            workflow_instance_id=4,
            principal="approver_x",
            principal_organization_id=1,
            is_admin=False,
        )

        self.assertEqual(
            [step["workflow_instance_step_id"] for step in visible_steps],
            [1001, 1002],
        )
        self.assertIsNone(inaccessible_steps)


class WorkflowRuntimeApiTests(unittest.TestCase):
    def setUp(self):
        from app_backend.services import main

        self.main = main

    def _error_json(self, exc: HTTPException):
        response = asyncio.run(self.main.http_exception_handler(None, exc))
        return response.status_code, json.loads(response.body.decode())

    def test_instances_endpoint_base_request_returns_200(self):
        with patch.object(workflow_runtime_api, "has_workflow_admin", return_value=True), patch.object(
            workflow_runtime_api.workflow_runtime_logic,
            "get_instances",
            return_value=[],
        ) as get_instances:
            response = workflow_runtime_api.get_workflow_instances_endpoint(
                auth_context=AUTH_CONTEXT,
            )

        self.assertEqual(response, {"success": True, "data": []})
        get_instances.assert_called_once()

    def test_invalid_date_syntax_returns_422_before_runtime_query(self):
        with self.assertRaises(ValidationError):
            TypeAdapter(date).validate_python("not-a-date")

        validation_error = RequestValidationError(
            [
                {
                    "type": "date_from_datetime_parsing",
                    "loc": ("query", "date_to"),
                    "msg": "Input should be a valid date",
                    "input": "not-a-date",
                }
            ]
        )
        response = asyncio.run(
            self.main.validation_exception_handler(None, validation_error)
        )

        self.assertEqual(response.status_code, 422)
        self.assertFalse(json.loads(response.body.decode())["success"])

    def test_organization_mismatch_returns_403(self):
        with patch.object(
            workflow_runtime_api.workflow_runtime_logic,
            "get_instances",
            return_value=[],
        ) as get_instances:
            with self.assertRaises(HTTPException) as exc:
                workflow_runtime_api.get_workflow_instances_endpoint(
                    organization_id=2,
                    auth_context=AUTH_CONTEXT,
                )

        status_code, response = self._error_json(exc.exception)
        self.assertEqual(status_code, 403)
        self.assertEqual(
            response,
            {"success": False, "error": "WORKFLOW_ORGANIZATION_ACCESS_DENIED"},
        )
        get_instances.assert_not_called()

    def test_runtime_query_failure_returns_stable_error_envelope(self):
        raw_error = "operator does not exist: timestamp with time zone < interval"
        with patch.object(workflow_runtime_api, "has_workflow_admin", return_value=True), patch.object(
            workflow_runtime_api.workflow_runtime_logic,
            "get_instances",
            side_effect=RuntimeError(raw_error),
        ), self.assertLogs(workflow_runtime_api.logger.name, level="ERROR"):
            with self.assertRaises(HTTPException) as exc:
                workflow_runtime_api.get_workflow_instances_endpoint(
                    auth_context=AUTH_CONTEXT,
                )

        status_code, response = self._error_json(exc.exception)
        self.assertEqual(status_code, 500)
        self.assertEqual(
            response,
            {"success": False, "error": workflow_runtime_api.WORKFLOW_RUNTIME_QUERY_FAILED},
        )
        self.assertNotIn(raw_error, json.dumps(response))

    def test_inbox_detail_and_steps_runtime_endpoints_still_return_controlled_results(self):
        with patch.object(workflow_runtime_api, "has_workflow_admin", return_value=False), patch.object(
            workflow_runtime_api.workflow_runtime_logic,
            "get_inbox",
            return_value=[],
        ):
            inbox = workflow_runtime_api.get_workflow_inbox_endpoint(
                auth_context=AUTH_CONTEXT,
            )
        with patch.object(workflow_runtime_api, "has_workflow_admin", return_value=False), patch.object(
            workflow_runtime_api.workflow_runtime_logic,
            "get_instance",
            return_value={"workflow_instance_id": 1},
        ):
            detail = workflow_runtime_api.get_workflow_instance_endpoint(
                workflow_instance_id=1,
                auth_context=AUTH_CONTEXT,
            )
        with patch.object(workflow_runtime_api, "has_workflow_admin", return_value=False), patch.object(
            workflow_runtime_api.workflow_runtime_logic,
            "get_steps",
            return_value=[{"workflow_instance_step_id": 1001}],
        ):
            steps = workflow_runtime_api.get_workflow_instance_steps_endpoint(
                workflow_instance_id=1,
                auth_context=AUTH_CONTEXT,
            )
        with patch.object(workflow_runtime_api, "has_workflow_admin", return_value=False), patch.object(
            workflow_runtime_api.workflow_runtime_logic,
            "get_instance",
            return_value=None,
        ):
            with self.assertRaises(HTTPException) as exc:
                workflow_runtime_api.get_workflow_instance_endpoint(
                    workflow_instance_id=999,
                    auth_context=AUTH_CONTEXT,
                )

        self.assertEqual(inbox, {"success": True, "data": []})
        self.assertEqual(detail, {"success": True, "data": {"workflow_instance_id": 1, "attachments": []}})
        self.assertEqual(
            steps,
            {"success": True, "data": [{"workflow_instance_step_id": 1001}]},
        )
        status_code, response = self._error_json(exc.exception)
        self.assertEqual(status_code, 404)
        self.assertEqual(
            response,
            {"success": False, "error": "WORKFLOW_INSTANCE_NOT_FOUND"},
        )


class ConsolidatedCorsErrorTests(unittest.TestCase):
    def test_cors_middleware_wraps_controlled_error_middleware(self):
        from app_backend.services import main

        self.assertIs(main.app.user_middleware[0].cls, CORSMiddleware)
        self.assertIs(
            main.app.user_middleware[1].cls,
            main.ControlledUnexpectedExceptionMiddleware,
        )

    def test_allowed_origin_receives_cors_on_controlled_and_unhandled_500(self):
        allowed_origin = "https://workflow-web.example"
        blocked_origin = "https://blocked-workflow-web.example"

        from app_backend.services import main

        async def controlled_runtime_500(scope, receive, send):
            response = JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "error": workflow_runtime_api.WORKFLOW_RUNTIME_QUERY_FAILED,
                },
            )
            await response(scope, receive, send)

        async def unhandled_runtime_error(scope, receive, send):
            raise RuntimeError("private SQL traceback should stay server-side")

        controlled_app = CORSMiddleware(
            controlled_runtime_500,
            allow_origins=[allowed_origin],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        unhandled_app = CORSMiddleware(
            main.ControlledUnexpectedExceptionMiddleware(unhandled_runtime_error),
            allow_origins=[allowed_origin],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        runtime_500 = _get_asgi(
            controlled_app,
            "/api/v1/workflow-engine/instances",
            headers={"Origin": allowed_origin},
        )
        with self.assertLogs(main.logger.name, level="ERROR"):
            unhandled_500 = _get_asgi(
                unhandled_app,
                "/__test__/unhandled-runtime-error",
                headers={"Origin": allowed_origin},
            )
            blocked_500 = _get_asgi(
                unhandled_app,
                "/__test__/unhandled-runtime-error",
                headers={"Origin": blocked_origin},
            )

        self.assertEqual(runtime_500["status_code"], 500)
        self.assertEqual(
            runtime_500["headers"].get("access-control-allow-origin"),
            allowed_origin,
        )
        self.assertEqual(
            runtime_500["json"],
            {"success": False, "error": workflow_runtime_api.WORKFLOW_RUNTIME_QUERY_FAILED},
        )
        self.assertNotIn("raw database SQL", runtime_500["text"])

        self.assertEqual(unhandled_500["status_code"], 500)
        self.assertEqual(
            unhandled_500["headers"].get("access-control-allow-origin"),
            allowed_origin,
        )
        self.assertEqual(
            unhandled_500["json"],
            {"success": False, "error": "INTERNAL_SERVER_ERROR"},
        )
        self.assertNotIn("private SQL traceback", unhandled_500["text"])

        self.assertEqual(blocked_500["status_code"], 500)
        self.assertIsNone(blocked_500["headers"].get("access-control-allow-origin"))


if __name__ == "__main__":
    unittest.main()
