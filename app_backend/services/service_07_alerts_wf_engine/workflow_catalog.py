from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class WorkflowCatalogEntry:
    workflow_code: str
    workflow_name: str
    service_name: str
    entity_name: str
    supported_actions: tuple[str, ...]
    organization_scope: str = "ORGANIZATION"
    is_active: bool = True


WORKFLOW_ALL_ACTION = "ALL"

WORKFLOW_CATALOG: dict[str, WorkflowCatalogEntry] = {
    "ASSET_MASTER": WorkflowCatalogEntry(
        workflow_code="ASSET_MASTER",
        workflow_name="Asset Master",
        service_name="service_08_financial_management",
        entity_name="asset_master",
        supported_actions=("CREATE", "UPDATE", "DELETE"),
    ),
    "CONTRACTS_MANAGEMENT": WorkflowCatalogEntry(
        workflow_code="CONTRACTS_MANAGEMENT",
        workflow_name="Contracts Management",
        service_name="service_06_contracts_management",
        entity_name="contracts_management",
        supported_actions=("CREATE", "UPDATE"),
    ),
    "FLEET_MANAGEMENT": WorkflowCatalogEntry(
        workflow_code="FLEET_MANAGEMENT",
        workflow_name="Fleet Management",
        service_name="service_03_fleet_management",
        entity_name="fleet_master",
        supported_actions=("CREATE", "UPDATE", "DELETE"),
    ),
    "PAYROLL": WorkflowCatalogEntry(
        workflow_code="PAYROLL",
        workflow_name="Payroll",
        service_name="service_02_hr_payroll",
        entity_name="payroll_run",
        supported_actions=("APPROVAL",),
    ),
    "ACCOUNTS_PAYABLE": WorkflowCatalogEntry(
        workflow_code="ACCOUNTS_PAYABLE",
        workflow_name="Accounts Payable",
        service_name="service_08_financial_management",
        entity_name="accounts_payables",
        supported_actions=("CREATE", "UPDATE", "DELETE"),
    ),
    "ACCOUNTS_RECEIVABLE": WorkflowCatalogEntry(
        workflow_code="ACCOUNTS_RECEIVABLE",
        workflow_name="Accounts Receivable",
        service_name="service_08_financial_management",
        entity_name="accounts_receivables",
        supported_actions=("CREATE", "UPDATE", "DELETE"),
    ),
}


def list_workflows() -> list[dict]:
    return [asdict(entry) for entry in WORKFLOW_CATALOG.values()]


def get_workflow_catalog_entry(workflow_code: str) -> WorkflowCatalogEntry | None:
    return WORKFLOW_CATALOG.get((workflow_code or "").upper())


def validate_workflow_action(
    workflow_code: str,
    workflow_action: str,
    allow_all_scope: bool = False,
) -> tuple[bool, str | None]:
    entry = get_workflow_catalog_entry(workflow_code)
    if not entry or not entry.is_active:
        return False, "WORKFLOW_CODE_NOT_FOUND"

    action = (workflow_action or "").upper()
    if action == WORKFLOW_ALL_ACTION and allow_all_scope:
        return True, None

    if action not in entry.supported_actions:
        return False, "UNSUPPORTED_WORKFLOW_ACTION"

    return True, None
