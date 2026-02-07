# ServiceNow 2026 Integration

## Overview

The ServiceNow 2026 integration synchronizes data between ServiceNow and Nautobot using the ServiceNow
CMDB tables for locations, companies, device models, and devices. The integration supports both PySNC
(primary) and PySnow (fallback) client backends.

## Job Options

- ServiceNow_to_Nautobot: Pull data from ServiceNow into Nautobot.
- Nautobot_to_ServiceNow: Push data from Nautobot into ServiceNow.

Defaults for missing ServiceNow fields (such as Location status or Device role) are configured in the
`mapping.yaml` file under each model's `defaults` section.

## Object Metadata

ServiceNow identity fields (sys_id, URL, table, instance) are stored as Object Metadata on Nautobot
objects. This metadata is used for matching and for rendering an "Open in ServiceNow" button.
