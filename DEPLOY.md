# Deployment-handleiding

De aanbevolen productie-route is **Optie 1: de compose-stack** — die zet in
één keer TLS, Postgres, de ingest-worker en dagelijkse backups neer. De
overige opties zijn er voor demo's en handmatige setups.

---

## ⚠️ Eerst: veiligheid

**Lees dit voordat je gaat deployen.**

| Datatype | Veilig om publiek te deployen? |
|---|---|
| Open-source data (kpszsu posts, nieuws, etc.) | Ja, mits met password-auth |
| Interne/eigen analyses zonder bronvermelding | Misschien — vraag IT |
| Geclassificeerde of Vertrouwelijke data | **NEE** — alleen on-premise |
| Persoonsgegevens (AVG/GDPR) | Verwerkersovereenkomst nodig |

Voor inlichtingen-werk: vraag **altijd** je IT-beveiliging voordat je
gevoelige data in een online tool stopt, ook al staat er een wachtwoord op.
Streamlit Community Cloud is voor dit werkveld **uitsluitend** geschikt
voor demo's met synthetische of publieke data.

---

## Optie 1 — Productie-stack (docker compose) — aanbevolen

Eén stack met Caddy (automatische TLS), de app, de ingest-worker,
Postgres en een dagelijkse backup-service.

```bash
# 1. Secrets instellen
cat > .env <<ENV
POSTGRES_PASSWORD=kies-een-sterk-db-wachtwoord
ANOMALY_PASSWORD=kies-een-sterk-app-wachtwoord
SENTINEL_DOMAIN=sentinel.jouwdomein.nl
AUTHELIA_JWT_SECRET=$(openssl rand -hex 32)
AUTHELIA_SESSION_SECRET=$(openssl rand -hex 32)
AUTHELIA_STORAGE_ENCRYPTION_KEY=$(openssl rand -hex 32)
ENV

# 2. Stack starten
docker compose -f docker-compose.prod.yml up -d --build

# 3. Database-migraties (eerste keer en na elke update)
docker compose -f docker-compose.prod.yml exec app python -m alembic upgrade head
```

- Backups landen dagelijks in `./backups/` (7 dagen retentie).
- Logs zijn JSON-per-regel: `docker compose logs app worker`.
- De audit-trail (wie deed wat) zit in de database: tabel `audit_log`,
  zichtbaar in de app onder Instellingen → Beheer.

### SSO en rollen (inbegrepen in de stack)

De stack bevat **Authelia** als identity provider. Caddy stuurt elk
verzoek eerst langs Authelia en geeft de identiteit door als
`X-Forwarded-User` / `X-Forwarded-Groups`; de app leidt daar de rol uit af
(`core/authz.py`). De app doet dus geen eigen gebruikersbeheer — dat hoort
centraal.

**Rollen** volgen uit de groep waarin een gebruiker zit:

| groep | rol | mag |
|---|---|---|
| `sentinel-viewers` | viewer | alles lezen |
| `sentinel-analysts` | analyst | + beoordelen, importeren, bronnen draaien |
| `sentinel-admins` | admin | + datasets verwijderen, bronnen beheren, audit inzien |

Andere groepsnamen koppel je met `SENTINEL_GROUP_MAP`, bijvoorbeeld
`SENTINEL_GROUP_MAP="ONS-TEAM-INTEL:analyst,ONS-TEAM-BEHEER:admin"`.

**Gebruikers aanmaken** (bestands-backend; vervang door LDAP/AD zodra dat
er is):

```bash
cp deploy/authelia/users.yml.example deploy/authelia/users.yml
docker run --rm authelia/authelia:4 authelia crypto hash generate argon2   --password 'het-wachtwoord'
# plak de hash in users.yml en zet de juiste groep
```

`users.yml` staat in `.gitignore` — committen zou wachtwoord-hashes lekken.

**Waarom de app niet zelf bereikbaar mag zijn**: wie de app rechtstreeks
kan benaderen, kan `X-Forwarded-Groups: sentinel-admins` verzinnen. Daarom
staat de app in de compose-stack op `expose` (alleen intern), wist Caddy
door de client meegestuurde identiteits-headers, en draait de app met
`SENTINEL_DEFAULT_ROLE=viewer` zodat een verkeerd geconfigureerde proxy
nooit stilzwijgend beheerdersrechten geeft. Tests in
`tests/test_authz.py::TestDeploymentHardening` bewaken deze drie punten.

Controleren of het werkt: `GET /whoami` op de API toont wie je bent, welke
rol je hebt en waar die identiteit vandaan komt.

### Automatische data-inwinning

De `worker`-service draait connectors uit `connectors/` op hun eigen
schema. Nieuwe bron toevoegen = één bestand met een `fetch()`-implementatie
(zie `connectors/demo_csv.py` als sjabloon) en `enabled = True`.
Bron-gezondheid is zichtbaar in Instellingen → Datasets.

### Updates uitrollen

```bash
git pull
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml exec app python -m alembic upgrade head
```

---

## Optie 2 — Losse Docker-container (zonder compose)

Voor een snelle interne test-server met SQLite:

```bash
docker build -t sentinel .
docker run -d \
  --name sentinel \
  --restart unless-stopped \
  -p 127.0.0.1:8501:8501 \
  -e ANOMALY_PASSWORD="echt-sterk-wachtwoord" \
  -v /var/lib/sentinel/data:/app/data \
  sentinel
```

Zet zelf een reverse proxy met TLS ervoor (Caddy/nginx) en richt backups
in (`scripts/backup_db.sh` via cron).

---

## Optie 3 — Streamlit Community Cloud (alleen demo's)

**Uitsluitend voor demo's met niet-gevoelige data.** Zie
`DEPLOY_STREAMLIT_CLOUD.md`. Opslag is er ephemeral; koppel desnoods een
externe Postgres via het `database_url`-secret (zie `SETUP_DATABASE.md`).

---

## Optie 4 — On-premise / corporate cloud

**Voor inlichtingen-werk de enige verantwoorde optie.** Zelfde
compose-stack als Optie 1, plus:

- Auth via jullie SSO (Azure AD/ADFS/LDAP) — federeer die in
  Authelia/Keycloak; de app leest `X-Forwarded-User`.
- TLS-certificaten van jullie eigen CA (vervang de Caddy auto-TLS).
- Toegang alleen via VPN; logs (JSON) naar centrale SIEM.
- Vraag het IT-beveiligingsteam vroeg in het proces.

---

## API voor andere tooling

De analyse-kern is ook als REST-API beschikbaar (voor koppelingen,
notebooks of een toekomstige frontend):

```bash
pip install -r requirements-dev.txt
SENTINEL_API_KEY=geheim uvicorn api.main:app --port 8000
# docs: http://localhost:8000/docs
```

---

## Beveiligings-checklist vóór live gaan

- [ ] Sterk uniek wachtwoord ingesteld (geen default); lockout is standaard actief
- [ ] `secrets.toml` / `.env` staan in `.gitignore` (al gedaan)
- [ ] HTTPS actief (niet over HTTP toegankelijk)
- [ ] Database in een volume/externe Postgres, niet in de container
- [ ] Backups draaien én een restore is één keer getest
- [ ] Migraties gedraaid (`python -m alembic upgrade head`)
- [ ] Audit-trail gecontroleerd (Instellingen → Beheer)
- [ ] Voor gevoelige data: IT-beveiliging goedkeuring + SSO-laag actief
- [ ] `/whoami` toont de juiste rol; app niet rechtstreeks bereikbaar

---

## Wat persisteert, wat niet?

| Wat | Waar | Persistent? |
|---|---|---|
| Geïmporteerde data, annotaties, audit-trail, ingest-runs | Postgres of `data/store.db` | Ja, mits volume/externe DB |
| Opgeslagen weergaves & markeringen | zelfde database | Ja |
| Theme-keuze | Browser session_state | Nee (per sessie) |

---

## Hulp

Vastgelopen? Stuur de output van:

```bash
docker compose -f docker-compose.prod.yml logs --tail 50 app worker db
```
