"""Task 196 test support: explicit disposable loopback PostgreSQL databases only."""
import os
from pathlib import Path
import re
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app_backend.services.service_07_alerts_wf_engine.workflow_repository import apply_workflow_engine_migration


SERVICE = Path(__file__).resolve().parents[1]
MIGRATIONS = SERVICE / "data/migrations/20261008_task196"
REQUESTER = "contract.requester@example.invalid"
REVIEWER = "contract.reviewer@example.invalid"


def test_url():
    value = os.environ.get("CONTRACT_TASK196_TEST_DATABASE_URL")
    if not value:
        raise RuntimeError("CONTRACT_TASK196_TEST_DATABASE_URL is required; never fall back to Railway.")
    url = make_url(value)
    if url.get_backend_name() != "postgresql" or url.host not in ("127.0.0.1", "localhost") or not (url.database or "").endswith("_tests"):
        raise RuntimeError("Task196 tests require an explicit loopback PostgreSQL *_tests database.")
    return url


def sql_body(path):
    return re.sub(r"^\s*(BEGIN|COMMIT);\s*$", "", Path(path).read_text(), flags=re.M)


def bootstrap(conn, *, legacy=False):
    """Real Contract DDL/FKs/checks and workflow schema; synthetic reference rows."""
    conn.exec_driver_sql("""
        CREATE TABLE organization_master (org_id_pk INTEGER PRIMARY KEY, org_name TEXT);
        INSERT INTO organization_master VALUES (77,'Task196 test organization'),(88,'Other test organization');
        CREATE TABLE customer_master (cust_id_pk INTEGER PRIMARY KEY, cust_org_id_fk INTEGER,
            UNIQUE(cust_id_pk,cust_org_id_fk));
        CREATE TABLE department_master (dep_id_pk INTEGER PRIMARY KEY, dep_org_id_fk INTEGER);
        INSERT INTO customer_master VALUES (10,77),(11,88);
        INSERT INTO department_master VALUES (20,77),(21,88);
        CREATE TABLE user_master (user_id_pk INTEGER PRIMARY KEY, user_principal_name TEXT,
            user_org_id_fk INTEGER, display_name TEXT, email TEXT,
            is_active BOOLEAN DEFAULT TRUE, is_deleted BOOLEAN DEFAULT FALSE);
        CREATE TABLE permission_master (module_name TEXT, action_name TEXT, permission_code TEXT);
        CREATE VIEW v_user_access_rights AS
            SELECT user_principal_name, 'WORKFLOW'::text AS module_name, action_name
            FROM user_master CROSS JOIN (VALUES ('SUBMIT'),('APPROVE')) AS actions(action_name);
    """)
    conn.execute(text("INSERT INTO user_master (user_id_pk,user_principal_name,user_org_id_fk) VALUES (1,:requester,77),(2,:reviewer,77)"),
                 {"requester": REQUESTER, "reviewer": REVIEWER})
    conn.exec_driver_sql(sql_body(SERVICE / "data/contracts_management.sql"))
    if legacy:
        conn.exec_driver_sql((SERVICE / "logic/fixtures/task196_legacy_constraints.sql").read_text())
    conn.exec_driver_sql(sql_body(SERVICE / "data/contracts_management_workflow_requests.sql"))
    apply_workflow_engine_migration(conn)
    definition = conn.execute(text("SELECT workflow_definition_id_pk FROM workflow_definitions WHERE organization_id_fk=77 AND workflow_code='CONTRACTS_MANAGEMENT'")).scalar_one()
    version = conn.execute(text("INSERT INTO workflow_definition_versions (workflow_definition_id_fk,version_number,version_status,applies_to_action) VALUES (:id,1,'PUBLISHED','ALL') RETURNING workflow_version_id_pk"), {"id": definition}).scalar_one()
    nodes = []
    for key, kind, principal in (("requester", "REQUESTER", REQUESTER), ("reviewer", "APPROVER", REVIEWER), ("end", "END", None)):
        nodes.append(conn.execute(text("INSERT INTO workflow_nodes (workflow_version_id_fk,node_key,node_type,user_principal_name) VALUES (:v,:k,:t,:p) RETURNING workflow_node_id_pk"), {"v": version, "k": key, "t": kind, "p": principal}).scalar_one())
    for sequence, (source, target) in enumerate(zip(nodes, nodes[1:]), 1):
        conn.execute(text("INSERT INTO workflow_edges (workflow_version_id_fk,source_node_id_fk,target_node_id_fk,edge_sequence) VALUES (:v,:s,:t,:n)"), {"v": version, "s": source, "t": target, "n": sequence})


def apply_task196_migration(engine, *, approved=True):
    """Exercise the production migration artifact, only in a disposable test DB."""
    url = engine.url
    if url.host not in ("127.0.0.1", "localhost") or not url.database.endswith("_tests"):
        raise RuntimeError("Refusing migration outside an isolated loopback test database.")
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        oid = conn.exec_driver_sql("SELECT 'public.contracts_management'::regclass::oid").scalar_one()
        conn.execute(text("SELECT set_config('task196.expected_database', :db, false), set_config('task196.expected_contract_oid', :oid, false), set_config('task196.migration_approved', :approval, false)"),
                     {"db": url.database, "oid": str(oid), "approval": "20261008_task196_v1" if approved else ""})
        try:
            # The SQL contains PL/pgSQL % placeholders; execute without DBAPI
            # bind parameters so psycopg does not interpret them as bindings.
            with conn.connection.cursor() as cursor:
                cursor.execute((MIGRATIONS / "TASK196_DATABASE_MIGRATION.sql").read_text())
        except Exception:
            conn.exec_driver_sql("ROLLBACK")
            raise


class DisposableContractDatabase:
    def __init__(self, *, legacy=False, migrate=False):
        url = test_url()
        self.name = f"task196_{uuid4().hex}_tests"
        self.admin = create_engine(url, isolation_level="AUTOCOMMIT")
        with self.admin.connect() as conn:
            conn.exec_driver_sql(f'CREATE DATABASE "{self.name}"')
        self.engine = create_engine(url.set(database=self.name))
        try:
            with self.engine.begin() as conn:
                bootstrap(conn, legacy=legacy)
            if migrate:
                apply_task196_migration(self.engine)
        except Exception:
            self.close()
            raise

    def close(self):
        self.engine.dispose()
        with self.admin.connect() as conn:
            conn.exec_driver_sql(f'DROP DATABASE "{self.name}" WITH (FORCE)')
        self.admin.dispose()
