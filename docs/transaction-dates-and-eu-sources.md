# Property Intelligence — Kauppapäivät ja EU-tason laajennus (lähdetutkimus)

> Versio 1.0 | 2026-05-26 | Lähdetutkimus: oikean kauppapäivän saatavuus + EU-datalähteet & -sääntely
> Status: tutkimus / päätöksenteon tueksi — ei vielä toteutettu

---

## 0. Tausta ja ongelma

`property.transaction.transaction_date` ei tällä hetkellä ole **oikea kauppapäivä**:
hintatiedot.fi / KVKL-lähde ei palauta kauppapäivää lainkaan (konnektori parsii 12
saraketta, ei päiväkenttää), joten kirjoittaja asettaa arvoksi *ingest-päivän*. Tämä
havaittiin dedup-siivouksessa 2026-05-26 (kaikki sha256-rivit = ingest-pvm 2026-04-16,
vanhat hajautuvat eri ajopäiville). Aineisto on tarkkuudeltaan käytännössä
**neljännesvuositasoa** (KVKL).

**Kysymys:** voiko oikeat kauppapäivät hankkia — Suomessa ja/tai EU-tasolla — ja
kannattaisiko moduuli laajentaa EU-laajuiseksi?

---

## 1. Suomi — kauppapäivän saatavuus kohdetyypeittäin

Suomessa lähde riippuu kohdetyypistä, koska **kiinteistöt** (maa + rakennukset omalla
tontilla) ja **osakeasunnot** (asunto-osakeyhtiön osakkeet) ovat eri oikeudellisia
kohteita ja eri rekistereissä.

### 1.1 Kiinteistöt — omakotitalot omalla tontilla, tontit, maa-alueet ✅

**MML Kiinteistöjen kauppahintarekisteri** on tähän paras lähde:

- Sisältää **kaikki kiinteistöjen luovutukset vuodesta 1985** (kaupat, vaihdot, lahjat,
  jakosopimukset, esisopimukset), notaarien (kaupanvahvistajien) ilmoituksista.
- Tietosisältö: kohde, **kauppahinta**, osapuolet — ja luovutus/kauppapäivä on rekisterin
  ydinkenttä (luovutusrekisteri).
- **Julkinen, CC 4.0 -avoin data** -lisenssi.
- Tietopalvelu-uudistus: WFS korvataan **OGC API Features** -palvelulla, **julkaisu kevät
  2026**. Lisäksi otteet/export-tiedostot (hinnoittelu €58 ensimmäinen + €0,64 seuraavat)
  sekä avoimet taulukot ja indeksit.

→ **Juuri detached-kategorialle** (sama, jossa parserin sarakesiirtymä-korruptio oli)
**oikea kauppapäivä on saatavilla avoimena datana.**

**Rajoitus:** rekisteri **ei kata osakeasuntoja** — ne ovat osakkeita, eivät kiinteistöjä.

### 1.2 Osakeasunnot — kerrostalo- ja rivitalo-osakkeet ⚠️

Yksittäistason kauppapäivää **ei ole avoimesti saatavilla** tänään. Vaihtoehdot:

| Lähde | Kauppapäivä | Avoin? | Huom |
|---|---|---|---|
| **Verohallinto** varainsiirtovero-ilmoitukset | ✅ tarkka | ❌ ei (verosalaisuus) | 2 kk maksuviive + käsittelyaika |
| **Tilastokeskus** *Osakeasuntojen hinnat* (ashi) | aggregoitu | osin (tilastot) | Lähde: Verohallinto + KVKL + suora raportointi; ei yksittäiskauppoja päivineen |
| **KVKL** hintaseurantapalvelu (= hintatiedot.fi, nyk. lähteemme) | ❌ vain neljännes/listaus | julkinen sivu | Kattaa 70–80 % vanhoista osakeasuntokaupoista |
| **Huoneistotietojärjestelmä (HTJ / ASREK)**, MML | tuleva | rajapinnat auki 2020 | Asunto-osakkeiden sähköinen rekisteri 2019–; kauppahinnan lisääminen mahdollistaisi "tarkemman, puolueettoman, ajantasaisen" hintatilastoinnin. HTJ2-ohjelma päättyy 2026 |

→ Osakeasunnoille: **KVKL = neljännestaso (nyt)**, **HTJ/ASREK = nouseva tuleva lähde**,
**Verohallinto = autoritatiivinen muttei avoin**.

---

## 2. EU-taso — sääntely ja datalähteet

### 2.1 Sääntelykehys (mitä EU velvoittaa)

- **Open Data Directive (EU) 2019/1024** + **High-Value Datasets -toimeenpanoasetus (EU)
  2023/138** (voimassa kaikissa jäsenmaissa 2024–). Kuusi HVD-kategoriaa: geospatiaalinen,
  maanhavainnointi/ympäristö, meteorologia, **tilastot**, yritykset/omistus, liikkuvuus.
  **Kiinteistörekisterikartat (cadastral parcels)** ovat HVD-geodataa → julkaistava
  *ilmaiseksi, koneluettavasti, API + bulk-lataus*. Linkittävät palstan omistukseen ja
  mahdollisesti kiinteistöarvoon — **mutta eivät sisällä kauppahintoja**.
- **INSPIRE-direktiivi 2007/2/EY**: harmonisoi geodatan (kiinteistörajat, rakennukset,
  osoitteet) jäsenmaiden + ISL/LIE/NOR/CHE kesken. **Ei kauppahintoja.**

→ **EU ei velvoita avaamaan yksittäisiä kauppahintatietoja.** Se pakottaa cadastral- ja
geospatiaalisen perusdatan. **Yksittäiset kauppahintarekisterit ovat kansallisia** ja
niiden avoimuus vaihtelee maittain (kansalliset läpinäkyvyyspäätökset).

### 2.2 Kansalliset avoimet kauppadatat kauppapäivineen — mallit

| Maa | Lähde | Kauppapäivä | Avoin | Muoto / kattavuus |
|---|---|---|---|---|
| 🇫🇷 Ranska | **DVF** (Demandes de Valeurs Foncières), DGFiP | ✅ | ✅ avoin (asetus 2018-1350) | Yksittäiskaupat 5 v + 2014–; hinta, tyyppi, geolokaatio. DVF+ (Cerema): PostGIS/CSV/GPKG. **Kultainen standardi** |
| 🇳🇱 Alankomaat | **Kadaster** (BRK, Basisregistratie Kadaster) | ✅ | ✅ julkinen | Kaikkien kauppojen hinta + **kauppapäivä** + kohdeominaisuudet |
| 🇩🇰 Tanska | **Statistics Denmark** / kiinteistörekisteri | ✅ (sopimus-/kauppakirja-/rekisteröintipäivä) | ✅ | Kaikki rekisteröidyt kaupat: omakoti, osakeasunnot, maatalous, liiketilat |
| 🇫🇮 Suomi | **MML kauppahintarekisteri** (vain kiinteistöt) | ✅ | ✅ CC4.0 | Ks. §1.1; ei osakeasuntoja |
| 🇸🇪 Ruotsi / 🇳🇴 Norja | Lantmäteriet / Kartverket | (todennäk. ✅) | osin / maksullinen | **Vahvistettava** — pääosin lisensoitua |

→ Avoin, kauppapäivällinen yksittäisdata on **vahvinta Ranskassa (DVF) ja Alankomaissa
(Kadaster)**; Tanska ja Suomi (kiinteistöt) hyviä. Malli EU-laajennukselle: **per-maa-
konnektorit kansallisiin avoimiin rekistereihin**, harmonisoituun skeemaan.

---

## 3. Yhteenveto — toteutettavuus

| Segmentti | Oikea kauppapäivä saatavilla? | Lähde | Aikataulu |
|---|---|---|---|
| FI — omakotitalot / tontit | ✅ avoimena | MML kauppahintarekisteri | OGC API kevät 2026 |
| FI — osakeasunnot | ⚠️ ei yksittäistasolla avoimesti | KVKL (neljännes) / HTJ (tuleva) / Verohallinto (suljettu) | HTJ-eteneminen avoin |
| EU — useita maita | ✅ vahvimmillaan FR/NL/DK | kansalliset avoimet rekisterit | per maa |
| EU-laajuinen yhtenäinen kauppahintadata | ❌ ei ole | (kansallinen pirstaleisuus) | — |

---

## 4. Roadmap

### Vaihe 1 — Suomi, kiinteistöt (lyhyt aikaväli, kevät 2026)
1. **Integroi MML kauppahintarekisteri** (OGC API Features, julkaisu kevät 2026) uutena
   konnektorina (`connectors/mml_kauppahinta/`).
2. **Korjaa `transaction_date`-semantiikka**: erota `sale_date` (todellinen kauppapäivä) ja
   `ingested_at` (nykyinen ingest-leima) omiksi kentikseen + `sale_date_precision`-lippu
   (`exact` | `quarter` | `unknown`). Päivitä detached/land-kaupat MML:n tarkalla päivällä.
3. Liitä MML-kaupat olemassa oleviin `property_asset`-kohteisiin (kiinteistötunnus/sijainti).

### Vaihe 2 — Suomi, osakeasunnot (keskipitkä)
1. Säilytä KVKL/hintatiedot.fi-lähde, mutta merkitse `sale_date_precision = quarter`
   (rehellinen tarkkuus) — älä esitä ingest-päivää kauppapäivänä.
2. **Seuraa HTJ/ASREK-kehitystä** (HTJ2 päättyy 2026): jos kauppahinta + päivä avautuvat,
   integroi autoritatiivisena osakeasuntolähteenä.
3. Selvitä Tilastokeskus-/Verohallinto-yhteistyön / tutkimuskäyttöluvan mahdollisuus, jos
   yksittäistason aineistoa tarvitaan.

### Vaihe 3 — EU-laajennus (pitkä aikaväli, "EU-tasoinen property-intel")
1. **Per-maa-konnektorit** kansallisiin avoimiin rekistereihin, prioriteetti datan
   avoimuuden mukaan: 🇫🇷 DVF → 🇳🇱 Kadaster → 🇩🇰 Statistics Denmark → (🇸🇪/🇳🇴 selvitys).
2. **Harmonisoitu skeema**: lisää `country_code`, alueavaimeksi **NUTS** + kansallinen
   postinumero, `currency`, `source_precision`. Säilytä kansallinen rikastus erikseen.
3. **INSPIRE/HVD-linjaus**: käytä cadastral parcels -HVD-rajapintoja geokoodaukseen ja
   palsta-/rakennuslinkitykseen (yhtenäinen geopohja maiden yli).
4. Tuotteista monikielisyys + maakohtaiset markkina-analytiikkanäkymät web-UI:hin.

### Läpileikkaavat
- **Datalaadun rehellisyys**: kauppapäivän tarkkuus aina eksplisiittinen (`precision`-lippu),
  ei koskaan ingest-päivää myyntipäivänä.
- **GDPR / henkilötiedot**: kaupoissa voi olla henkilötietoja (osapuolet) — käsittele
  per-maa lisenssin + tietosuojan mukaan; säilytä vain analytiikkaan tarvittava.

---

## 5. Avoimet kysymykset / riskit

- **MML:n osakeasunto-kattamattomuus** → Suomen osakeasuntojen tarkka päivä jää auki
  kunnes HTJ tai Verohallinto-väylä avautuu.
- **MML OGC API:n** lopullinen tietosisältö/formaatti/lisenssiehdot vahvistettava julkaisun
  (kevät 2026) yhteydessä.
- **Ruotsi/Norja**: avoimen kauppadatan saatavuus + lisenssi vahvistamatta.
- **DVF/Kadaster**: päivitystiheys, historian pituus, geokoodauksen kattavuus per maa.
- **Lisenssien yhteensopivuus** (CC4.0 vs maakohtaiset ehdot) data-tuotteen jakelussa.

---

## 6. Lähteet

**Suomi**
- [MML — Kiinteistöjen kauppahintarekisteri](https://www.maanmittauslaitos.fi/kiinteistotiedot-ja-niiden-hankinta/kiinteistojen-kauppahintarekisteri)
- [MML — Kauppahintarekisterin tietopalveluiden uudistus (OGC API, kevät 2026)](https://www.maanmittauslaitos.fi/kiinteistot/kiinteistojen_kauppahintarekisterin_tietopalveluiden_uudistus)
- [MML — Huoneistotietojärjestelmä (HTJ/ASREK)](https://www.maanmittauslaitos.fi/en/residential-and-commercial-property-information-system)
- [Tilastokeskus — Osakeasuntojen hinnat: tietosuojaseloste (aineistolähteet)](https://www2.tilastokeskus.fi/meta/tietosuojaselosteet/tietosuojaseloste_osakeasuntojen_kauppahinnat.html)
- [Tilastokeskus — Osakeasuntojen hinnat: dokumentaatio](https://stat.fi/fi/tilasto/dokumentaatio/ashi)

**EU-sääntely**
- [Euroopan komissio — Open data & high-value datasets](https://digital-strategy.ec.europa.eu/en/policies/open-data)
- [Komissio — High-value datasets: Q&A](https://digital-strategy.ec.europa.eu/en/faqs/high-value-datasets-questions-and-answers)
- [datos.gob.es — INSPIRE & High Value Geospatial Assemblies Regulation](https://datos.gob.es/en/blog/complying-europe-inspire-and-high-value-geospatial-assemblies-regulation)
- [European e-Justice — Land registers in EU countries](https://e-justice.europa.eu/topics/registers-business-insolvency-land/land-registers-eu-countries/nl_en)

**Kansalliset avoimet kauppadatat**
- [🇫🇷 DVF — Demandes de valeurs foncières (data.gouv.fr)](https://www.data.gouv.fr/datasets/demandes-de-valeurs-foncieres)
- [🇫🇷 DVF+ open-data (Cerema, PostGIS/CSV/GPKG)](https://datafoncier.cerema.fr/donnees/autres-donnees-foncieres/dvfplus-open-data)
- [🇫🇷 DVF-sovellus (haku kauppapäivällä)](https://app.dvf.etalab.gouv.fr/)
- [🇳🇱 Kadaster — property sales and prices](https://www.dacb.nl/content/property-sales-and-prices)
- [🇩🇰 Statistics Denmark — Sales of real property](https://www.dst.dk/en/Statistik/dokumentation/documentationofstatistics/sales-of-real-property/statistical-presentation)
