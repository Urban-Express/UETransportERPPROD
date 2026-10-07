# Rollback and rollout

Do not execute these artifacts automatically or against production during development.
Apply `migration.sql`, then the separately reviewed `seed_urban_express.sql` with an explicit
`ar_rollout.urban_express_org_id`. Verify the target database and organization first. The seed
fills a blank existing registration field with the approved trade license; conflicting values
abort the entire seed. It never overwrites maintained tax/invoice configuration on rerun.

Capture historical rows/money using `baseline_verification.sql` before migration. `verification.sql` checks totals, keys,
constraints and indexes afterwards. No historical backfill is included. The six audited rows
must keep their monetary values and remain header-only.

For application rollback, stop line-based submissions and let already pending line workflows
finish or be explicitly rejected/cancelled using the existing workflow. Preserve their files.
Never run the old AR backend against line invoices: it cannot keep their totals/documents in
sync. Revert only after verifying there are no line invoices or pending line proposals, or keep
the compatible new backend while rolling the frontend back. A header-only frontend must not
edit a line-based invoice.

Keep additive tables and columns during rollback. This preserves financial data, snapshots,
configuration and workflow document provenance. Restore the previous application only when
the above checks permit it. Do not drop line/configuration tables, header columns or snapshots
after use. Any later destructive schema removal or backup restoration is a separate reviewed
maintenance operation. Do not delete staged or historical cloud objects as part of rollback.

Configuration maintenance uses controlled organization-scoped SQL/admin database tooling;
no unauthenticated configuration API is introduced. Maintain one active default tax per org,
with optional invoice-date bounds. Select explicit tax codes only from that organization's
active, date-valid records. Nonstandard treatments require zero rate. Updating configuration
does not update approved line tax snapshots. Invoice identity snapshots similarly retain the
original customer/issuer/bank/signatory details; new invoices use the current configuration.
