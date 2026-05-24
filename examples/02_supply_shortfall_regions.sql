-- ============================================================================
-- Regions where 2024 demand outstripped supply by the largest margin
-- ============================================================================
-- Use case: developer / builder looking for under-supplied markets to enter.
-- Highlights regions where the demand_supply_ratio_pct (net migration / new
-- dwellings completed) is highest — i.e. price/rent pressure is strongest.

SELECT
    region_code,
    region_name,
    yr,
    dwellings_completed,
    total_net_migration,
    intl_net,
    inter_muni_net,
    demand_supply_ratio_pct
FROM property.v_supply_demand
WHERE yr = 2024
  AND region_code <> 'SSS'
  AND demand_supply_ratio_pct IS NOT NULL
ORDER BY demand_supply_ratio_pct DESC
LIMIT 10;
