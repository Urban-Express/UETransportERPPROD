# UETransportERP - HR AND PAYROLL MANAGEMENT
To create/ delete/ update employee data, integration with Firebase for uploading payslips and run monthly payroll with integration with Temporal for wf-driven approvals.


## Fines — ID_003 (revised CRUD-only scope)

Fines persist employee-linked data and Firebase attachment pointers. All employees
in the authenticated organization are selectable, regardless of designation or
Fleet allocation. The selected key is `employee_master.empl_id_pk`, stored in
`fine_master.fine_empl_id_fk`. Names are joined for display.

`amount_paid` and `recovery_split` are stored values. There are no installment
calculations, recovery tables, payroll adjustments, payroll-period decisions,
recovery balances, or Fine-to-Payroll links. Monthly and Off-Cycle Payroll code is
unchanged. **No Workflow Engine changes made.**

### Database and deployment

Apply [data/20260929_add_fines_module.sql](data/20260929_add_fines_module.sql)
to the target database before deploying the new endpoints. It is an idempotent,
transactional creation script that creates only `public.fine_master`, its ID
sequence, and three non-unique query indexes. It has been tested against
a disposable local PostgreSQL database; it has **not** been applied to the live
database. It does not remove columns or constraints from a table that was
already created using an earlier DDL version.

| Columns | Storage / purpose |
| --- | --- |
| `fine_id_pk` | BIGINT with a sequence default for automatic IDs; no primary-key constraint |
| `fine_org_id_fk` | BIGINT organization identifier, bound from authentication |
| `fine_empl_id_fk` | BIGINT selected employee identifier; API validates `employee_master.empl_id_pk` |
| `fine_date` | DATE |
| `fine_on` | DRIVER or EMPLOYEE; manual selection, not employee qualification |
| `fine_accountability`, `payment_authority` | DRIVER or URBAN_EXPRESS, conditionally required |
| `amount_paid`, `recovery_split` | NUMERIC(18,2), NUMERIC(5,2); stored without recovery calculations |
| `fine_attachment_path` | Nullable TEXT; controlled exclusively by upload integration |
| `created_by`, `updated_by` | Authenticated user principal; VARCHAR(100) |
| `created_at`, `updated_at` | TIMESTAMPTZ audit data |

The table declares no PRIMARY KEY, FOREIGN KEY, CHECK, UNIQUE, or NOT NULL
constraints. All columns are nullable at the database level. A separate sequence
provides automatic IDs without the implicit NOT NULL behavior of BIGSERIAL.
Database types, numeric precision, and timestamp defaults remain.

Non-unique indexes: `idx_fine_org_id` (organization/Fine ID),
`idx_fine_org_date` (organization/date), and `idx_fine_org_employee`
(organization/employee). No existing table is altered.

The request to remove constraints applies to the SQL DDL. Existing API field
validation, employee/organization validation, authentication, and file-pointer
ownership remain in place. The API retains these conditional form rules:

| Fine On | Accountability | Payment Authority | Amount / split |
| --- | --- | --- | --- |
| DRIVER | DRIVER | URBAN_EXPRESS | Required stored values; amount > 0, split > 0 and <= 100 |
| DRIVER | DRIVER | DRIVER | Both NULL |
| DRIVER | URBAN_EXPRESS | Either authority | Both NULL |
| EMPLOYEE | NULL | NULL | Both NULL |

Every branch requires the selected employee FK. Numeric values support up to two
decimal places; excess nonzero fractional digits are rejected rather than
silently changed. For example, `amount_paid = 0.01`, `recovery_split = 25`
are stored directly. This does not calculate installments.

### API contract

All endpoints require the existing bearer authentication and return the existing
`{"success": true, "data": ...}` / error envelope. Organization and audit fields
are bound from authenticated context; an explicitly different body organization
is rejected. A missing or foreign-organization Fine returns not found.

| Method | Route | Purpose |
| --- | --- | --- |
| POST | `/api/v1/fines/create` | Create a Fine |
| POST | `/api/v1/fines/update` | Partially update a Fine |
| POST | `/api/v1/fines/delete` | Delete the Fine record |
| GET | `/api/v1/fines` | List the organization's Fines |
| POST | `/api/v1/fines/get` | Retrieve one Fine with employee ID/name |
| POST | `/api/v1/fines/attachments/upload` | Multipart attachment upload/replacement |
| POST | `/api/v1/fines/attachments/download` | Generate an on-demand signed URL |

Use the existing authenticated **GET `/api/v1/employees`** for the dropdown:
`empl_id_pk` is the selection value, and `employee_id` /
`employee_name` are display fields. No additional Driver lookup is required.

Create example:

```json
{
  "fine_date": "2026-09-29",
  "fine_on": "DRIVER",
  "fine_empl_id_fk": 49,
  "fine_accountability": "DRIVER",
  "payment_authority": "URBAN_EXPRESS",
  "amount_paid": "0.01",
  "recovery_split": "25"
}
```

An EMPLOYEE Fine requires only `fine_date`, `fine_on: "EMPLOYEE"`, and
`fine_empl_id_fk`. Its Driver-specific fields remain NULL.

Get/update/delete/download accept `fine_id` or `fine_id_pk`. Update preserves
omitted fields and applies explicit NULL values; when changing the conditional
combination, explicitly clear fields that no longer apply. For example:

```json
{
  "fine_id": 123,
  "payment_authority": "DRIVER",
  "amount_paid": null,
  "recovery_split": null
}
```

JSON create ignores any supplied `fine_attachment_path`; update preserves the
committed pointer. Audit fields cannot be controlled through ordinary
business-field updates. Returned amounts/splits are decimal **strings** (or NULL)
so their full NUMERIC precision survives JSON serialization.

Deletion physically removes the organization-scoped Fine row. Subsequent
get/update/delete/download requests return not found. There is no status or
cancellation audit data. Previously uploaded Firebase objects are retained;
this endpoint deletes the database record only.

### Firebase behavior

Upload accepts multipart `fine_id` / `fine_id_pk` and `file`, with optional
`storage_folder` and `content_type` metadata. The storage helper confines the
folder to `firebase_upload_files/fine_attachments`; objects use:

```text
firebase_upload_files/fine_attachments/<fine_id>/versions/<unique-version>/<safe-filename>
```

Upload requires an existing Fine with DRIVER accountability and URBAN_EXPRESS
payment under `fine_on = DRIVER`. Existing Employee Firebase initialization and
shared version/path/cleanup helpers are reused without modification.

A Fine row lock serializes uploads, updates, and deletion. The backend commits
the new pointer only after upload succeeds. A pointer-update/commit failure rolls
back the pointer and attempts cleanup of only the new object. Failed replacements
retain the prior pointer/object; successful replacements retain the prior object
as in the existing integrations. Cleanup outcome is returned on failure.

Download verifies organization ownership, pointer presence, and object existence,
then returns a V4 signed GET URL, `expires_at`, `expires_in_seconds: 900`,
filename, content type, Fine PK, and current pointer. Signed URLs are never
stored or included in ordinary Fine lists. Existing attachments remain
downloadable while their Fine record exists, including after a form-combination
change; that change does not rewrite the attachment pointer.

### Verification SQL

Bind `:org_id` and `:fine_id` to the organization and Fine under review:

```sql
SELECT f.fine_id_pk, f.fine_empl_id_fk, e.employee_id, e.employee_name,
       f.fine_date, f.fine_on, f.fine_accountability, f.payment_authority,
       f.amount_paid, f.recovery_split
FROM fine_master f
JOIN employee_master e
  ON e.empl_id_pk = f.fine_empl_id_fk
 AND e.empl_org_id_fk = f.fine_org_id_fk
WHERE f.fine_org_id_fk = :org_id
  AND f.fine_id_pk = :fine_id;

SELECT fine_id_pk, fine_attachment_path,
       created_by, created_at, updated_by, updated_at
FROM fine_master
WHERE fine_org_id_fk = :org_id AND fine_id_pk = :fine_id;
```

There is intentionally no installment or payroll-join verification query for
the revised scope.

### Implementation files

All paths below are relative to the repository root.

Created:

```text
app_backend/services/service_02_hr_payroll/data/20260929_add_fines_module.sql
app_backend/services/service_02_hr_payroll/logic/fine_master_common.py
app_backend/services/service_02_hr_payroll/logic/fine_master_create_data.py
app_backend/services/service_02_hr_payroll/logic/fine_master_get_data.py
app_backend/services/service_02_hr_payroll/logic/fine_master_update_data.py
app_backend/services/service_02_hr_payroll/logic/fine_master_delete_data.py
app_backend/services/service_02_hr_payroll/integrations/firebase_fine_attachment_upload.py
app_backend/services/service_02_hr_payroll/integrations/firebase_fine_attachment_download.py
app_backend/services/service_02_hr_payroll/logic/test_fine_management_logic.py
app_backend/services/service_02_hr_payroll/logic/test_fine_management_postgres.py
```

Modified:

- `app_backend/services/service_02_hr_payroll/api/main.py`: payload models,
  authentication binder, and seven routes.
- `app_backend/services/service_02_hr_payroll/README.md`: this contract,
  migration guidance, implementation inventory, and verification evidence.

The consolidated `app_backend/services/main.py` is unchanged; its existing
route inclusion exposes each new route once. Payroll, Fleet, shared Firebase,
Workflow Engine, and frontend programs are unchanged. The pre-existing local
change in `app_backend/services/auth_context.py` was left untouched.

### Tests executed

| Suite | Result |
| --- | --- |
| `logic.test_fine_management_logic` | 12 passed |
| `logic.test_fine_management_postgres` | 39 passed |
| Existing `logic.test_payroll_module_comprehensive` | 39 passed |
| Existing `service_07_alerts_wf_engine.tests.test_firebase_current_pointer_corrections` | 9 passed |
| Existing `logic.test_payroll_module_logic` | 3 passed |
| Total | **102 passed, 0 failed** |

The 39 PostgreSQL tests run the actual migration, CRUD queries, consolidated API,
and payroll isolation checks in a unique schema of a disposable local database.
They verify idempotent DDL, absence of table constraints and removed fields, all-employee selection,
organization isolation, exact small/large numeric storage, audit ownership,
partial updates, physical deletion, rollback, concurrent updates, and Firebase
replacement/cleanup/signed URL contracts. Firebase calls are mocked.

The payroll isolation test stores Fine data exceeding salary, processes Monthly
Payroll, and confirms `fine_deduction = 0`, `total_deduction = 0`, and unchanged
net salary. Creating/updating/deleting Fines remains independent of
PROCESSED/APPROVED/PAID payroll status. A separate test confirms ordinary manual
`FINE_DEDUCTION` adjustment approval still works.

From the repository root, run the new tests with:

```bash
FINE_TEST_DATABASE_URL=postgresql://USER@127.0.0.1:PORT/ue_fines_tests \
RAILWAY_DB_URL=postgresql://invalid:invalid@127.0.0.1:1/fines_tests \
.venv/bin/python -m unittest \
  app_backend.services.service_02_hr_payroll.logic.test_fine_management_logic \
  app_backend.services.service_02_hr_payroll.logic.test_fine_management_postgres
```

Without `FINE_TEST_DATABASE_URL`, PostgreSQL tests are explicitly skipped.
The test URL must be PostgreSQL on localhost/127.0.0.1 with a database name ending
in `_tests`; every run creates and removes a unique schema. The production
`RAILWAY_DB_URL` is not used for these tests.

No implementation dependency remains unresolved. Deployment still requires the
migration and the existing Firebase environment configuration. Live Firebase
connectivity and live-data scripts were not exercised; tests used fake storage
and the isolated database. The installed Starlette test client emits an existing
httpx deprecation warning, which does not affect the passing results.


## Penalties Management

See [the implementation report and API contract](PENALTIES_IMPLEMENTATION.md) for
the Penalties CRUD/Firebase module, migration, endpoints, and test results. Payroll
linkage is handled by the frontend as requested.


## Independent Payroll deduction component

The existing `payroll_employee_detail.penalty_deduction` is supported by the
Payroll detail update API, detail reads, shared calculations and insert handling.
See [implementation, API contract, migration and test evidence](PAYROLL_PENALTY_DEDUCTION_IMPLEMENTATION.md).
No Penalties-to-Payroll integration is included. The expression migration must be
applied before deploying these application changes; it does not add the column.
