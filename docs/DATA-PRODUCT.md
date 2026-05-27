# Property-Intel Data Product

Analyst-ready Finnish residential housing-market data for property investors,
asset managers, banks, and PropTech tooling. Combines hourly listing fetches,
quarterly market aggregates, monthly construction activity, and annual
demographic flows into a single relational schema with REST + CSV access.

---

## What you get

### A. Live listings (hourly refresh)

| Table | Rows | Coverage |
|---|---:|---|
| `property.listing` | 16 k | 19 cities, active + 12-month removed |
| `property.property_asset` | 16 k | 100% postal-coded |
| `property.listing_event` | 23 k | created / price_change / removed |
| `property.listing_price_snapshot` | 10 k+ | daily snapshots of active inventory |

Each listing carries enriched detail (Oikotie `/api/card/{id}`):
**maintenance_fee_eur, financial_fee_eur, water_fee_eur, parking_fee_eur,
sauna_fee_eur, share_of_liabilities_eur, debt_free_price,
apartment_condition_code, heating_method (+ code), building_material,
has_lift, has_sauna, lot_ownership_code, energy_class** (parsed C2018-style code).

### B. Realised transactions (rolling 12 mo)

| Table | Rows | Source |
|---|---:|---|
| `property.transaction` | 30 k | hintatiedot.fi (KVKL) |
| `property.transaction_history` | 36 k | per-field audit trail |

Per-transaction: neighborhood, room_config, building_type, living_area_m2,
debt_free_price, price_per_m2, year_built, floor, elevator, condition,
lot_type, energy_class.

### C. Market aggregates (StatFi quarterly)

| Table | Rows | Coverage |
|---|---:|---|
| `property.area_snapshot` | 64 k | postal-code × quarter × segment, **2020-Q1 → 2025-Q4** |
| `property.rent_snapshot` | 42 k | postal-code × quarter × room-band, **2015-Q1 → 2024-Q2** |

Measures: median_sold_m2, median_ask_m2, dom_median, inventory_count,
price_cut_ratio, income_median, owner_occupancy_ratio (area_snapshot);
median_rent_per_m2, rental_contract_count (rent_snapshot).

### D. Macro & supply/demand signals

| Table | Rows | Granularity |
|---|---:|---|
| `property.interest_rate` | 5 k | Euribor 1M/3M/6M/12M (monthly) + ECB MRO/DFR (daily), 2020+ |
| `property.construction_activity` | 190 k | maakunta × month × phase × building-class, 2015+ |
| `property.migration_activity` | 3 k | municipality × year (population, net migration), 2015+ |

### E. Geometry

| Table | Rows | Source |
|---|---:|---|
| `property.postal_code_area` | 3 026 | Tilastokeskus Paavo (covers all of Finland) |

PostGIS polygons + `property.lookup_postal_code(lat, lon)` SQL function for
reverse-geocoding when source APIs lack postal_code (e.g. Oikotie listing search).

---

## Analytical views

```
property.v_postal_investor_lens         — latest yield + 5y growth per pc
property.v_yield_anomalies              — above-median yield + below-median price
property.v_market_velocity_timeseries   — quarterly DOM + inventory per pc
property.v_supply_demand                — annual completions vs net migration
property.v_national_headline            — Finland-wide quarterly time series
```

These are denormalized; one row = one analytical fact. Yield is computed as
`(median_rent_per_m2 × 12) / median_sold_m2 × 100` using 2-room apartment
proxies (rent room_count_band='2h', sold segment='Kerrostalo kaksiot').

---

## Access

### REST API

`GET /api/v1/intel/<endpoint>` with optional API key (header `X-Api-Key`).

```
/investor-lens         postal_code, municipality_code, min_yield_pct, max_price_m2
/yield-anomalies       limit
/market-velocity       postal_code, quarters_back
/national-headline     quarters_back
/supply-demand         region_code
/rates                 rate_type, from, to
/rents                 postal_code, room_count_band, from_year
/migration             municipality_code, from_year
/construction          region_code, phase, building_class_code, from_year
/listing/{id}/detail   single enriched listing snapshot
```

All read-only. Responses are JSON, filterable, and bounded by `limit`/`offset`.

### CSV bundle

`scripts/export_product_bundle.py` writes a 7-file CSV bundle + README.md
to `data/exports/<YYYY-MM-DD>/`. Suitable for:
- Loading into BI tools (PowerBI, Tableau, Metabase)
- Distribution to non-technical analysts
- Snapshot archival

Files: investor_lens, yield_anomalies, market_velocity, supply_demand,
national_headline, interest_rates, rent_snapshot.

### Direct SQL

PostgreSQL 16 + PostGIS 3.4 in Docker. Connect with the `property_intel`
database; all relevant objects are in the `property` schema.

---

## Update cadence

| Source | Pipeline | Frequency |
|---|---|---|
| Oikotie listings | hourly_pipeline.py | every hour at :17 |
| Oikotie listing detail | enrich_oikotie_details (in pipeline) | continuous, budget-bounded |
| Hintatiedot transactions | hourly_pipeline.py | every hour at :17 |
| StatFi area_snapshot (current) | hourly_pipeline.py | every hour at :17 |
| StatFi area_snapshot (historical) | backfill_statfi_history.py | one-shot (or on-demand) |
| StatFi rents | fetch_statfi_rents.py | weekly Monday 06:00 |
| StatFi construction | fetch_statfi_construction.py | weekly Monday 07:00 |
| StatFi migration | fetch_statfi_migration.py | weekly Monday 08:00 |
| ECB rates | fetch_interest_rates.py | daily 05:30 |
| Paavo polygons | seed_postal_areas.py | one-shot (rarely changes) |

---

## Methodology notes & limitations

- **Yield** uses **gross** rent/price ratio. Add holding costs
  (maintenance + financial fees) for net yield via the per-listing detail data.
- **Hintatiedot** publishes only **rolling 12 months** of transactions —
  longer trend analysis relies on StatFi 6-year aggregate medians.
- **Construction** is at maakunta (region) level, not postal_code.
  For drill-down to municipality, integrate kunta-avoindata sources.
- **Migration** lags 12-18 months (StatFi annual release cycle).
- **2024-Q3 / Q4 rent** missing — PxWeb daily quota issue, refetch when quota resets.
- **5y price growth** missing for areas with insufficient transaction
  volume in the comparison quarter (StatFi suppresses cells with <6 txns).
- ~22 listings carry NULL postal_code (offshore islands, recent coastal
  redevelopment outside Paavo polygon coverage).

---

## Data sources & licensing

| Data | Source | License | Rate limit |
|---|---|---|---|
| Oikotie listings | asunnot.oikotie.fi (public consumer API) | Web-scraping under fair-use, attribution required | 1 req/s |
| Hintatiedot transactions | asuntojen.hintatiedot.fi (HTML scrape) | Public KVKL feed, attribution required | 0.5 req/s |
| StatFi PxWeb | pxdata.stat.fi | CC BY 4.0 | ~5-10 req/min, 429-throttled |
| ECB SDW | data-api.ecb.europa.eu | Free, attribution required | Unrestricted (reasonable) |
| Tilastokeskus Paavo WFS | geo.stat.fi/geoserver | CC BY 4.0 | Unrestricted |

**Excluded by design**: Etuovi.com listings (robots.txt explicitly disallows
automated agents incl. AI bots; Alma Media has historically litigated scraping).
For broader market coverage, license Etuovi data directly from Alma Media
or use partner B2B feeds.

---

## Schema migrations

| # | Description |
|---|---|
| 001 | Initial schema (asset, listing, transaction, area_snapshot, …) |
| 002 | transaction_history + pipeline_run audit trail |
| 003 | postal_code_area (PostGIS) + lookup_postal_code() |
| 004 | listing detail fields (15 cols) + detail_fetched_at index |
| 005 | interest_rate |
| 006 | rent_snapshot |
| 007 | construction_activity |
| 008 | migration_activity |
| 009 | analytical views (v_postal_investor_lens, v_yield_anomalies, …) |

Each schema migration has both a Python file under `sql/migrations/versions/`
and a raw SQL counterpart under `sql/migrations/sql/` (for migrations that
contain DDL too cumbersome to inline into the alembic op DSL).

---

## Operations

### Hourly pipeline status

```bash
docker exec property-db psql -U property -d property_intel -c "
SELECT * FROM property.pipeline_run ORDER BY started_at DESC LIMIT 5;
"
```

### Detail-enrichment progress

```bash
docker exec property-db psql -U property -d property_intel -c "
SELECT
  COUNT(*) FILTER (WHERE detail_fetched_at IS NOT NULL) AS done,
  COUNT(*) FILTER (WHERE detail_fetched_at IS NULL AND status='active') AS pending
FROM property.listing WHERE source='oikotie';"
```

### Manual re-run

```bash
cd /home/sami/code/JARVIS/property-intel
python3 scripts/hourly_pipeline.py                    # full pipeline
python3 scripts/fetch_interest_rates.py               # ECB rates
python3 scripts/fetch_statfi_rents.py                 # rents
python3 scripts/fetch_statfi_construction.py          # permits/starts/completions
python3 scripts/fetch_statfi_migration.py             # migration
python3 scripts/backfill_listing_details.py           # per-listing detail
python3 scripts/export_product_bundle.py              # CSV bundle
```

### Smoke tests

```bash
# Top 5 deals by yield
docker exec property-db psql -U property -d property_intel -c "
SELECT postal_code, area_name, gross_yield_pct, median_sold_m2, median_rent_per_m2
FROM property.v_yield_anomalies LIMIT 5;"

# National headline (latest 4 quarters)
docker exec property-db psql -U property -d property_intel -c "
SELECT * FROM property.v_national_headline LIMIT 4;"

# Listings missing detail enrichment
docker exec property-db psql -U property -d property_intel -c "
SELECT COUNT(*) FROM property.listing
WHERE source='oikotie' AND status='active' AND detail_fetched_at IS NULL;"
```

---

## Roadmap (not yet integrated)

- **PRH housing-company financials** — taloyhtiön taloustilanne; connector stub exists
- **MML rakennusrekisteri** — authoritative build-year/usage/sqft cross-validation
- ✅ **HSL GTFS transit-access score** (2026-05-27) — `building_features.transit_score_proxy`
  (0-100 proxy: nearest-stop distance + 800 m stop density) for ~9.2k capital-region assets.
  `scripts/fetch_transit_score.py` loads HSL stops into `transit_stop` and scores via PostGIS;
  runs daily in `make fetch-all`. Scoped to HSL member municipalities (others NULL, honest).
  *Open:* add Waltti/other-city feeds; frequency-weighted scoring (stop_times)
- **Kunta open data** — school zones, services density, noise/air, plot zoning
- **Tori.fi rents** — broader coverage of small markets where StatFi has thin data
- **API key tiers + usage metering** — paid tiers, quotas, billing integration
