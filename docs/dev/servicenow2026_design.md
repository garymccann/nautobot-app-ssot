# ServiceNow 2026 Integration Design

## Purpose

This document explains how the ServiceNow 2026 integration works end-to-end in `nautobot-app-ssot`, with emphasis on:

- runtime flow across Jobs, DiffSync adapters, and DiffSync models
- how `nautobot_ssot.contrib` (`NautobotAdapter`/`NautobotModel`) is leveraged
- metadata, mapping, filtering, relationship handling, and failure behavior

It is intended as a developer handoff/reference for safely extending or debugging the integration.

## High-Level Architecture

The integration is implemented in `nautobot_ssot/integrations/servicenow2026/` and has five major layers:

1. Job layer (`jobs.py`)
2. ServiceNow client layer (`client.py`)
3. Data normalization layer (`mapping.py` + `mapping.yaml`)
4. DiffSync adapter/model layer (`diffsync/adapters/*.py`, `diffsync/models/__init__.py`)
5. Metadata/UI layer (`object_metadata.py`, `utils/metadata.py`, `signals.py`, `template_extensions.py`)

At runtime, a Job builds two adapters, computes a diff (`source_adapter.diff_to(target_adapter)`), and optionally executes sync (`source_adapter.sync_to(target_adapter)`), using shared DiffSync models on both sides.

## Runtime Registration and Enablement

- Plugin setting `enable_servicenow2026` controls whether this integration is considered enabled.
- Integration Jobs are auto-registered through `nautobot_ssot/jobs/__init__.py` using `each_enabled_integration_module("jobs")`.
- Integration signals are auto-registered in `NautobotSSOTAppConfig.ready()` using `each_enabled_integration_module("signals")`.
- ServiceNow template extensions are declared at app config level (`template_extensions = "integrations.servicenow2026.template_extensions.template_extensions"`).

## Sync Directions

Two Jobs exist:

- `ServiceNowToNautobot` (`DataSource`): ServiceNow is source, Nautobot is target.
- `NautobotToServiceNow` (`DataTarget`): Nautobot is source, ServiceNow is target.

Both rely on `DataSyncBaseJob.sync_data()` for orchestration: load adapters, diff, optional sync, and timing/logging.

### ServiceNow to Nautobot

Execution path:

1. Load mapping (`mapping.yaml` or custom `mapping_path`).
2. Build `ServiceNowAdapter` as source.
3. Build `TheNautobotAdapter` as target.
4. Diff source to target.
5. If not dry-run, apply create/update/delete on Nautobot through contrib model logic.

Important options:

- `filter_mode`, `root_location_sys_id`, `location_types`, `include_unknown_type` constrain loaded locations.
- mapping `defaults` are cached on the Job (`self.mapping_defaults`) and reused by model fallback logic.

### Nautobot to ServiceNow

Execution path:

1. Build `TheNautobotAdapter` as source.
2. Build `ServiceNowAdapter` as target (loads current ServiceNow state for diffing).
3. Diff source to target.
4. If not dry-run, execute diff operations against the target adapter model methods.

Deletion behavior:

- If `delete_records` is false, job sets `DiffSyncFlags.SKIP_UNMATCHED_DST`, so destination-only records are not deleted.

Implementation note:

- In the current 2026 code path, shared models inherit `NautobotModel` (ORM CRUD for Nautobot), and the 2026 ServiceNow client layer is read-oriented (`iter_table`).
- There is no dedicated ServiceNow create/update/delete implementation in this module set.

## Data Model Contract

Shared DiffSync models are defined in `diffsync/models/__init__.py`:

- `Company` -> `Tenant`
- `Manufacturer` -> `Manufacturer`
- `Platform` -> `Platform`
- `DeviceType` -> `DeviceType`
- `Location` -> `Location`
- `Device` -> `Device`

All use `servicenow_sys_id` as their DiffSync identifier (`_identifiers = ("servicenow_sys_id",)`), so ServiceNow identity is the primary key for synchronization.

`ServiceNowBaseModel` derives from:

- `ObjectMetadataMixin` (integration-specific metadata behavior)
- `NautobotModel` (generic contrib CRUD behavior)

## How Contrib Is Used

`NautobotAdapter` and `NautobotModel` provide most ORM sync mechanics:

- adapter-side loading of model fields, FK traversal, and relationship extraction
- model-side `create()`, `update()`, `delete()` and DB persistence (`validated_save()`)
- deferred handling of foreign keys and many-to-many relationships after object creation
- standardized conversion of DiffSync operations into ORM CRUD exceptions

The ServiceNow integration extends this foundation in three ways:

1. `TheNautobotAdapter` customizes parameter loading for metadata-backed fields and related `*_sys_id` lookups.
2. `ObjectMetadataMixin` extends `NautobotModel` write path to persist annotated fields into `ObjectMetadata`.
3. specific model classes override `_update_obj_with_parameters()` to resolve ServiceNow metadata foreign keys and apply fallback behavior.

## ServiceNow Client Layer

`ServiceNowClient` selects backend by job input:

- `pysnc` -> `PySNCBackend`
- `pysnow` -> `PySnowBackend`

Credential model:

- External Integration must have an HTTP secrets group.
- Valid auth is either token or username/password.
- SSL verification follows `ExternalIntegration.verify_ssl`.

Backend abstraction contract:

- both backends expose `iter_table(table, query)` yielding dictionary records
- client consumers do not care which SDK backend was chosen

## Mapping and Normalization Pipeline

`mapping.yaml` describes each logical model:

- ServiceNow table name (`table`)
- optional `table_query`
- field mappings (`column` or `reference.key`)
- optional `transform`
- optional per-model `defaults`

Pipeline for each ServiceNow table:

1. fetch records via `client.iter_table()`
2. convert each record to model attributes via `map_record()`
3. apply mapping defaults for missing/empty fields
4. inject ServiceNow context fields (`sys_id`, `servicenow_sys_id`, `servicenow_url`, etc.)
5. model-specific normalization in adapter (ordering, dedup suffixes, unresolved ref nulling)
6. instantiate DiffSync model and add to adapter store

Supported transforms include:

- `strip`, `lower`, `upper`, `to_string`
- decimal transforms (`to_decimal`, `to_latitude`, `to_longitude`)

## Adapter Deep Dive

### ServiceNowAdapter

Key behaviors:

- deterministic ordering for all models; locations are parent-before-child ordered
- location filtering modes:
  - `none`
  - `subtree`
  - `types`
  - `subtree+types`
- ancestor inclusion for filtered location sets (parents retained to preserve hierarchy integrity)
- duplicate name handling by appending `(<first 8 chars of sys_id>)` for configured models
- unresolved reference nulling for foreign keys (`tenant_sys_id`, `manufacturer_sys_id`, etc.)

This allows robust ingest even when ServiceNow data is incomplete or references out-of-scope records.

### TheNautobotAdapter

Key behaviors:

- loads only objects with ServiceNow Sys ID metadata by default (`include_without_sys_id=False`)
- maps Nautobot FK relationships back into ServiceNow `*_sys_id` values via metadata lookups
- reads annotated metadata-backed fields (for example `servicenow_sys_id`, `servicenow_url`) into DiffSync parameters

Practical effect: only Nautobot objects already known to this integration participate in outbound sync unless explicitly broadened.

## Object Metadata Strategy

ServiceNow-related metadata types are:

- `ServiceNow Sys ID`
- `ServiceNow URL`

Current sync behavior actively writes annotated fields (`ServiceNow Sys ID` and `ServiceNow URL`).

Metadata lifecycle:

1. `signals.py` ensures metadata types exist and are attached to relevant content types at startup.
2. DiffSync model fields annotated with `ObjectMetadataAnnotation` are intercepted by `ObjectMetadataMixin`.
3. During create/update, annotated values are written to `ObjectMetadata` (currently sys_id/url annotations).
4. `utils/metadata.py` provides read helpers, lookup by sys_id, and ServiceNow URL derivation.
5. `template_extensions.py` uses metadata helpers to render an "Open in ServiceNow" button on object detail pages.

## Model-Specific Relationship and Fallback Logic

Model overrides in `diffsync/models/__init__.py` resolve relationships by ServiceNow sys_id metadata and enforce Nautobot requirements:

- `Platform`/`DeviceType`: resolve manufacturer by metadata
- `Location`: resolve parent and tenant by metadata
- `Device`: resolve location/device_type/platform by metadata

Fallback and guardrails:

- missing location/device type for devices can fall back to generated "SSoT Unknown"/"SSoT Unassigned" objects
- missing role can use mapping defaults (for example `role__name`)
- missing required relationships with no fallback raises `ObjectCrudException`

This makes sync tolerant of partial ServiceNow data while still failing hard when Nautobot constraints cannot be satisfied.

## Failure, Logging, and Safety Behavior

- CRUD failures are wrapped in DiffSync exceptions (`ObjectNotCreated`, `ObjectNotUpdated`, etc.) via contrib.
- Job-level flow supports dry-run, memory profiling, and optional parallel adapter loading.
- diff summaries and structured sync logs are persisted (`Sync`, `SyncLogEntry`).
- `CONTINUE_ON_FAILURE` is enabled by default in `DataSyncBaseJob`, so individual object failures do not necessarily stop the full run.

## Extension Guidelines

When extending this integration:

1. Update `mapping.yaml` and model attributes together.
2. If a field should live in Object Metadata, annotate it with `ObjectMetadataAnnotation`.
3. Add adapter/model override logic only when base contrib behavior is insufficient.
4. Preserve ServiceNow sys_id identity semantics; do not change `_identifiers` lightly.
5. Add tests under `nautobot_ssot/tests/servicenow2026/` for transforms, metadata behavior, and relationship edge cases.

## Quick Mental Model

Think of the integration as:

- a schema-driven extractor/normalizer (`mapping.yaml` + `ServiceNowAdapter`)
- a metadata-driven identity layer (`ObjectMetadata` + `servicenow_sys_id`)
- a contrib-powered ORM writer (`NautobotModel` CRUD + ServiceNow model overrides)

That combination is what keeps synchronization deterministic while handling real-world data gaps.
