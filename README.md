# SENTINEL — Normbeeld & afwijkingsanalyse

Een Streamlit-tool voor analisten: upload tijdreeksdata (waarnemingen,
incidenten, bewegingen), en de tool bouwt per locatie een **normbeeld** —
wat is normaal hier — signaleert afwijkingen, en voorspelt vooruit.

Gebouwd voor niet-programmeurs: importeren via de browser, kolommen worden
automatisch herkend, analyse draait zonder configuratie.

## Snel starten

```bash
pip install -r requirements.txt
streamlit run app.py
```

Windows: dubbelklik `start.bat`.

Klik daarna op **"Laad demo-dataset"** voor een gevuld voorbeeld
(open-source data: Russian missile attacks op Oekraïne, 2022-2026).

## Wat de tool doet

1. **Import** — Excel/CSV upload; kolom-rollen (tijd, waarde, locatie,
   categorie, coördinaten) worden automatisch voorgesteld. Gemengde
   datum-formaten worden robuust geparsed; gedropte rijen worden gemeld.
2. **Normbeeld** — per locatie een verwachte waarde + tolerantieband.
   Banden zijn asymmetrisch (quantile-gebaseerd) en wegen recente data
   zwaarder, zodat ze het huidige regime volgen.
3. **Forecast** — 5 voorspelmethoden (STL, Holt-Winters, rolling,
   seasonal naive, mediaan). In de detail-weergave kiest een **backtest**
   de empirisch beste methodes en toont de eerlijke voorspelfout.
4. **Afwijkingsdetectie** — 5 detectie-algoritmes stemmen (met deels
   overlappende gevoeligheid — zie METHODS.md);
   severity vereist minimaal 2 stemmen (hoog = vrijwel unaniem).
5. **Vergelijken** — twee reeksen overlay met automatische lag-detectie
   ("reeks B volgt A met ~X perioden", via cross-correlatie), plus
   change-point markers (significante niveau-verschuivingen) en
   seizoensindicatie op de tijdlijn.
6. **Export** — PDF-briefing en Excel-rapport.

## Opslag

Standaard lokaal SQLite (`data/store.db`). Voor blijvende, gedeelde opslag
koppel je een externe Postgres via `DATABASE_URL` (env-var) of
`database_url` als secret — de code schakelt automatisch over. Zie
`SETUP_DATABASE.md`. Schema-wijzigingen lopen via Alembic
(`python -m alembic upgrade head`).

Toegang loopt via één gedeeld teamwachtwoord (`core/auth.py`, met
lockout na herhaald falen). Elke muterende actie en login-poging staat in
de audit-trail (tabel `audit_log`); achter een SSO-reverse-proxy wordt de
`X-Forwarded-User`-header automatisch de identiteit.

## Architectuur

```
app.py                  Entry-shell: config, auth, sidebar, routing (~180 regels)
ui/
  theme.py              Paletten + CSS (P = lazy palet-proxy)
  state.py              Sessie-state-defaults
  cache.py              st.cache_data-wrappers rond de analyse-kern
  components.py         Gedeelde componenten (topbar, markeringen, annotaties)
  pages/                overview / normbeeld / compare / settings
core/
  storage.py            Opslag via SQLAlchemy (SQLite of Postgres) + audit-trail
  import_data.py        Excel/CSV-parsing + kolom-mapping
  validation.py         Import-validatie (blokkerende fouten + warnings)
  auto_mapping.py       Kolom-rol detectie (heuristieken)
  normbeeld.py          Normbeeld: forecast, banden, backtest  ← kern
  comparison.py         Cross-correlatie/lag (permutatietest), change-points
  auto_pilot.py         Detectie-ensemble + severity-stemming
  profiler.py           Data-profilering (seizoen, trend, stationariteit)
  explanations.py       Plain-language uitleg per bevinding
  ingest.py             Ingest-pipeline (connector → validatie → opslag)
  briefing.py           PDF-export (fpdf2)
  excel_export.py       XLSX-export
  annotations.py        Analist-notities per bevinding
  auth.py               Wachtwoord-login met lockout
  logging_setup.py      Gestructureerde (JSON-)logging
api/                    FastAPI-service over core/ (uvicorn api.main:app)
connectors/             Plug-in databronnen voor automatische inwinning
ingest_worker.py        Geplande inwinning (APScheduler; aparte service)
detectors/              Plug-in detectie-algoritmes (1 bestand = 1 methode)
visualizations/         Plug-in grafieken
migrations/             Alembic-migraties
i18n/nl.py              Alle UI-teksten (Nederlands)
tests/                  Pytest-suite (kern-wiskunde, storage, API, UI-smoke)
```

Wetenschappelijke verantwoording van elke rekenmethode: **METHODS.md**.
Productie-deployment (Postgres + TLS + backups): `docker-compose.prod.yml`.

### Een detectiemethode toevoegen

Maak een bestand in `detectors/` met een klasse die erft van
`detectors.base.Detector` en een `detect(df, time_col, value_col, **params)`
implementeert die `anomaly_score`- en `is_anomaly`-kolommen teruggeeft.
Het bestand wordt bij de volgende start automatisch opgepikt
(zie `detectors/zscore.py` als voorbeeld).

### Een voorspelmethode toevoegen

In `core/normbeeld.py`: voeg een `_xxx_forecast(series, period, horizon)`
toe, registreer hem in `PREDICTION_METHODS` + `PREDICTION_METHOD_DETAILS`,
en voeg een branch toe in `_forecast_with()`. De backtest pikt hem
automatisch mee.

## Belangrijke ontwerpkeuzes

- **Banden**: quantile-gebaseerd op residuen, met exponentiële
  recency-weging (halfwaardetijd = ⅓ van de historie) en adaptieve tail
  (`alpha = clip(5/n, 0.01, 0.10)`). Een symmetrische ±2σ-band gaf
  betekenisloze ondergrenzen (0) op scheve count-data.
- **Severity**: absolute stem-aantallen, niet fracties. Minimaal 2 methodes
  moeten het eens zijn; zie `classify_severity()` in `core/auto_pilot.py`.
- **Methode-selectie**: overzichten gebruiken een snelle heuristiek;
  de detail-weergave draait een rolling-origin backtest (gecachet).
- **Incomplete buckets**: bij week/maand-aggregatie wordt de laatste
  onvolledige periode weggelaten (voorkomt valse "onder band"-alerts).

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

De suite dekt: datum-parsing (gemengde formaten), kolom-detectie
(JSON-afwijzing, coördinaat-validatie), banden (niet-degeneraat,
spike-detectie), backtest, severity-tabel, en storage (dedupe, batch-insert).
**Draai dit na elke wijziging aan `core/`.**

## Deployment

- Lokaal: zie Snel starten hierboven.
- Streamlit Community Cloud (demo's, niet-gevoelige data):
  `DEPLOY_STREAMLIT_CLOUD.md`
- Docker / eigen server / on-premise: `DEPLOY.md`

**Let op**: op Streamlit Cloud is de opslag ephemeral — geüploade data
verdwijnt bij een herstart. Voor blijvend gebruik: eigen server met
gemount volume, of een externe database.

## Bekende beperkingen

- Compound-locaties ("X and Y and Z") worden als één unieke locatie
  behandeld; tellingen zijn niet te splitsen over de delen.
- Forecast-nauwkeurigheid hangt af van de data; de backtest-tabel in de
  detail-weergave toont de eerlijke fout per methode. Escalaties die
  buiten elk historisch patroon vallen zijn per definitie niet voorspelbaar
  — de tool flagt ze dan als afwijking.
- Eén gebruiker per database; geen gelijktijdige multi-user editing.
