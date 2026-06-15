-- Second database for the vendored storage engine (`agent_trace` DB name).
-- The hub database `tracehub` is created by POSTGRES_DB in compose.
-- See docs/02-IMPLEMENTATION-PLAN.md Phase 0.5 for full stack wiring.

CREATE DATABASE agent_trace;
