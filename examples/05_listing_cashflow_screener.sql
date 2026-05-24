-- ============================================================================
-- Live cash-flow screener: active Oikotie listings ranked by net monthly cash
-- flow assuming the area's median rent and the listing's actual recurring fees.
-- ============================================================================
-- Use case: investor scanning today's market for cash-flow-positive deals.
-- Net cashflow = (median_rent_per_m2 × living_area_m2)
--                − maintenance_fee − financial_fee − parking_fee
--                − (mortgage_payment at 70% LTV, e12+0.5%, 25y annuity)
--
-- Ranks by gross_yield (rent ÷ debt_free_price) and net_cashflow.

WITH rent_lookup AS (
    SELECT DISTINCT ON (postal_code)
        postal_code, median_rent_per_m2
    FROM property.rent_snapshot
    WHERE room_count_band = '2h' AND median_rent_per_m2 IS NOT NULL
    ORDER BY postal_code, period_start DESC
),
latest_euribor AS (
    SELECT value_pct FROM property.interest_rate
    WHERE rate_type = 'euribor_12m' ORDER BY observation_date DESC LIMIT 1
)
SELECT
    l.source_listing_id,
    pa.canonical_address,
    pa.municipality,
    pa.postal_code,
    l.living_area_m2,
    l.rooms,
    l.debt_free_price,
    l.maintenance_fee_eur,
    l.financial_fee_eur,
    rl.median_rent_per_m2,
    -- Estimated monthly rent (at area-median per m²)
    ROUND((rl.median_rent_per_m2 * l.living_area_m2)::numeric, 0) AS est_monthly_rent,
    -- Mortgage payment at 70% LTV, 25y, Euribor 12M + 0.5%
    ROUND(
      ((0.7 * l.debt_free_price * (eu.value_pct/100 + 0.005) / 12) /
       NULLIF(1 - POWER(1 + (eu.value_pct/100 + 0.005)/12, -300), 0)
      )::numeric, 0
    ) AS est_monthly_mortgage,
    -- Net monthly cashflow
    ROUND((
        (rl.median_rent_per_m2 * l.living_area_m2)
        - COALESCE(l.maintenance_fee_eur, 0)
        - COALESCE(l.financial_fee_eur, 0)
        - COALESCE(l.parking_fee_eur, 0)
        - (
            (0.7 * l.debt_free_price * (eu.value_pct/100 + 0.005) / 12)
            / NULLIF(1 - POWER(1 + (eu.value_pct/100 + 0.005)/12, -300), 0)
          )
    )::numeric, 0) AS net_monthly_cashflow,
    -- Gross yield based on debt-free price
    ROUND((rl.median_rent_per_m2 * 12 * l.living_area_m2 / NULLIF(l.debt_free_price, 0) * 100)::numeric, 2)
        AS gross_yield_pct,
    l.energy_class,
    l.apartment_condition_code,
    l.has_lift,
    l.has_sauna
FROM property.listing l
JOIN property.property_asset pa ON pa.asset_id = l.asset_id
JOIN rent_lookup rl ON rl.postal_code = pa.postal_code
CROSS JOIN latest_euribor eu
WHERE l.source = 'oikotie'
  AND l.status = 'active'
  AND l.detail_fetched_at IS NOT NULL
  AND l.living_area_m2 BETWEEN 25 AND 80   -- focus on rentable size class
  AND l.debt_free_price IS NOT NULL
  -- Exclude unrealistic per-m² prices (right-to-occupy / shares-only / data anomalies)
  AND l.debt_free_price / NULLIF(l.living_area_m2, 0) BETWEEN 1500 AND 12000
ORDER BY net_monthly_cashflow DESC NULLS LAST
LIMIT 25;
