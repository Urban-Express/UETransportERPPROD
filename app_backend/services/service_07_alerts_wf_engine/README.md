# UETransportERP - ALERTS AND WORKFLOW ENGINE
To integrate with Firebase (image + PDF + documents upload), Truckoom (GPS), Entra (SSON) and Temporal (Workflow).

## Configurable Workflow Routing

New workflow requests use the central configuration-driven workflow engine in this service. Routing is stored in PostgreSQL through workflow definitions, versions, nodes, edges, runtime instances, and runtime steps.

Apply the additive migration in:

`app_backend/services/service_07_alerts_wf_engine/data/workflow_engine_configurable_routing_ddl.sql`

Frontend/API contract details are documented in:

`app_backend/services/service_07_alerts_wf_engine/WORKFLOW_ENGINE_CONTRACT.md`

Existing module-specific workflow request tables are retained for compatibility. New rows are linked to central runtime instances with `routing_source = CONFIGURED_WORKFLOW`; older pending rows remain assigned to their stored legacy approver.
