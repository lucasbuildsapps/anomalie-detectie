# Fase 0 — haalbaarheidsprobe verkeersimpact

Doel van dit document: vastleggen wat er **vastgesteld** is en wat er nog
**aangenomen** wordt, zodat niemand later een aanname voor een meting
aanziet. Bijgewerkt terwijl er gebouwd wordt, niet erna.

Status op 13-08-2026: **de probe is gebouwd en getest, maar heeft nog
niets gemeten.** Er zijn geen API-keys, dus de poort is niet getest — niet
open, niet dicht. De dekkingstabel hieronder is leeg met opzet.

## De vraag van deze fase

Levert enig kanaal voor een Oekraïense stad een verkeerssignaal dat
*leeft en beweegt*? Concreet, voor Google Routes: wijkt `duration` af van
`staticDuration`, en verandert die afwijking over de tijd?

Google zette in februari 2022 op verzoek van de Oekraïense autoriteiten de
live-verkeerslaag en de drukte-informatie voor Oekraïne uit, maar
verklaarde publiek dat navigeren naar een bestemming nog wél routes en
aankomsttijden met de actuele verkeerssituatie geeft. Dat maakt de Routes
API het meest waarschijnlijke werkende kanaal — en een hypothese, geen
fundament.

Blijft de poort dicht, dan wordt er geen keten gebouwd. Er wordt in dat
geval **niet** teruggevallen op het typisch-verkeer-model
(`trafficModel`/`BEST_GUESS`) alsof dat een waarneming van een specifieke
nacht is. Dat model kent die nacht niet.

## Wat empirisch is vastgesteld in deze omgeving

Getest op 13-08-2026 vanuit de container waarin dit gebouwd is.

| Bevinding | Hoe vastgesteld | Gevolg |
|---|---|---|
| `routes.googleapis.com` is bereikbaar | POST `directions/v2:computeRoutes` zonder key → `HTTP 403 PERMISSION_DENIED, "Method doesn't allow unregistered callers"` | Het verzoek komt bij Google aan; alleen de key ontbreekt. De Google-probe kán hier draaien. |
| `api.tomtom.com` is geblokkeerd | `CONNECT tunnel failed, response 403` van de egress-proxy | Kan hier niet gemeten worden, ook niet met key. |
| `data.traffic.hereapi.com` is geblokkeerd | idem | idem |
| `www.waze.com` is geblokkeerd | idem | idem |
| `api.alerts.in.ua` is geblokkeerd | idem | Raakt Fase 2, niet Fase 0. |
| `firms.modaps.eosdis.nasa.gov` is geblokkeerd | idem | Raakt Fase 2. Bestaat al als `connectors/firms.py`. |
| Primaire prijspagina's zijn geblokkeerd | `developers.google.com`, `mapsplatform.google.com`, `docs.tomtom.com`, `here.com` → egress-fout | Prijzen komen uit websearch-samenvattingen; per regel gemarkeerd als `secundair`. |

De probe maakt dit onderscheid zelf, en verwart het niet met een uitspraak
over dekking: `onbereikbaar` staat los van `alleen-statisch` en van
`geen-key`. Een geblokkeerde uitgaande verbinding is een eigenschap van
*deze omgeving*, geen eigenschap van het kanaal.

**Gevolg voor de uitvoering.** Alleen het Google-kanaal is hier te meten.
Voor TomTom, HERE en Waze zijn er twee opties: het egress-beleid van de
omgeving uitbreiden met die hosts, of de probe draaien op een machine met
open netwerk (of via GitHub Actions). Dezelfde code, ander netwerk — de
probe is hervatbaar en schrijft naar `data/traffic/<label>/`, dus resultaten
zijn samen te voegen.

## Wat nog niet is vastgesteld

- **Of Oekraïne een levend signaal geeft.** De kernvraag. Wacht op een key.
- **De eenheid van de HERE-snelheidsvelden.** Aangenomen m/s, omgerekend
  met factor 3,6. `traffic/providers/here.py::verify_speed_unit()` betrapt
  een verkeerde aanname bij de eerste echte respons (free-flow van "13" is
  m/s, "47" is km/h).
- **Of `staticDuration` bij Google een constante opslag heeft.** Zou een
  vaste afwijking van bijvoorbeeld 3% opleveren die géén verkeer is.
  Daarom het aparte oordeel `divergent-maar-stil` (zie hieronder).
- **De prijzen tegen de primaire bron.** Zie `python -m traffic.cli pricing`.

## Dekkingstabel

Nog niets gemeten. De tabel wordt gevuld door
`python -m traffic.cli coverage --label <label>`.

| stad | google_routes | tomtom | here | waze |
|---|---|---|---|---|
| Kyiv | geen-key | geen-key · onbereikbaar | geen-key · onbereikbaar | onbereikbaar |
| Lviv | geen-key | geen-key · onbereikbaar | geen-key · onbereikbaar | onbereikbaar |
| Odesa | geen-key | geen-key · onbereikbaar | geen-key · onbereikbaar | onbereikbaar |
| Kharkiv | geen-key | geen-key · onbereikbaar | geen-key · onbereikbaar | onbereikbaar |
| Dnipro | geen-key | geen-key · onbereikbaar | geen-key · onbereikbaar | onbereikbaar |
| Warsaw (controle) | geen-key | geen-key · onbereikbaar | geen-key · onbereikbaar | onbereikbaar |

### Hoe een oordeel tot stand komt

Een geldig antwoord is géén levend signaal. Alle providers antwoorden
netjes zonder realtime-invoer: Google geeft dan `duration ==
staticDuration`, TomTom geeft `currentSpeed == freeFlowSpeed`. Wie op
"HTTP 200" afgaat bouwt op een dood signaal. Daarom:

| verdict | betekenis |
|---|---|
| `live` | afwijking van de statische reistijd **én** die afwijking beweegt over de rondes |
| `divergent-maar-stil` | er is een afwijking, maar constant — niet te scheiden van een modelverschil tussen de twee velden |
| `alleen-statisch` | geldige antwoorden, ratio steeds 1,0 → geen verkeersinvoer |
| `leeg` | geldig antwoord zonder inhoud (geen route, geen wegvak) |
| `geen-key` | key ontbreekt; er is niets gevraagd en niets betaald |
| `onbereikbaar` | netwerk of egress-beleid blokkeert het endpoint |
| `te-weinig-samples` | minder dan 3 geslaagde metingen; geen uitspraak |

De poort (`traffic/coverage.py::poort`) gaat alleen open bij `live` voor
minstens één **Oekraïense** stad. Warschau op `live` is geen argument: dat
werkt vast wel, en daar gaat het onderzoek niet over.

## Kosten

`python -m traffic.cli pricing` toont de tabel met herkomst en
verificatiedatum. Stand 13-08-2026, alle regels `secundair`:

| kanaal | SKU | $/1.000 | gratis staffel |
|---|---|---|---|
| google_routes | Compute Routes Pro | 10,00 | 5.000 events/maand |
| tomtom | Traffic Flow non-tile | 0,50 | 2.500 requests/dag |
| here | Traffic Flow transaction | 1,00 | 250.000 transacties/maand |
| waze | n.v.t. | — | ongedocumenteerd endpoint |

`TRAFFIC_AWARE_OPTIMAL` valt in de Pro-SKU. De variant zonder
verkeersvoorkeur is goedkoper (Essentials, $5,00) en voor dit onderzoek
waardeloos.

Gemeten kostenschatting van de probe zoals nu geconfigureerd — 19
segmenten, 18 rondes van 10 minuten over 3 uur:

```
google_routes    $    3.42
tomtom           $    0.17
here             $    0.34
totaal           $    3.93
```

Eén spitsvenster en één dalvenster kost dus ongeveer **$7,90**, binnen het
afgesproken plafond van $10. Het plafond is hard: `CostLedger.check()`
weigert de aanroep die het zou doorbreken, in plaats van hem te doen en het
daarna te melden. De gratis staffels zijn *niet* verrekend — die zijn
accountbreed en dit proces weet niet wat er elders al verbruikt is. De
geboekte bedragen zijn dus een bovengrens.

## Meetsegmenten

19 actief: 16 in Oekraïne (Kyiv 4, Lviv 3, Odesa 3, Kharkiv 3, Dnipro 3) en
3 in Warschau als externe controle. Chisinau en Kraków staan erbij als
optionele extra controle en zijn standaard uit.

Selectiecriterium: 5–20 km, over een **enkel faalpunt** — een brug, een
ringweg-tak, een grote kruising. Daar verraadt een omleiding zich in de
geometrie, ook 's nachts als de reistijd op lege wegen nauwelijks
verandert. `python -m traffic.cli segments` toont per segment welk faalpunt
het bevat.

Dat de omweg als eersteklas signaal wordt behandeld is geen detail: om
03:00 verandert een verwoeste brug de reistijd op een leeg wegennet
misschien nauwelijks, maar de route wél zichtbaar. Google is daarom het
enige kanaal dat het volledige beeld kan geven — TomTom en HERE meten op
één punt en hebben geen routegeometrie, dus zij kunnen een gesloten brug
principieel niet als omleiding zien. Dat staat als `heeft_geometrie` in de
provider-interface en hoort zo in de dekkingsafweging.

## Uitdrukkelijk voorbehoud bij Waze

Het livemap-endpoint is geen gedocumenteerde API. De keten mag niet op
ongedocumenteerde kanalen leunen — dat is een van de non-goals. De
provider staat daarom standaard uit (`--include-waze`), doet één ronde, en
het resultaat gaat alleen de dekkingstabel in. Zou de uitkomst `live` zijn,
dan is de juiste vervolgstap een gedocumenteerd kanaal zoeken, niet daar
gaan verzamelen.

## Gebruik

```bash
cp .env.example .env          # keys invullen
python -m traffic.cli preflight                    # keys + bereikbaarheid + kostenschatting
python -m traffic.cli pricing                      # prijzen met herkomst
python -m traffic.cli segments                     # de meetsegmenten
python -m traffic.cli run --label piek-2026-08-14 --duration-min 180 --budget 5
python -m traffic.cli run --label dal-2026-08-15  --duration-min 180 --budget 5
python -m traffic.cli coverage --label piek-2026-08-14
```

`run` is hervatbaar: opnieuw starten met hetzelfde label vult aan waar het
gebleven was, en het kostenlogboek telt eerdere aanroepen mee zodat een
herstart niet opnieuw het volle budget mag uitgeven. Exitcode van
`coverage` is 0 als de poort open is, 2 als hij dicht is — bruikbaar in een
geplande taak.

## Wat er expliciet níet gebeurt

- Geen interpolatie. Een mislukte aanroep wordt bewaard als mislukte
  aanroep, met reden; er komt geen geschat getal in de plaats.
- Geen SQLite-schema. Dat is Fase 1 en wacht op de poort. NDJSON is voor
  Fase 0 genoeg en leest terug zonder migratie.
- Geen `departureTime` in het verleden. Die weigert de API voor `DRIVE`;
  alleen `TRANSIT` accepteert dat. Een toekomstige of weggelaten
  `departureTime` levert een historisch profiel — bruikbaar als baseline
  (Fase 3), nooit als waarneming.
- Geen realtime uitvoer, geen voorspelling, geen conclusies over
  militaire bewegingen. Zie de non-goals in de opdracht; live publicatie
  van verkeersanomalieën in Oekraïne is precies de schade waarom Google de
  verkeerslaag uitzette.
