# Anomalie-detectie — Normbeeld & afwijkingsanalyse

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
4. **Afwijkingsdetectie** — 5 onafhankelijke detectie-algoritmes stemmen;
   severity vereist minimaal 2 stemmen (hoog = vrijwel unaniem).
5. **Export** — PDF-briefing en Excel-rapport.

## Architectuur

```
app.py                  Streamlit UI (pagina's, styling, routing)
core/
  storage.py            SQLite-laag (datasets, observaties, annotaties)
  import_data.py        Excel/CSV-parsing + kolom-mapping
  auto_mapping.py       Kolom-rol detectie (heuristieken)
  normbeeld.py          Normbeeld: forecast, banden, backtest  ← kern
  auto_pilot.py         Detectie-ensemble + severity-stemming
  profiler.py           Data-profilering (seizoen, trend, stationariteit)
  explanations.py       Plain-language uitleg per bevinding
  briefing.py           PDF-export (fpdf2)
  excel_export.py       XLSX-export
  annotations.py        Analist-notities per bevinding
  auth.py               Optionele wachtwoord-login
detectors/              Plug-in detectie-algoritmes (1 bestand = 1 methode)
visualizations/         Plug-in grafieken
i18n/nl.py              Alle UI-teksten (Nederlands)
tests/                  Pytest-suite voor de kern-wiskunde
```

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
