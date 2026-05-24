-- v_listing_seller_class
--
-- Classifies each Oikotie listing into a seller_class bucket based on the
-- agency_name brand text:
--
--   asumisoikeus  — right-of-occupancy operators (Asuntosäätiö, TA-Asumis-
--                   oikeus, Haso, Avain Asumisoikeus, Mangrove, Jaso, VASO).
--                   Their stock is distinct from outright purchase market.
--   rakennusliike — construction companies selling new units directly
--                   (Skanska Kodit, T2H, Bonava, YIT, Peab, Lujatalo,
--                   Jatke, Arkta, Fira, Hausia, Varte, Reponen, Lapti,
--                   Nastarakennus, Pohjola Rakennus, EKE-Rakennus, SSA,
--                   TKU-Rakennus, SRV, Hartela, generic "Rakennus*").
--   lkv           — real-estate agencies (everything else with brand text).
--   unknown       — listings without agency_name (private sale or stripped
--                   data).
--
-- Pattern order matters: asumisoikeus runs first because Asuntosäätiö-
-- style names would otherwise leak into rakennusliike via the broad
-- "rakennus" pattern.

CREATE OR REPLACE VIEW property.v_listing_seller_class AS
SELECT
  l.listing_id,
  l.source,
  l.source_listing_id,
  l.status,
  pa.canonical_address,
  pa.postal_code,
  pa.municipality,
  l.asking_price,
  l.living_area_m2,
  l.rooms,
  l.first_seen_at,
  l.last_seen_at,
  l.json_blob->>'agency_name' AS agency_name,
  NULLIF(l.json_blob->>'new_development', '')::boolean AS new_development,
  CASE
    WHEN l.json_blob->>'agency_name' ~* '(asumisoikeus|asuntosäätiö|^vaso$)'
      THEN 'asumisoikeus'
    WHEN l.json_blob->>'agency_name' ~* '(rakennus|skanska kodit|^peab |^bonava |^lujatalo |^jatke |^yit asuntomyynti|^srv rakennus|^t2h |^hartela|^fira rakennus|^hausia |^varte |reponen|^lapti|nastarak|^arkta |pohjola rakennus|eke-rakennus|^ssa kodit|^tku-rak|salminen oy)'
      THEN 'rakennusliike'
    WHEN l.json_blob->>'agency_name' IS NOT NULL
      AND l.json_blob->>'agency_name' <> ''
      THEN 'lkv'
    ELSE 'unknown'
  END AS seller_class
FROM property.listing l
JOIN property.property_asset pa ON pa.asset_id = l.asset_id
WHERE l.source = 'oikotie';

COMMENT ON VIEW property.v_listing_seller_class IS
  'Per-listing seller classification: asumisoikeus | rakennusliike | lkv | unknown. Source-of-truth for downstream supply-demand splits and competitor analyses.';
