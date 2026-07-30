# METHODS — wetenschappelijke verantwoording

Dit document beschrijft elke rekenmethode in SENTINEL: formule, aannames,
literatuur en beperkingen. Doel: een methodoloog of auditor moet elke
getoonde uitspraak kunnen herleiden en toetsen. Code-referenties wijzen
naar de implementatie; de pytest-suite (`tests/`) verifieert het gedrag.

**Leeswijzer voor de analist**: elk getal in de tool heeft een
betrouwbaarheids-context (banddekking, backtest-fout, gevoeligheid,
aantal stemmen). Een bevinding zonder die context lezen is de tool
verkeerd gebruiken.

---

## 1. Aggregatie en gap-beleid

Waarnemingen worden per uur/dag/week/maand gesommeerd
(`core/normbeeld.py::_aggregate`). Een periode zonder waarnemingen is
ambigu; het **gap-beleid** maakt de interpretatie expliciet:

| beleid | interpretatie | verwerking |
|---|---|---|
| `zero` | geen rapport = geen activiteit | lege bucket → 0 (default voor event-data) |
| `interpolate` | gat = collectie-uitval, activiteit liep door | lineair geschat |
| `mask` | waarheid onbekend | geschat vóór modelfit, maar uitgesloten van bandberekening en nooit als afwijking geflagd |

Bij week/maand-aggregatie wordt een onvolledige laatste bucket weggelaten
(voorkomt valse "onder band"-meldingen; getest in `tests/test_normbeeld.py`).

## 2. Periode-detectie

Autocorrelatie op kandidaat-lags (`_detect_period`). Dagdata: vrije
zoektocht over lags 2–60, acceptatie bij r > 0.25. Week-/maand-/uurdata:
alleen de a-priori plausibele jaarlijkse/dagelijkse lag (52, 12, 24) met
r > 0.3 en een minimale reekslengte. Zonder detectie geldt een
domein-fallback (dag → 7, week → 4, maand → 12, uur → 24).

*Beperking*: de vrije zoektocht kan een harmonische van de echte periode
kiezen (bv. 14 i.p.v. 7). Gevolg is een suboptimale maar geen foutieve fit.

## 3. Voorspelmethoden

Alle methoden leveren (verwachting-op-historie, toekomst-verwachting,
residu-σ). Implementatie: `core/normbeeld.py::_forecast_with` e.o.

### 3.1 STL + lineaire trend-extrapolatie
Seasonal-Trend decomposition using LOESS — **Cleveland et al. (1990),
J. Official Statistics 6(1)**. Robuuste variant (`robust=True`). De trend
wordt geëxtrapoleerd met een kleinste-kwadraten-lijn door de laatste ~14
trendpunten; het seizoen wordt fase-correct herhaald.
*Aannames*: additief seizoen, lokaal lineaire trend.
*Beperkingen*: extrapolatie op 14 punten is gevoelig voor recente
uitschieters in de trendcomponent; vereist ≥ 2 volle perioden + 1 punt.

### 3.2 Holt-Winters / ETS
Exponentiële demping met additieve trend en (bij voldoende data) additief
seizoen — **Holt (1957), Winters (1960)**; moderne behandeling in
**Hyndman & Athanasopoulos, *Forecasting: Principles and Practice* (3e
ed., hfst. 8)**. Implementatie: `statsmodels.tsa.holtwinters`, parameters
via maximum likelihood. Bij optimalisatie-falen: terugval zonder
seizoenscomponent.

### 3.3 Voortschrijdend gemiddelde (rolling)
Verwachting op t = gemiddelde van het venster **vóór** t (`shift(1)`) —
zonder die shift zou een spike zijn eigen detectie dempen (leakage;
getest in `tests/test_prediction_robustness.py`). Venster ≈ 7 perioden.
Toekomst: gemiddelde van de laatste w punten (vlak).

### 3.4 Seasonal naive
Herhaal de waarde van één periode terug. Standaard-benchmark
(**Hyndman & Athanasopoulos, hfst. 5**): elke complexere methode hoort
deze te verslaan, anders is het patroon zwakker dan gedacht.
*NB*: de eerste periode van de historie-fit gebruikt backfill en is dus
licht vertekend; de eerste `period` punten tellen daarom niet mee in de
residu-σ.

### 3.5 Mediaan + MAD
Vlakke voorspelling (mediaan) met Median Absolute Deviation als
spreidingsmaat (σ ≈ 1.4826·MAD bij normaliteit; wij gebruiken
conservatief 1.5·MAD met vloer 1.0). Fallback voor reeksen waar niets
anders betrouwbaar past.

## 4. Ensemble en methode-selectie

- **Heuristische selectie** (overzichten, snel): op basis van
  reekslengte en gedetecteerd seizoen (`_auto_select_methods`).
- **Backtest-selectie** (detailweergave, rigoureus): rolling-origin
  backtest (zie §5); de twee methoden met de laagste **MASE** worden
  gecombineerd, gewogen met 1/(MASE+0,5) zodat een aantoonbaar betere
  methode zwaarder telt en de demping voorkomt dat één lage score de
  ensemble tot één methode reduceert.
- Het ensemble-gemiddelde wordt licht gladgestreken (venster 3).

## 5. Backtest (rolling origin) en foutmaat

`_backtest_method`. Per methode worden 4 folds achtergehouden (rolling
origin, Tashman 2000): train op alles vóór de cutoff, voorspel `horizon`
perioden vooruit, vergelijk met de werkelijkheid. Getest op maximaal de
laatste 400 punten, zodat het recente regime telt.

### Foutmaat: MASE (en waarom niet een percentage)

**Wat er misging.** De eerste versie gebruikte
`|fout| / max(|werkelijk|, 1)`. Op reeksen met lege perioden deelt dat
door ~1, waardoor één lege dag met een voorspelling van 75 een fout van
7500% opleverde. Gevolg op de demo-dataset: dagbasis leek 650–1000%
"fout" en weekbasis 31% — een artefact van de maat, geen
kwaliteitsverschil. Erger nog: de sléchtste reeksen scoorden het best,
want een regio die vrijwel altijd nul is, wordt door "voorspel nul"
perfect bediend. Deze cijfers stuurden de **methode-selectie**, dus de
tool koos aantoonbaar verkeerde modellen.

**Wat het nu is.** MASE (Hyndman & Koehler 2006, *Another look at
measures of forecast accuracy*):

    MASE = mean(|fout|) / Q,   Q = mean(|y_t − y_(t−1)|) op de trainingsdata

Q is één vaste schaal per fold in plaats van een deling per punt, dus
lege perioden blazen niets op. Interpretatie: MASE < 1 betekent beter
dan de naïeve voorspelling "volgende = vorige", MASE > 1 slechter.

**Bewust m = 1, niet de seizoensperiode.** De seizoensperiode verschilt
per tijdschaal (7 bij dagen, 52 bij weken). Met een seizoens-noemer
vergelijkt MASE tussen tijdschalen appels met peren; met een vaste m = 1
stelt elke tijdschaal dezelfde vraag. Hyndman & Koehler bevelen
consistentie aan bij vergelijking tussen reeksen.

**wMAPE blijft zichtbaar** (Σ|fout| / Σ|werkelijk|) omdat "gemiddeld 20%
ernaast" leesbaar is, maar de UI zegt er expliciet bij dat die maat niet
tussen tijdschalen vergeleken mag worden.

### Tijdschaal-advies (`recommend_timescale`)

Omdat MASE schaalvrij is, kan de tool tijdschalen eerlijk náást elkaar
zetten: per kandidaat (dag/week/maand) draait een volledige backtest en
telt de beste methode. De rangschikking weegt twee dingen:

1. **voorspelbaarheid** — MASE;
2. **bruikbaarheid** — het aandeel lege perioden. Een reeks die voor 92%
   uit nullen bestaat is formeel goed voorspelbaar ("morgen weer niets")
   maar analytisch waardeloos. De penalty is
   `1 + 2·max(0, aandeel_leeg − 0.3)`.

Het advies wordt in de UI getoond mét de onderbouwing en de losse
cijfers per tijdschaal, zodat de analist het kan overrulen. Op de
demo-dataset: Ukraine → dagen (MASE 0,99), het schaarse Mykolaiv oblast
→ weken (dagbasis is daar 92% leeg).

## 6. Tolerantieband

`_quantile_band`. Asymmetrisch en quantile-gebaseerd op residuen
(werkelijk − ensemble-verwachting op de historie):

- **alpha** schaalt met de reekslengte: `clip(5/n, 0.01, 0.10)` — korte
  reeksen krijgen bredere staarten zodat het aantal geflagde punten
  werkbaar blijft.
- **Recency-weging**: exponentieel, halfwaardetijd = max(10, n/3). De band
  volgt het huidige regime, niet het volledige verleden.
- **Minimale breedte**: max(1, 10% van het mediaanniveau) — voorkomt een
  degeneratieve band op vlakke reeksen.
- Ondergrens wordt op 0 geklemd voor niet-negatieve (count-)data; de
  invariant upper ≥ lower blijft daarbij behouden.

**Waarom geen ±2σ**: op scheve count-data gaf een symmetrische band een
betekenisloze ondergrens (0) en te veel valse "boven band"-meldingen.

**Eerlijkheids-kanttekening (in-sample)**: de residu-quantiles worden
berekend op dezelfde data waarop het ensemble is gefit; de band is daarmee
licht optimistisch. Twee correcties maken dit inzichtelijk en beheersbaar:

1. **Empirische banddekking** (`band_coverage`): het feitelijke aandeel
   van de historie binnen de band wordt berekend en in de UI getoond
   naast het doel (≈ 1 − 2·alpha). Wijkt de dekking sterk af, dan is de
   band voor die reeks niet betrouwbaar.
2. **Horizon-verbreding** (§7): de voorspelband gebruikt out-of-sample
   backtest-fouten, niet de in-sample residuen.

### 6a. Lokale spreiding — waarom de band met het regime meebeweegt

De eerste versie berekende één bandbreedte voor de héle reeks
(recency-gewogen residu-quantiles) en telde die overal bij de verwachting
op. Op een reeks met een regimewissel is dat aantoonbaar fout.

**Wat het opleverde** op de demo-dataset (Oekraïne, dagbasis, gemiddelde
loopt van ~9/dag in 2022 naar ~200/dag in 2026):

| jaar | gem. werkelijk | band (oud) | afwijkingen (oud) |
|---|---|---|---|
| 2022 | 9,1 | 0 – 469 | **0** |
| 2023 | 7,9 | 0 – 470 | **0** |
| 2024 | 31,8 | 0 – 491 | **0** |
| 2025 | 154,3 | 8 – 603 | 10 |

Drie volle jaren — 826 dagen — zonder één enkele afwijking, niet omdat er
niets gebeurde maar omdat de band van het drukste regime op de rustigste
jaren werd losgelaten. De tool was blind voor alles vóór het huidige
regime, en dat was aan de cijfers niet te zien: er stond simpelweg
"normaal".

**Twee oorzaken, beide opgelost:**

1. **Vaste breedte in eenheden.** Voor tellingen groeit de spreiding mee
   met het niveau (Poisson: var = μ). Bij 8 per dag hoort een smallere
   band dan bij 200. De discrete band (§6b) rekent per periode vanuit
   μ_t en doet dat vanzelf; die geldt nu voor **alle** telling-data, niet
   alleen schaarse reeksen.
2. **Globale spreidingsschatting.** Ook de dispersie φ was één getal voor
   de hele reeks, gedomineerd door het drukke regime (φ = 133). Die wordt
   nu lopend geschat over ~90 perioden (`_local_dispersion`), zodat
   rustige en onstuimige perioden hun eigen breedte krijgen. Voor continue
   data doet `_local_residual_scale` hetzelfde met een lopende
   MAD-achtige maat die de quantile-band schaalt.

Beide schattingen gebruiken `shift(1)`: de spreiding op t rust op data
strikt vóór t. Zonder die shift zit een uitschieter in zijn eigen venster,
blaast hij de dispersie op en verdwijnt hij in een band die hij zelf
verbreedde — dezelfde leakage die eerder in de rolling-detector zat.

**Na de correctie:**

| jaar | gem. werkelijk | band (nieuw) | afwijkingen (nieuw) |
|---|---|---|---|
| 2022 | 9,1 | 0 – 92 | 2 |
| 2023 | 7,9 | 0 – 82 | 2 |
| 2024 | 31,8 | 0 – 170 | 10 |
| 2025 | 154,3 | 7 – 588 | 17 |

Empirische banddekking blijft 97,2% bij een doel van 98% — de band is
smaller geworden zonder de kalibratie te verliezen. Bewaakt in
`tests/test_local_bands.py`.

**Blijvende beperking**: de band is nog steeds breed in absolute zin
(0–92 bij een gemiddelde van 9), omdat deze data werkelijk zeer bursty is
(φ ≈ 50). Dat is eerlijk — geen artefact meer, maar een eigenschap van het
verschijnsel.

### 6a-bis. Verfijningen van de lokale spreiding

Vier aanscherpingen op §6a, alle gericht op dezelfde vraag: is "normaal"
hier per periode goed bepaald?

**1. Spreiding per regime** (`_segment_ids`, `_rolling_within_segments`).
Een lopend venster van ~90 perioden mengt na een scherpe breuk maandenlang
het oude en het nieuwe regime. De reeks wordt daarom eerst in regimes
verdeeld (change-points, §10); de spreidingsschatting kijkt nooit over een
regimegrens heen. Vlak na een breuk is er weinig historie — dan is de
schatting terecht onzeker in plaats van stilzwijgend geleend van het
vorige regime. Segmenten korter dan 30 perioden worden niet als regime
geteld: daar valt niets uit te schatten.

**2. Spreiding per seizoensfase** (`_seasonal_spread_factors`). Het
seizoen zat al in de verwáchting, maar niet in de bandbreedte. Zijn
weekenden structureel rustiger én regelmatiger, dan werd een
weekendafwijking ondergedetecteerd en een doordeweekse overgedetecteerd.
De factoren zijn genormaliseerd op gemiddeld 1 (herverdeling, geen
verschuiving van de totale kalibratie) en begrensd op [0,5 – 2,0], zodat
één toevallig rustige fase het beeld niet overneemt.

**3. Venstergrootte gemeten in plaats van aangenomen**
(`_pick_spread_window`). De 90 perioden waren een gok. Nu worden
kandidaten (30/60/90/180) doorgerekend en wint het venster waarvan de
empirische dekking het dichtst bij het doel (1 − 2·α) ligt; bij gelijke
dekking het kortere, want dat volgt het regime sneller. De evaluatie
gebruikt hetzelfde bandmechanisme dat de reeks daadwerkelijk krijgt —
een eerdere versie beoordeelde telling-reeksen met de quantile-formule
en koos daardoor systematisch mis (dekking 0,95 bij een doel van 0,98).

**4. Vertrouwen weegt regime-stabiliteit mee** (`_confidence`).
Reekslengte alleen is misleidend: drie jaar historie met een breuk van
vorige maand is minder betrouwbaar dan een korte stabiele reeks. Het
oordeel daalt nu bij een vers regime (< 15 of < 30 perioden sinds de
breuk), bij een band die zijn eigen doel meer dan 10 procentpunt mist, en
bij veel recente afwijkingen (> 25% of > 50% van de recente perioden).
Dat laatste vangt precies wat de change-point-detectie nog niet kan zien:
een breuk van een paar dagen oud is te kort om als regime te herkennen,
terwijl het normbeeld juist dán het minst te vertrouwen is. Of het een
blip of een nieuw regime is, kan de tool op dat moment niet weten — en
dat niet-weten ís de reden voor minder vertrouwen.

**Gemeten effect** (demo-dataset, dagbasis, doel 0,98):

| regio | dekking vóór | dekking na |
|---|---|---|
| Ukraine | 0,951 | 0,962 |
| Mykolaiv oblast | 0,979 | 0,979 |
| Kyiv oblast | 0,988 | 0,988 |
| south | — | 0,976 |

Bewaakt in `tests/test_band_refinements.py`.

### 6b. Discrete band voor schaarse telling-data (Poisson / negatief-binomiaal)

Voor reeksen van **niet-negatieve gehele aantallen met mediaan < 5**
(`_is_low_count_series`) worden residual-quantiles vervangen door een
discreet interval (`_count_band`). Reden: bij bv. 0,6 gebeurtenissen per
dag nemen residuen maar een handvol waarden aan en wordt de quantile-band
volledig gedomineerd door enkele spikes — met een ondergrens die niets
betekent en een bovengrens die óf te ruim óf te krap is.

Model per periode *t* met ensemble-verwachting μ<sub>t</sub> (gevloerd op
0,1):

- **Poisson** als de Pearson-dispersie φ ≤ 1,3:
  `band = [F⁻¹(α; μ_t), F⁻¹(1−α; μ_t)]` met F de Poisson-CDF.
- **Negatief-binomiaal (NB2)** bij overdispersie (φ > 1,3), zoals bij
  geclusterde aanvalsgolven: var = φ·μ, dus r = μ/(φ−1),
  p = r/(r+μ). Cameron & Trivedi (2013), *Regression Analysis of Count
  Data*.

φ wordt geschat als recency-gewogen gemiddelde van (y−μ)²/μ over de
historie (zelfde gewichten als §6); bij `gap_policy='mask'` tellen
niet-geobserveerde buckets niet mee. De voorspelband hergebruikt de φ van
de historie (op de voorspelling valt niets te schatten) en past dezelfde
horizon-verbreding toe door de offsets rond μ te schalen. Het gekozen
model (`band_model`) en φ (`dispersion`) staan in de UI-betrouwbaarheids-
regel, zodat de analist ziet wélk band-mechanisme actief is.

## 7. Voorspelband en horizon-verbreding

Onzekerheid groeit met de voorspelafstand; een band die op stap 14 even
smal is als op stap 1 is aantoonbaar te optimistisch.
`backtest_step_widening` meet de gemiddelde absolute out-of-sample-fout
per voorspelstap (uit de backtest-folds), normaliseert op stap 1, maakt
de factoren monotoon niet-dalend (cummax) en begrenst ze op 3×. De
band-offsets q_lo/q_hi worden per stap met die factor geschaald.

Zonder backtest-informatie (overzichten, korte reeksen) geldt een
conservatieve default: +3% bandbreedte per stap, gemaximeerd op 1.5×.
De bron van de verbreding (`backtest` of `default`) staat op het
`Normbeeld`-object en in de UI.

## 8. Afwijkingsdetectie (detector-ensemble)

Vijf detectoren (`detectors/`), elk met eigen aannames:

| detector | statistiek | referentie |
|---|---|---|
| Z-score (MAD) | modified z = 0.6745·(x−med)/MAD | Iglewicz & Hoaglin (1993) |
| Rolling mean ± N·σ | afstand tot lokaal gemiddelde | — |
| STL-residu | z-score op decompositie-residu | Cleveland et al. (1990) |
| Change-point | windowed Welch-achtige t-statistiek | zie §10 |
| Isolation Forest | isolatie-diepte in random trees | Liu, Ting & Zhou (2008), ICDM |

### Stemmen en severity
`core/auto_pilot.py::classify_severity` — absolute stem-aantallen:
minimaal 2 methoden moeten een punt markeren; "hoog" vereist (vrijwel)
unanimiteit. De exacte tabel staat in de docstring en in
`tests/test_severity.py`.

**Belangrijk**: de detectoren zijn **niet per definitie statistisch
onafhankelijk** — Z-score, rolling en STL-residu meten alle drie een vorm
van "afstand tot het lokale niveau". Stemmen zijn daarom corroboratie-
indicaties, geen onafhankelijke bewijzen. De UI-teksten zijn hierop
aangepast.

Hoe sterk die afhankelijkheid is, blijkt **datasetafhankelijk** en wordt
daarom gemeten in plaats van aangenomen (zie hieronder). Op de
demo-dataset (missile attacks, per regio gegroepeerd) komt het effectieve
aantal detectoren uit op 4,8 van 5 — daar dragen de stemmen dus vrijwel
volledig eigen informatie. Op vlakkere reeksen valt dat getal lager uit.
Dit is precies waarom de maat per dataset wordt getoond.

### Gemeten onafhankelijkheid (`detector_agreement`)
De kanttekening hierboven is inmiddels **gekwantificeerd** in plaats van
alleen benoemd. Per dataset worden de binaire vlaggen van alle
detectoren vergeleken:

- **φ-coëfficiënt**: Pearson-correlatie op de binaire vlaggen (bij
  constante vlaggen — niets of alles gemarkeerd — niet gedefinieerd).
- **Jaccard-index**: |A∩B| / |A∪B| over de gemarkeerde punten; dit is
  het getal dat in de UI staat omdat het direct leesbaar is
  ("deze twee markeren 70% dezelfde punten").
- **Effectief aantal detectoren**: participatie-ratio van de
  eigenwaarden van de correlatiematrix, n² / Σλᵢ². Gelijk aan *n* bij
  ongecorreleerde detectoren, richting 1 als ze allemaal hetzelfde
  zeggen. Dezelfde maat wordt in de portefeuille-analyse gebruikt voor
  het effectieve aantal onafhankelijke posities.

De UI toont dit onder de bevindingenlijst ("hoe zelfstandig zijn deze
stemmen?"), met een expliciete interpretatie-regel: bij een effectief
aantal onder de helft van *n* moet een meerderheid als **één**
waarneming gelezen worden, niet als meerdere.

### Auto-tuning — quota, geen significantie
De gevoeligheid ('streng'/'normaal'/'soepel') wordt maximaal 3 iteraties
bijgesteld tot 0.3%–5% van de rijen signaal-severity heeft. Dit is een
**triage-ontwerp**: het garandeert een werkbare lijst "meest opvallende
punten van deze dataset", óók als de dataset statistisch onopvallend is.
Consequenties:

- severity-labels zijn **niet vergelijkbaar tussen datasets**;
- een gevulde lijst is **geen bewijs dat er iets aan de hand is**.

De gebruikte gevoeligheid en het aantal iteraties staan daarom bij elke
bevindingenlijst en in elke bevinding (`gevoeligheid`-veld).

## 9. Lag-detectie (cross-correlatie)

`core/comparison.py::cross_correlation_lag`. Correlatie van de **eerste
verschillen**, niet de niveaus: twee reeksen die beide groeien lijken in
niveaus altijd gecorreleerd zonder enig echt verband (spurious
correlation; klassiek: **Yule (1926)**; zie ook Granger & Newbold (1974)
over spurious regression).

**Significantie**: omdat de beste lag over ~61 kandidaten wordt gekozen,
is de hoogste correlatie ook in pure ruis substantieel (selectie-effect).
Een permutatietest (200 hershufflingen van de verschilreeks, telkens
max|r| over alle lags) levert de 95%-drempel `sig_threshold`;
`significant` geeft aan of de gevonden lag daarboven ligt. Niet-
significante lags worden in de UI als indicatief gemarkeerd.

## 10. Change-points

Windowed t-statistiek: |gem(na) − gem(voor)| / √(pooled var · 2/w),
venster w = 3–8 punten, met non-maximum suppression (minimale onderlinge
afstand). De drempel is **Bonferroni-gecorrigeerd over het effectieve
aantal onafhankelijke vensters** (overlappende vensters correleren over
~w posities) met Student-t-kwantielen (df = 2w−2), vloer 2.0. Getest:
een duidelijke niveauverschuiving wordt gevonden; 400 punten pure ruis
leveren (vrijwel) niets op (`tests/test_statistical_fixes.py`).

*Alternatief bij doorgroei*: PELT (`ruptures`-bibliotheek) is de
standaard voor offline change-point-detectie — **Killick, Fearnhead &
Eckley (2012), JASA 107(500)**.

## 11. Anomalie-percentiel

Per historiepunt de empirische rang van het residu (0–1). "Extremer dan
X% van de historie" is een **rang-uitspraak binnen de eigen reeks**,
geen kansuitspraak.

## 11b. Peer-groepen (`region_comovement`)

Het normbeeld vraagt: *is deze regio ongewoon vergeleken met haar eigen
verleden?* Deze analyse vraagt iets anders: *is deze regio ongewoon
vergeleken met de regio's die normaal hetzelfde doen?*

Dat onderscheid is operationeel relevant. Stijgen alle regio's tegelijk,
dan is dat waarschijnlijk landelijk — of een wijziging in de rapportage.
Stijgt er één terwijl haar peers vlak blijven, dan is dat lokaal, en
meestal het interessantere signaal.

Werkwijze:
1. reeks per regio, genormaliseerd naar z-scores (anders domineert de
   drukste regio de hele correlatiematrix);
2. correlatiematrix over de gezamenlijke historie;
3. peers = regio's met correlatie ≥ 0,4; minder dan 2 peers betekent
   géén uitspraak (zonder groep valt er niets te vergelijken);
4. het verschil tussen de regio en haar peer-gemiddelde wordt uitgedrukt
   in standaarddeviaties van dat verschil over de historie; vanaf 2σ
   volgt een melding.

Beperking: dit vindt alleen wat correleert. Een regio zonder duidelijke
peer-groep (35 van 103 in de demo-dataset hebben er wél een) blijft
buiten beeld — daar is het normbeeld het enige signaal.

## 11c. Evaluatie tegen bekende incidenten (`core/evaluation.py`)

De rest van dit document beschrijft *hoe* er gemeten wordt. Deze
paragraaf gaat over de vraag of het **werkt** op een concrete dataset.

Per detector wordt tegen een lijst gelabelde incidenten berekend:

- **recall** — welk deel van de incidenten is opgemerkt. In
  inlichtingenwerk is een misser doorgaans duurder dan een vals alarm,
  dus dit is de belangrijkste maat.
- **precisie** — welk deel van de meldingen was raak.
- **F1** — harmonisch gemiddelde, om op te sorteren.

Een melding telt als treffer binnen `tolerance_periods` van het label:
detectie op de 3e bij een label op de 2e is een rapportageverschil, geen
misser. Labels komen bij voorkeur uit het gewone triage-werk (bevindingen
gemarkeerd als bevestigd/geëscaleerd), zodat er geen apart
annotatie-project nodig is.

**Beperking**: dit meet alleen wat gelabeld is. Een detector die iets
terechts vindt dat niet op de lijst staat, telt hier als vals alarm.
Labels zijn dus zelf een bron van vertekening.

**Wat de eerste meting opleverde** (demo-dataset, 6 zwaarste dagen als
ijkpunt): de ensemble scoorde het best (F1 0,30; recall 83%), en
change-point vond terecht niets — die zoekt niveauverschuivingen, geen
pieken. Belangrijker: Z-score markeerde aanvankelijk **nul** punten
terwijl de grootste piek op z = 33 lag. Oorzaak was een NaN-gevoelige
mediaan (drie lege waarden op 1544 rijen maakten élke score NaN). Die
detector droeg dus niets bij aan de stemming, zonder foutmelding. Na de
fix vindt Z-score 6 van 6. Dit is precies waarvoor het harnas bestaat:
zonder meting was die stilte nooit opgevallen.

## 12. Bekende beperkingen (open)

- De banddekking wordt gerapporteerd maar (nog) niet automatisch
  gekalibreerd naar een doeldekking.
- De detector-correlatie wordt gemeten en getoond (§8), maar de
  severity-drempels wegen het effectieve aantal detectoren nog niet
  mee; twee sterk gecorreleerde stemmen tellen in de tabel nog als
  twee.
- Het aggregeren gebeurt in pandas, niet in de database; bij datasets
  richting miljoenen rijen is SQL-side aggregatie nodig.
- Het evaluatie-harnas (§11c) meet per detector, maar de
  severity-drempels worden er nog niet automatisch op bijgesteld.

**Opgelost sinds de eerste versie**: lage-count-reeksen gebruiken nu een
Poisson/negatief-binomiaal band (§6b) in plaats van residual-quantiles;
de backtest-foutmaat is vervangen door MASE (§5), waarmee ook het
tijdschaal-advies mogelijk werd; detector-correlatie wordt gemeten (§8).

## 13. Reproduceerbaarheid

- Alle stochastische onderdelen (permutatietest, Isolation Forest)
  gebruiken vaste seeds.
- `tests/` dekt elke bewering in dit document; draai
  `python -m pytest tests/ -q` na elke wijziging aan `core/` of
  `detectors/`.
