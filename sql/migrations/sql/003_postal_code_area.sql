-- 003: postal_code_area — PostGIS polygons for reverse-geocoding lat/lon → postal_code.
-- Source: Tilastokeskus Paavo WFS (postialue:pno_tilasto_2024).
-- Seeded via property-intel/scripts/seed_postal_areas.py.
-- Idempotent: safe to re-run.

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS property.postal_code_area (
    postal_code        VARCHAR(5)  PRIMARY KEY,
    name               VARCHAR(200),
    municipality_code  VARCHAR(10),
    municipality_name  VARCHAR(100),
    geom               GEOMETRY(MULTIPOLYGON, 4326) NOT NULL,
    fetched_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_layer       VARCHAR(80)
);

CREATE INDEX IF NOT EXISTS ix_postal_code_area_geom
    ON property.postal_code_area USING GIST (geom);

CREATE INDEX IF NOT EXISTS ix_postal_code_area_municipality
    ON property.postal_code_area (municipality_name);

-- Helper function: reverse-geocode lat/lon → postal_code.
-- Strategy: prefer ST_Intersects (point inside polygon); fall back to nearest
-- polygon within 500 m (handles harbor edges, rooftop GPS jitter, Paavo polygon
-- imprecision near coastlines). Returns NULL only if the point is far from
-- any known polygon (e.g. lat/lon outside Finland or grossly wrong).
CREATE OR REPLACE FUNCTION property.lookup_postal_code(p_lat DOUBLE PRECISION, p_lon DOUBLE PRECISION)
RETURNS VARCHAR(5)
LANGUAGE sql
STABLE
AS $$
    WITH point AS (
        SELECT ST_SetSRID(ST_MakePoint(p_lon, p_lat), 4326) AS g
    ),
    inside AS (
        SELECT pca.postal_code
        FROM property.postal_code_area pca, point
        WHERE ST_Intersects(pca.geom, point.g)
        ORDER BY ST_Area(pca.geom) ASC
        LIMIT 1
    ),
    nearest AS (
        SELECT pca.postal_code
        FROM property.postal_code_area pca, point
        WHERE NOT EXISTS (SELECT 1 FROM inside)
          AND ST_DWithin(pca.geom::geography, point.g::geography, 500.0)
        ORDER BY pca.geom <-> point.g
        LIMIT 1
    )
    SELECT postal_code FROM inside
    UNION ALL
    SELECT postal_code FROM nearest
    LIMIT 1;
$$;

COMMENT ON TABLE  property.postal_code_area IS
    'Finnish postal-code polygons from Tilastokeskus Paavo WFS, used to reverse-geocode listing lat/lon when source API does not provide postal_code (e.g. Oikotie /api/cards).';
COMMENT ON FUNCTION property.lookup_postal_code(DOUBLE PRECISION, DOUBLE PRECISION) IS
    'Returns the postal_code containing the given lat/lon point, or NULL.';
