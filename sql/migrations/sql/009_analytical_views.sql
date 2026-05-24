-- 009: Analytical views — denormalized analyst-ready output joining
-- listings, transactions, rents, construction, migration, and rates.
-- Idempotent (CREATE OR REPLACE).

-- ---------------------------------------------------------------------------
-- v_postal_investor_lens — latest yield, sold price, rent, 5y growth per pc
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW property.v_postal_investor_lens AS
WITH latest_sold AS (
    SELECT DISTINCT ON (postal_code)
           postal_code, period_start, segment,
           median_sold_m2, inventory_count
    FROM property.area_snapshot
    WHERE segment = 'Kerrostalo kaksiot' AND median_sold_m2 IS NOT NULL
    ORDER BY postal_code, period_start DESC
),
latest_rent AS (
    SELECT DISTINCT ON (postal_code)
           postal_code, period_start, median_rent_per_m2, rental_contract_count
    FROM property.rent_snapshot
    WHERE room_count_band = '2h' AND median_rent_per_m2 IS NOT NULL
    ORDER BY postal_code, period_start DESC
),
sold_5y_ago AS (
    SELECT DISTINCT ON (postal_code)
           postal_code, median_sold_m2 AS sold_m2_5y_ago, period_start AS period_5y_ago
    FROM property.area_snapshot
    WHERE segment = 'Kerrostalo kaksiot' AND median_sold_m2 IS NOT NULL
      AND period_start <= (CURRENT_DATE - INTERVAL '5 years')
    ORDER BY postal_code, period_start DESC
)
SELECT
    s.postal_code,
    pca.municipality_code,
    pca.name AS area_name,
    s.period_start AS sold_period,
    s.median_sold_m2,
    r.median_rent_per_m2,
    r.rental_contract_count,
    ROUND((r.median_rent_per_m2 * 12 / NULLIF(s.median_sold_m2, 0) * 100)::numeric, 2) AS gross_yield_pct,
    s5.sold_m2_5y_ago,
    ROUND(((s.median_sold_m2 / NULLIF(s5.sold_m2_5y_ago, 0) - 1) * 100)::numeric, 1) AS price_growth_5y_pct,
    s.inventory_count
FROM latest_sold s
LEFT JOIN latest_rent r  ON r.postal_code = s.postal_code
LEFT JOIN sold_5y_ago s5 ON s5.postal_code = s.postal_code
LEFT JOIN property.postal_code_area pca ON pca.postal_code = s.postal_code;

COMMENT ON VIEW property.v_postal_investor_lens IS
    'Latest available yield + 5y price growth per postal code, joining StatFi 13mt apartment_prices, StatFi 13eb rents, and Paavo postal-code geometry. Uses Kerrostalo kaksiot / 2h as comparable proxies.';


-- ---------------------------------------------------------------------------
-- v_yield_anomalies — above-median yield AND below-median price (deal lens)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW property.v_yield_anomalies AS
WITH base AS (
    SELECT * FROM property.v_postal_investor_lens
    WHERE gross_yield_pct IS NOT NULL AND median_sold_m2 IS NOT NULL
),
medians AS (
    SELECT
        percentile_cont(0.5) WITHIN GROUP (ORDER BY gross_yield_pct) AS median_yield,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY median_sold_m2) AS median_price
    FROM base
)
SELECT b.*,
       (SELECT median_yield FROM medians) AS median_yield_pct,
       (SELECT median_price FROM medians) AS median_price_m2
FROM base b, medians m
WHERE b.gross_yield_pct > m.median_yield
  AND b.median_sold_m2 < m.median_price
ORDER BY b.gross_yield_pct DESC NULLS LAST;

COMMENT ON VIEW property.v_yield_anomalies IS
    'Postal codes that simultaneously beat the median on yield AND price-affordability — the "deal" lens for value investors.';


-- ---------------------------------------------------------------------------
-- v_market_velocity_timeseries — quarterly DOM/inventory per pc
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW property.v_market_velocity_timeseries AS
WITH listing_status_periods AS (
    SELECT
        l.listing_id,
        pa.postal_code,
        pa.municipality,
        date_trunc('quarter', l.first_seen_at)::date AS quarter_start,
        EXTRACT(EPOCH FROM (l.last_seen_at - l.first_seen_at)) / 86400 AS days_on_market,
        l.status
    FROM property.listing l
    JOIN property.property_asset pa ON pa.asset_id = l.asset_id
    WHERE l.source = 'oikotie'
      AND pa.postal_code IS NOT NULL
      AND pa.postal_code <> ''
)
SELECT
    postal_code,
    municipality,
    quarter_start,
    COUNT(*) AS listings_in_quarter,
    COUNT(*) FILTER (WHERE status = 'active') AS still_active,
    COUNT(*) FILTER (WHERE status = 'removed') AS removed,
    ROUND(percentile_cont(0.5) WITHIN GROUP (ORDER BY days_on_market)::numeric, 1) AS dom_median,
    ROUND(AVG(days_on_market)::numeric, 1) AS dom_avg
FROM listing_status_periods
GROUP BY postal_code, municipality, quarter_start;

COMMENT ON VIEW property.v_market_velocity_timeseries IS
    'Quarterly listing volume + DOM (days-on-market) per postal code. Computed from Oikotie listing.first_seen_at vs last_seen_at. Used for time-trend analysis of market liquidity.';


-- ---------------------------------------------------------------------------
-- v_supply_demand — annual completions vs net migration, per region + national.
-- Aggregates kunta-level migration up to maakunta via municipality_region.
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS property.v_supply_demand CASCADE;
CREATE VIEW property.v_supply_demand AS
WITH supply AS (
    SELECT region_code,
           EXTRACT(YEAR FROM period_start)::int AS yr,
           SUM(new_dwellings) AS dwellings_completed
    FROM property.construction_activity
    WHERE phase = 'completion' AND building_class_code = 'SSS'
    GROUP BY region_code, EXTRACT(YEAR FROM period_start)
),
demand AS (
    -- Kunta-level migration → roll up to maakunta via municipality_region
    SELECT 'MK' || mr.region_code AS region_code,
           m.period_year AS yr,
           SUM(m.total_net_migration) AS total_net_migration,
           SUM(m.intl_net) AS intl_net,
           SUM(m.inter_muni_net) AS inter_muni_net
    FROM property.migration_activity m
    JOIN property.municipality_region mr
      ON mr.municipality_code = SUBSTRING(m.municipality_code FROM 3)
    WHERE m.municipality_code LIKE 'KU%'
    GROUP BY mr.region_code, m.period_year
    UNION ALL
    -- National total stays as 'SSS'
    SELECT 'SSS' AS region_code, period_year AS yr,
           total_net_migration::bigint, intl_net::bigint, inter_muni_net::bigint
    FROM property.migration_activity
    WHERE municipality_code = 'SSS'
),
region_names AS (
    SELECT DISTINCT 'MK' || region_code AS rcode, region_name FROM property.municipality_region
)
SELECT
    s.region_code,
    rn.region_name,
    s.yr,
    s.dwellings_completed,
    d.total_net_migration,
    d.intl_net,
    d.inter_muni_net,
    CASE WHEN s.dwellings_completed > 0
         THEN ROUND((d.total_net_migration::numeric / s.dwellings_completed * 100), 1)
         ELSE NULL
    END AS demand_supply_ratio_pct
FROM supply s
LEFT JOIN demand d        ON d.region_code = s.region_code AND d.yr = s.yr
LEFT JOIN region_names rn ON rn.rcode = s.region_code
ORDER BY s.region_code, s.yr DESC;

COMMENT ON VIEW property.v_supply_demand IS
    'Annual housing completions vs net migration per maakunta (and national SSS). Aggregates kunta-level migration up to maakunta via property.municipality_region. demand_supply_ratio_pct > 100 indicates demand outstripping new supply.';


-- ---------------------------------------------------------------------------
-- v_national_headline — Finland-wide quarterly time series
-- ---------------------------------------------------------------------------
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
ORDER BY p.period_start DESC;

COMMENT ON VIEW property.v_national_headline IS
    'Finland-wide quarterly time series: avg apartment price, avg 2h rent, Euribor 12M, derived national yield, annual completions. Top-line market dashboard.';
