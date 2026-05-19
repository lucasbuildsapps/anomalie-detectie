# Test-datasets voor evaluatie

Hier vier publieke, niet-gevoelige datasets die geschikt zijn om de tool
mee te valideren. Allemaal hebben ze duidelijke seizoens- of
gebeurtenis-patronen die zichtbaar zouden moeten zijn in het normbeeld.

## 1. ACLED (Armed Conflict Location & Event Data)

**Wat**: gestructureerde data over gewapende incidenten wereldwijd —
ideaal als test voor inlichtingen-analyse use cases. Open data, maar je
moet een gratis academisch/non-profit account aanvragen.

- **URL**: https://acleddata.com/data-export-tool/
- **Format**: CSV download
- **Kolommen om te mappen**:
  - `event_date` → time
  - `fatalities` of `event_count` (na groepering) → value
  - `country` of `admin1` → location_name
  - `event_type` (bv. "Battle", "Explosion") → category
  - `latitude` / `longitude` → lat / lon

**Wat de tool zou moeten vinden**: pieken rond bekende escalaties
(invasies, grote offensieven, terrorist incidents).

---

## 2. USGS Earthquake Catalog

**Wat**: alle aardbevingen wereldwijd, real-time data. Geen account nodig.

- **URL**: https://earthquake.usgs.gov/earthquakes/feed/v1.0/csv.php
- **Direct CSV (laatste maand)**:
  `https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_month.csv`
- **Format**: CSV
- **Kolommen om te mappen**:
  - `time` → time
  - `mag` (magnitude) → value
  - `place` (string locatie) → location_name
  - `type` (earthquake/explosion/etc) → category
  - `latitude` / `longitude` → lat / lon

**Wat de tool zou moeten vinden**: grote bevingen als statistische
uitschieters; clusters rond actieve breuklijnen.

---

## 3. NYC Open Data — 311 service requests

**Wat**: alle meldingen aan de NYC 311 service (gat in de weg,
geluidsoverlast, etc.). Miljoenen rijen, ideaal voor schaal-test.

- **URL**: https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2010-to-Present/erm2-nwe9
- **Format**: CSV (filter eerst op recente periode)
- **Kolommen om te mappen**:
  - `Created Date` → time
  - Aggregaat per dag (count rows) → value
  - `Borough` of `City` → location_name
  - `Complaint Type` → category
  - `Latitude` / `Longitude` → lat / lon

**Wat de tool zou moeten vinden**: weekend- en feestdagen-patronen,
piek-meldingen na grote events (storm, hittegolf).

---

## 4. KNMI Daagse Weersgegevens

**Wat**: weerdata Nederland per dag per station. Open data, geen account.

- **URL**: https://www.knmi.nl/nederland-nu/klimatologie/daggegevens
- **Format**: CSV per station
- **Kolommen om te mappen**:
  - `YYYYMMDD` → time
  - `RH` (neerslag), `FG` (windsnelheid), of `TG` (gemiddelde temp) → value
  - station-naam (afgeleid uit bestandsnaam) → location_name

**Wat de tool zou moeten vinden**: jaarcyclus, weersextremen
(hittegolven, stormen).

---

## Hoe we elke dataset gaan toetsen

Voor elke test:

1. **Importeren** → checken hoeveel rijen worden gedropt (>10% = probleem)
2. **Auto-mapping** → check of de juiste kolommen zijn gekozen
3. **Normbeeld berekenen** → check of expected_value en band realistisch zijn
4. **Bekende events checken** — bv. voor aardbevingen: M7+ in 2024,
   voor 311: dagen na Hurricane Sandy

## Snelle download-commando's

```bash
# USGS earthquakes (laatste maand, ~10K rijen)
curl -o earthquakes.csv \
  "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_month.csv"

# NYC 311 (filter via API, laatste 30 dagen)
curl -o nyc_311.csv \
  "https://data.cityofnewyork.us/resource/erm2-nwe9.csv?\$where=created_date%3E%272025-04-01%27&\$limit=50000"
```

## Welke dataset eerst proberen?

Mijn voorstel: **USGS Earthquakes** voor de eerste demo. Reden:
- Geen account nodig
- Duidelijke structuur (één event = één rij)
- Werelddekkende `place` strings die zich gedragen als locaties
- Magnitude-waarden hebben mooie verdelingen
- Iedereen begrijpt aardbevingen (geen domein-kennis nodig voor demo)

Dan **ACLED** als de demo aan inlichtingen-mensen is — die herkennen
direct welke pieken bij welke conflicten horen.
