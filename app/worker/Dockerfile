FROM python:3.11-slim

WORKDIR /app

# PYTHONUNBUFFERED sorgt dafür, dass print() sofort im Docker-Log erscheint!
ENV PYTHONUNBUFFERED=1 

# Installiere System-Abhängigkeiten (für psycopg2 / Postgres)
RUN apt-get update && apt-get install -y libpq-dev gcc && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY worker.py .

# Führe den Worker aus
CMD ["python", "worker.py"]