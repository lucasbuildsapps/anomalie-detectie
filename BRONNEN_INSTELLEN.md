# Automatische bronnen instellen

Stap-voor-stap voor de drie meegeleverde bronnen. Reken op **45 minuten**
voor de eerste; daarna is elke volgende bron een kwestie van een sleutel
plakken.

De volgorde is bewust: eerst een database, dan één bron, dan pas de rest.
Elke stap is los te controleren, zodat je bij een probleem weet wélke
stap het is.

---

## Stap 0 — Waarom eerst een database

Streamlit Cloud wist zijn lokale opslag bij elke herstart. De geplande
inwinning draait bovendien in een **ander proces** (GitHub Actions) dan
de app. Zonder gedeelde database schrijft de inwinning dus in het niets,
en ziet de app er nooit iets van.

Gratis Postgres bij [supabase.com](https://supabase.com) volstaat:

1. **New project** → naam, sterk wachtwoord (bewaren), regio Frankfurt/EU.
2. **Project Settings → Database → Connection string → URI**.
3. Neem de **pooler**-variant (poort `6543`) en vervang `[YOUR-PASSWORD]`.

Die ene connectiestring gaat straks op **twee** plekken:

| waar | naam | waarvoor |
|---|---|---|
| Streamlit Cloud → Settings → Secrets | `database_url` | de app leest |
| GitHub → Settings → Secrets → Actions | `DATABASE_URL` | de inwinning schrijft |

Verschillende schrijfwijzen, zelfde waarde. Wijken ze af, dan kijken app
en inwinning naar verschillende databases — een fout die er van buiten
uitziet als "de inwinning doet niets".

---

## Stap 1 — GDELT (geen sleutel nodig)

Begin hier, juist omdat er niets aan te vragen valt. Zo weet je of de
keten werkt vóórdat je met sleutels gaat puzzelen.

**Aanzetten**: in `connectors/gdelt.py`, zet `enabled = True`. Pas
meteen `WATCHLIST` aan naar jouw aandachtsgebieden — de standaardlijst
(Oekraïne, Rusland, Midden-Oosten, Sahel, Taiwan-straat) is een voorbeeld,
geen aanbeveling.

```python
WATCHLIST = {
    "Oekraïne": 'sourcecountry:UP (drone OR missile OR strike OR shelling)',
    "Jouw gebied": 'sourcecountry:XX (trefwoord OR ander_trefwoord)',
}
```

**Controleren**: app → Instellingen → Bronnen → *Verbinding testen*.
Reken op **2–4 minuten**: GDELT rate-limit hard en de retry wacht dat
netjes uit. Dat is normaal, geen storing.

**Wat je krijgt**: aantal nieuwsartikelen per dag per gebied. Let op wat
dat wél en niet is — meer artikelen kan ook betekenen dat er meer
journalisten kijken. Bruikbaar als activiteits-indicator naast hardere
bronnen, niet als telling van gebeurtenissen.

---

## Stap 2 — NASA FIRMS (gratis sleutel, direct)

De beste eerste "echte" bron: satelliet-warmtedetecties, meerdere keren
per dag ververst, met eigen coördinaten.

1. Ga naar <https://firms.modaps.eosdis.nasa.gov/api/area/>
2. Vraag een **MAP_KEY** aan met je e-mailadres — die komt direct binnen.
3. Zet hem als secret:
   - Streamlit Cloud → Secrets: `FIRMS_MAP_KEY = "..."`
   - GitHub → Actions secrets: `FIRMS_MAP_KEY`
4. In `connectors/firms.py`: `enabled = True`, en pas `AREAS` aan
   (bounding boxes: west, zuid, oost, noord).

**Wat je krijgt**: elke detectie is een warmtebron met coördinaten. De
kaart werkt hier zonder gazetteer.

**Wat het niet is**: geen aanvals-detectie. FIRMS ziet wármte — dat is
net zo goed landbouw-afbranden, industrie of een gasfakkel. In de
zomermaanden domineert landbouw het beeld volledig. Gebruik het als
indicator naast andere bronnen; die waarschuwing staat ook in de
dataset-omschrijving die de tool aanmaakt.

---

## Stap 3 — ACLED (gratis sleutel, registratie)

De inhoudelijk sterkste bron: handmatig gecodeerde conflict-gebeurtenissen
met datum, coördinaten, type en slachtoffers.

1. Registreer op <https://acleddata.com/register/> (gratis voor
   onderzoek/non-profit; goedkeuring duurt soms een dag).
2. Je krijgt een **API-key**; je registratie-e-mailadres hoort erbij.
3. Zet beide als secret, op allebei de plekken:
   - `ACLED_API_KEY`
   - `ACLED_EMAIL`
4. In `connectors/acled.py`: `enabled = True` en pas `COUNTRIES` aan.
   Gebruik de landnamen precies zoals ACLED ze schrijft (zie hun codebook).

**Ritme**: ACLED zelf ververst wekelijks. De connector draait dagelijks;
dat is onschadelijk (dedupe vangt herhaling af) maar levert de meeste
dagen niets nieuws op. Dat is geen storing.

---

## Stap 4 — De geplande inwinning aanzetten

1. GitHub → **Actions → Ingest → Run workflow** (handmatig, één keer).
2. Controleer dat hij groen wordt. De migraties draaien automatisch mee.
3. Vanaf dan draait hij elke dag om 05:17 UTC.

Aanpassen van dat tijdstip: de `cron`-regel in
`.github/workflows/ingest.yml`.

---

## Stap 5 — Waarschuwingen (optioneel maar aanbevolen)

Zonder meldkanaal waarschuwt de tool alleen wie hem opent.

Maak een inkomende webhook in Teams, Slack of Mattermost en zet de URL
als `SENTINEL_WEBHOOK_URL` — **zowel** in Streamlit Cloud als in de
GitHub Actions-secrets, want de meldingen komen uit de geplande run.

Testen: Instellingen → Bronnen → *Testbericht sturen*. Doe dat vóórdat je
erop vertrouwt; een waarschuwingssysteem dat je niet hebt geverifieerd is
erger dan geen waarschuwingssysteem.

**Let op bij de eerste run**: die meldt bewust niets en legt alleen een
nulmeting vast. Anders krijg je bij het aanzetten van een bron meteen
maanden historie over je heen, en is het kanaal binnen een week gedempt.

---

## Controleren of alles draait

Vanaf de commandline, tegen de échte API's (de tests mocken die):

```bash
python scripts/check_connectors.py
```

Bronnen zonder sleutel worden overgeslagen — dat telt niet als fout.
Exit-code 1 betekent dat een geconfigureerde bron faalt; bruikbaar in een
monitoring-check.

In de app: Instellingen → Bronnen toont per bron de laatste runs, en
Instellingen → Beheer toont de momentopnames en de audit-trail.

---

## Als het niet werkt

| symptoom | meest waarschijnlijke oorzaak |
|---|---|
| Inwinning groen, app ziet niets | `DATABASE_URL` en `database_url` wijzen naar verschillende databases |
| "Ontbrekende instelling" | secret staat alleen in Streamlit Cloud, niet in GitHub (of andersom) |
| GDELT-test duurt lang | normaal; rate-limit met retry, reken op 2–4 minuten |
| ACLED weigert de aanvraag | key en e-mailadres horen bij elkaar; controleer beide |
| Geen meldingen ondanks afwijkingen | eerste run is de nulmeting; vanaf de tweede run gaat er wél iets uit |
| Nieuwe rijen = 0 bij elke run | de bron levert niets nieuws; kijk naar 'aangeboden' in de run-historie |

Logs: `docker compose logs worker` (compose-stack) of het Actions-log in
GitHub (geplande run). Alles is JSON per regel, dus filteren kan met
`grep`.
