-- ============================================================================
-- Mortgage affordability index: how many years of median income to buy a 60 m²
-- 2-room apartment, given the latest 12-month Euribor.
-- ============================================================================
-- Use case: bank / mortgage lender estimating buyer purchasing power per area.
-- Methodology: full price (no loan), expressed in years of pre-tax median
-- household income. Lower = more affordable. We also show the implied
-- monthly mortgage payment at 70% LTV at the latest Euribor 12M + 0.5%
-- bank margin, 25-year amortising loan.

WITH latest_euribor AS (
    SELECT value_pct
    FROM property.interest_rate
    WHERE rate_type = 'euribor_12m'
    ORDER BY observation_date DESC
    LIMIT 1
),
latest_paavo_income AS (
    -- Paavo writes income_median into area_snapshot with segment IS NULL.
    SELECT DISTINCT ON (postal_code) postal_code, income_median
    FROM property.area_snapshot
    WHERE segment IS NULL AND income_median IS NOT NULL
    ORDER BY postal_code, period_start DESC
)
SELECT
    inv.postal_code,
    inv.area_name,
    inv.median_sold_m2 AS price_m2,
    inc.income_median  AS median_income,
    ROUND(((inv.median_sold_m2 * 60) / NULLIF(inc.income_median, 0))::numeric, 1) AS years_of_income_60m2,
    -- Monthly payment for 0.7 × (60 m² × price_m2) at e+0.5%, 25y annuity.
    ROUND((
        (0.7 * inv.median_sold_m2 * 60) *
        ((eu.value_pct/100 + 0.005)/12) /
        (1 - POWER(1 + ((eu.value_pct/100 + 0.005)/12), -300))
    )::numeric, 0) AS monthly_payment_eur,
    eu.value_pct AS euribor_12m_pct
FROM property.v_postal_investor_lens inv
JOIN latest_paavo_income inc ON inc.postal_code = inv.postal_code
CROSS JOIN latest_euribor eu
WHERE inv.median_sold_m2 IS NOT NULL
  AND inc.income_median > 0
ORDER BY years_of_income_60m2
LIMIT 15;
