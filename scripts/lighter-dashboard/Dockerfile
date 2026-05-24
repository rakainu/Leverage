FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY templates/ ./templates/
COPY config.yaml run_dashboard.py ./

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONPATH=/app/src \
    TZ=America/Sao_Paulo

EXPOSE 8080

CMD ["python", "-u", "run_dashboard.py", "--config", "config.yaml", "--host", "0.0.0.0", "--port", "8080"]
