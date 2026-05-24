-- 012: Pivot Paavo long-format attributes into a wide demographic view per pc.
-- Latest year wins (DISTINCT ON). Codes follow Paavo 2024 schema:
--   he_*  population (5-year age bands; he_25_29 ... he_60_64 etc)
--   tr_*  household income (tr_mtu = median, tr_ktu = mean)
--   ko_*  education
--   ra_*  housing stock
--   te_*  households
--   pt_*  employment (pt_vakiy = working-age pop, pt_tyoll = employed,
--                    pt_tyott = unemployed)
-- Idempotent (CREATE OR REPLACE).

CREATE OR REPLACE VIEW property.v_postal_demographics AS
WITH latest_year AS (
    SELECT DISTINCT ON (postal_code) postal_code, year
    FROM property.paavo_attribute
    ORDER BY postal_code, year DESC
),
pivoted AS (
    SELECT
        ly.postal_code,
        ly.year,
        MAX(pa.value) FILTER (WHERE pa.attribute_code = 'he_vakiy')   AS population_total,
        MAX(pa.value) FILTER (WHERE pa.attribute_code = 'he_kika')    AS mean_age,
        MAX(pa.value) FILTER (WHERE pa.attribute_code = 'he_miehet')  AS population_male,
        MAX(pa.value) FILTER (WHERE pa.attribute_code = 'he_naiset')  AS population_female,
        -- Working-age population sum (25–64) over 5-year bands
        COALESCE(MAX(pa.value) FILTER (WHERE pa.attribute_code = 'he_25_29'), 0)
        + COALESCE(MAX(pa.value) FILTER (WHERE pa.attribute_code = 'he_30_34'), 0)
        + COALESCE(MAX(pa.value) FILTER (WHERE pa.attribute_code = 'he_35_39'), 0)
        + COALESCE(MAX(pa.value) FILTER (WHERE pa.attribute_code = 'he_40_44'), 0)
        + COALESCE(MAX(pa.value) FILTER (WHERE pa.attribute_code = 'he_45_49'), 0)
        + COALESCE(MAX(pa.value) FILTER (WHERE pa.attribute_code = 'he_50_54'), 0)
        + COALESCE(MAX(pa.value) FILTER (WHERE pa.attribute_code = 'he_55_59'), 0)
        + COALESCE(MAX(pa.value) FILTER (WHERE pa.attribute_code = 'he_60_64'), 0)
                                                                      AS pop_25_64,
        -- Income (Paavo uses tr_* for tulot)
        MAX(pa.value) FILTER (WHERE pa.attribute_code = 'tr_mtu')     AS median_income_eur,
        MAX(pa.value) FILTER (WHERE pa.attribute_code = 'tr_ktu')     AS mean_income_eur,
        -- Education
        MAX(pa.value) FILTER (WHERE pa.attribute_code = 'ko_yl_kork') AS edu_higher,
        MAX(pa.value) FILTER (WHERE pa.attribute_code = 'ko_al_kork') AS edu_lower_higher,
        MAX(pa.value) FILTER (WHERE pa.attribute_code = 'ko_perus')   AS edu_basic_only,
        -- Employment
        MAX(pa.value) FILTER (WHERE pa.attribute_code = 'pt_vakiy')   AS working_age_pop,
        MAX(pa.value) FILTER (WHERE pa.attribute_code = 'pt_tyoll')   AS employed,
        MAX(pa.value) FILTER (WHERE pa.attribute_code = 'pt_tyott')   AS unemployed,
        -- Housing stock
        MAX(pa.value) FILTER (WHERE pa.attribute_code = 'ra_asunn')   AS dwellings_total,
        MAX(pa.value) FILTER (WHERE pa.attribute_code = 'te_taly')    AS households_total,
        MAX(pa.value) FILTER (WHERE pa.attribute_code = 'te_omis_as') AS households_owner_occupied,
        MAX(pa.value) FILTER (WHERE pa.attribute_code = 'te_as_valj') AS households_rental
    FROM latest_year ly
    JOIN property.paavo_attribute pa
      ON pa.postal_code = ly.postal_code AND pa.year = ly.year
    GROUP BY ly.postal_code, ly.year
)
SELECT
    p.postal_code,
    p.year,
    pca.name AS area_name,
    pca.municipality_code,
    pca.municipality_name,
    p.population_total,
    p.population_male,
    p.population_female,
    p.mean_age,
    NULLIF(p.pop_25_64, 0) AS pop_25_64,
    p.median_income_eur,
    p.mean_income_eur,
    p.edu_higher,
    p.edu_lower_higher,
    p.edu_basic_only,
    p.working_age_pop,
    p.employed,
    p.unemployed,
    CASE WHEN (p.employed + p.unemployed) > 0
         THEN ROUND((p.unemployed / NULLIF(p.employed + p.unemployed, 0) * 100)::numeric, 1)
         ELSE NULL END AS pct_unemployed,
    CASE WHEN (p.edu_higher + p.edu_lower_higher + p.edu_basic_only) > 0
         THEN ROUND(
            (p.edu_higher / NULLIF(
                p.edu_higher + p.edu_lower_higher + p.edu_basic_only, 0
            ) * 100)::numeric, 1)
         ELSE NULL END AS pct_higher_education,
    p.dwellings_total,
    p.households_total,
    p.households_owner_occupied,
    p.households_rental,
    CASE WHEN p.households_total > 0
         THEN ROUND(
            (p.households_owner_occupied /
             NULLIF(p.households_total, 0) * 100)::numeric, 1)
         ELSE NULL END AS pct_owner_occupied
FROM pivoted p
LEFT JOIN property.postal_code_area pca ON pca.postal_code = p.postal_code;

COMMENT ON VIEW property.v_postal_demographics IS
    'Latest-year Paavo demographic profile per postal code: population, age (mean + 25-64 working-age band), income (tr_mtu/tr_ktu), education share, employment, housing-stock composition. Joined to postal_code_area for kunta/maakunta lookup.';
