# ServiceNow 2026 Setup

## Prerequisites

- Create an External Integration record for ServiceNow with the base URL.
- Attach a Secrets Group containing either a token or username/password.

## Enable Integration

Set the plugin config option:

```python
PLUGINS_CONFIG = {
    "nautobot_ssot": {
        "enable_servicenow2026": True,
    }
}
```

## Client Backend

Select the backend in the job form:

- PySNC
- PySnow

## Mapping File

The default mapping file is located at:

- `nautobot_ssot/integrations/servicenow2026/mapping.yaml`

Update this file or provide a custom path in the job form as needed.

## Required Defaults

If ServiceNow does not provide LocationType, Device Status, or Device Role values,
set defaults in `nautobot_ssot/integrations/servicenow2026/mapping.yaml` under the
`defaults` key for each model.
