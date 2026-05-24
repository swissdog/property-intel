-- ==========================================================================
-- Gold-layer materialized views for property-intel
-- PostgreSQL 16 compatible
-- ==========================================================================

-- Ensure schema exists
CREATE SCHEMA IF NOT EXISTS property;

-- --------------------------------------------------------------------------
-- 1. latest_listing_state
--    Active listings joined with property_asset and building_features.
-- --------------------------------------------------------------------------
DROP MATERIALIZED VIEW IF EXISTS property.latest_listing_state;

CREATE MATERIALIZED VIEW property.latest_listing_state AS
SELECT
    l.listing_id,
    l.source,
    l.source_listing_id,
    l.first_seen_at,
    l.last_seen_at,
    l.status,
    l.asking_price,
    l.living_area_m2,
    l.year_built,
    l.rooms,
    l.lot_area_m2,
    l.energy_class,
    -- price per m2
    CASE
        WHEN l.living_area_m2 IS NOT NULL AND l.living_area_m2 > 0
        THEN ROUND((l.asking_price / l.living_area_m2)::numeric, 2)
        ELSE NULL
    END                                     AS asking_price_per_m2,
    -- days on market
    EXTRACT(DAY FROM (CURRENT_TIMESTAMP - l.first_seen_at))::int
                                            AS days_on_market,
    -- property_asset fields
    pa.asset_id,
    pa.asset_type,
    pa.canonical_address,
    pa.postal_code,
    pa.municipality,
    pa.lat,
    pa.lon,
    pa.parcel_id,
    pa.housing_company_name,
    -- building_features fields
    bf.heating_type,
    bf.sauna,
    bf.garage,
    bf.waterfront_proxy,
    bf.school_distance_m,
    bf.elevation,
    bf.transit_score_proxy
FROM property.listing l
LEFT JOIN property.property_asset pa ON pa.asset_id = l.asset_id
LEFT JOIN property.building_features bf ON bf.asset_id = pa.asset_id
WHERE l.status = 'active';

CREATE UNIQUE INDEX ON property.latest_listing_state (listing_id);
CREATE INDEX ON property.latest_listing_state (postal_code);
CREATE INDEX ON property.latest_listing_state (municipality);

-- REFRESH MATERIALIZED VIEW CONCURRENTLY property.latest_listing_state;


-- --------------------------------------------------------------------------
-- 2. price_change_history
--    All price-change events with computed delta and percentage change.
-- --------------------------------------------------------------------------
DROP MATERIALIZED VIEW IF EXISTS property.price_change_history;

CREATE MATERIALIZED VIEW property.price_change_history AS
SELECT
    le.event_id,
    le.listing_id,
    l.source,
    l.source_listing_id,
    pa.postal_code,
    pa.municipality,
    le.event_at,
    le.old_value::numeric                   AS old_price,
    le.new_value::numeric                   AS new_price,
    (le.new_value::numeric - le.old_value::numeric)
                                            AS price_delta,
    CASE
        WHEN le.old_value::numeric IS NOT NULL AND le.old_value::numeric <> 0
        THEN ROUND(
            ((le.new_value::numeric - le.old_value::numeric)
             / le.old_value::numeric * 100)::numeric,
            2
        )
        ELSE NULL
    END                                     AS price_change_pct
FROM property.listing_event le
JOIN property.listing l ON l.listing_id = le.listing_id
LEFT JOIN property.property_asset pa ON pa.asset_id = l.asset_id
WHERE le.event_type = 'price_changed';

CREATE UNIQUE INDEX ON property.price_change_history (event_id);
CREATE INDEX ON property.price_change_history (listing_id, event_at);
CREATE INDEX ON property.price_change_history (postal_code, event_at);

-- REFRESH MATERIALIZED VIEW CONCURRENTLY property.price_change_history;


-- --------------------------------------------------------------------------
-- 3. market_velocity_by_postal_code
--    Weekly aggregate metrics per postal code:
--      - active listing count
--      - median asking price
--      - median days-on-market
--      - new listings appeared that week
--      - listings removed (went non-active) that week
-- --------------------------------------------------------------------------
DROP MATERIALIZED VIEW IF EXISTS property.market_velocity_by_postal_code;

CREATE MATERIALIZED VIEW property.market_velocity_by_postal_code AS
WITH weeks AS (
    -- Generate week boundaries covering the listing data
    SELECT
        date_trunc('week', dd)::date        AS week_start,
        (date_trunc('week', dd) + INTERVAL '6 days')::date
                                            AS week_end
    FROM generate_series(
        (SELECT date_trunc('week', MIN(first_seen_at)) FROM property.listing),
        CURRENT_DATE,
        '1 week'::interval
    ) dd
),
listing_with_postal AS (
    SELECT
        l.listing_id,
        l.first_seen_at,
        l.last_seen_at,
        l.status,
        l.asking_price,
        pa.postal_code
    FROM property.listing l
    JOIN property.property_asset pa ON pa.asset_id = l.asset_id
    WHERE pa.postal_code IS NOT NULL
),
weekly_active AS (
    -- Listings that were active during each week
    SELECT
        w.week_start,
        w.week_end,
        lp.postal_code,
        lp.listing_id,
        lp.asking_price,
        EXTRACT(DAY FROM (
            LEAST(lp.last_seen_at, w.week_end::timestamp WITH TIME ZONE)
            - lp.first_seen_at
        ))::int                              AS dom
    FROM weeks w
    JOIN listing_with_postal lp
        ON lp.first_seen_at <= (w.week_end + 1)::timestamp WITH TIME ZONE
       AND lp.last_seen_at  >= w.week_start::timestamp WITH TIME ZONE
),
new_per_week AS (
    SELECT
        date_trunc('week', first_seen_at)::date AS week_start,
        postal_code,
        COUNT(*)                                AS new_count
    FROM listing_with_postal
    GROUP BY 1, 2
),
removed_per_week AS (
    SELECT
        date_trunc('week', last_seen_at)::date  AS week_start,
        postal_code,
        COUNT(*)                                AS removed_count
    FROM listing_with_postal
    WHERE status <> 'active'
    GROUP BY 1, 2
)
SELECT
    wa.week_start,
    wa.week_end,
    wa.postal_code,
    COUNT(DISTINCT wa.listing_id)                AS active_count,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY wa.asking_price)
                                                 AS median_asking_price,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY wa.dom)
                                                 AS median_dom,
    COALESCE(npw.new_count, 0)                   AS new_listings,
    COALESCE(rpw.removed_count, 0)               AS removed_listings
FROM weekly_active wa
LEFT JOIN new_per_week npw
    ON npw.week_start = wa.week_start AND npw.postal_code = wa.postal_code
LEFT JOIN removed_per_week rpw
    ON rpw.week_start = wa.week_start AND rpw.postal_code = wa.postal_code
GROUP BY wa.week_start, wa.week_end, wa.postal_code,
         npw.new_count, rpw.removed_count;

CREATE UNIQUE INDEX ON property.market_velocity_by_postal_code
    (postal_code, week_start);
CREATE INDEX ON property.market_velocity_by_postal_code (week_start);

-- REFRESH MATERIALIZED VIEW CONCURRENTLY property.market_velocity_by_postal_code;
