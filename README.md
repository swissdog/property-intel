# property-intel

JARVIS v2 property intelligence module — Finnish real-estate data ingestion, enrichment, and analytics.

## Quick start

```bash
cp .env.template .env
# Edit .env with real credentials
uv sync
JARVIS_PROPERTY_INTEL_SKIP_REGISTRATION=true uv run uvicorn jarvis_property_intel.main:app --port 8031
```
