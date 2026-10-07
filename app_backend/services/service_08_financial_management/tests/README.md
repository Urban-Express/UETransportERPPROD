# AR acceptance tests

Run from the repository root with the normal project Python dependencies plus
`pip install -r app_backend/services/service_08_financial_management/tests/requirements-test.txt`.
PostgreSQL must run locally; the database login needs `CREATEDB`. Create an empty
database with a name ending in `_tests`. Supply its SQLAlchemy PostgreSQL URL via
`AR_REDESIGN_TEST_DATABASE_URL` using host `127.0.0.1` or `localhost`.

```bash
export AR_REDESIGN_TEST_DATABASE_URL=postgresql+psycopg2://localhost/ue_ar_tests
export AR_REDESIGN_ARTIFACT_DIR=/tmp/ue-ar-redesign-artifacts
PYTHONPATH=. .venv/bin/python -m app_backend.services.service_08_financial_management.tests.run_ar_redesign_tests
```

Use the appropriate local username/port for your PostgreSQL installation. The
runner deliberately overrides the application's `RAILWAY_DB_URL` with an invalid
loopback address before any application import. It never uses the repository's
remote database. Fixtures create a random database, install real AR/contract/
workflow schemas, execute the migrations and controlled seed, and drop that
database afterwards. A test URL pointing elsewhere is rejected. Missing database
configuration is a runner error, not a successful skipped acceptance suite.

Both FastAPI applications and the actual workflow runtime/adapters execute against
PostgreSQL. Authentication uses explicit dependency overrides for the synthetic
principals and removes them to check 401 responses. Firebase operations use the
existing Task 164 versioned fake bucket and actual GCS SDK offline signing. No
operational customer/contract, remote database, or real cloud object is created.

The runner records three suites separately: the accepted 61-test baseline, the 40
existing redesign tests, and six closure tests for approval ownership and document
primary-key resolution. The closure tests check CREATE/UPDATE through both apps and
JSON/multipart, already-pending pre-correction proposals, actual final approver and
database execution timestamp, pending isolation, legacy compatibility and both ID
aliases for document upload/download. Generated evidence includes exact API examples, AR OpenAPI schemas,
the Contract A PDF, its extracted text/hash, workflow outcomes and test IDs. A
cloud/deployment smoke test remains a rollout step; these local tests do not claim
to validate production credentials, IAM, networking or hosting configuration.
