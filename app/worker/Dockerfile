FROM python:3.11-slim

WORKDIR /app

# show print() immediatly in Docker-Log
ENV PYTHONUNBUFFERED=1 

# install dependencies
RUN apt-get update && apt-get install -y libpq-dev gcc && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY worker.py .

CMD ["python", "worker.py"]