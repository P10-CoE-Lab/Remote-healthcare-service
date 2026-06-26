FROM python:3.11-slim

RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/logs && chown -R appuser:appuser /app
USER appuser

CMD ["uvicorn", "http_server:app", "--host", "0.0.0.0", "--port", "8000"]
