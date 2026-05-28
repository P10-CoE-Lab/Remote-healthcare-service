FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer cache efficiency)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Expose demo API port
EXPOSE 8000

# Default command for testing - runs with demo UI and rules engine enabled
CMD ["python", "run.py", "--persona", "personas/cardiac_patient.yaml", "--scenario", "scenarios/health/tachycardia_episode.yaml", "--compression", "60", "--demo", "--rules-config", "config/rules_config.yaml"]
