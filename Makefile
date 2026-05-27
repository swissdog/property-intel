# Property-Intel v2 — operational targets.

.PHONY: help setup-fresh fetch-rates fetch-rents fetch-construction \
        fetch-migration fetch-paavo-attributes fetch-bof-loans \
        fetch-flood-risk fetch-all export pipeline-once test test-unit \
        test-integration smoke-e2e stats api

UV ?= uv run python
DB_URL ?= postgresql+asyncpg://property_intel_user:changeme@localhost:5435/jarvis_property_intel
EXPORT_OUT ?= data/exports/$(shell date +%Y-%m-%d)

help:                               ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

setup-fresh:                        ## One-shot setup for a brand-new DB (seed polygons, history, mappings)
	@echo "→ Seeding postal-code polygons (Paavo)"
	$(UV) scripts/seed_postal_areas.py
	@echo "→ Fetching Paavo full demographic attributes"
	$(UV) scripts/fetch_paavo_attributes.py
	@echo "→ Seeding kunta→maakunta lookup"
	$(UV) scripts/seed_municipality_region.py
	@echo "→ Backfilling StatFi 6y apartment-price history"
	$(UV) scripts/backfill_statfi_history.py
	@echo "→ Fetching ECB rates 2020+"
	$(UV) scripts/fetch_interest_rates.py
	@echo "→ Fetching StatFi rent history"
	$(UV) scripts/fetch_statfi_rents.py
	@echo "→ Fetching StatFi construction (2015+)"
	$(UV) scripts/fetch_statfi_construction.py --from 2015
	@echo "→ Fetching StatFi migration (2015-2024)"
	$(UV) scripts/fetch_statfi_migration.py --from 2015 --to 2024
	@echo "→ Backfilling Oikotie listing details"
	$(UV) scripts/backfill_listing_details.py
	@echo "→ Setup complete. Run make stats to verify."

fetch-rates:                        ## Refresh ECB Euribor + MRO/DFR
	$(UV) scripts/fetch_interest_rates.py

fetch-rents:                        ## Fetch new StatFi rent quarters
	$(UV) scripts/fetch_statfi_rents.py

fetch-construction:                 ## Fetch new construction months
	$(UV) scripts/fetch_statfi_construction.py --from 2015

fetch-migration:                    ## Fetch new migration years
	$(UV) scripts/fetch_statfi_migration.py --from 2015

fetch-paavo-attributes:             ## Fetch full Paavo attribute set
	$(UV) scripts/fetch_paavo_attributes.py

fetch-bof-loans:                    ## Fetch BoF monthly housing-loan metrics
	$(UV) scripts/fetch_bof_loans.py

fetch-flood-risk:                   ## Fetch SYKE flood-risk polygons
	$(UV) scripts/fetch_flood_risk.py

fetch-all: fetch-rates fetch-rents fetch-construction fetch-migration fetch-bof-loans  ## Run all fetchers

pipeline-once:                      ## Run hourly pipeline once
	$(UV) scripts/hourly_pipeline.py

backfill-details:                   ## Resumable detail-fetch for listings missing enrichment
	$(UV) scripts/backfill_listing_details.py

export:                             ## Write product bundle to data/exports/<today>/
	$(UV) scripts/export_product_bundle.py --out $(EXPORT_OUT)

api:                                ## Start the module API on localhost:8031
	JARVIS_PROPERTY_INTEL_SKIP_REGISTRATION=true \
	  uv run uvicorn jarvis_property_intel.main:app --host 127.0.0.1 --port 8031

test: test-unit test-integration    ## Run all tests

test-unit:                          ## Unit tests only
	uv run pytest tests/unit/ -v

test-integration:                   ## Integration tests (requires live DB)
	uv run pytest tests/integration/ -v

smoke-e2e:                          ## Live e2e smoke (hits external APIs, writes to a TEST DB)
	uv run python tests/integration/smoke_e2e.py

stats:                              ## Quick DB stats
	@echo "TODO: update docker exec command for v2 PG container"
