# Property Intelligence — Arkkitehtuuridokumentaatio

> Versio 1.0 | 2026-03-23 | Kattavuus: 20 kaupunkia, 5 datalähdettä, ~70 000 riviä

---

## 1. Yleiskuva

Property Intelligence on JARVIS-järjestelmän kiinteistödatamoduuli joka kerää, rikastaa ja analysoi Suomen asuntomarkkinadataa. Se yhdistää viisi eri datalähdettä yhteen PostGIS-tietokantaan ja tuottaa analyyttisia näkymiä sijoituspäätösten tueksi.

```
┌──────────────────────────────────────────────────────────────┐
│                     TIETOLÄHTEET                              │
│                                                              │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐│
│  │ Oikotie  │ │  StatFi  │ │  Paavo   │ │  Hintatiedot.fi  ││
│  │ SCRAPER  │ │   API    │ │   API    │ │     SCRAPER      ││
│  │(listauks)│ │(hintatil)│ │(aluetil) │ │ (tot. kaupat)    ││
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────────┬─────────┘│
│       │            │            │                 │          │
│       ▼            ▼            ▼                 ▼          │
│  ┌──────────────────────────────────────────────────────────┐│
│  │           hourly_pipeline.py (cron xx:17)                ││
│  │   normalize → upsert → refresh materialized views        ││
│  └──────────────────────────┬───────────────────────────────┘│
│                             ▼                                │
│  ┌──────────────────────────────────────────────────────────┐│
│  │         PostGIS 16 (Docker: property-db :5433)           ││
│  │                                                          ││
│  │  TAULUT:                    MATVIEWIT:                   ││
│  │  ├── property_asset         ├── latest_listing_state     ││
│  │  ├── listing                ├── market_velocity          ││
│  │  ├── listing_event          ├── price_change_history     ││
│  │  ├── area_snapshot          └── price_gap_by_municipality││
│  │  ├── transaction                                         ││
│  │  ├── building_features                                   ││
│  │  └── entity_match                                        ││
│  └──────────────────────────────────────────────────────────┘│
│                                                              │
│  ┌──────────┐  (konnektori valmis, ei pipelinessa)          │
│  │   MML    │                                                │
│  │   API    │                                                │
│  └──────────┘                                                │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. Tietolähteet

### 2.1 Scraperit (ei virallista API-sopimusta)

| # | Lähde | Tyyppi | Konnektori | Data | Päivitys |
|---|-------|--------|------------|------|----------|
| 1 | **Oikotie.fi** | HTML + consumer JSON API | `oikotie/connector.py` | Asuntolistaukset: hinta, m², rv, sijainti, kuvaus, koordinaatit | 1h (cron) |
| 2 | **Asuntojen hintatiedot.fi** (KVKL) | HTML table scraping | `hintatiedot/connector.py` | Toteutuneet kauppahinnat: velaton hinta, €/m², kunto, energia | 1h (cron) |

**Oikotie-konnektori** (`packages/connectors/oikotie/connector.py`):
- Autentikointi: meta-tagit homepage:lta (`api-token`, `loaded`, `cuid`)
- Endpoint: `GET /api/cards?cardType=100&locations=[id,level,name]`
- Sivutus: `offset` + `limit=24`, max 10 sivua/kaupunki
- Normalisointi: `normalize(raw) → NormalizedRecord(record_type="listing")`
- Rate limit: 2s delay between pages

**Hintatiedot-konnektori** (`packages/connectors/hintatiedot/connector.py`):
- Ei autentikointia (julkinen palvelu)
- Endpoint: `GET /haku/?c={city}&cr=1&renderType=renderTypeTable&search=1`
- Sivutus: `z={page}` query-param, ~50 kauppaa/sivu
- Normalisointi: HTML table parsing → `NormalizedRecord(record_type="transaction")`
- Rate limit: 0.5s delay between pages
- Lähde: KVKL-jäsenvälittäjät (Kiinteistömaailma, OP Koti, Huoneistokeskus, Aktia, RE/MAX, Sp-Koti)

### 2.2 Viralliset API:t (avoin data, CC BY 4.0)

| # | Lähde | Tyyppi | Konnektori | Data | Päivitys |
|---|-------|--------|------------|------|----------|
| 3 | **Tilastokeskus StatFi** | PxWeb REST API | `statfi/pxweb.py` | Vanhojen asuntojen mediaanihinnat + volyymit postinumeroittain | 1h |
| 4 | **Tilastokeskus Paavo** | OGC WFS (GeoJSON) | `paavo/connector.py` | Aluetilastot: mediaanitulo, väestö, koulutus, asuntokanta | 1h |
| 5 | **MML Kauppahintarekisteri** | OGC API Features | `mml/transactions.py` | Toteutuneet kiinteistökaupat (konnektori valmis, ei pipelinessa) | — |

**StatFi PxWeb** (`packages/connectors/statfi/pxweb.py`):
- Endpoint: `https://pxnet2.stat.fi/PXWeb/api/v1/fi/StatFin/`
- Taulut: `ashi_pxt_112p.px` (hinnat), `ashi_pxt_112q.px` (hintaindeksi), `ashi_pxt_112r.px` (kuukausi-indeksit)
- Lisenssi: CC 4.0 BY

**Paavo WFS** (`packages/connectors/paavo/connector.py`):
- Endpoint: `https://geo.stat.fi/geoserver/postialue/wfs`
- TypeName: `postialue:pno_tilasto_2024`
- Formaatti: GeoJSON (EPSG:4326)
- Lisenssi: CC 4.0 BY

**MML Kauppahintarekisteri** (`packages/connectors/mml/transactions.py`):
- Endpoint: `https://avoindata.maanmittauslaitos.fi/ogcapi/kiinteistokaupat/v1/`
- Auth: API key (query param tai Basic auth)
- Status: Konnektori valmis, `avoindata`-domain alhaalla 23.3.2026
- Lisenssi: CC 4.0 BY

---

## 3. Seuratut kaupungit (TOP-20)

### Tier 1 — Core market
| Kaupunki | Oikotie ID | Kuntanumero |
|----------|:---:|:---:|
| Tampere | 71 | 837 |
| Turku | 79 | 853 |
| Helsinki | 64 | 91 |
| Vantaa | 82 | 92 |
| Espoo | 39 | 49 |

### Tier 1 — Tampereen kehyskunnat
| Kaupunki | Oikotie ID | Kuntanumero |
|----------|:---:|:---:|
| Pirkkala | 604 | 604 |
| Ylöjärvi | 980 | 980 |
| Nokia | 536 | 536 |
| Kangasala | 211 | 211 |

### Tier 2 — Yliopisto- ja aluekeskukset
| Kaupunki | Oikotie ID | Kuntanumero |
|----------|:---:|:---:|
| Oulu | 54 | 564 |
| Jyväskylä | 43 | 179 |
| Kuopio | 46 | 297 |
| Rovaniemi | 698 | 698 |
| Lahti | 47 | 398 |

### Tier 3 — Kassavirta-kaupungit
| Kaupunki | Oikotie ID | Kuntanumero |
|----------|:---:|:---:|
| Kotka | 285 | 285 |
| Kajaani | 205 | 205 |
| Kouvola | 286 | 286 |
| Pori | 609 | 609 |
| Lappeenranta | 405 | 405 |

Konfiguroitu: `packages/connectors/oikotie/connector.py` → `LOCATION_IDS`

---

## 4. Tietokantarakenne (PostGIS 16)

### Yhteys
```
Host:     localhost:5433 (Docker: property-db)
DB:       property_intel
Schema:   property
User:     property
Engine:   PostgreSQL 16 + PostGIS 3.4
```

### 4.1 Perustaulut

#### `property_asset` — Kiinteistökohteet
| Sarake | Tyyppi | Kuvaus |
|--------|--------|--------|
| `asset_id` | UUID PK | Uniikki tunniste |
| `asset_type` | VARCHAR(50) | apartment_unit / rowhouse_unit / detached_house |
| `canonical_address` | VARCHAR(500) | Katuosoite |
| `postal_code` | VARCHAR(10) | Postinumero |
| `municipality` | VARCHAR(100) | Kunta |
| `lat` / `lon` | FLOAT | GPS-koordinaatit |
| `parcel_id` | VARCHAR(100) | Kiinteistötunnus |
| `building_id` | VARCHAR(100) | Rakennustunnus |
| `housing_company_name` | VARCHAR(300) | Taloyhtiö |
| `source_confidence` | FLOAT | Datan luotettavuusskoori |
| `created_at` / `updated_at` | TIMESTAMPTZ | Aikaleimat |

**Rivejä:** 5 917 | **Indeksit:** lat_lon, municipality, postal_code, parcel_id (unique)

#### `listing` — Asuntolistaukset (Oikotie)
| Sarake | Tyyppi | Kuvaus |
|--------|--------|--------|
| `listing_id` | UUID PK | Uniikki tunniste |
| `asset_id` | UUID FK → property_asset | Kiinteistöviite |
| `source` | VARCHAR(50) | "oikotie" |
| `source_listing_id` | VARCHAR(255) | Oikotie ID |
| `first_seen_at` / `last_seen_at` | TIMESTAMPTZ | Seuranta-ajat |
| `status` | VARCHAR(30) | active / removed |
| `asking_price` | FLOAT | Pyyntihinta € |
| `living_area_m2` | FLOAT | Asuinpinta-ala |
| `year_built` | INT | Rakennusvuosi |
| `rooms` | INT | Huoneluku |
| `lot_area_m2` | FLOAT | Tontin pinta-ala |
| `description_text` | TEXT | Ilmoitusteksti |
| `energy_class` | VARCHAR(10) | Energialuokka |
| `json_blob` | JSON | Koko Oikotie-vastaus (kaupunginosa, kerros, välittäjä, url) |

**Rivejä:** 5 917 | **Unique:** (source, source_listing_id)

#### `listing_event` — Listaustapahtumien historia
| Sarake | Tyyppi | Kuvaus |
|--------|--------|--------|
| `event_id` | UUID PK | |
| `listing_id` | UUID FK → listing | |
| `event_type` | VARCHAR(30) | created / removed / price_change |
| `event_at` | TIMESTAMPTZ | Tapahtuma-aika |
| `old_value` / `new_value` | TEXT | Vanha/uusi arvo (hintamuutoksissa) |

**Rivejä:** 7 314

#### `area_snapshot` — Aluetilastot (StatFi + Paavo)
| Sarake | Tyyppi | Kuvaus |
|--------|--------|--------|
| `snapshot_id` | UUID PK | |
| `postal_code` | VARCHAR(10) | Postinumero |
| `municipality` | VARCHAR(100) | Kunta/alue |
| `period_start` / `period_end` | DATE | Tilastokausi |
| `segment` | VARCHAR(50) | Segmentti |
| `median_ask_m2` | FLOAT | Mediaanipyyntihinta €/m² |
| `median_sold_m2` | FLOAT | Mediaani toteutunut €/m² |
| `dom_median` | FLOAT | Mediaani myyntiaika pv |
| `inventory_count` | INT | Listausten lukumäärä |
| `price_cut_ratio` | FLOAT | Hinnanalennusten osuus |
| `income_median` | FLOAT | Alueen mediaanitulo € |
| `owner_occupancy_ratio` | FLOAT | Omistusasumisen osuus |

**Rivejä:** 53 245 | **Unique:** (postal_code, period_start, period_end, segment)

#### `transaction` — Toteutuneet kaupat (Hintatiedot.fi / KVKL)
| Sarake | Tyyppi | Kuvaus |
|--------|--------|--------|
| `transaction_id` | UUID PK | |
| `asset_id` | UUID FK → property_asset | (valinnainen linkki) |
| `source` | VARCHAR(50) | "hintatiedot_kvkl" / "test_mml" |
| `source_record_id` | VARCHAR(255) | Deduplikointi-avain |
| `transaction_date` | DATE | Kaupan päivämäärä |
| `transaction_price` | FLOAT | Velaton kauppahinta € |
| `transaction_type` | VARCHAR(50) | "sale" |
| `municipality` | VARCHAR(100) | Kaupunki |
| `neighborhood` | VARCHAR(200) | Kaupunginosa |
| `building_type` | VARCHAR(50) | apartment / rowhouse / detached |
| `living_area_m2` | FLOAT | Pinta-ala |
| `price_per_m2` | FLOAT | €/m² |
| `year_built` | INT | Rakennusvuosi |
| `room_config` | VARCHAR(200) | Huonejako (esim. "3h+k+s") |
| `floor` | VARCHAR(20) | Kerros (esim. "2/5") |
| `elevator` | BOOLEAN | Hissi |
| `condition` | VARCHAR(50) | hyvä / tyyd. / huono |
| `lot_type` | VARCHAR(50) | oma / vuokra |
| `energy_class` | VARCHAR(10) | A–G |
| `fetched_at` | TIMESTAMPTZ | Hakuajankohta |

**Rivejä:** 10 885 | **Unique:** (source, source_record_id)

#### `building_features` — Rakennuksen lisäominaisuudet
| Sarake | Tyyppi | Kuvaus |
|--------|--------|--------|
| `asset_id` | UUID PK FK → property_asset | |
| `heating_type` | VARCHAR(50) | Lämmitysmuoto |
| `sauna` / `garage` | BOOLEAN | Sauna / autotalli |
| `waterfront_proxy` | FLOAT | Rantaetäisyys-proxy |
| `school_distance_m` | FLOAT | Kouluetäisyys m |
| `elevation` | FLOAT | Korkeus mpy |
| `transit_score_proxy` | FLOAT | Joukkoliikenneskoori |

**Rivejä:** 0 (rikastusta varten, ei vielä täytetty)

#### `entity_match` — Kohteiden yhdistely (dedup)
Linkittää eri lähteistä tulevia kohteita toisiinsa `match_score` + `match_reason` perusteella.

**Rivejä:** 0 (käyttöönottoa varten valmis)

### 4.2 Materialisoidut näkymät (auto-refresh)

#### `latest_listing_state` — Aktiivisten listausten rikastettu näkymä
Yhdistää: `listing` + `property_asset` + `building_features`. Lisää lasketut kentät:
- `asking_price_per_m2` (€/m²)
- `days_on_market` (päivää markkinoilla)

**Rivejä:** 4 534 | **Refresh:** tunnin välein

#### `market_velocity_by_postal_code` — Viikottainen markkinadynamiikka
| Sarake | Kuvaus |
|--------|--------|
| `week_start` / `week_end` | Viikko |
| `postal_code` | Postinumero |
| `active_count` | Aktiivisten listausten lkm |
| `median_asking_price` | Mediaanipyyntihinta |
| `median_dom` | Mediaani myyntiaika |
| `new_listings` / `removed_listings` | Uudet / poistuneet viikolla |

**Rivejä:** 555 | **Refresh:** tunnin välein

#### `price_change_history` — Hintamuutosten rikastettu historia
Yhdistää: `listing_event` (price_change) + `listing` + `property_asset`. Lisää:
- `price_delta` (muutos €)
- `price_change_pct` (muutos %)

**Rivejä:** 0 (kasvaa kun hintamuutoksia havaitaan)

#### `price_gap_by_municipality` — Pyyntihinta vs. toteutunut per kunta
| Sarake | Kuvaus |
|--------|--------|
| `municipality` | Kunta |
| `active_listings` / `transactions_12m` | Listausten / kauppojen lkm |
| `avg_asking_price` / `avg_realized_price` | Keskihinta: pyynti vs. toteutunut |
| `median_asking_price` / `median_realized_price` | Mediaalihinta |
| `avg_price_gap_pct` / `median_price_gap_pct` | Hintaero-% |
| `avg_asking_m2` / `avg_realized_m2` | €/m² vertailu |
| `m2_gap_pct` | Neliöhintaero-% |
| `avg_area_m2` / `avg_dom` | Keskim. pinta-ala ja myyntiaika |

**Rivejä:** 19 | **Refresh:** tunnin välein

---

## 5. Pipeline

### Tiedosto
`property-intel/scripts/hourly_pipeline.py`

### Cron
```
17 * * * * cd property-intel && python3 scripts/hourly_pipeline.py >> data/pipeline.log 2>&1
```

### Vaiheet

```
1. Fetch (rinnakkain):
   ├── StatFi PxWeb → statfi_records[]
   ├── Paavo WFS → paavo_records[]
   └── (ei rinnakkain, rate-limited):
       ├── Oikotie → oikotie_records[]
       └── Hintatiedot → hintatiedot_records[]

2. Write:
   ├── StatFi → area_snapshot (upsert)
   ├── Paavo → area_snapshot (upsert)
   ├── Oikotie → property_asset + listing + listing_event (change tracking)
   └── Hintatiedot → transaction (upsert on source_record_id)

3. Refresh materialized views:
   ├── latest_listing_state
   ├── price_change_history
   ├── market_velocity_by_postal_code
   └── price_gap_by_municipality

4. Summary counts + Telegram alert
```

### Suoritusaika
~135–205 sekuntia (riippuen Oikotien sivumäärästä ja hintatiedot.fi kauppojen määrästä).

### Datavolyymit (23.3.2026)
| Metriikka | Arvo |
|-----------|------|
| Oikotie-listauksia haettu | ~1 900 / ajo |
| Hintatiedot-kauppoja haettu | ~10 900 / ajo |
| StatFi-rivejä | ~13 100 / ajo |
| Paavo-rivejä | ~149 / ajo |
| Uusia listauksia / ajo | 5–80 |
| Hintamuutoksia / ajo | 0–5 |
| Poistuneita / ajo | 0–60 |

---

## 6. Tiedostorakenne

```
property-intel/
├── packages/connectors/
│   ├── base.py                          # NormalizedRecord, RawFetchResult protokollat
│   ├── registry.py                      # Konnektorirekisteri
│   ├── oikotie/
│   │   ├── config.py                    # OikotieConfig (card_type, max_pages)
│   │   └── connector.py                 # OikotieConnector + LOCATION_IDS (20 kaupunkia)
│   ├── hintatiedot/
│   │   ├── config.py                    # HintatiedotConfig (delay, timeout)
│   │   └── connector.py                 # HintatiedotConnector (HTML table parser)
│   ├── statfi/
│   │   ├── config.py                    # StatFiConfig (PxWeb taulut)
│   │   └── pxweb.py                     # StatFiPxWebConnector
│   ├── paavo/
│   │   ├── config.py                    # PaavoConfig (WFS params)
│   │   └── connector.py                 # PaavoConnector (GeoJSON)
│   ├── mml/
│   │   ├── config.py                    # MMLConfig (OGC/legacy, API key)
│   │   └── transactions.py              # MMLTransactionConnector (EI PIPELINESSA)
│   └── energy_cert/                     # (tyhjä — aloitettu)
│
├── apps/api/db/
│   └── models.py                        # SQLAlchemy ORM mallit
│
├── scripts/
│   └── hourly_pipeline.py               # Pää-pipeline (cron)
│
├── data/
│   └── pipeline.log                     # Pipeline-ajoloki
│
└── docs/
    └── architecture.md                  # Tämä dokumentti
```

---

## 7. Indeksit ja suorituskyky

| Taulu | Indeksi | Tyyppi |
|-------|---------|--------|
| property_asset | `ix_property_asset_lat_lon` | btree (lat, lon) |
| property_asset | `ix_property_asset_municipality` | btree |
| property_asset | `ix_property_asset_postal_code` | btree |
| property_asset | `uq_property_asset_parcel_id` | unique |
| listing | `uq_listing_source` | unique (source, source_listing_id) |
| listing | `ix_listing_status` | btree |
| listing | `ix_listing_asset_id` | btree |
| listing_event | `ix_listing_event_listing_at` | btree (listing_id, event_at) |
| area_snapshot | `uq_area_snapshot_period` | unique (postal_code, period_start, period_end, segment) |
| transaction | `uq_transaction_source` | unique (source, source_record_id) |
| transaction | `ix_transaction_municipality` | btree |
| transaction | `ix_transaction_date` | btree |
| latest_listing_state | `municipality_idx`, `postal_code_idx`, `listing_id_idx` | btree |
| market_velocity | `postal_code_week_start_idx` | unique |
| price_gap_by_municipality | `municipality_idx` | unique |

---

## 8. Lisenssit ja juridiikka

| Lähde | Lisenssi | Kaupallinen käyttö |
|-------|----------|-------------------|
| Tilastokeskus StatFi | CC 4.0 BY | Sallittu, lähdemaininta |
| Tilastokeskus Paavo | CC 4.0 BY | Sallittu, lähdemaininta |
| MML Kauppahintarekisteri | CC 4.0 BY | Sallittu, lähdemaininta |
| Oikotie.fi | Ei virallista API:a — consumer endpoint scraping | Harmaa alue — ei TOS-rikkomus mutta ei sopimusta |
| Hintatiedot.fi (KVKL) | Julkinen ympäristöministeriön palvelu | Julkinen data, lähdemaininta suositeltava |
