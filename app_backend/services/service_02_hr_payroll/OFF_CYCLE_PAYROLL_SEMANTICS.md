# Off-cycle payroll approval semantics — Task 164

Implementation baseline: `ede8af78a55c57e4cb10432f00a6264c02c195fc` (`dev`).
This document characterizes existing code. Task 164 changes no payroll calculation, update validation, approval adapter, or payroll schema.

**2026-10-01 compatibility update:** the independent `penalty_deduction` Payroll component now contributes once to deductions after the incremental database migration. It is not an Adjustment mapping and has no Penalties-module integration. See [implementation and deployment notes](PAYROLL_PENALTY_DEDUCTION_IMPLEMENTATION.md). Task 164 itself remains unchanged.

## Sources and producing functions

`logic/payroll_module.py` is the application source of truth. `data/simplified_payroll_module_ddl.sql` defines generated detail columns; `data/20260907_add_payroll_correction_recovery.sql` defines the existing correction migration. A deployed database must already have that existing schema; Task 164 does not run that migration on operational data.

| Producer | Responsibility |
|---|---|
| `_PayrollRunBase.create_payroll_adjustment` | Validates a positive magnitude and derives/validates EARNING/DEDUCTION from adjustment type |
| `_retrieve_approved_unprocessed_adjustments` | Selects organization, employee, year/month, APPROVED, unprocessed adjustments; optionally selected IDs |
| `aggregate_adjustments_by_type` / `map_adjustments_to_payroll_detail` | Sums each adjustment amount into its type's detail column; processing classifies by type |
| `_prepare_employee_snapshot` / `_build_detail_payload` | Builds detail inputs and snapshots |
| `OffCyclePayrollRun.process_selected_employees` | Processes ONE_TIME, CORRECTION and FINAL_SETTLEMENT with `include_fixed_salary=False` |
| `OffCyclePayrollRun._prepare_correction_recovery` | Calculates signed correction and recovery shortfall; rejects shortfalls for non-correction types |
| `_resolve_correction_source_detail` | Resolves an eligible same-organization/employee MONTHLY source; requires an unambiguous source |
| `_available_source_recovery_balance` | Source net minus prior APPLIED recoveries, floored at zero |
| `_allocate_correction_recovery` / `_insert_correction_recovery_entries` | Allocates and records positive recovery amounts against deduction adjustments |
| PostgreSQL generated columns | Calculate detail `gross_salary`, `total_deduction`, `net_salary` |
| `_recalculate_payroll_run_totals` | Sums stored employee detail columns into run totals |
| Off-cycle `_decorate_payroll_run_*` / `_decorate_payroll_detail_*` | Adds aliases and changes correction display values |
| `service_07_alerts_wf_engine/payroll_wf.py` | Submission/approval lifecycle; not a separate payroll recalculation |

## Detail formulas and adjustment inclusion

Let `G = gross_salary`, `D = total_deduction`, `C = correction_net_amount`, `R = correction_recovery_amount`, and `P = net_salary` on a stored `payroll_employee_detail` row.

All three off-cycle processing paths set `basic_salary`, `monthly_allowance` and `accommodation_allowance` to zero, even when the employee master contains recurring salary. Gross is therefore the sum of the selected earning adjustments:

```
G = overtime_amount + bonus_amount + incentive_amount
    + reimbursement_amount + other_earning_amount
D = unpaid_leave_deduction + loan_deduction + advance_deduction
    + fine_deduction + fuel_deduction + salik_deduction + darb_deduction
    + charging_cost_deduction + other_deduction_amount
    + COALESCE(penalty_deduction, 0)
```

`penalty_deduction` is a separate stored component; new processing defaults it to zero, and reads preserve stored nulls/values. Direct penalty edits on existing CORRECTION runs are rejected with HTTP 400 because the current update mechanism does not recalculate their ledger/adjustment amounts. This compatibility restriction does not repair or extend that architecture.

Each consumed `payroll_adjustment.adjustment_amount` already contributes to one of these columns. Never add adjustment amounts again to gross, net, payable or recoverable totals. Unprocessed/unapproved/unselected adjustments are not included. The existing selector treats an empty ID list like no explicit selection; this document does not change that behavior.

`adjustment_amount` is a positive magnitude. `earning_deduction_flag` is `EARNING` or `DEDUCTION`. Earnings increase G; deductions increase D and reduce the economic net. The processor maps by `adjustment_type`; see the separate flag-consistency defect below.

The shared generated `net_salary` column is:

```
IF C != 0 OR R != 0: P = MAX(C, 0)
ELSE:                P = G - D
```

For successful correction processing, C=G-D, including zero. This produces `P=MAX(C,0)` for correction rows. Stored payable detail values cannot be negative.

## Field dictionary

| Requested field | Table.column or API projection | Run types and formula | Meaning / sign / adjustment inclusion |
|---|---|---|---|
| total_gross_salary | payroll_run.total_gross_salary | All types: SUM(detail.gross_salary) | Gross earnings; nonnegative; already includes consumed earning adjustments. Off-cycle excludes recurring fixed salary. |
| total_deductions | payroll_run.total_deductions | All types: SUM(detail.total_deduction) | Positive deduction magnitude; already includes consumed deduction adjustments. |
| total_net_salary (stored) | payroll_run.total_net_salary | All types: SUM(detail.net_salary) | Nonnegative payable amount, including adjustments once. |
| total_net_salary (off-cycle API) | Decorated run response | CORRECTION: total_correction_net_amount; otherwise stored total_net_salary | CORRECTION changes this response field to signed delta. It does not update the database column. |
| total_payable_amount | API only; based on payroll_run.total_net_salary | All off-cycle: SUM(stored detail.net_salary) | Nonnegative cash payable for this batch; preserved before correction response decoration. |
| total_recoverable_amount | API only; alias of payroll_run.total_correction_recovery | All off-cycle: SUM(detail.correction_recovery_amount) | Positive recovery magnitude; normally zero for ONE_TIME/FINAL_SETTLEMENT. |
| total_correction_recovery | payroll_run.total_correction_recovery | SUM(detail.correction_recovery_amount) | CORRECTION: sum of employee shortfalls. Other normal processing paths set detail recovery to zero. Not a further deduction to subtract from an already signed correction. |
| total_correction_net_amount | payroll_run.total_correction_net_amount | SUM(detail.correction_net_amount) | CORRECTION: signed G-D. Other normal processing paths set C=0. |
| net_correction_amount | API only; run alias of total_correction_net_amount; detail alias of correction_net_amount | CORRECTION: SUM(C) or C | Signed delta, positive payment / negative recovery. Already incorporates adjustments. |
| display_total_net_salary | API only | CORRECTION: SUM(C); ONE_TIME/FINAL_SETTLEMENT: SUM(P) | Display value, not a separate calculation or stored column. |
| adjustment_amount | payroll_adjustment.adjustment_amount | Any eligible payroll processing type; positive input mapped into a detail amount column | Input earning/deduction magnitude; included once when consumed. |
| adjustment_amount (recovery ledger) | payroll_correction_recovery.adjustment_amount | Snapshot of the original contributing deduction adjustment | Positive source magnitude; NOT an extra summand. The allocated amount is `recovered_amount`. |
| earning_deduction_flag | payroll_adjustment.earning_deduction_flag | EARNING or DEDUCTION; created consistently with type | Classification, not a numeric sign. |

Run aggregation includes all employee-detail rows belonging to the run; it does not filter by detail status. The recovery ledger limits and records recoveries but is not added separately to the run totals. For newly processed corrections, its APPLIED `recovered_amount` sum reconciles to the detail recovery sum. Cancellation cancels ledger entries; do not treat cancelled historical totals as current payable obligations.

## Replit display mapping

| RUN TYPE | DISPLAY CONCEPT | AUTHORITATIVE BACKEND FIELD | FORMULA | DETAIL RECONCILIATION | SIGN CONVENTION | APPROVER-FACING LABEL |
|---|---|---|---|---|---|---|
| ONE_TIME | Amount payable for this one-time batch | total_payable_amount | SUM(G-D) | SUM(detail.payable_amount), alias of stored detail.net_salary | Nonnegative | One-time amount payable |
| ONE_TIME | Recoverable | total_recoverable_amount | Normally 0 | SUM(detail.recoverable_amount) | Nonnegative; normally zero | Total recoverable |
| CORRECTION | Primary economic delta | net_correction_amount | Total payable - Total recoverable | SUM(detail.net_correction_amount) = SUM(G-D) | Signed | Net correction |
| CORRECTION | Payable portion | total_payable_amount | SUM(MAX(C,0)) | SUM(detail.payable_amount) | Nonnegative | Total payable |
| CORRECTION | Recoverable portion | total_recoverable_amount | SUM(MAX(-C,0)) | SUM(detail.recoverable_amount) | Nonnegative | Total recoverable |
| FINAL_SETTLEMENT | Payable for entered settlement adjustments | total_payable_amount | SUM(G-D) | SUM(detail.payable_amount) | Nonnegative | Settlement amount payable |

### ONE_TIME

`total_net_salary`/`total_payable_amount` represent the entire ONE_TIME batch across included employee rows. They are not the monthly salary plus this batch. Recurring salary is excluded. Non-correction processing rejects an employee whose deductions exceed earnings, so the successful path normally has zero recoverable amount.

### CORRECTION

For each employee: `Correction = Gross - Deductions`, `Payable = MAX(Correction,0)`, `Recoverable = MAX(-Correction,0)`.

The original MONTHLY payroll is used as a recovery basis. The processor does not overwrite the original salary or independently recompute a replacement monthly payroll. Source selection requires the same organization/employee, a different MONTHLY run in PROCESSED/APPROVED/PAID status and an eligible detail. A missing/ambiguous source or insufficient remaining balance fails processing. Source balance is `MAX(source detail.net_salary - SUM(prior APPLIED recovered_amount), 0)`.

A batch may pay one employee while recovering from another. Keep the three figures visible. Example executed in isolated PostgreSQL: +200 and -350 produce payable 200, recoverable 350, net correction -150. Stored `payroll_run.total_net_salary` is 200; the decorated API's `total_net_salary` and `display_total_net_salary` are -150. Detail `net_salary` stays payable; `display_net_salary` provides signed correction display.

### FINAL_SETTLEMENT

The existing implementation processes entered earning/deduction adjustments. It does not independently calculate gratuity, leave settlement/encashment, arrears, or a comprehensive end-of-service settlement. Shared payroll columns and a FINAL_SETTLEMENT run type do not establish those business calculations. The payable amount is authoritative for the entered batch only. A deduction shortfall is rejected by the shared non-correction path.

## Separate backlog defects — deliberately unchanged

1. **Adjustment type/flag consistency on update.** `update_payroll_adjustment()` permits a draft type/flag combination that `create_payroll_adjustment()` would reject. Processing follows type, so a mismatching flag can mislabel its effect. A separate fix should validate the resulting type/flag pair and add regression coverage. Affected fields: adjustment_type, earning_deduction_flag and display interpretation of adjustment_amount.
2. **Monthly detail update run-type boundary.** `update_monthly_employee_payroll_detail()` reaches `_PayrollRunBase.update_employee_payroll_detail()` without a MONTHLY-only guard. It can edit earning/deduction inputs of a draft/processed off-cycle run without recomputing correction amounts/recovery allocations. A separate fix should enforce the monthly boundary; any off-cycle edit capability needs an explicit design. Affected fields: detail earnings/deductions, correction_net_amount, correction_recovery_amount and run totals.

No remediation for either defect is included in Task 164.
