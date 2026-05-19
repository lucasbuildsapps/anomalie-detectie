# Streamlit Community Cloud — stap voor stap

**Voor niet-gevoelige data en demos.** Voor sensitive/classified data: zie
DEPLOY.md voor andere opties.

## Vooraf

Je hebt nodig:
- GitHub-account (gratis)
- Streamlit-account, gekoppeld aan GitHub (https://share.streamlit.io)
- Een sterk wachtwoord dat je niemand vertelt (anders is de tool publiek)

## Stap 1 — Code naar GitHub

In de project-folder (`C:\Users\lucas\anomalie-detectie`):

```cmd
git init
git add .
git commit -m "Initial commit"
```

Maak een repository aan op GitHub (private of public — kies **private**
als je de sourcecode niet wilt delen):

```cmd
git remote add origin https://github.com/<jouw-username>/anomalie-detectie.git
git branch -M main
git push -u origin main
```

**Controleer**: zorg dat `.streamlit/secrets.toml` **NIET** is meegekomen.
Hij staat in `.gitignore` dus zou niet moeten — controleer met:

```cmd
git log --all --full-history -- .streamlit/secrets.toml
```

Als dit een commit toont, is je wachtwoord (of placeholder) gelekt en
moet je de repo opnieuw beginnen.

## Stap 2 — Deploy op Streamlit Cloud

1. Ga naar https://share.streamlit.io
2. Klik **Create app** → **Deploy a public app from GitHub**
3. Selecteer je `anomalie-detectie` repository
4. **Branch**: `main`
5. **Main file path**: `app.py`
6. **App URL**: kies een gewenste subdomein, bv. `anomalie-demo`
7. Klik nog NIET op Deploy — eerst **Advanced settings**

## Stap 3 — Secrets configureren (CRUCIAAL)

Onder Advanced settings, plak in het Secrets-veld:

```toml
password = "kies-een-sterk-wachtwoord-van-minstens-16-tekens"
```

Vervang door een écht sterk wachtwoord (gebruik een password manager).
**Zonder deze step is je app open voor iedereen op internet.**

## Stap 4 — Deploy

Klik **Deploy**. Eerste build duurt ~3-5 minuten (alle Python-packages
installeren). Je krijgt een URL als `https://anomalie-demo.streamlit.app`.

## Stap 5 — Testen

1. Open de URL — je zou direct een login-scherm moeten zien
2. Probeer een willekeurig wachtwoord → moet "Onjuist wachtwoord" geven
3. Type het juiste wachtwoord → app opent

Als de login NIET verschijnt: je secrets zijn niet goed gezet. Ga terug
naar Streamlit Cloud → app dashboard → Settings → Secrets.

## Belangrijke gotcha's

### Data persisteert niet tussen reboots

Streamlit Community Cloud heeft een **ephemeral filesystem** — de
SQLite-database `data/store.db` wordt gewist bij elke redeploy of restart.
Voor permanent gebruik:

**Optie A**: Accepteer dit voor demo's. Gebruikers laden bij elke sessie
  de demo-data of uploaden hun bestand opnieuw.

**Optie B**: Externe database. Voeg een **PostgreSQL** of **Supabase**
  connectie toe — dit vereist code-aanpassing in `core/storage.py`.
  Wil je dat ik dat bouw, laat het weten.

### Resource-limieten

Gratis tier krijgt ~1 GB RAM. Datasets boven ~100.000 rijen kunnen krap
worden. Voor grotere data: zelfgehoste Docker of betaalde Streamlit-tier.

### "Sleep" na inactiviteit

Apps slapen na ~7 dagen geen verkeer. Eerste bezoeker na slaap wacht
~30 sec. tot wakker.

### URL is publiek bekend

Je `anomalie-demo.streamlit.app` URL is door iedereen op te zoeken.
**Het wachtwoord is je enige verdediging.** Gebruik geen zwak wachtwoord.

## Updaten

Code-wijzigingen pushen → Streamlit Cloud rebuild automatisch:

```cmd
git add .
git commit -m "<wat veranderde>"
git push
```

Build duurt ~1-2 minuten voor incrementele changes.

## App stopzetten / verwijderen

Op Streamlit Cloud dashboard → je app → **Settings** → **Delete app**.
Dit haalt de app offline en geeft de URL vrij.

## Checklist voor live gaan

- [ ] Sterk wachtwoord in Secrets gezet
- [ ] `.streamlit/secrets.toml` NIET in git history
- [ ] Repo is private (tenzij je sourcecode wil delen)
- [ ] Eerst zelf getest met de juiste login
- [ ] Vrienden/collega's gevraagd het wachtwoord NIET te delen
- [ ] **GEEN gevoelige data uploaden** zolang dit op Streamlit Cloud staat

## Alternatieven bij problemen

| Probleem | Oplossing |
|---|---|
| Tool werkt lokaal niet meer na clone | `pip install -r requirements.txt` opnieuw |
| Streamlit Cloud build faalt | Check logs in dashboard — vaak een package-versie-conflict |
| Wachtwoord vergeten | Update Secrets in dashboard, geen reboot nodig |
| App is te traag | Upgrade naar betaalde tier of zelf-hosten via Docker |
