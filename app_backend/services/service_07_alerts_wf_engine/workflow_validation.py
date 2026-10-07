from collections import defaultdict, deque

from app_backend.services.service_07_alerts_wf_engine.workflow_access import (
    WORKFLOW_APPROVE_ACTION,
    WORKFLOW_SUBMIT_ACTION,
    get_active_user,
    organization_exists,
    user_has_workflow_action,
)
from app_backend.services.service_07_alerts_wf_engine.workflow_catalog import (
    WORKFLOW_ALL_ACTION,
    get_workflow_catalog_entry,
)


TERMINAL_NODE_TYPE = "END"
REQUESTER_NODE_TYPE = "REQUESTER"
APPROVER_NODE_TYPE = "APPROVER"


def validate_workflow_graph(
    conn,
    workflow_code: str,
    organization_id: int,
    applies_to_action: str,
    allow_self_approval: bool,
    nodes: list[dict],
    edges: list[dict],
) -> list[dict]:
    errors: list[dict] = []
    catalog_entry = get_workflow_catalog_entry(workflow_code)

    if not catalog_entry:
        errors.append({"code": "WORKFLOW_CODE_NOT_FOUND", "message": "Workflow code is not registered."})
        return errors

    action = (applies_to_action or "").upper()
    if action != WORKFLOW_ALL_ACTION and action not in catalog_entry.supported_actions:
        errors.append({"code": "UNSUPPORTED_WORKFLOW_ACTION", "message": f"Unsupported action: {applies_to_action}"})

    if not organization_exists(conn, organization_id):
        errors.append({"code": "ORGANIZATION_NOT_FOUND", "message": "Organization does not exist."})

    node_by_key = {}
    for node in nodes:
        node_key = node.get("node_key")
        node_type = (node.get("node_type") or "").upper()
        if not node_key:
            errors.append({"code": "NODE_KEY_REQUIRED", "node": node, "message": "Node key is required."})
            continue
        if node_key in node_by_key:
            errors.append({"code": "DUPLICATE_NODE_KEY", "node_key": node_key, "message": "Duplicate node key."})
        if node_type not in {REQUESTER_NODE_TYPE, APPROVER_NODE_TYPE, TERMINAL_NODE_TYPE}:
            errors.append({"code": "UNSUPPORTED_NODE_TYPE", "node_key": node_key, "message": f"Unsupported node type: {node_type}"})
        node_by_key[node_key] = {**node, "node_type": node_type}

    requesters = [node for node in node_by_key.values() if node["node_type"] == REQUESTER_NODE_TYPE]
    approvers = [node for node in node_by_key.values() if node["node_type"] == APPROVER_NODE_TYPE]
    end_nodes = [node for node in node_by_key.values() if node["node_type"] == TERMINAL_NODE_TYPE]

    if not requesters:
        errors.append({"code": "REQUESTER_REQUIRED", "message": "At least one requester node is required."})
    if not approvers:
        errors.append({"code": "APPROVER_REQUIRED", "message": "At least one approver node is required."})
    if len(end_nodes) != 1:
        errors.append({"code": "SINGLE_END_NODE_REQUIRED", "message": "Exactly one END node is required."})

    requester_identities = [
        (requester.get("user_principal_name") or "").strip().lower()
        for requester in requesters
        if requester.get("user_principal_name")
    ]
    duplicate_requesters = {
        requester
        for requester in requester_identities
        if requester_identities.count(requester) > 1
    }
    for requester in sorted(duplicate_requesters):
        errors.append({
            "code": "DUPLICATE_REQUESTER_IDENTITY",
            "user_principal_name": requester,
            "message": "Each requester identity must appear in exactly one requester route.",
        })
    wildcard_requesters = [
        requester
        for requester in requesters
        if not requester.get("user_principal_name")
    ]
    if len(wildcard_requesters) > 1:
        errors.append({
            "code": "DUPLICATE_WILDCARD_REQUESTER",
            "message": "Only one unassigned REQUESTER node can represent any eligible requester.",
        })

    for requester in requesters:
        upn = requester.get("user_principal_name")
        if not upn:
            continue
        if not get_active_user(conn, upn, organization_id):
            errors.append({"code": "REQUESTER_USER_INVALID", "node_key": requester.get("node_key"), "message": "Requester must be an active ERP user."})
        elif not user_has_workflow_action(conn, upn, WORKFLOW_SUBMIT_ACTION, organization_id):
            errors.append({"code": "REQUESTER_SUBMIT_PERMISSION_MISSING", "node_key": requester.get("node_key"), "message": "Requester lacks WORKFLOW/SUBMIT."})

    for approver in approvers:
        upn = approver.get("user_principal_name")
        if not upn or not get_active_user(conn, upn, organization_id):
            errors.append({"code": "APPROVER_USER_INVALID", "node_key": approver.get("node_key"), "message": "Approver must be an active ERP user."})
        elif not user_has_workflow_action(conn, upn, WORKFLOW_APPROVE_ACTION, organization_id):
            errors.append({"code": "APPROVER_APPROVE_PERMISSION_MISSING", "node_key": approver.get("node_key"), "message": "Approver lacks WORKFLOW/APPROVE."})

    outgoing = defaultdict(list)
    incoming = defaultdict(list)
    for edge in edges:
        source = edge.get("source_node_key")
        target = edge.get("target_node_key")
        edge_type = (edge.get("edge_type") or "SEQUENTIAL").upper()
        condition_json = edge.get("condition_json") or {}
        if edge_type != "SEQUENTIAL":
            errors.append({"code": "UNSUPPORTED_EDGE_TYPE", "edge": edge, "message": "Only SEQUENTIAL edges are supported."})
        if condition_json:
            errors.append({"code": "UNSUPPORTED_EDGE_CONDITION", "edge": edge, "message": "Conditional routing is not supported."})
        if source not in node_by_key:
            errors.append({"code": "EDGE_SOURCE_INVALID", "edge": edge, "message": "Edge source node does not exist."})
            continue
        if target not in node_by_key:
            errors.append({"code": "EDGE_TARGET_INVALID", "edge": edge, "message": "Edge target node does not exist."})
            continue
        outgoing[source].append(target)
        incoming[target].append(source)

    for node_key, targets in outgoing.items():
        source_type = node_by_key[node_key]["node_type"]
        if source_type in {REQUESTER_NODE_TYPE, APPROVER_NODE_TYPE} and len(targets) > 1:
            errors.append({"code": "UNSUPPORTED_BRANCHING", "node_key": node_key, "message": "Sequential workflows cannot branch."})
        if source_type == TERMINAL_NODE_TYPE and targets:
            errors.append({"code": "END_NODE_HAS_OUTGOING_EDGE", "node_key": node_key, "message": "END nodes cannot have outgoing edges."})

    reachable_approver_keys = set()
    for requester in requesters:
        node_key = requester["node_key"]
        if not outgoing.get(node_key):
            errors.append({"code": "ORPHAN_REQUESTER", "node_key": node_key, "message": "Requester has no outgoing route."})
            continue
        path = _walk_single_path(node_key, outgoing, node_by_key)
        path_types = [node_by_key[key]["node_type"] for key in path if key in node_by_key]
        if APPROVER_NODE_TYPE not in path_types:
            errors.append({"code": "REQUESTER_PATH_HAS_NO_APPROVER", "node_key": node_key, "message": "Requester route must reach an approver."})
        reachable_approver_keys.update(
            key for key in path if node_by_key.get(key, {}).get("node_type") == APPROVER_NODE_TYPE
        )
        if not path or path[-1] not in node_by_key or node_by_key[path[-1]]["node_type"] != TERMINAL_NODE_TYPE:
            errors.append({"code": "APPROVAL_CHAIN_MUST_REACH_END", "node_key": node_key, "message": "Approval chain must terminate at END."})
        if not allow_self_approval:
            requester_upn = (requester.get("user_principal_name") or "").lower()
            for key in path:
                node = node_by_key.get(key, {})
                if node.get("node_type") != APPROVER_NODE_TYPE:
                    continue
                approver_upn = node.get("user_principal_name")
                if requester_upn and (approver_upn or "").lower() == requester_upn:
                    errors.append({"code": "SELF_APPROVAL_NOT_ALLOWED", "node_key": key, "message": "Self-approval is disabled for this workflow."})
                elif not requester_upn and approver_upn and user_has_workflow_action(
                    conn,
                    approver_upn,
                    WORKFLOW_SUBMIT_ACTION,
                    organization_id,
                ):
                    errors.append({
                        "code": "SELF_APPROVAL_NOT_ALLOWED",
                        "node_key": key,
                        "user_principal_name": approver_upn,
                        "message": (
                            "Wildcard requester route includes an approver who is also "
                            "eligible to submit this workflow while self-approval is disabled."
                        ),
                    })

    for approver in approvers:
        if approver["node_key"] not in reachable_approver_keys:
            errors.append({"code": "UNREACHABLE_APPROVER", "node_key": approver["node_key"], "message": "Approver is not reachable from any requester route."})

    if _has_cycle(outgoing):
        errors.append({"code": "UNSUPPORTED_CYCLE", "message": "Cycles are not supported in sequential workflows."})

    return errors


def _walk_single_path(start_key: str, outgoing: dict, node_by_key: dict) -> list[str]:
    path = []
    seen = set()
    current = start_key
    while current in outgoing and outgoing[current]:
        if current in seen:
            return path
        seen.add(current)
        current = outgoing[current][0]
        path.append(current)
        if node_by_key.get(current, {}).get("node_type") == TERMINAL_NODE_TYPE:
            break
    return path


def _has_cycle(outgoing: dict[str, list[str]]) -> bool:
    indegree = defaultdict(int)
    nodes = set(outgoing.keys())
    for source, targets in outgoing.items():
        nodes.add(source)
        for target in targets:
            nodes.add(target)
            indegree[target] += 1

    queue = deque([node for node in nodes if indegree[node] == 0])
    visited = 0
    while queue:
        node = queue.popleft()
        visited += 1
        for target in outgoing.get(node, []):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    return visited != len(nodes)
