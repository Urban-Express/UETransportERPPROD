# Configurable Workflow Engine Contract

## Endpoints

`GET /api/v1/workflow-engine/workflows`

Returns the central workflow catalog with stable `workflow_code`, display name, service/entity names, supported actions, organization scope, and active flag.

`GET /api/v1/workflow-engine/workflows/{workflow_code}/eligible-users?organization_id=1`

Returns:

```json
{
  "success": true,
  "data": {
    "requesters": [
      {
        "user_principal_name": "user_a",
        "display_name": "User A",
        "roles": ["Accounts Payable User"],
        "workflow_permissions": ["SUBMIT"]
      }
    ],
    "approvers": []
  }
}
```

Eligibility is derived from active `user_master` users and `v_user_access_rights` for `WORKFLOW/SUBMIT` and `WORKFLOW/APPROVE`.

`GET /api/v1/workflow-engine/workflows/{workflow_code}/definition?organization_id=1&applies_to_action=CREATE`

Returns the workflow definition plus the current published version and, when requested, latest draft version. Nodes include `node_key`, `node_type`, `user_principal_name`, canvas coordinates, and metadata. Edges include source/target node references, sequence, type, and condition JSON.

`PUT /api/v1/workflow-engine/workflows/{workflow_code}/draft`

Request:

```json
{
  "organization_id": 1,
  "applies_to_action": "CREATE",
  "allow_self_approval": false,
  "nodes": [
    {"node_key": "requester_user_a", "node_type": "REQUESTER", "user_principal_name": "user_a"},
    {"node_key": "approver_x", "node_type": "APPROVER", "user_principal_name": "approver_x"},
    {"node_key": "end", "node_type": "END"}
  ],
  "edges": [
    {"source_node_key": "requester_user_a", "target_node_key": "approver_x", "edge_sequence": 1},
    {"source_node_key": "approver_x", "target_node_key": "end", "edge_sequence": 2}
  ],
  "canvas_metadata": {},
  "user_principal_name": "workflow_admin"
}
```

Saving a draft creates a new draft version and does not affect in-flight transactions or the published route.

`POST /api/v1/workflow-engine/workflows/{workflow_code}/publish`

Request:

```json
{
  "workflow_version_id": 991,
  "user_principal_name": "workflow_admin"
}
```

Publishing validates the graph, retires the previous published version for the same workflow/organization/action, and makes the draft authoritative.

## Approval review documents — Task 164

`GET /api/v1/workflow-engine/instances/{workflow_instance_id}` adds a safe
`data.attachments` array for proposed Contract/AP/AR CREATE/UPDATE documents.
It contains attachment/version identity, role, filename, content type, size,
SHA-256 and availability, with no signed URLs or internal storage references.

`POST /api/v1/workflow-engine/instances/{workflow_instance_id}/attachments/{attachment_id}/download`
accepts only `{"version_id":"<discovered version>"}`. It derives organization
from authentication, applies existing workflow visibility, requires pending
review, verifies staging provenance and the recorded object generation, and
returns a generation-bound GCS V4 URL with 300-second expiry and
`Cache-Control: no-store`. Paths occur only within that signed URL.

Unverified legacy attachments and unavailable/replaced generations fail closed.
Final execution and rejection retain the existing pointer/cleanup behavior;
current committed files must not substitute for historical proposed versions.
The complete contract, error codes and frontend integration sequence are in
[the Task 164 Replit handoff](../../../docs/REPLIT_TASK_164_BACKEND_HANDOFF.md).

## Backward Compatibility

Existing module workflow request tables are retained. New requests write both a central `workflow_instances` row and the module compatibility row with `routing_source = CONFIGURED_WORKFLOW`. Pending rows that predate this migration keep `routing_source = LEGACY_ASSIGNED_APPROVER` and remain actionable only by their stored `approver_user_principal_name`; they are not rerouted to newly published diagrams.

## Runtime Guarantees

New submissions fail closed with `WORKFLOW_CONFIGURATION_NOT_FOUND` when no published route exists. Line-manager routing and automatic submit-plus-approve self-approval are not used for new requests. Runtime instances retain `workflow_version_id`, route snapshot JSON, and immutable step history so workflow changes after submission do not reroute existing approvals.
