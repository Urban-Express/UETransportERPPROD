Penalties Management — implementation report

Implemented under `app_backend/services/service_02_hr_payroll/` on 2026-09-30.

**Scope and source of requirements**

The user's direct request limits this delivery to basic CRUD and Firebase, with
Penalty-to-Payroll linkage handled by the frontend. The user confirmed that this
is a new **Penalties** module. The full requirements document supplies the fields,
conditional rules, employee linkage, authentication, file-pointer ownership, and
seven endpoint paths. Its backend recovery scheduling, adjustment generation,
period checks, processed-recovery guards, and payroll-engine changes are outside
the directly requested scope.

`penalty_amount` and `recovery_split_percentage` are stored values. There is no
backend installment calculation, recovery table, generated payroll adjustment,
or payroll status lookup. Update and delete operate on the Penalty record only.
Frontend payroll linkage and any associated recovery restrictions remain outside
this backend implementation. No workflow, frontend, or consolidated API changes
were made.

**A. Files created**

All paths are relative to the repository root.

| Path | Purpose |
| --- | --- |
| `app_backend/services/service_02_hr_payroll/data/20260930_add_penalties_module.sql` | Transactional, idempotent Penalty table creation |
| `app_backend/services/service_02_hr_payroll/logic/penalty_master_common.py` | Field, identity, employee/organization validation and scoped row lookup |
| `app_backend/services/service_02_hr_payroll/logic/penalty_master_create_data.py` | Create |
| `app_backend/services/service_02_hr_payroll/logic/penalty_master_get_data.py` | List and get, including employee display fields |
| `app_backend/services/service_02_hr_payroll/logic/penalty_master_update_data.py` | Partial update with row locking and conditional clearing |
| `app_backend/services/service_02_hr_payroll/logic/penalty_master_delete_data.py` | Organization-scoped deletion |
| `app_backend/services/service_02_hr_payroll/integrations/firebase_penalty_attachment_upload.py` | Versioned upload and committed database pointer |
| `app_backend/services/service_02_hr_payroll/integrations/firebase_penalty_attachment_download.py` | Signed download URL from stored pointer |
| `app_backend/services/service_02_hr_payroll/logic/test_penalty_management_logic.py` | Offline validation and API contract tests |
| `app_backend/services/service_02_hr_payroll/logic/test_penalty_management_postgres.py` | Isolated PostgreSQL, API, and fake-Firebase integration tests |
| `app_backend/services/service_02_hr_payroll/PENALTIES_IMPLEMENTATION.md` | This report and API contract |

**B. Files modified**

| Path | Change |
| --- | --- |
| `app_backend/services/service_02_hr_payroll/api/main.py` | Penalty imports, request models, authenticated binder, and seven routes |
| `app_backend/services/service_02_hr_payroll/README.md` | Link to this report |

**C. Database changes**

The migration creates only `public.penalty_master`, its BIGSERIAL sequence, the
primary-key index, and three query indexes. It does not alter existing tables.

| Column(s) | SQL type / rule |
| --- | --- |
| `penalty_id_pk` | BIGSERIAL PRIMARY KEY |
| `penalty_org_id_fk` | BIGINT NOT NULL; FK to `organization_master.org_id_pk` |
| `penalty_empl_id_fk` | BIGINT NOT NULL; FK to `employee_master.empl_id_pk` |
| `penalty_date` | DATE NOT NULL |
| `penalty_reason` | TEXT NOT NULL; non-whitespace reason required |
| `warning_letter_issued` | BOOLEAN NOT NULL DEFAULT FALSE |
| `warning_letter_date` | Nullable DATE |
| `warning_letter_accepted` | Nullable BOOLEAN |
| `penalty_attachment_path` | Nullable TEXT; backend-owned object path |
| `financial_implication` | BOOLEAN NOT NULL DEFAULT FALSE |
| `penalty_amount` | Nullable NUMERIC(18,2) |
| `recovery_split_percentage` | Nullable NUMERIC(5,2) |
| `created_by`, `updated_by` | Nullable VARCHAR(100); authenticated principal |
| `created_at` | TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP |
| `updated_at` | Nullable TIMESTAMP; set on update/upload |

BIGINT foreign keys match the existing Employee/Organization BIGSERIAL keys.
Both foreign keys use ON UPDATE CASCADE / ON DELETE RESTRICT. Application
validation additionally requires the employee to belong to the authenticated
organization; a foreign key alone cannot establish this.

CHECK constraints require a meaningful reason, valid warning-letter conditional
values, and valid financial conditional values. A warning letter requires its date
and accepted flag; `false` is a valid accepted value. Its attachment is optional.
Financial values require amount > 0 and a percentage from 0 through 100 inclusive.
Non-finite numbers are rejected. Decimal input with nonzero precision beyond two
fractional places is rejected instead of silently rounded.

Indexes:

- `idx_penalty_org_date`: organization + penalty date.
- `idx_penalty_org_employee`: organization + employee.
- `idx_penalty_employee_date`: employee + penalty date.

When either branch is false, its dependent values are cleared. Disabling the
warning letter also clears the attachment pointer. Existing Firebase objects are
retained. Create ignores any client attachment pointer; ordinary update cannot
replace the pointer.

**D. API inventory**

All routes use the existing bearer authentication and service response envelopes:
`{"success": true, "data": ...}` or `{"success": false, "error": "..."}`.
Organization and audit identity come from authenticated context. An explicitly
mismatched body organization is rejected. Decimal output uses strings to preserve
precision, and dates/timestamps use the shared JSON encoder.

| Method | Path | Request | Purpose / success data |
| --- | --- | --- | --- |
| GET | `/api/v1/penalties` | No body | List of organization-scoped Penalty records |
| POST | `/api/v1/penalties/get` | `PenaltyMasterGetPayload` | One Penalty record |
| POST | `/api/v1/penalties/create` | `PenaltyMasterPayload` | Created record and message |
| POST | `/api/v1/penalties/update` | `PenaltyMasterUpdatePayload` | Updated record and message |
| POST | `/api/v1/penalties/delete` | `PenaltyMasterDeletePayload` | `penalty_id_pk` and message |
| POST | `/api/v1/penalties/attachments/upload` | Multipart `penalty_id` or `penalty_id_pk`, plus `file` | Committed pointer, previous pointer, version, filename, content type, storage metadata |
| POST | `/api/v1/penalties/attachments/download` | `PenaltyAttachmentDownloadPayload` | Pointer, filename, content type, signed URL, expiry |

Get/update/delete/download accept `penalty_id` or `penalty_id_pk`. Penalty records
include all stored fields plus `employee_id` and `employee_name`. Continue using
the existing GET `/api/v1/employees` for employee selection, storing `empl_id_pk`
in `penalty_empl_id_fk`.

Create example (replace the example employee key with an employee in the caller's
organization):

```json
{
  "penalty_empl_id_fk": 1,
  "penalty_date": "2026-09-30",
  "penalty_reason": "Repeated late attendance",
  "warning_letter_issued": true,
  "warning_letter_date": "2026-09-30",
  "warning_letter_accepted": false,
  "financial_implication": true,
  "penalty_amount": "1000.00",
  "recovery_split_percentage": "50"
}
```

For a Penalty without a warning letter or financial implication, send the employee,
date, reason, and both branch flags as `false`; dependent fields can be omitted.

Update is partial, following the existing Fines convention: omitted fields are
preserved; explicit nulls are validated. For example, this clears warning-letter
fields and the database attachment pointer:

```json
{"penalty_id": 123, "warning_letter_issued": false}
```

Changing a branch from false to true requires its dependent fields in that update.
Missing records and foreign-organization record IDs return 404. Business validation
uses the existing 400 mapping; request-model errors return 422. Deletion removes the
Penalty row and retains previously uploaded objects.

Both HR/Payroll and the consolidated ERP API expose each new route exactly once.
Comparison with HEAD confirmed all 50 existing HR/Payroll paths and all 30 existing
request-model schemas are unchanged.

**E. Payroll integration boundary**

Per the direct request, the frontend owns Penalty-to-Payroll linkage. This module
stores the amount and percentage without generating installments, creating or
cancelling payroll adjustments, inspecting payroll periods, or modifying monthly,
off-cycle, or workflow code. Consequently this backend does not enforce guards
based on payroll processing status. The original document's recovery-specific
acceptance criteria and test matrices do not apply to the simplified delivery.

**F. Firebase integration**

The upload requires `warning_letter_issued = true`, locks the scoped Penalty row,
and uploads a new versioned object under:

```text
firebase_upload_files/penalty_attachments/<penalty_id>/versions/<version_id>/<filename>
```

Only after upload success does it update `penalty_attachment_path` and commit.
If the pointer update fails, the transaction rolls back and the shared helper
cleans up the newly uploaded object. Previous objects are retained. Row locking
serializes upload with Penalty update/delete, including warning-letter removal.
The folder and stored pointer are backend controlled.

Download reads the scoped record's current pointer, checks the Firebase blob,
and generates a v4 GET signed URL expiring after 900 seconds. The signed URL is
returned on demand and never persisted. Firebase initialization reuses the
existing Employee integration and its existing environment configuration.

**G. Test results**

The Penalties suite passed **31/31** tests: 12 offline validation/API-contract tests
and 19 integration tests on disposable local PostgreSQL 16.15. Firebase was replaced
with a test double; no real Firebase uploads or signed requests were performed.
The new migration was applied and rerun while retaining an existing test record.
Tests used the repository's Employee Master DDL, isolated schemas, and disposable
employees from two organizations.

| Suite | Result | Evidence |
| --- | --- | --- |
| `test_penalty_management_logic.py` | PASS — 12 | Conditional validation, decimals, identity binding, models, OpenAPI |
| `test_penalty_management_postgres.py` | PASS — 19 | PostgreSQL CRUD, constraints, migration rerun, API, organization isolation, attachment failures, concurrency |
| Existing `test_payroll_module_comprehensive.py` | PASS — 39 | Monthly/off-cycle payroll, adjustments, correction recovery, workflow, API |
| Existing `test_fine_management_logic.py` | PASS — 12 | Fines validation and existing route contracts |
| Existing `test_firebase_current_pointer_corrections.py` | PASS — 9 | Employee and shared file-pointer CRUD/upload/download behavior |
| Existing `test_payroll_module_logic.py` | PASS — 3 | Aggregation and monthly/off-cycle salary snapshots |
| Existing `test_fine_management_postgres.py` | 38 PASS, 1 existing FAIL | Fines CRUD/API/Firebase, Employee lookup, manual adjustment and monthly payroll; schema expectation mismatch below |
| Existing OpenAPI comparison | PASS | 50 existing paths and 30 existing schemas unchanged |
| `git diff --check` | PASS | No whitespace errors |

Total automated cases: **132 passed, 1 pre-existing failure** (excluding the
additional baseline reproduction of that same failure).

The existing failure is
`FinePostgresTests.test_database_has_no_constraints_or_removed_fields`:
`AssertionError: 3 != 0`. The test expects zero constraints while the committed
Fines migration declares a primary key and two foreign keys. The same failure
was reproduced with the original HEAD HR/Payroll API; both the Fines migration
and its test were verified byte-for-byte unchanged from HEAD. These unrelated
files were not modified.

Run the new tests from the repository root:

```bash
.venv/bin/python -m unittest -v \
  app_backend.services.service_02_hr_payroll.logic.test_penalty_management_logic

PENALTY_TEST_DATABASE_URL='postgresql+psycopg2://USER@127.0.0.1:PORT/penalties_tests' \
.venv/bin/python -m unittest -v \
  app_backend.services.service_02_hr_payroll.logic.test_penalty_management_postgres
```

The PostgreSQL suite refuses non-loopback hosts and database names not ending in
`_tests`. It creates and removes its own isolated schema. It never falls back to
the application database URL.

Individual test results follow, captured from the test runner output.

**New Penalties tests**

| Test | Result |
| --- | --- |
| `test_binding_rejects_foreign_organization` | PASS |
| `test_both_id_aliases` | PASS |
| `test_dates_and_decimal_values` | PASS |
| `test_decimal_storage_boundaries` | PASS |
| `test_disabled_branches_clear_dependent_values` | PASS |
| `test_invalid_values` | PASS |
| `test_missing_required_fields` | PASS |
| `test_models_do_not_accept_attachment_pointer_as_business_data` | PASS |
| `test_routes_and_openapi_are_exposed_once_in_both_apps` | PASS |
| `test_untrusted_organization_and_actor_are_not_used` | PASS |
| `test_update_binding_preserves_omitted_fields_and_explicit_nulls` | PASS |
| `test_warning_and_financial_branches_are_independent` | PASS |
| `test_all_reads_writes_and_attachments_are_org_scoped` | PASS |
| `test_amount_precision_and_all_split_boundaries` | PASS |
| `test_api_crud_multipart_and_error_envelopes` | PASS |
| `test_api_requires_authentication` | PASS |
| `test_api_validation_and_foreign_organization_errors` | PASS |
| `test_attachment_requires_warning_letter_and_existing_blob` | PASS |
| `test_create_employee_display_and_backend_owned_fields` | PASS |
| `test_create_rolls_back_if_response_read_fails` | PASS |
| `test_database_constraints_and_restricted_employee_delete` | PASS |
| `test_delete_financial_and_nonfinancial_penalties` | PASS |
| `test_failed_pointer_update_rolls_back_and_cleans_new_blob` | PASS |
| `test_failed_upload_preserves_existing_pointer` | PASS |
| `test_financial_toggle_clears_values_and_reenable_requires_them` | PASS |
| `test_invalid_or_foreign_employee_create_and_update_rejected` | PASS |
| `test_migration_rerun_preserves_data_and_schema_is_minimal` | PASS |
| `test_partial_update_preserves_pointer_and_changes_financial_fields` | PASS |
| `test_versioned_upload_signed_download_and_pointer_ownership` | PASS |
| `test_warning_disable_waits_for_upload_and_clears_committed_pointer` | PASS |
| `test_warning_toggle_clears_pointer_and_requires_fields_to_reenable` | PASS |

**Existing isolated regression tests**

| Test | Result |
| --- | --- |
| `test_consolidated_api_contains_payroll_routes` | PASS |
| `test_hr_payroll_api_endpoint_calls_logic_function` | PASS |
| `test_hr_payroll_workflow_api_endpoint_calls_workflow_function` | PASS |
| `test_off_cycle_source_business_errors_return_4xx` | PASS |
| `test_all_correction_deduction_types_are_preserved_with_signed_net` | PASS |
| `test_all_correction_earning_types_set_signed_positive_correction_net` | PASS |
| `test_cancel_off_cycle_run_releases_adjustments_and_cancels_recovery` | PASS |
| `test_cancel_recoveries_marks_applied_ledger_cancelled` | PASS |
| `test_cancelled_or_paid_run_cannot_be_processed` | PASS |
| `test_corr_2026_08_named_employee_totals_return_signed_net_correction` | PASS |
| `test_correction_deduction_greater_than_available_recovery_does_not_consume_adjustment` | PASS |
| `test_correction_deduction_only_records_recovery_without_salary_offset` | PASS |
| `test_correction_header_period_source_ambiguity_requires_explicit_source` | PASS |
| `test_correction_header_period_source_missing_returns_clean_business_error` | PASS |
| `test_correction_mixed_earning_and_deduction_recovers_only_shortfall` | PASS |
| `test_correction_multiple_deductions_for_one_employee_are_preserved` | PASS |
| `test_correction_process_uses_header_period_source_without_frontend_source_fields` | PASS |
| `test_correction_recovery_insert_failure_does_not_mark_adjustments` | PASS |
| `test_correction_source_selector_defaults_to_header_period` | PASS |
| `test_create_payroll_run_conflicts_with_cancelled_duplicate_code` | PASS |
| `test_create_payroll_run_conflicts_with_completed_duplicate_code` | PASS |
| `test_create_payroll_run_rejects_code_longer_than_schema_limit` | PASS |
| `test_create_payroll_run_reuses_matching_draft_after_concurrent_insert` | PASS |
| `test_create_payroll_run_reuses_matching_draft_header` | PASS |
| `test_create_payroll_run_unique_violation_returns_clean_conflict` | PASS |
| `test_duplicate_adjustment_processing_is_blocked` | PASS |
| `test_duplicate_employee_in_run_is_blocked` | PASS |
| `test_monthly_payroll_with_fixed_salary_only` | PASS |
| `test_monthly_payroll_with_overtime_and_transport_deductions` | PASS |
| `test_multiple_adjustments_of_same_type_are_summed` | PASS |
| `test_multiple_sequential_corrections_respect_remaining_source_balance` | PASS |
| `test_off_cycle_bonus_only_keeps_fixed_salary_zero` | PASS |
| `test_off_cycle_earning_only_consumes_adjustment_without_fixed_salary` | PASS |
| `test_off_cycle_zero_net_consumes_adjustments_without_prior_balance` | PASS |
| `test_payroll_run_totals_are_returned_from_database_generated_columns` | PASS |
| `test_rejected_workflow_keeps_payroll_run_processed` | PASS |
| `test_self_approval_workflow_approves_run` | PASS |
| `test_sequential_approval_workflow_creates_pending_request` | PASS |
| `test_unauthorized_approval_attempt_is_rejected` | PASS |
| `test_all_original_driver_combinations` | PASS |
| `test_client_audit_actor_is_not_trusted_by_logic` | PASS |
| `test_client_organization_is_not_trusted_by_logic` | PASS |
| `test_employee_branch_has_selected_employee` | PASS |
| `test_employee_branch_rejects_driver_only_fields` | PASS |
| `test_hidden_payment_values_are_rejected` | PASS |
| `test_invalid_values` | PASS |
| `test_missing_fields` | PASS |
| `test_new_fine_programs_do_not_depend_on_payroll_or_fleet` | PASS |
| `test_routes_are_in_consolidated_app_once` | PASS |
| `test_update_binding_preserves_omitted_and_explicit_null_fields` | PASS |
| `test_values_are_stored_without_installment_calculation` | PASS |
| `test_create_operations_ignore_public_current_path_fields` | PASS |
| `test_downloads_resolve_current_database_pointer` | PASS |
| `test_failed_pointer_update_deletes_only_new_uncommitted_blob` | PASS |
| `test_normal_crud_updates_ignore_stale_path_fields` | PASS |
| `test_ordinary_updates_cannot_revert_pointer_committed_during_inflight_update` | PASS |
| `test_standalone_uploads_use_unique_paths_and_keep_previous_objects` | PASS |
| `test_trusted_workflow_replay_promotes_staged_paths_for_contract_ap_ar` | PASS |
| `test_upload_not_found_returns_before_firebase_upload` | PASS |
| `test_versioned_blob_path_generation_and_storage_folder_hardening` | PASS |

**Existing Fines PostgreSQL regression tests**

| Test | Result |
| --- | --- |
| `test_all_conditional_combinations_and_employee_branch` | PASS |
| `test_api_crud_auth_audit_and_explicit_null_update` | PASS |
| `test_api_org_isolation_for_list_get_update_delete_and_attachments` | PASS |
| `test_api_preserves_large_decimal_amount_exactly` | PASS |
| `test_api_rejects_forged_org_and_invalid_employee` | PASS |
| `test_api_requires_authentication_on_all_routes` | PASS |
| `test_attachment_commit_failure_rolls_back_pointer_and_cleans_blob` | PASS |
| `test_attachment_eligibility_and_deleted_fine` | PASS |
| `test_attachment_missing_fine_pointer_and_object` | PASS |
| `test_attachment_pointer_failure_cleans_only_new_blob` | PASS |
| `test_attachment_replacement_and_update_preserve_both_changes` | PASS |
| `test_attachment_request_validation_and_missing_fine` | PASS |
| `test_attachment_upload_and_download_endpoints` | PASS |
| `test_attachment_upload_download_signed_expiry_and_same_filename_replacement` | PASS |
| `test_attachment_upload_failure_preserves_previous_pointer` | PASS |
| `test_concurrent_partial_updates_preserve_both_changes` | PASS |
| `test_create_and_update_roll_back_on_error` | PASS |
| `test_create_forces_pointer_and_audit_fields` | PASS |
| `test_create_uses_employee_pk_without_driver_qualification` | PASS |
| `test_cross_org_update_and_delete_are_rejected` | PASS |
| `test_database_has_no_constraints_or_removed_fields` | FAIL |
| `test_delete_non_payment_fine` | PASS |
| `test_delete_removes_record_and_keeps_uploaded_object` | PASS |
| `test_deleted_fine_cannot_be_updated` | PASS |
| `test_existing_employee_lookup_returns_all_org_employees` | PASS |
| `test_existing_manual_fine_adjustment_still_works` | PASS |
| `test_get_missing_and_foreign_fines` | PASS |
| `test_historical_fine_remains_readable_after_employee_details_change` | PASS |
| `test_invalid_or_foreign_employee_is_rejected_without_writes` | PASS |
| `test_invalid_update_preserves_original` | PASS |
| `test_list_is_org_scoped_and_includes_employee_names` | PASS |
| `test_migration_is_idempotent_and_has_no_recovery_table` | PASS |
| `test_monthly_payroll_does_not_consume_fine_data` | PASS |
| `test_no_editable_fields_or_missing_fine_id` | PASS |
| `test_partial_update_preserves_omitted_fields_and_pointer` | PASS |
| `test_repeated_update_does_not_duplicate_fines` | PASS |
| `test_small_amount_and_split_are_persisted_exactly` | PASS |
| `test_update_between_conditional_branches` | PASS |
| `test_update_employee_amount_split_and_date` | PASS |

**H. Regression coverage and limits**

Retested Employee Master pointer handling and organization-scoped lookup, Employee
Firebase pointer replacement/download, existing Fines, monthly payroll, off-cycle
payroll, payroll adjustments, correction recovery, payroll workflow logic, and
existing API contracts. Existing scripts that directly use application/live
credentials were not run. Regression tests used stubs or isolated local PostgreSQL.

**I. Deployment and remaining issues**

The code and SQL are ready for review. The migration has **not** been applied to
production, no production records were modified, and no deployment was performed.
Apply `data/20260930_add_penalties_module.sql` to the intended database before
using the new endpoints. It uses BEGIN/COMMIT and IF NOT EXISTS; it does not repair
or alter an existing table created from a different schema.

Real Firebase credentials, bucket access, and live signed-URL behavior remain
unverified in this delivery. Backend upload/download behavior was exercised with
fake Firebase storage, including success, replacement, missing blob, rollback,
and orphan cleanup.

The existing Fines constraint-test mismatch remains unresolved as described above.
There are no outstanding Penalties endpoint or business-field clarifications.
