-- 016: Spatial intersection of postal-code polygons against SYKE flood-risk
-- polygons. One row per (postal_code, scenario) pair where any overlap exists.
-- Returns absolute overlap area (km2) and percentage of the postal-code
-- polygon that sits inside the flood zone.
--
-- Idempotent (CREATE OR REPLACE). Uses PostGIS ST_Intersection which is
-- expensive on first run but cached by GIST indexes thereafter.
--
-- Note: uses CAST(... AS geography) instead of ::geography because
-- SQLAlchemy text() interprets `:` as a bind-parameter prefix and
-- alembic+asyncpg can choke on the cast operator.

CREATE OR REPLACE VIEW property.v_postal_flood_risk AS
WITH flood_overlap AS (
    SELECT
        pca.postal_code,
        pca.name              AS area_name,
        pca.municipality_code,
        pca.municipality_name,
        fr.scenario,
        ST_Area(
            CAST(ST_Intersection(pca.geom, fr.geom) AS geography)
        ) / 1e6 AS overlap_km2,
        ST_Area(CAST(pca.geom AS geography)) / 1e6 AS pc_total_km2
    FROM property.postal_code_area pca
    JOIN property.flood_risk_area fr
      ON ST_Intersects(pca.geom, fr.geom)
)
SELECT
    postal_code,
    area_name,
    municipality_code,
    municipality_name,
    scenario,
    SUM(overlap_km2) AS overlap_km2,
    MAX(pc_total_km2) AS pc_total_km2,
    CASE
        WHEN MAX(pc_total_km2) > 0
        THEN ROUND(CAST(SUM(overlap_km2) / MAX(pc_total_km2) * 100 AS numeric), 3)
        ELSE NULL
    END AS pct_pc_area
FROM flood_overlap
GROUP BY postal_code, area_name, municipality_code, municipality_name, scenario;

COMMENT ON VIEW property.v_postal_flood_risk IS
    'Per (postal_code, scenario) overlap with SYKE flood-hazard polygons. overlap_km2 is the geographic area inside the flood zone, pct_pc_area is its share of the postal-code polygon.';
