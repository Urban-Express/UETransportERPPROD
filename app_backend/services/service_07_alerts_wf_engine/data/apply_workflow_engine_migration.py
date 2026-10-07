from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_07_alerts_wf_engine.workflow_repository import (
    apply_workflow_engine_migration,
)


def main() -> None:
    engine = db_engine()
    try:
        with engine.begin() as conn:
            apply_workflow_engine_migration(conn)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
