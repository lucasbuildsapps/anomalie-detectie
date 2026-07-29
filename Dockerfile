# Containerized versie van SENTINEL (anomalie-detectie).
# Build: docker build -t sentinel .
# Run:   docker run -p 8501:8501 -e ANOMALY_PASSWORD=jouw-wachtwoord sentinel
#
# Multi-stage: build-tools (compiler) blijven buiten de runtime-image;
# de app draait als niet-root gebruiker.

# ---------- Stage 1: dependencies bouwen ----------
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---------- Stage 2: runtime ----------
FROM python:3.12-slim

# Niet-root gebruiker: beperkt de schade bij een container-compromis.
RUN groupadd --gid 1000 app && \
    useradd --uid 1000 --gid app --create-home app

WORKDIR /app

COPY --from=builder /install /usr/local

# App-bestanden (geen data/ — databases horen in een volume, niet in de image)
COPY app.py alembic.ini ./
COPY core ./core
COPY detectors ./detectors
COPY visualizations ./visualizations
COPY i18n ./i18n
COPY ui ./ui
COPY assets ./assets
COPY migrations ./migrations
COPY connectors ./connectors
COPY .streamlit ./.streamlit
COPY scripts ./scripts

# Demo-CSV wél meeleveren (geen gevoelige data); store.db expliciet niet.
COPY data/missile_attacks_demo.csv ./data/

RUN mkdir -p /app/data && chown -R app:app /app/data

USER app

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health').read()" || exit 1

# Streamlit moet op 0.0.0.0 luisteren in containers, niet localhost.
CMD ["streamlit", "run", "app.py", \
     "--server.address=0.0.0.0", \
     "--server.port=8501", \
     "--server.enableXsrfProtection=true", \
     "--browser.gatherUsageStats=false"]
