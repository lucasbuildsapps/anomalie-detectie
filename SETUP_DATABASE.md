# Persistente database instellen (Supabase)

Standaard slaat SENTINEL data op in een lokaal SQLite-bestand. Op Streamlit
Cloud is dat **ephemeral**: bij elke herstart verdwijnt de data. Voor blijvende,
gedeelde opslag koppel je een gratis **Supabase**-database (Postgres). De code
schakelt automatisch over zodra een connectie-URL is ingesteld — je hoeft niets
in de code te wijzigen.

## Stap 1 — Supabase-project aanmaken

1. Ga naar [supabase.com](https://supabase.com) en log in (gratis account).
2. **New project** → kies een naam, een sterk database-wachtwoord (bewaar dit),
   en een regio dichtbij (bv. Frankfurt/EU).
3. Wacht ~2 minuten tot het project klaar is.

## Stap 2 — Connectie-URL ophalen

1. In je project: **Project Settings** (tandwiel) → **Database**.
2. Onder **Connection string** kies je **URI**.
3. Kopieer de string. Die ziet er zo uit:
   ```
   postgresql://postgres.xxxx:[YOUR-PASSWORD]@aws-0-eu-central-1.pooler.supabase.com:6543/postgres
   ```
4. Vervang `[YOUR-PASSWORD]` door het database-wachtwoord uit stap 1.

> Gebruik bij voorkeur de **"Connection pooling"** URI (poort `6543`) — die werkt
> beter met Streamlit Cloud dan een directe verbinding.

## Stap 3 — URL als secret instellen

### Op Streamlit Cloud
Dashboard → je app → **Settings** → **Secrets**. Voeg toe (naast je wachtwoord):

```toml
password = "jouw-app-wachtwoord"
database_url = "postgresql://postgres.xxxx:WACHTWOORD@aws-0-eu-central-1.pooler.supabase.com:6543/postgres"
```

Opslaan. De app herstart en maakt automatisch de tabellen aan.

### Lokaal (optioneel testen)
```cmd
set DATABASE_URL=postgresql://postgres.xxxx:WACHTWOORD@...:6543/postgres
streamlit run app.py
```

## Stap 4 — Controleren

Open de app. In de zijbalk staat nu **"Verbonden met gedeelde database"** in
plaats van de demo-waarschuwing. Upload een dataset, herstart de app, en
controleer dat de data er nog is.

## Hoe het werkt

- Geen `database_url` → lokale SQLite (`data/store.db`). Niets verandert.
- Wel een `database_url` → Postgres. Tabellen worden automatisch aangemaakt
  (`datasets`, `observations`, `annotations`). Dezelfde code, ander backend.
- De omschakeling zit in `core/storage.py` (`_database_url()`); de rest van de
  app merkt er niets van.

## Belangrijke aandachtspunten

- **Wachtwoord = teamtoegang.** Iedereen met het app-wachtwoord deelt dezelfde
  database en ziet dezelfde datasets. Dat is bewust (teamlogin). Wil je
  gescheiden accounts per analist, dan is dat een uitbreiding — vraag erom.
- **Gevoelige data**: ook met Supabase staat de data bij een externe
  cloudprovider (AWS-regio die je koos). Voor geclassificeerde data: gebruik een
  on-premise Postgres in plaats van Supabase — zet dan diezelfde `database_url`
  naar je interne server. Zie `DEPLOY.md`.
- **Gratis tier**: Supabase free pauzeert een project na ~1 week inactiviteit.
  Eén bezoek wekt het weer; data blijft behouden. Voor productie: betaalde tier.
- **Back-ups**: Supabase maakt dagelijkse back-ups op betaalde tiers. Op free
  exporteer je zelf periodiek via de SQL-editor of de Excel-export in SENTINEL.
