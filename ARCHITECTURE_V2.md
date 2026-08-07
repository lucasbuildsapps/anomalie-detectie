# SENTINEL v2 — architectuur en ontwerpbesluiten

> Status: **ontwerp**, nog geen implementatie.
> Dit document is het resultaat van de requirements-discovery na de review van
> v1. Het beschrijft wat gebouwd wordt, waarom, en in welke volgorde.
> Rekenkundige verantwoording per methode blijft in `METHODS.md`; dit document
> gaat over structuur en besluiten.

SENTINEL v2 is geen anomaliedetector met een intelligence-jasje. Het is een
**warning-platform** dat één vraag beantwoordbaar moet maken:

> *Is er iets aan de hand, hoe zeker weten we dat, en waarop rust dat oordeel?*

Inclusief het antwoord "nee". Een rustige omgeving hoort een rustige uitvoer
te geven, en dat antwoord moet net zo navolgbaar zijn als een alarm.

---

## 0. Vastgelegde besluiten

Deze acht besluiten zijn genomen in de discovery-fase en sturen de rest van het
ontwerp. Ze staan hier zodat later duidelijk is wat een besluit was en wat een
afleiding.

| # | Besluit | Gevolg |
|---|---|---|
| 1 | **Twee lagen**: entiteit-engine voedt de event-laag | Entiteitsgedrag produceert géén oordelen, maar getypeerde events. Eén oordeelmodel over beide paradigma's. |
| 2 | **Eén regio diep + gerepareerde kern**; UX-schil voor alle vijf regio's | Regiomodules worden declaratief/config-gedreven vanaf dag één. |
| 3 | Inhoudelijke focus: **Euro-Atlantic** (count) + **NLD EEZ** (entiteit) | Eén regio per paradigma; bewijst de abstractie. |
| 4 | **OSINT-only, self-hosted, klein team** | Geen accreditatietraject. Bindende juridische randvoorwaarde is AIS-licentie, niet rubricering. |
| 5 | AIS: **aisstream.io** (live, vooruit opnemen) + **DMA/Kystverket open AIS** (historie, validatie) | Methodiek wordt gevalideerd op historische open data; Nederlandse dekking groeit vooruit mee. |
| 6 | **Alert-budget 5–15 per week per regio** | Dit is het kalibratiedoel dat het verwijderde quotum vervangt. |
| 7 | **Streamlit blijft** in v2; FastAPI-splitsing in fase 6 | Behoudt bestaande UI-investering; kaartinteractie is bewust tijdelijk suboptimaal. |
| 8 | **Dubbele baseline**: vastgelegde referentie + adaptief | Divergentie tussen beide *is* het escalatiesignaal. |

Open, niet-blokkerend: onafhankelijke incidentchronologie voor Euro-Atlantic
(nodig voor retrospectieve validatie), onderhoudscapaciteit, meertaligheid.

---

## 1. Wat blijft, wat wordt herbouwd, wat verdwijnt

v1 wordt niet weggegooid. De analyse hieronder is de basis voor de
migratiestrategie in §7.

### Behouden — vrijwel ongewijzigd

| Component | Waarom |
|---|---|
| `core/estimative.py` | De ICD 203-scheiding tussen waarschijnlijkheid (WEP) en vertrouwen (LCA), inclusief `violates_separation()`, is het sterkste onderdeel van v1. Alleen `assess_confidence()` wordt vervangen. |
| `core/indicators.py` (watchboard) | Vooraf geregistreerde, deductieve indicatoren. Wordt in v2 gepromoveerd van zijfeature tot **dragend principe** — zie §4. |
| `core/changes.py` | Verschil met de vorige beoordeling; ICD 203 vraagt dit expliciet. |
| Gap-policy `zero`/`interpolate`/`mask` | Het expliciet modelleren van "geen rapport ≠ geen activiteit" is precies goed voor inlichtingenwerk en wordt door de meeste tools fout gedaan. |
| MASE-backtest, `recommend_timescale` | Statistisch correct en schaalvrij; migreert vrijwel ongewijzigd. |
| Dedupe op `row_hash`, Alembic, `audit_log` | Degelijke techniek die blijft werken. |
| `METHODS.md`-discipline | Wordt in v2 een harde eis: elke detectiemethode krijgt een sectie én een test die de bewering controleert. |

### Herbouwen — idee klopt, uitvoering niet

| Component | Wat er mis is | Wat ervoor in de plaats komt |
|---|---|---|
| `core/normbeeld.py` (1663 r.) | Gecentreerde smoothing, hindsight-segmentatie, in-sample kalibratie | Causale online baseline-schatting achter een `AsOf`-contract (§3) |
| `assess_confidence()` | Feitelijk een lengtecheck: n≥60 + zelf-gekalibreerde dekking ⇒ "hoog" | Confidence uit meetbare inputs incl. out-of-sample kalibratie (§5) |
| `core/evaluation.py` | Labels uit eigen bevindingen (circulair), scoring per rij i.p.v. per event | Synthetische injectie + retrospectief, event-niveau (§6) |
| `core/signals.py` | Juiste vragen, ongeldige statistiek (onafhankelijkheidsaanname, geen multipliciteitscorrectie) | Blijven als **evidence providers**, met correcte nulverdeling |

### Verwijderen

| Component | Reden |
|---|---|
| `run_auto_pilot()` gevoeligheids-lus | Het quotum. Kan niet naast eis #2 bestaan. |
| `IsolationForest(contamination=...)` | Markeert 5% per constructie; een kwantielsnede, geen toets. |
| Detector-stemming als zelfstandige waarheidsbron | Wordt bewijs, nooit oordeel (§4). |
| `detectors/ensemble.py` | Dubbelop met de stemming; beide verdwijnen. |
| `similar_period()` | Ongecontroleerde max-correlatiezoektocht; produceerde een geruststellende historische analogie tijdens een ongekende escalatie. |
| `_suggest_best_aggregation()` als beslisser | De backtest doet dit meetbaar; de heuristiek blijft hooguit als fallback-hint. |

### Principes die blijven

Nederlandstalige analistuitvoer · ICD 203-taal · elk getal herleidbaar tot een
gedocumenteerde methode · afwezigheid is informatie · de UI vertelt wat er
*niet* is gedaan en waarom.

---

## 2. Architectuur in lagen

```
┌─────────────────────────────────────────────────────────────────────┐
│  ANALIST-INTERFACE   watchboard · alert-queue · kaart · assessment   │
├─────────────────────────────────────────────────────────────────────┤
│  ASSESSMENT-LAAG     ICD 203-oordeel · bewijsketen · alternatieven   │
├─────────────────────────────────────────────────────────────────────┤
│  CONFIDENCE          datakwaliteit · OOS-kalibratie · historische    │
│                      prestatie · corroboratie · detectievermogen     │
├─────────────────────────────────────────────────────────────────────┤
│  INDICATOR-LAAG  ←── HET ENIGE OORDEELMODEL                          │
│    één indicator = één vraag = één toets = één verdict               │
│    verdict ∈ {actief, niet-actief, onvoldoende data}                 │
├──────────────────────────┬──────────────────────────────────────────┤
│  BASELINE-LAAG           │  EVIDENCE-LAAG (nooit oordelend)          │
│   vaste referentie       │   detectoren · signalen · co-movement     │
│   adaptief (causaal)     │   correlatie tussen indicatoren           │
│   divergentie = escalatie│   alternatieve verklaringen               │
├──────────────────────────┴──────────────────────────────────────────┤
│  EVENT-STORE   ← het gedeelde datacontract (§3)                      │
├──────────────────────────┬──────────────────────────────────────────┤
│  ENTITEIT-ENGINE         │  DIRECTE EVENTS                           │
│   identiteit · tracks    │  (ACLED, MND-releases, meldingen)         │
│   gedrag → emit(Event)   │                                           │
├──────────────────────────┴──────────────────────────────────────────┤
│  INGESTIE   connectors · normalisatie · provenance · validatie       │
└─────────────────────────────────────────────────────────────────────┘

         Alles hierboven leest uitsluitend via  AsOfView(t)
```

### 2.1 De belangrijkste structurele ingreep: `AsOfView`

Eis #6 (causale verwerking) is in v1 een kwestie van discipline, en discipline
faalt: er zitten drie lekpaden in code die expliciet over lekkage nadenkt.
In v2 wordt het een **structureel onmogelijk gemaakt**.

Elke berekening die een productie-uitvoer voedt, krijgt data uitsluitend via:

```python
class AsOfView:
    """Een read-only venster op de wereld zoals die op tijdstip t bekend was."""
    as_of: datetime
    def observations(...) -> pd.DataFrame   # WHERE ingested_at <= as_of
    def events(...)       -> pd.DataFrame   #   AND event_time  <= as_of
    def positions(...)    -> pd.DataFrame
```

Twee gevolgen die er echt toe doen:

1. **`ingested_at` wordt verplicht op elke rij.** v1 heeft dit niet. Zonder
   "wanneer wisten wij dit" kun je niet reconstrueren wat de tool op datum X
   had kunnen zeggen, en is elke backtest structureel te optimistisch —
   laatkomende rapportage zit er al in verwerkt.
2. **Backtest en productie draaien exact dezelfde code.** Een replay is niets
   anders dan de pipeline draaien met een andere `as_of`. Dat is de enige
   manier waarop een evaluatiecijfer iets zegt over productiegedrag.

Handhaving: de baseline- en detectiefuncties accepteren geen kale DataFrame
meer, alleen een `AsOfView`. Een test controleert dat geen enkele module in
`core/baseline/` of `core/detect/` de storage-laag direct importeert.

### 2.2 De twee-lagen-brug

De entiteit-engine produceert **geen oordelen**. Zij produceert getypeerde
events met geometrie, entiteitsverwijzing en herkomst:

```
ais_gap · loiter · route_deviation · proximity_critical_infra
identity_inconsistency · dark_rendezvous · speed_anomaly
```

Die events landen in dezelfde `event`-tabel als een ACLED-strike of een
Taiwan-MND-melding, en worden daarna door precies dezelfde indicator-machinerie
beoordeeld. Dit is wat één oordeelmodel over twee paradigma's mogelijk maakt,
in plaats van twee systemen onder één logo.

Consequentie die bewaakt moet worden: een `loiter`-event is een **waarneming**,
geen afwijking. Of loitering ongewoon is, bepaalt de indicator — tegen een
baseline van hoeveel loitering daar normaal is, voor dat scheepstype, in dat
seizoen. Zonder die scheiding sluipt het quotum via de achterdeur terug binnen.

---

## 3. Gemeenschappelijk datamodel

PostgreSQL + PostGIS; `position` als TimescaleDB-hypertable.

### 3.1 Kern-entiteiten

```sql
-- ── Herkomst ────────────────────────────────────────────────────────
source(
  id, key, name, kind,                    -- 'ais'|'event_db'|'osint'|'manual'
  url, reliability_grade CHAR(1),         -- Admiraliteitsschaal A–F
  credibility_grade  CHAR(1),             -- 1–6
  licence, redistribution_allowed BOOL,
  retention_days, created_at
)

ingest_run(
  id, source_id, started_at, finished_at,
  status, rows_in, rows_kept, rows_rejected,
  watermark,                              -- hervattingspunt
  error
)

-- ── Waarnemingen (append-only, immutable) ───────────────────────────
observation(
  id, source_id, ingest_run_id,
  event_time    TIMESTAMPTZ NOT NULL,     -- wanneer het gebeurde
  ingested_at   TIMESTAMPTZ NOT NULL,     -- wanneer wíj het wisten  ★
  reported_at   TIMESTAMPTZ,              -- wanneer de bron het meldde
  entity_id, event_type,
  value DOUBLE PRECISION, unit,
  geom GEOGRAPHY(Point,4326), geo_precision_m,
  region_key, area_key,
  attrs JSONB,
  row_hash,
  UNIQUE(source_id, row_hash)
)
```

★ `ingested_at` is de belangrijkste kolom in het schema. Zie §2.1.

```sql
-- ── Entiteiten en identiteit ────────────────────────────────────────
entity(
  id, kind,                               -- 'vessel'|'aircraft'|'actor'|'site'
  canonical_key, display_name,
  first_seen, last_seen, attrs JSONB
)

entity_identifier(                        -- identiteit verandert over tijd
  entity_id, scheme,                      -- 'mmsi'|'imo'|'callsign'|'name'
  value, valid_from, valid_to,
  confidence DOUBLE PRECISION,
  source_id
)

position(                                 -- hypertable, partitie op ts
  entity_id, ts TIMESTAMPTZ,
  geom GEOGRAPHY(Point,4326),
  sog, cog, heading, nav_status,
  source_id, ingested_at
)

track(
  id, entity_id, start_ts, end_ts,
  geom GEOGRAPHY(LineString,4326),
  n_points, gap_count, max_gap_seconds, quality
)

-- ── De brug ─────────────────────────────────────────────────────────
event(
  id, region_key, event_type,
  entity_id,                              -- nullable
  event_time, ingested_at,
  geom, area_key, magnitude, unit,
  attrs JSONB,
  producer,                               -- 'ingest' | 'entity_engine'
  derived_from JSONB                      -- herkomstketen naar observations
)
```

### 3.2 Beoordeling

```sql
indicator(
  id, region_key, key, name,
  question TEXT,                          -- de vraag in gewone taal
  test_type, test_config JSONB,
  reference_baseline JSONB,               -- vastgelegde referentieperiode
  meaning TEXT,                           -- wat het betekent als hij afgaat
  status,                                 -- 'draft'|'active'|'retired'
  owner, created_at, reviewed_at
)

baseline(
  id, indicator_id,
  kind,                                   -- 'fixed_reference' | 'adaptive'
  as_of, params JSONB, fitted JSONB,
  n_periods, coverage_oos                 -- out-of-sample, niet zelf-gekalibreerd
)

signal(                                   -- het oordeel, één per indicator per as_of
  id, indicator_id, as_of,
  verdict,                                -- 'actief'|'niet_actief'|'onvoldoende_data'
  effect_size, direction,
  detection_power JSONB,                  -- wat had gedetecteerd kunnen worden
  confidence_level, confidence_inputs JSONB
)

evidence(
  id, signal_id, kind,                    -- 'corroboration'|'alternative'|'context'
  weight, summary, refs JSONB
)

assessment(
  id, signal_id, region_key, created_at,
  statement, wep_term, wep_probability,
  lca_level, lca_reasons JSONB,
  alternatives JSONB, follow_up JSONB,
  analyst_id, superseded_by
)

evaluation_run(id, indicator_id, kind, config JSONB, created_at)
evaluation_result(
  run_id, scenario, injected_effect_size,
  detected BOOL, lead_time_periods, false_alarms, notes
)
```

### 3.3 Aggregatieregels (één plek, niet drie)

v1 aggregeerde op drie onderling onverenigbare manieren tegelijk (Z-score op
ruwe rijen, Rolling/STL op dagbuckets, normbeeld op de gekozen bucket). In v2:

- Aggregatie gebeurt **uitsluitend** in de indicator-laag, op basis van
  `test_config.aggregation`.
- Detectoren en evidence-providers krijgen een **al geaggregeerde reeks**
  aangeleverd en mogen zelf niet resamplen.
- Een test bewaakt dit: geen `.resample(` in `core/detect/` of `evidence/`.

---

## 4. Het oordeelmodel — één waarheid

### 4.1 Het contract

> **Eén indicator = één vraag = één toets = één verdict.**
> Bewijs verandert het vertrouwen, nooit het oordeel.

Dit lost de v1-splitsing tussen normbeeld en detector-stemming op zonder
stemming door iets anders te vervangen. Er wordt niet meer gestemd. Elke
warning-vraag wordt expliciet geformuleerd als indicator, met één gedeclareerde
toets. Het verdict is drieledig:

| Verdict | Betekenis |
|---|---|
| `actief` | De toets is overschreden, met effectgrootte en richting |
| `niet_actief` | De toets is uitgevoerd en niet overschreden — **een positief resultaat**, mét detectievermogen |
| `onvoldoende_data` | De toets kon niet uitgevoerd worden; expliciet onderscheiden van "niets aan de hand" |

Dat derde niveau is essentieel. In v1 zijn "we hebben gekeken en er is niets"
en "we konden niet kijken" niet van elkaar te onderscheiden, en dat is precies
het verschil waar warning-fouten in zitten.

### 4.2 Toetstypen

Vier, meer niet. Elke uitbreiding moet aantonen dat ze een vraag beantwoordt
die de bestaande vier niet aankunnen.

| Toets | Vraag | Methode |
|---|---|---|
| `level_deviation` | Is het nu ongewoon hoog/laag? | Causale band rond verwachting; overschrijding met effectgrootte |
| `sustained_divergence` | Loopt dit al langer op? | **Adaptief vs. vaste referentie** + CUSUM op gestandaardiseerde residuen |
| `condition` | Doet zich een vooraf beschreven omstandigheid voor? | Deterministische regel (drempel, stilte, nabijheid, N perioden achtereen) |
| `entity_behaviour` | Gedraagt een entiteit zich afwijkend? | Toets over entity-events t.o.v. peer-baseline (scheepsklasse × gebied × tijd) |

### 4.3 Aanhoudende escalatie — de dubbele baseline

Dit is de directe oplossing voor het ernstigste v1-gebrek: een vijfvoudige
aanhoudende escalatie leverde 5 markeringen in 30 dagen op, bij "hoog"
vertrouwen, omdat de verwachting mee-escaleerde van 17,8 naar 87,5.

Elke indicator wordt tegen **twee** baselines gehouden:

```
adaptief   — volgt het recente regime (wat v1 deed)
referentie — vastgelegd door een analist ("vredesnormaal = 2015–2019"),
             periodiek herzien, met datum en onderbouwing in het register
```

De **divergentie tussen beide is zelf het signaal**:

| Adaptief | Referentie | Interpretatie |
|---|---|---|
| normaal | normaal | rustig |
| **afwijkend** | normaal | incident / uitschieter |
| normaal | **afwijkend** | ⚠ **aanhoudende escalatie is baseline geworden** |
| afwijkend | afwijkend | acute verslechtering bovenop verhoogd niveau |

Rij drie is precies het geval dat v1 miste, en het is nu een expliciete,
benoembare toestand in plaats van een blinde vlek. Aanvullend wordt de
aanpassingssnelheid van de adaptieve baseline begrensd zodra de divergentie
oploopt — een ontwikkelende dreiging mag zichzelf niet wegmiddelen.

### 4.4 Tegenstrijdig bewijs

Bewijs kan het oordeel niet omkeren, maar wel drie dingen doen:

1. **Vertrouwen verhogen** — onafhankelijke corroboratie (met correctie voor
   effectieve onafhankelijkheid; `detector_agreement()` uit v1 migreert hierheen).
2. **Vertrouwen verlagen** — bekende verstoringen: datagat, bronwissel,
   rapportage-artefact, feestdag.
3. **Alternatieve verklaringen aandragen** — verplicht veld in de assessment.
   "Stijging valt samen met start van een nieuwe bron" is vaak de juiste
   verklaring, en de tool hoort hem zelf op te werpen.

---

## 5. Confidence-raamwerk

De ICD 203-vocabulaire uit v1 blijft; de scoring wordt vervangen. Vier
meetbare pijlers, geen zelfbeoordeling:

| Pijler | Meting | Bron |
|---|---|---|
| **Datakwaliteit** | dekking, versheid, bron-betrouwbaarheid (Admiraliteit) | `data_quality()` + `source.reliability_grade` |
| **Kalibratie** | out-of-sample banddekking t.o.v. doel, gemeten op een holdout die níét voor tuning is gebruikt | evaluatie-harnas |
| **Historische prestatie** | precision/recall van **deze indicator** op het evaluatie-harnas | `evaluation_result` |
| **Corroboratie** | effectief aantal onafhankelijke bewijsbronnen | evidence-laag |

Twee harde regels:

- **Geen enkele input mag door het model zelf gekalibreerd zijn.** In v1 koos
  `_pick_spread_window()` het venster dat de dekking op het doel bracht, waarna
  diezelfde dekking als bewijs van kalibratie werd gerapporteerd. In v2 lopen
  selectie en rapportage over gescheiden folds.
- **Reekslengte is geen vertrouwen.** Lengte mag hooguit een plafond zetten,
  nooit een pluspunt zijn.

### 5.1 Detectievermogen — de sleutel tot een geloofwaardig nulresultaat

Een kale "niets gevonden" is *minder* betrouwbaar dan een quotum, want de lezer
kan niet zien of er goed gekeken is. Elk `niet_actief`-verdict draagt daarom
het gemeten detectievermogen mee, afkomstig uit het evaluatie-harnas:

> *Geen significante afwijking. Bij de huidige configuratie zou een aanhoudende
> stijging van ≥35% binnen 10 dagen zijn opgemerkt (80% kans, gemeten op
> injectietests). Een langzamere opbouw dan dat valt onder de detectiedrempel.*

Dit is het verschil tussen "wij zwijgen" en "wij hebben gekeken, dit is hoe
scherp, en het bleef eronder".

---

## 6. Evaluatie-raamwerk

Vervangt de circulaire evaluatie volledig. Analist-bevestigingen blijven
bestaan, maar uitsluitend als *kalibratiesignaal voor triage-kwaliteit* —
nooit als grondwaarheid, omdat een analist alleen ziet wat het systeem heeft
opgeworpen en een gemiste gebeurtenis dus per constructie nooit als misser kan
tellen.

### 6.1 Synthetische injectie (primair)

Neem een echte, rustige reeks; injecteer een bekend effect; meet.

| Scenario | Parameters | Meet |
|---|---|---|
| Losse piek | amplitude × duur 1 | recall per amplitude |
| Plotselinge daling | amplitude | recall, richtingsjuistheid |
| Aanhoudende stijging | niveau × lengte | recall **en doorlooptijd** |
| Geleidelijke escalatie | helling × lengte | recall per helling ← *v1-blinde vlek* |
| Regimewissel | stapgrootte | detectie + hersteltijd baseline |
| Stilteperiode | lengte | recall (afwezigheid als signaal) |
| Ontbrekende data | gat-lengte × policy | géén vals alarm |
| Bronwissel | niveauverschuiving zonder betekenis | géén vals alarm |
| Zuivere ruis | — | **verwacht 0 alarmen** |

De laatste drie zijn negatieve controles en minstens zo belangrijk als de
positieve: v1 produceerde 13 bevindingen waaronder één "hoog" op zuivere
Poisson-ruis.

Voor de entiteit-laag een eigen generator: gesimuleerde scheepstracks met
geïnjecteerde loitering, AIS-gaten, identiteitswissels en routeafwijkingen —
plus negatieve controles die *niet* mogen vuren (legitiem vissen, wachten op
een ankerplaats, verkeersscheidingsstelsel volgen).

### 6.2 Retrospectieve validatie (secundair)

Onafhankelijk samengestelde chronologie per regio, uit gepubliceerde bronnen,
**niet** uit tool-output. Replay met `AsOfView` op elke dag in de historie;
meet of de indicator afging vóór of na het incident, en met hoeveel voorsprong.

### 6.3 Event-niveau, niet rij-niveau

v1 telde markeringen per waarnemingsrij, waardoor één gemarkeerde dag met 100
rijen als 100 markeringen telde en precisie betekenisloos werd. In v2:

- Markeringen worden geclusterd tot **episodes** (aaneengesloten actieve perioden).
- Eén episode die een incident binnen tolerantie raakt = één treffer.
- Meerdere episodes voor één incident = één treffer + duplicaten apart geteld.
- Precisie = episodes-met-incident / totaal-episodes.

### 6.4 Rapport

Per indicator een detectievermogen-curve (recall vs. effectgrootte per
scenariotype) en een verwacht wekelijks alarmvolume. Dat laatste is de knop
waarmee het budget van 5–15 per week wordt gehaald: drempels worden gezet op
gemeten volume bij vereist vermogen, niet op een gewenst aantal alarmen.

---

## 7. Softwarestructuur

```
sentinel/
  core/
    contracts/        dataclasses + validatie van het datamodel
    time/             AsOfView, PointInTimeStore        ← eerst bouwen
    baseline/         causale schatters (fixed + adaptive), dubbele baseline
    detect/           de vier toetstypen
    indicators/       registry, evaluatie, lifecycle
    confidence/       vier pijlers + detectievermogen
    evidence/         corroboratie, alternatieve verklaringen
    estimative/       ICD 203  (uit v1, ~ongewijzigd)
    assess/           assessment-generatie, bewijsketen
  entity/
    identity/         MMSI/IMO-resolutie, spoofing-detectie
    tracks/           trackopbouw, segmentatie, gap-detectie
    behaviour/        loiter, deviation, proximity, rendezvous
    emit/             behaviour → Event
  ingest/
    connectors/       aisstream, dma_ais, kystverket, acled, gdelt, ...
    normalize/        naar het gedeelde contract
    provenance/       source-grading, ingest_run-administratie
  regions/
    base.py           RegionModule-descriptor
    euro_atlantic/    volledig
    nld_eez/          volledig
    mena/             config-only schil
    indo_pacific/     config-only schil
    caribbean/        config-only schil
  eval/
    synthetic/        injectors per scenario
    retrospective/    replay-harnas
    power/            detectievermogen-curves
    report/
  storage/            SQLAlchemy + PostGIS + Timescale
  api/                FastAPI (bestaat al; groeit mee)
  ui/                 Streamlit (v2), splitsing in fase 6
```

### 7.1 De regiomodule als declaratie

Een regio is configuratie plus optionele eigen detectoren — geen eigen
applicatie. Dit is wat de vijf-tabbladen-schil goedkoop maakt.

```python
@dataclass(frozen=True)
class RegionModule:
    key: str                        # 'nld_eez'
    name: str                       # 'Nederlandse EEZ'
    status: MonitoringStatus        # NOT_MONITORED | DATA_ONLY | MONITORED
    geography: GeoScope             # polygonen, gebieden van belang
    sources: list[SourceSpec]
    indicators: list[IndicatorSpec]
    default_aggregation: str
    reference_period: DateRange | None    # vastgelegde baseline
    activation_requirements: list[str]    # wat nodig is om te activeren
```

### 7.2 Eerlijke lege tabbladen

Een regio zonder monitoring mag **niet** als "rustig" lezen. Elk tabblad toont
onmiskenbaar zijn `MonitoringStatus`, en een niet-gemonitorde regio toont
`activation_requirements` in plaats van een lege grafiek. Zo is de schil
tegelijk een eerlijke weergave en een zichtbare roadmap.

---

## 8. Regionaal raamwerk — twee uitgewerkte voorbeelden

### 8.1 NLD EEZ — entiteit-paradigma

```
Regio        NLD EEZ
Indicator    Onbekend vaartuiggedrag nabij kritieke infrastructuur
Vraag        Is er een vaartuig dat zich nabij kabels/leidingen/windparken
             gedraagt op een manier die voor zijn klasse ongebruikelijk is?

Bronnen      aisstream.io (live NL) · DMA/Kystverket (historie, validatie)
             EMODnet infrastructuur-lagen · Kadaster/RVO windparkgeometrie

Entity-events  ais_gap · loiter · proximity_critical_infra
               identity_inconsistency · dark_rendezvous

Baseline     Peer-baseline: scheepsklasse × gebied × maand × dagdeel
             (vissersschepen loiteren normaal; bulk carriers niet)
             + vaste referentie: gedragsverdeling 2022–2023

Toets        entity_behaviour — afwijking t.o.v. peer-baseline,
             gecombineerd met contextregels (afstand tot infrastructuur,
             duur, herhaling, AIS-gat-lengte)

Bewijs       track-geometrie · gap-duur · identiteitshistorie ·
             eerdere passages van dezelfde entiteit ·
             alternatieve verklaring: weer, ankerplaats-wachtrij, storing

Uitvoer      ICD 203-assessment met kaartfragment, track, en bewijsketen
```

Bewust **niet** gebouwd: attributie ("dit is een Russisch vaartuig dat
sabotage voorbereidt"). De tool rapporteert gedrag en context; de duiding is
het werk van de analist.

### 8.2 Euro-Atlantic — count-paradigma

```
Regio        Euro-Atlantic (Rusland–Oekraïne)
Indicator    Aanhoudend verhoogd aanvalstempo
Vraag        Ligt het tempo structureel boven het referentieniveau,
             ook als het recente gemiddelde het inmiddels normaal vindt?

Bronnen      ACLED (licentie nodig) · bestaande demo-dataset ·
             onafhankelijke chronologie voor validatie

Baseline     Adaptief (recent regime) + vaste referentie (analist declareert)

Toets        sustained_divergence — CUSUM op gestandaardiseerde residuen
             t.o.v. de vaste referentie; divergentiematrix uit §4.3

Bewijs       change-point · persistentie-run · regio-co-movement ·
             alternatieve verklaring: wisseling in rapportagebron

Uitvoer      ICD 203-assessment + verschil met vorige beoordeling
```

---

## 9. Roadmap

| Fase | Inhoud | Klaar wanneer |
|---|---|---|
| **1. Kritieke correcties** | `AsOfView` + `ingested_at`; quotum-lus, contamination en stemming eruit; causale baseline; aggregatie op één plek | Zuivere ruis levert 0 alarmen; geen module leest buiten `AsOfView` |
| **2. Evaluatie & kalibratie** | Synthetische injectie (9 scenario's), event-niveau scoring, detectievermogen-curves, retrospectief replay | Detectievermogen-curve per indicator; drempels gezet op 5–15/week |
| **3. Regio-architectuur** | `RegionModule`, indicator-registry, dubbele baseline, confidence-raamwerk, vijf-tabbladen-schil met eerlijke status | Euro-Atlantic draait volledig als regiomodule |
| **4. NLD EEZ** | Postgres/PostGIS/Timescale, AIS-connectors, identiteit, tracks, gedragsprimitieven, entity-events, synthetische trackgenerator | Loitering en dark-gaps aantoonbaar gedetecteerd op DMA-historie |
| **5. Overige regio's** | MENA / Indo-Pacific / Caribbean, config-gedreven, per stuk pas activeren als de bron er is | Elk actief tabblad heeft een gevalideerde indicator |
| **6. UX & hardening** | FastAPI-splitsing, kaartcomponent, RBAC, retention, monitoring | Multi-user, kaartinteractie op trackniveau |

Fase 1 en 2 zijn niet onderhandelbaar en gaan vooraf aan élke regionale
functionaliteit. Een regiomodule bovenop een ongekalibreerde kern is een
duurdere versie van het huidige probleem.

---

## 10. Eerste implementatiedoelen

In volgorde. De eerste drie zijn de kritieke pad.

1. ~~**`core/time/as_of.py`** — `AsOfView`, `PointInTimeStore`. Plus migratie die
   `ingested_at` toevoegt en backfilled (voor bestaande rijen: `event_time`,
   gemarkeerd als geschat). Alles hangt hieraan.~~ **Gedaan** — zie
   `sentinel/core/time/as_of.py`, migratie `0007_ingested_at.py`,
   `storage.load_observations_as_of()`, en de drie testbestanden in §12.
   Aanvulling die tijdens het bouwen nodig bleek: een **aankomst-beleid** bij
   import (`arrival="now"` voor connector-inwinning, `"event_time"` voor
   bulk-historie). Zonder dat onderscheid krijgt een historische CSV-import de
   importdatum als aankomsttijd en levert elke replay over die historie nul
   rijen op.
2. **`core/contracts/`** — de dataclasses uit §3 met validatie. Het contract
   vóór de implementatie, zodat entiteit- en count-paden niet uit elkaar lopen.
3. **`eval/synthetic/injectors.py`** — de negen scenario's. Bewust vóór de
   nieuwe detectie: zonder meetlat is elke verbetering een bewering. Dit is ook
   het goedkoopste onderdeel van het hele plan.
4. **`core/baseline/causal.py`** — trailing smoothing, online segmentatie,
   dubbele baseline. Vervangt het hart van `normbeeld.py`.
5. **`core/detect/level.py` + `sustained.py`** — de eerste twee toetstypen.
6. **`core/indicators/registry.py`** — indicator als eerste-klas object met
   lifecycle; migratie van `core/indicators.py`.
7. **`core/confidence/`** — vier pijlers + detectievermogen.

### Migratiestrategie

`sentinel/` naast de bestaande boom opbouwen, niet in plaats van. v1 blijft
draaien terwijl v2 groeit. De volgorde:

1. Storage-migratie (`ingested_at`, nieuwe tabellen) — v1 blijft werken.
2. `sentinel/core/` opbouwen met eigen tests; v1 onaangeroerd.
3. Euro-Atlantic als eerste `RegionModule` op de nieuwe kern.
4. UI schakelt per pagina over; `ui/pages/normbeeld.py` als laatste.
5. v1-kern verwijderen pas als de evaluatie aantoont dat v2 minstens
   gelijkwaardig is op dezelfde data.

### Afhankelijkheden

Nieuw: `psycopg`, `GeoAlchemy2`, `shapely`, `timescaledb` (extensie),
`websockets` (aisstream), `hypothesis` (property-based tests).
Weg: `scikit-learn` (alleen nog voor IsolationForest gebruikt — die verdwijnt).

---

## 11. Wat bewust niet gebouwd wordt

Elke regel hieronder is een besparing, geen tekortkoming. Ze staan hier zodat
ze niet stilletjes terugkeren.

| Niet bouwen | Reden |
|---|---|
| **Automatische attributie** | "Wie zit hierachter" is analistenwerk. Een tool die dit suggereert, creëert onnavolgbare oordelen. |
| **Voorspelling van de volgende aanval** | Geen data die dit ondersteunt; de illusie van voorspelling is gevaarlijker dan het ontbreken ervan. |
| **Netwerk-/linkanalyse voor drugsvaart** | Vereist data die in OSINT niet bestaat. Zonder relatiedata is elk netwerkbeeld verzonnen. |
| **Real-time streaming-detectie** | De missie is strategische waarschuwing. Uurlijkse batch is ruim voldoende; streaming verdrievoudigt de operationele complexiteit. |
| **Per-regio maatwerk-dashboards** | Vijf dashboards is vijf keer onderhoud en nul gedeelde lessen. Configuratie, geen code. |
| **Drone-meldingen als detectiemodule** | Er is geen systematische machine-leesbare bron. Wordt een handmatige registratie- + indicatormodule, niet detectie. Anders bouwen we detectie op ruis. |
| **Meer dan vier toetstypen** | Elke vijfde toets moet aantonen dat de bestaande vier de vraag niet aankunnen. |

---

## 12. Hoe we weten dat v2 geslaagd is

Concrete, toetsbare acceptatiecriteria per niet-onderhandelbare eis:

1. **Evaluatie-integriteit** — detectievermogen-curve per indicator uit
   synthetische injectie; geen enkel gepubliceerd cijfer rust op
   analist-bevestigingen.
2. **Echt nulresultaat** — zuivere Poisson-ruis levert 0 alarmen; elk
   `niet_actief` draagt detectievermogen mee.
3. **Aanhoudende escalatie** — een 3× escalatie over 30 perioden wordt
   gedetecteerd met gerapporteerde doorlooptijd; de divergentiematrix benoemt
   expliciet "escalatie is baseline geworden".
4. **Eén waarheid** — geen enkele UI-weergave toont twee verschillende
   afwijkingsoordelen over hetzelfde punt.
5. **Confidence** — geen input is door het model zelf gekalibreerd;
   reekslengte kan het niveau niet verhogen.
6. **Causaliteit** — testsuite bewijst dat productie-uitvoer op `as_of`
   identiek is aan replay op datzelfde tijdstip.
7. **Datacontract** — entiteit- en count-regio's delen dezelfde `event`-tabel
   en dezelfde indicator-machinerie.
8. **Analistvertrouwen** — elke assessment bevat baseline, bewijs,
   alternatieve verklaring en vervolgstap; ontbreekt er één, dan faalt de test.
