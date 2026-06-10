"""Continue v_national_headline rent series past the frozen postal table.

StatFi's postal-code rent table (13eb) terminated permanently at 2025Q4;
from 2025Q1 the new 2025-base table (15fa) publishes only municipality and
whole-country level. The headline rent CTE previously averaged ALL
rent_snapshot rows — mixing the new municipality-level rows into the same
unweighted average would silently change the series composition.

This migration splits the rent CTE explicitly:
- period_start < 2026-01-01: unweighted average over 5-digit postal codes
  (original semantics; postal data exists through 2025Q4)
- period_start >= 2026-01-01: the whole-country ('SSS') 2h series from 15fa

There can be a visible level step at the boundary (different composition);
that is an honest representation of the source change, not a bug.

Revision ID: 021_headline_rent_cont
Revises: 020_pipeline_run_results
Create Date: 2026-06-10
"""

from alembic import op

revision = "021_headline_rent_cont"
down_revision = "020_pipeline_run_results"
branch_labels = None
depends_on = None

VIEW_NEW = """
CREATE OR REPLACE VIEW property.v_national_headline AS
WITH price AS (
    SELECT period_start, ROUND(AVG(median_sold_m2)::numeric, 0) AS avg_price_m2
    FROM property.area_snapshot
    WHERE segment LIKE 'Kerrostalo%' AND median_sold_m2 IS NOT NULL
    GROUP BY period_start
),
rent AS (
    SELECT period_start, ROUND(AVG(median_rent_per_m2), 2) AS avg_rent_m2
    FROM property.rent_snapshot
    WHERE room_count_band = '2h' AND median_rent_per_m2 IS NOT NULL
      AND postal_code ~ '^[0-9]{5}$'
      AND period_start < DATE '2026-01-01'
    GROUP BY period_start
    UNION ALL
    SELECT period_start, ROUND(median_rent_per_m2, 2) AS avg_rent_m2
    FROM property.rent_snapshot
    WHERE room_count_band = '2h' AND median_rent_per_m2 IS NOT NULL
      AND postal_code = 'SSS' AND source = 'statfi_asvu_15fa'
      AND period_start >= DATE '2026-01-01'
),
rate AS (
    SELECT date_trunc('quarter', observation_date)::date AS period_start,
           ROUND(AVG(value_pct)::numeric, 3) AS euribor_12m
    FROM property.interest_rate
    WHERE rate_type = 'euribor_12m'
    GROUP BY date_trunc('quarter', observation_date)
),
supply AS (
    SELECT date_trunc('year', period_start)::date AS year_start,
           SUM(new_dwellings) AS new_dwellings_completed
    FROM property.construction_activity
    WHERE phase = 'completion' AND region_code = 'SSS' AND building_class_code = 'SSS'
    GROUP BY date_trunc('year', period_start)
)
SELECT
    p.period_start,
    p.avg_price_m2,
    r.avg_rent_m2,
    rt.euribor_12m,
    ROUND((r.avg_rent_m2 * 12 / NULLIF(p.avg_price_m2, 0) * 100)::numeric, 2) AS national_yield_pct,
    s.new_dwellings_completed AS dwellings_completed_in_year
FROM price p
LEFT JOIN rent r   ON r.period_start = p.period_start
LEFT JOIN rate rt  ON rt.period_start = p.period_start
LEFT JOIN supply s ON s.year_start = date_trunc('year', p.period_start)
ORDER BY p.period_start DESC
"""

VIEW_OLD_RENT_CTE = """
CREATE OR REPLACE VIEW property.v_national_headline AS
WITH price AS (
    SELECT period_start, ROUND(AVG(median_sold_m2)::numeric, 0) AS avg_price_m2
    FROM property.area_snapshot
    WHERE segment LIKE 'Kerrostalo%' AND median_sold_m2 IS NOT NULL
    GROUP BY period_start
),
rent AS (
    SELECT period_start, ROUND(AVG(median_rent_per_m2), 2) AS avg_rent_m2
    FROM property.rent_snapshot
    WHERE room_count_band = '2h' AND median_rent_per_m2 IS NOT NULL
    GROUP BY period_start
),
rate AS (
    SELECT date_trunc('quarter', observation_date)::date AS period_start,
           ROUND(AVG(value_pct)::numeric, 3) AS euribor_12m
    FROM property.interest_rate
    WHERE rate_type = 'euribor_12m'
    GROUP BY date_trunc('quarter', observation_date)
),
supply AS (
    SELECT date_trunc('year', period_start)::date AS year_start,
           SUM(new_dwellings) AS new_dwellings_completed
    FROM property.construction_activity
    WHERE phase = 'completion' AND region_code = 'SSS' AND building_class_code = 'SSS'
    GROUP BY date_trunc('year', period_start)
)
SELECT
    p.period_start,
    p.avg_price_m2,
    r.avg_rent_m2,
    rt.euribor_12m,
    ROUND((r.avg_rent_m2 * 12 / NULLIF(p.avg_price_m2, 0) * 100)::numeric, 2) AS national_yield_pct,
    s.new_dwellings_completed AS dwellings_completed_in_year
FROM price p
LEFT JOIN rent r   ON r.period_start = p.period_start
LEFT JOIN rate rt  ON rt.period_start = p.period_start
LEFT JOIN supply s ON s.year_start = date_trunc('year', p.period_start)
ORDER BY p.period_start DESC
"""

COMMENT = """
COMMENT ON VIEW property.v_national_headline IS
    'Finland-wide quarterly time series: avg apartment price, avg 2h rent (postal avg through 2025; whole-country 15fa series from 2026), Euribor 12M, derived national yield, annual completions.'
"""


def upgrade() -> None:
    op.execute(VIEW_NEW)
    op.execute(COMMENT)


def downgrade() -> None:
    op.execute(VIEW_OLD_RENT_CTE)
