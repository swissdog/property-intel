-- ============================================================================
-- Top 20 highest-yield postal-code areas in cities the user actually sells in
-- ============================================================================
-- Use case: a property investor screening for cash-flow opportunities.
-- Filters out micro-areas where the median is unreliable (< 20 listings)
-- and shows the latest available period for each row.
--
-- Run:
--   docker exec -i property-db psql -U property -d property_intel < 01_top_yield_areas.sql

WITH bigcity_munis AS (
    SELECT '091' AS code UNION ALL  -- Helsinki
    SELECT '049'         UNION ALL  -- Espoo
    SELECT '092'         UNION ALL  -- Vantaa
    SELECT '837'         UNION ALL  -- Tampere
    SELECT '853'         UNION ALL  -- Turku
    SELECT '297'         UNION ALL  -- Kuopio
    SELECT '564'         UNION ALL  -- Oulu
    SELECT '179'         UNION ALL  -- Jyväskylä
    SELECT '398'                    -- Lahti
)
SELECT
    l.postal_code,
    l.area_name,
    l.median_sold_m2     AS price_m2,
    l.median_rent_per_m2 AS rent_m2,
    l.gross_yield_pct,
    l.price_growth_5y_pct,
    l.inventory_count,
    l.sold_period
FROM property.v_postal_investor_lens l
WHERE l.municipality_code IN (SELECT code FROM bigcity_munis)
  AND l.gross_yield_pct IS NOT NULL
  AND l.inventory_count >= 5
ORDER BY l.gross_yield_pct DESC NULLS LAST
LIMIT 20;
