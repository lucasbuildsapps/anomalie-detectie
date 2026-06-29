# Containerized versie van de Anomalie-detectie tool.
# Build: docker build -t anomalie-detectie .
# Run:   docker run -p 8501:8501 -e ANOMALY_PASSWORD=jouw-wachtwoord anomalie-detectie

FROM python:3.12-slim

WORKDIR /app

# Systeem-deps voor pandas/scipy/etc. (klein houden)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Eerst alleen requirements voor cache-efficiëntie
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# App-bestanden
COPY app.py ./
COPY core ./core
COPY detectors ./detectors
COPY visualizations ./visualizations
COPY i18n ./i18n
COPY assets ./assets
COPY .streamlit ./.streamlit
COPY data ./data
COPY scripts ./scripts

# Schrijf-rechten voor SQLite + uploaded data
RUN mkdir -p /app/data && chmod 777 /app/data

EXPOSE 8501

# Healthcheck zodat orchestrators (Docker/Kubernetes) state kennen
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health').read()" || exit 1

# Streamlit moet op 0.0.0.0 luisteren in containers, niet localhost.
# CORS uit voor reverse-proxy gebruik.
CMD ["streamlit", "run", "app.py", \
     "--server.address=0.0.0.0", \
     "--server.port=8501", \
     "--server.enableXsrfProtection=true", \
     "--browser.gatherUsageStats=false"]
