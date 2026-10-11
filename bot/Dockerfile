FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080 DATA_DIR=/data BIND_HOST=0.0.0.0
RUN apt-get update && apt-get install -y --no-install-recommends tzdata ca-certificates && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 bot && useradd --uid 10001 --gid bot --create-home bot && mkdir /data && chown bot:bot /data
WORKDIR /app
COPY --chown=bot:bot app.py engine.py market.py strategy.py ranked_scanner.py ranked_backtest.py scanner.py news.py news.html broker_check.py dashboard.html backtest.py research.py research.html updater.py version.py mac_update.py updates.html ./
USER bot
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/healthz',timeout=3)"
CMD ["python", "app.py"]
