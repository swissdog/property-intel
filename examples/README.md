# Property-Intel — Example Queries

Real analytical use-cases that demonstrate what the data product enables.
Each `.sql` file is self-contained — copy/paste into psql or run directly:

```bash
docker exec -i property-db psql -U property -d property_intel < 01_top_yield_areas.sql
```

| File | Use case | Audience |
|---|---|---|
| `01_top_yield_areas.sql` | Top-20 yield postal codes in the 9 biggest Finnish cities | Property investor screening |
| `02_supply_shortfall_regions.sql` | Regions where 2024 demand outstripped supply most | Developer / homebuilder |
| `03_mortgage_affordability_index.sql` | Years-of-income to buy a 60 m² 2-room + monthly mortgage | Bank / lender |
| `04_dom_deterioration.sql` | Postal codes where days-on-market is rising YoY (early demand softening) | Hedge fund / market-timer |
| `05_listing_cashflow_screener.sql` | Active listings ranked by net monthly cashflow at market rent + current Euribor | Active buyer / cashflow investor |

## What the data composes

- `property.listing` — live Oikotie listings (16k, hourly refresh) with detail
  enrichment: vastike, rahoitusvastike, kunto, lämmitys, energialuokka, hissi/sauna
- `property.rent_snapshot` — StatFi 9y postal-code rent history (42k rows)
- `property.area_snapshot` — StatFi 6y postal-code apartment prices (64k rows)
- `property.interest_rate` — ECB Euribor (1M/3M/6M/12M) + MRO/DFR (5k rows)
- `property.construction_activity` — StatFi 11y maakunta permits/starts/completions (190k rows)
- `property.migration_activity` — StatFi 10y municipal net migration (3k rows)
- `property.municipality_region` — kunta→maakunta lookup
- `property.postal_code_area` — Paavo PostGIS polygons (3k areas)

Five denormalized views (`property.v_*`) compose these into analyst-ready shapes;
the example queries lean on those views to keep readable.
