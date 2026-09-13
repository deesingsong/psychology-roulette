FROM python:3.12-alpine

WORKDIR /app
RUN addgroup -S app && adduser -S app -G app \
    && pip install --no-cache-dir "fastapi>=0.116,<1" "httpx>=0.28,<1" \
       "pydantic>=2.11,<3" "uvicorn>=0.35,<1"
COPY app.py /app/app.py
USER app
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
