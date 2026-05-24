-- ============================================================================
-- Postal codes where days-on-market is rising fastest year-over-year
-- (a leading signal of demand softening / price stress).
-- ============================================================================
-- Use case: hedge fund / fund-of-funds looking for early-stage market turns.
-- DOM is one of the few real-time signals that a market is cooling before
-- prices visibly drop.

WITH yearly_dom AS (
    SELECT
        postal_code,
        EXTRACT(YEAR FROM quarter_start)::int AS yr,
        ROUND(AVG(dom_median)::numeric, 1) AS dom_median_avg
    FROM property.v_market_velocity_timeseries
    WHERE dom_median IS NOT NULL
    GROUP BY postal_code, EXTRACT(YEAR FROM quarter_start)
),
yoy AS (
    SELECT
        a.postal_code, a.yr, a.dom_median_avg AS dom_now,
        b.dom_median_avg AS dom_prev,
        (a.dom_median_avg - b.dom_median_avg) AS dom_change_days,
        ROUND(((a.dom_median_avg / NULLIF(b.dom_median_avg, 0) - 1) * 100)::numeric, 1) AS dom_change_pct
    FROM yearly_dom a
    JOIN yearly_dom b ON b.postal_code = a.postal_code AND b.yr = a.yr - 1
)
SELECT
    yoy.postal_code,
    pca.name AS area_name,
    pca.municipality_code,
    yoy.yr,
    yoy.dom_prev,
    yoy.dom_now,
    yoy.dom_change_days,
    yoy.dom_change_pct
FROM yoy
LEFT JOIN property.postal_code_area pca ON pca.postal_code = yoy.postal_code
WHERE yoy.yr = (SELECT MAX(yr) FROM yoy)
  AND yoy.dom_prev > 7   -- skip noisy tiny areas
ORDER BY yoy.dom_change_pct DESC NULLS LAST
LIMIT 20;
