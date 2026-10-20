FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=3000 \
    DORMCARE_DATA=/data/store.json

WORKDIR /app
COPY . /app

RUN useradd --create-home --uid 10001 dormcare \
    && mkdir -p /data \
    && chown -R dormcare:dormcare /app /data

USER dormcare
EXPOSE 3000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','3000')+'/api/health', timeout=3)"

CMD ["python", "app.py"]
