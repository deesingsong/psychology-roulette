FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PSYCHOLOGY_ROULETTE_DB_PATH=/data/psychology-roulette.sqlite3

WORKDIR /app

RUN addgroup --system roulette && adduser --system --ingroup roulette roulette

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . && mkdir /data && chown roulette:roulette /data

USER roulette

EXPOSE 8000

HEALTHCHECK --interval=20s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"

CMD ["uvicorn", "psychology_roulette.api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
