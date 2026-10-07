# Payroll `penalty_deduction` compatibility implementation

Implemented against the user's 56-point instructions and subsequent explicit decision to reject direct penalty edits on CORRECTION runs. This change supports one independent Payroll deduction component. No Penalties Management integration is included.

## A. Database inspection and deployment status

The configured Railway PostgreSQL database was inspected in a **read-only transaction** on 2026-10-01. Its server version is PostgreSQL **18.6**. No production Payroll records or schema objects were changed.

| Property | Observed live definition |
|---|---|
| Column | `public.payroll_employee_detail.penalty_deduction` |
| Type | `NUMERIC(18,2)` |
| Nullable | Yes |
| Default | None |
| Generated | No |
| Dedicated penalty constraint | None |
| Generated `total_deduction` | Present; did **not** include penalty deduction |
| Generated `net_salary` | Present; its ordinary gross-minus-deductions branch did **not** include penalty deduction |
| Correction recovery limit | Present; did **not** include penalty deduction in deduction components |
| Referencing views / materialized views | None found |
| Public stored functions/procedures referencing the detail table | None found |
| Non-internal triggers on the detail table | None found |

The required migration is `data/20261001_include_penalty_deduction_in_payroll.sql`. It was **executed and verified only in disposable local PostgreSQL 18.6 databases**. It remains **unapplied to Railway**. Application deployment and Git operations were not performed.

The migration uses `ALTER COLUMN ... SET EXPRESSION AS` for the two generated columns, preserving the existing column identities, rather than dropping and recreating them. PostgreSQL documents this operation and its rewrite of stored generated values in [ALTER TABLE](https://www.postgresql.org/docs/18/sql-altertable.html). The migration also updates the existing correction-recovery-limit constraint's component sum; it does not change the correction ledger or how recoveries are allocated.

Deployment order:

1. Review and apply the incremental expression migration to the intended database, after the existing correction-recovery schema is present.
2. Deploy these application changes. The application insert now names `penalty_deduction`, which must already exist.
3. Verify a safe Payroll detail update and its readback/run totals in that environment.

The migration **does not add the column**, alter its nullable/default/type properties, run from application startup, or modify Penalties tables. It uses transaction-scoped locks/timeouts. Existing null values remain null and contribute zero to arithmetic. Affected editable non-correction run totals are refreshed from the generated detail columns; the penalty is not added again to aggregate deductions.

Preflight stops the entire migration without changes if the column does not have the expected numeric definition, stored penalties are negative, or nonzero penalties exist on correction/non-editable payroll. The latter requires an explicit reconciliation decision, not an automatic change to historical salaries or correction economics. Existing net-salary constraints also remain enforced. The new bootstrap DDL is for **fresh databases only**, not for deployment over an existing table. Do not reapply the older correction migration after this newer expression migration, because the older historical migration contains the previous formulas.

## B. Files modified and created

All repository changes are under `app_backend/services/service_02_hr_payroll/`.

| File | Change |
|---|---|
| `logic/payroll_module.py` | Modified: amount/update allowlist, independent deduction sum, explicit insert column and default normalization, process default zero, scoped correction edit restriction |
| `api/main.py` | Modified: optional Decimal update input and documentation of the four existing employee-detail response schemas |
| `data/simplified_payroll_module_ddl.sql` | Modified: fresh-schema definition matching the existing nullable column and updated generated/constraint expressions |
| `data/20261001_include_penalty_deduction_in_payroll.sql` | New: incremental expression migration; never adds penalty column |
| `logic/test_payroll_penalty_deduction.py` | New: 25 unit/API/PostgreSQL tests |
| `README.md` | Modified: compatibility/deployment documentation link |
| `OFF_CYCLE_PAYROLL_SEMANTICS.md` | Modified: current deduction formula and approved correction-edit limitation |
| `PAYROLL_PENALTY_DEDUCTION_IMPLEMENTATION.md` | New: this implementation report and Replit handoff |
| `PAYROLL_PENALTY_DEDUCTION_AUDIT.csv` | New: occurrence-by-occurrence source audit |
| `payroll_penalty_deduction_evidence.json` | New: schema observations, executed test results and scope checks |

## C. Operations impacted

| Operation | Impacted? | Change made / verification |
|---|---|---|
| Monthly Payroll create/process | Yes | Shared detail INSERT accepts an explicitly supplied internal component and normalizes absent/null values to Decimal zero. Public process models still accept no individual earning/deduction inputs. Newly processed detail starts at zero. |
| Monthly Payroll get | Compatible already; schema documented | Existing `ped.*` detail queries return the stored field; no projection or serializer discards it. Stored null is returned as null. |
| Monthly Payroll update | Yes | Existing detail update accepts `penalty_deduction`, with existing authenticated organization binding, editable-status rules and nonnegative monetary validation. |
| Partial update / explicit zero | Yes | Omitted or null input preserves the stored amount; explicit zero clears it. A request with no non-null component retains the existing validation error. |
| Monthly Payroll delete/reset | No additional deletion behavior | There is no public Payroll detail-delete/reset route. Existing cancellation changes status and releases adjustments; it preserves the amount. Existing explicit test-fixture deletes are column-independent. |
| Payroll calculation | Yes | Shared Python deduction sum and database expressions include the component once, with null contributing zero. |
| Payroll summaries | No new aggregate field required | Existing `SUM(total_deduction)` and `SUM(net_salary)` reconcile correctly. There are no comparable per-component run totals requiring `total_penalty_deduction`. |
| Payroll approval detail | Compatible | Approval uses existing detail/run reads; component and updated totals survive serialization. No routing or decision behavior changed. |
| Payroll approval execution | Not modified | Existing adapter changes statuses; local integration test verifies component/net preservation. |
| Payroll register | No backend generator found | The HR/Payroll service exposes detail datasets, not a dedicated register generator. These datasets contain the stored component. Frontend register implementation is outside this change. |
| Off-cycle ONE_TIME / FINAL_SETTLEMENT | Yes, shared compatibility | Insert default, stored reads, component arithmetic and serialization work. Existing fixed-salary exclusion and process inputs remain unchanged. The pre-existing shared update path remains as-is except the approved correction-specific restriction. |
| Correction Payroll | Scoped compatibility | Stored amounts are read/preserved; deduction sums include the component. Direct non-null penalty edits through the monthly-detail update route return controlled HTTP 400. No ledger repair, new recovery allocation, automatic original-versus-corrected diff, or adjustment mapping. |
| Copy/clone/re-run | No copy path found | Existing processing rejects duplicate employee detail; tests prove retry does not overwrite a stored penalty. Corrections process selected adjustment deltas rather than cloning original salary components. |
| Exports/downloads | Not applicable as a backend operation | No Payroll CSV/Excel/PDF/export generator or endpoint exists here. Existing DataFrame/JSON detail output preserves the component for downstream consumers. |
| Workflow adapter/payload | Not modified | Payload references run ID/type; no component projection/copy needed. Status-only detail UPDATE remains unchanged. |
| Views/procedures | No changes required | No affected live views/materialized views/public functions/procedures found. |

## D. Exact calculations

With `P = COALESCE(penalty_deduction, 0)`, database `total_deduction` is:

```text
D = unpaid_leave_deduction
  + loan_deduction
  + advance_deduction
  + fine_deduction
  + fuel_deduction
  + salik_deduction
  + darb_deduction
  + charging_cost_deduction
  + other_deduction_amount
  + P
```

Existing gross salary is unchanged:

```text
G = basic_salary + monthly_allowance + accommodation_allowance
  + overtime_amount + bonus_amount + incentive_amount
  + reimbursement_amount + other_earning_amount
```

The existing correction branch is preserved:

```text
net_salary = CASE
  WHEN correction_net_amount <> 0 OR correction_recovery_amount <> 0
    THEN MAX(correction_net_amount, 0)
  ELSE G - D
END
```

Python `_calculate_detail_total_deduction()` sums the same deduction components with Decimal arithmetic; `_calculate_detail_net_salary()` continues using the existing branch above. PostgreSQL rounds stored monetary values to two decimals using its existing NUMERIC behavior.

Run totals remain:

```text
total_deductions = SUM(detail.total_deduction)
total_net_salary (stored) = SUM(detail.net_salary)
```

The new component occurs **once** in D. There is no second subtraction from net salary, no extra penalty addition to aggregate totals, and no mapping to OTHER_DEDUCTION. The backend does not infer whether a caller has duplicated a financial amount in `other_deduction_amount`; those are independent supplied components.

Executed example: `G = 10000.00`, existing deductions `1000.00`, penalty `500.00` → `D = 1500.00`, net `8500.00`. With another unchanged employee earning `10000.00`, run deductions are `1500.00` and run net is `18500.00`.

## E. API contract and Replit instructions

All routes retain their methods, authentication, organization authorization and success/error envelope.

| POST endpoint | Contract change |
|---|---|
| `/api/v1/payroll/monthly/details/update` | Optional `penalty_deduction: Decimal` input; existing identifier aliases supported; correction restriction below |
| `/api/v1/payroll/monthly/details` | Complete detail list includes stored field; now explicitly documented in OpenAPI |
| `/api/v1/payroll/monthly/details/employee` | Complete employee detail includes stored field; now explicitly documented in OpenAPI |
| `/api/v1/payroll/off-cycle/details` | Complete detail list includes stored field; now explicitly documented in OpenAPI |
| `/api/v1/payroll/off-cycle/details/employee` | Complete employee detail includes stored field; now explicitly documented in OpenAPI |

Example update body (substitute real authorized IDs):

```json
{
  "payroll_run_id": 123,
  "payroll_employee_id_fk": 456,
  "penalty_deduction": "250.00"
}
```

Decimal strings and JSON numeric inputs are accepted. Omit the field, or send null alongside another component update, to preserve its value. Send `0` to clear it. Authentication supplies the organization; a supplied organization assertion must match, as before. Use the existing employee-detail read for amount readback; the existing update response remains a confirmation plus run totals.

Illustrative successful read excerpt (the actual DTO also retains its other existing columns):

```json
{
  "success": true,
  "data": {
    "penalty_deduction": 250.00,
    "gross_salary": 10000.00,
    "total_deduction": 250.00,
    "net_salary": 9750.00
  }
}
```

Do not send this field to run-header create or process endpoints. Those endpoints do not accept individual monetary components; they continue creating employee details using existing salary/adjustment rules and zero penalty. Populate the independent component through the supported detail-update flow after processing. Internal explicit detail INSERT handling also preserves supplied values, verified with `250.00`. No new write endpoint has been introduced.

No frontend changes were made. Replit owns future value acquisition/population. Downstream register/export code can use the returned `penalty_deduction` with a “Penalty Deduction” column label; null means zero only when doing arithmetic, not a missing component to derive from another module.

### Approved correction-run limitation

A valid direct penalty edit on an existing CORRECTION run returns **HTTP 400**:

```json
{
  "success": false,
  "error": "Invalid direct edit: penalty_deduction cannot be edited directly on a CORRECTION run because the current correction mechanism does not recalculate the correction ledger/adjustment amounts."
}
```

This includes explicit zero and sending the same non-null value, because both are explicit component update requests. Omitted/null values do not attempt a direct penalty edit and preserve the stored value. The restriction applies to correction runs only, including terminal correction runs; existing authorization still runs first. No successful mutation precedes this rejection.

The broader pre-existing monthly endpoint/run-type boundary for other components is not repaired here. Correction reads return existing stored penalty values and current stored correction display/payable/recovery fields. This task does **not** infer or rewrite an inconsistent historical correction ledger. It is the architectural limitation explicitly accepted by the user, not unfinished work in this task.

The suggested original-penalty `500` → corrected-penalty `300` replacement example is not a supported copy/diff operation in this backend. The processor does not clone the original detail. Tests confirm an original monthly penalty remains `500` and a separately entered correction delta leaves the correction penalty at zero. Direct replacement edits on correction details are rejected as approved.

## F. Executed tests and evidence

Evidence: [payroll_penalty_deduction_evidence.json](payroll_penalty_deduction_evidence.json).

**297 distinct automated tests passed; 0 failed; 0 errors; 0 skipped.** The initial combined execution ran 296 cases. After tightening the terminal-correction error case, the final focused execution ran 64 Payroll cases, including one new test; all passed. Counting distinct cases avoids double-counting reruns.

| Test / verification | Result | Evidence |
|---|---|---|
| Insert supplied `250.00`, public read, update `300.00`, zero | PASS | New PostgreSQL CRUD and explicit insert tests |
| Omitted/null create and update; stored null reads | PASS | Process defaults zero; unrelated updates preserve amount; legacy null does not nullify generated totals |
| Decimal precision | PASS | `250.155` stored as `250.16`; Decimal Python `0.10 + 0.20 = 0.30` |
| Controlled `10000/1000/500` calculation | PASS | Detail deductions `1500`, net `8500`; two-employee run net `18500` |
| All nine existing deduction adjustment components | PASS | Nine `10.10` deductions plus penalty `0.20` → `91.10`, each included once |
| Monthly batch/single processing, duplicate retry, recalculate, cancellation | PASS | No penalty loss; existing duplicate/status behavior retained |
| Approval adapter preservation | PASS | Existing APPROVAL adapter preserves `250` and net `9750` |
| ONE_TIME and FINAL_SETTLEMENT | PASS | Fixed salary zero; `1000` earnings minus `250` penalty yields payable `750`; detail/list serializers preserve component |
| CORRECTION reads and decorators | PASS | Existing fixture penalty `50`, deductions `550`, signed correction `-450`, payable `0`, recoverable `450` |
| CORRECTION direct edits including zero/terminal statuses | PASS | HTTP 400; snapshots prove detail, run, adjustments and recovery ledger unchanged |
| CORRECTION omitted/null field | PASS | Existing value preserved; unrelated existing update behavior retained |
| Organization mismatch and missing authentication | PASS | 403 / 401, no mutation |
| Migration and rerun | PASS | Old expressions upgraded; column identity/type/null/default preserved; no duplicate component inclusion; run totals reconciled |
| Migration protection for nonzero correction/finalized records | PASS | Controlled stop/rollback, original values retained |
| Correction-recovery limit expression | PASS | Includes the independent component without changing allocation code |
| OpenAPI | PASS | Optional Decimal update field and relevant detail response property; no new routes |
| Penalties API/schema baseline comparison | PASS | Penalties paths and their request/response schemas unchanged |
| Existing Payroll regressions | PASS | 39 comprehensive unit/workflow cases plus 3 standalone legacy functions |
| Existing Task 164 PostgreSQL integration | PASS | 57 cases, including off-cycle/run-detail characterization |
| Other existing workflow/authentication regressions | PASS | Remaining regression cases in evidence |
| Production-backed legacy live scripts | NOT RUN | Tests used isolated local databases; existing named-user/live-data scripts were inspected, not executed |
| Backend register/export tests | NOT APPLICABLE | No such backend generator/endpoints found; actual detail datasets and serialization tested |

An initial test-only approval fixture used `APPROVE` instead of the existing adapter action `APPROVAL`; it was corrected without changing production workflow code. All final tests pass.

To run the new tests and the comprehensive Payroll unit suite:

```bash
PAYROLL_COMPONENT_TEST_DATABASE_URL='postgresql://TEST_USER@127.0.0.1:TEST_PORT/payroll_tests' \
.venv/bin/python -m unittest \
  app_backend.services.service_02_hr_payroll.logic.test_payroll_penalty_deduction \
  app_backend.services.service_02_hr_payroll.logic.test_payroll_module_comprehensive -v
```

The explicit URL must be PostgreSQL on loopback with a database name ending `_tests`, and the local test user must be able to create databases. Tests create and drop their own random database. Never substitute the production connection. PostgreSQL 17+ is required for migration testing; this execution used 18.6.

## G. Repository audit and acceptance coverage

[PAYROLL_PENALTY_DEDUCTION_AUDIT.csv](PAYROLL_PENALTY_DEDUCTION_AUDIT.csv) records each matched line in the audited source/test/document set, classified **Updated** or **Not impacted** with the containing function and reason. Searches covered `payroll_employee_detail`, `total_deduction`, `net_salary`, `other_deduction_amount` and the new component; the service was also checked for register/export/copy/clone/report implementations. Generated evidence/audit artifacts are excluded from their own inventory.

Historical migrations, old Task 164 reports, manual SQL scratch notes and production-backed test scripts remain historical/manual references. They are not startup code. In particular, the scratch SQL suggestion to replace `loan_deduction` was not followed: this task adds a distinct component and retains all existing deductions.

| Instruction points | Fulfilment |
|---|---|
| 1–7 | Existing schema inspected; Payroll-only change; existing architecture and every deduction retained |
| 8–14 | Explicit INSERT support, zero default during processing, stored reads, optional Decimal update, omission/null preservation and explicit-zero clearing |
| 15–18 | Python and generated database formulas plus recovery-limit expression include the component exactly once |
| 19–23 | Monthly/all off-cycle shared model, approved correction restriction, run sums without double counting |
| 24–25 | No backend register/export generator found; existing full detail datasets preserve field |
| 26–29 | Field only on existing component-write contract; OpenAPI detail schemas; explicit INSERT list; SELECT-star preservation verified |
| 30–35 | No cloning path; status/delete compatibility; workflow adapter unchanged; no affected views/functions found |
| 36–43 | Ready for future caller-supplied component; no automatic population, adjustment mapping, Penalties/Firebase/workflow changes |
| 44–48 | Executed CRUD, null/zero, calculation, no-double-count, Monthly and Off-Cycle tests |
| 49 | No correction copy/diff model exists; stored value preservation and approved direct-edit rejection tested |
| 50–53 | Existing requests/regressions, component arithmetic, OpenAPI, per-occurrence audit |
| 54–56 | This report, evidence, deployment requirements and narrow-scope confirmation |

## H. Scope confirmation

- No Penalties-to-Payroll integration was implemented.
- No `penalty_master` queries were introduced into Payroll.
- No Penalties API was modified.
- No Penalties Firebase logic was modified.
- No Penalties workflow logic was introduced.
- No penalty recovery scheduler, mapping, adjustment generation or automatic population was added.
- Existing correction recovery/allocation functions and adjustment mapping functions are unchanged.
- No Git operations, deployment, Railway configuration changes or production database writes were performed.

Production activation still requires applying the prepared migration and deploying the application. Local implementation and automated verification are complete; deployed behavior has not been claimed as updated.
