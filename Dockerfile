# Socialscraper als Hintergrunddienst auf einem Linux-Server (kein Fenster).
# NICHT AUSGEFÜHRT / UNGEPRÜFT: Auf dem Entwicklungsrechner war kein Docker vorhanden.
#
# Bauen (im Programmordner):   docker build -t socialscraper .
# Starten:                     siehe deploy/docker-compose.yml und docs/SERVER.md
#
# Hinweis: Google Chrome gibt es für Linux nur für 64-Bit-Intel/AMD (amd64), nicht für ARM-Server.
FROM --platform=linux/amd64 python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SS_HEADLESS=true \
    TZ=Europe/Berlin

# Google Chrome (Stable) aus dem offiziellen Google-Paketquelle; Patchright/Playwright steuert es über channel="chrome".
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg tini fonts-liberation tzdata \
 && curl -fsSL https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
 && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends google-chrome-stable \
 && apt-get purge -y --auto-remove gnupg \
 && rm -rf /var/lib/apt/lists/*

# Node.js 22 (wird von yt-dlp für YouTube gebraucht) aus dem offiziellen Node-Image.
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node

# Nutzer ohne Root; /data ist das Volume für alle Laufzeitdaten (Datenbank, Cookies, Browserprofile, Protokolle).
RUN useradd --create-home --uid 10001 socialscraper \
 && mkdir -p /app /data \
 && chown socialscraper:socialscraper /app /data

WORKDIR /app
COPY requirements.txt /app/
RUN pip install -r requirements.txt

COPY --chown=socialscraper:socialscraper . /app
# data\ des Programms zeigt auf das Volume.
RUN rm -rf /app/data && ln -s /data /app/data && chown -h socialscraper:socialscraper /app/data

USER socialscraper
VOLUME ["/data"]

# Prüft, ob der Dienst seinen Herzschlag schreibt (data/daemon.json, Agent B).
HEALTHCHECK --interval=2m --timeout=20s --start-period=60s --retries=3 \
  CMD python -c "import json,time,datetime,sys; d=json.load(open('/data/daemon.json')); t=datetime.datetime.strptime(d['last_beat'],'%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=datetime.timezone.utc); sys.exit(0 if time.time()-t.timestamp()<600 else 1)"

ENTRYPOINT ["tini", "--", "python", "-m", "socialscraper"]
CMD ["daemon", "--root", "/app"]
