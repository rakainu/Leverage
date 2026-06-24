# scripts/marketdata/Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/
COPY run_gateway.py config.yaml ./
ENV PYTHONPATH=/app/src GATEWAY_CONFIG=/app/config.yaml
EXPOSE 8060
HEALTHCHECK --interval=15s --timeout=4s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8060/__gw/health').status==200 else 1)"
CMD ["python", "run_gateway.py"]
