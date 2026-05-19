# Deployment-handleiding

Drie opties om de tool online beschikbaar te maken, oplopend in veiligheid en
beheer-complexiteit.

---

## ⚠️ Eerst: veiligheid

**Lees dit voordat je gaat deployen.**

Deze tool slaat geüploade data lokaal in SQLite op (`data/store.db`). Bij online
deployment betekent dat: alle data die analisten uploaden wordt opgeslagen op
de server waar de app draait.

| Datatype | Veilig om publiek te deployen? |
|---|---|
| Open-source data (kpszsu posts, nieuws, etc.) | Ja, mits met password-auth |
| Interne/eigen analyses zonder bronvermelding | Misschien — vraag IT |
| Geclassificeerde of Vertrouwelijke data | **NEE** — alleen on-premise |
| Persoonsgegevens (AVG/GDPR) | Verwerkersovereenkomst nodig |

Voor inlichtingen-werk: vraag **altijd** je IT-beveiliging voordat je gevoelige
data in een tool stopt die online draait, ook al staat er een wachtwoord op.

---

## Wachtwoord-authenticatie instellen

Voordat je gaat deployen, configureer een wachtwoord:

**Optie A — secrets.toml** (aanbevolen voor self-hosted)

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# Bewerk en zet een sterk wachtwoord
```

**Optie B — Environment variable** (handig voor Docker/cloud)

```bash
export ANOMALY_PASSWORD="kies-een-sterk-wachtwoord"
```

Zonder een van beide draait de app zonder login (alleen geschikt voor lokaal).

---

## Optie 1 — Streamlit Community Cloud (snelste, gratis)

**Geschikt voor**: demo's, niet-gevoelige data, persoonlijke projecten.
**Niet geschikt voor**: classified, vertrouwelijk, of bedrijfskritisch.

### Stappen

1. Push de code naar een **public of private GitHub repository**.
2. Ga naar [share.streamlit.io](https://share.streamlit.io).
3. Klik *New app* → kies je repo, branch, en `app.py` als entry point.
4. Onder *Advanced settings*, zet je `password` als secret:
   ```toml
   password = "..."
   ```
5. Deploy. De URL wordt `https://[naam].streamlit.app`.

**Caveats**:
- Data persisteert NIET tussen redeploys (SQLite-file wordt overschreven).
- Alles in `data/` is publiek leesbaar als je repo public is.
- Geen HTTPS-certificaat-control, geen IP-whitelisting in gratis versie.

---

## Optie 2 — Docker op eigen VPS

**Geschikt voor**: serieus intern gebruik, IT-beheerde omgeving, opslag van
data onder eigen controle.

### Build

```bash
docker build -t anomalie-detectie .
```

### Lokaal testen

```bash
docker run -p 8501:8501 \
  -e ANOMALY_PASSWORD="test-wachtwoord" \
  -v $(pwd)/data:/app/data \
  anomalie-detectie
```

Open `http://localhost:8501`. De `-v` mount zorgt dat de SQLite-database
persisteert in de host-map.

### Productie-deployment op een VPS

Op een Linux-VPS (Ubuntu 22.04, Debian 12, etc.):

```bash
# Op de server
git clone https://your-git-host/anomalie-detectie.git
cd anomalie-detectie
docker build -t anomalie-detectie .
docker run -d \
  --name anomalie \
  --restart unless-stopped \
  -p 127.0.0.1:8501:8501 \
  -e ANOMALY_PASSWORD="echt-sterk-wachtwoord" \
  -v /var/lib/anomalie/data:/app/data \
  anomalie-detectie
```

Zet **Caddy** of **nginx** als reverse proxy ervoor voor HTTPS:

```caddy
# /etc/caddy/Caddyfile
anomalie.jouwdomein.nl {
    reverse_proxy 127.0.0.1:8501
}
```

Caddy regelt automatisch een Let's Encrypt TLS-certificaat.

### Updates uitrollen

```bash
docker stop anomalie && docker rm anomalie
git pull
docker build -t anomalie-detectie .
docker run -d --name anomalie ...  # zelfde commando als hierboven
```

---

## Optie 3 — On-premise / corporate cloud

**Voor inlichtingen-werk de enige verantwoorde optie.**

Praktisch:
- Container draaien op een VM in jullie eigen datacenter / private cloud
- Auth via jullie SSO (Azure AD, ADFS, LDAP) — vereist een extra auth-proxy
  laag zoals **oauth2-proxy** of **Authelia** vóór de Streamlit-app
- TLS-certificaten van jullie eigen CA
- Toegang alleen via VPN
- Logs naar centrale SIEM
- Vraag het IT-beveiligingsteam vroeg in het proces

Voor een minimale corporate setup:

```
Browser (intern) → Reverse proxy + SSO (oauth2-proxy) → Streamlit container
                                                      ↓
                                                Volumes voor data
```

---

## Beveiligings-checklist vóór live gaan

- [ ] Sterk uniek wachtwoord ingesteld (geen default)
- [ ] `secrets.toml` staat in `.gitignore` (al gedaan)
- [ ] HTTPS actief (niet over HTTP toegankelijk)
- [ ] `data/` map persistent én buiten de container/server-root
- [ ] Backups van `data/store.db` ingericht (cron + rsync)
- [ ] Toegang gelogd (Caddy/nginx access log)
- [ ] Bewust gemaakt: wie heeft toegang tot welke datasets
- [ ] Voor gevoelige data: IT-beveiliging goedkeuring

---

## Wat persisteert, wat niet?

| Wat | Waar | Persistent? |
|---|---|---|
| Geïmporteerde data | `data/store.db` (SQLite) | Ja, mits volume gemount |
| Datasets-metadata | `data/store.db` | Ja |
| Annotaties | `data/store.db` | Ja |
| Logo | `assets/logo.png` | Image-fixed |
| Theme-keuze | Browser session_state | Nee (per sessie) |

**Belangrijk**: bij Streamlit Community Cloud is de filesystem **ephemeral** —
data overleeft een redeploy niet. Voor data-persistentie moet je een externe
database gebruiken (PostgreSQL via Streamlit secrets bijvoorbeeld). Dat is een
toekomstige uitbreiding.

---

## Hulp

Vastgelopen? Stuur de output van:

```bash
docker logs anomalie 2>&1 | tail -50
```

Bij Streamlit Cloud: dashboard → *Manage app* → *Logs*.
