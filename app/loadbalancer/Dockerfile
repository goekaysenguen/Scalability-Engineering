FROM --platform=linux/amd64 python:3.14-slim

WORKDIR /app

COPY main.py .

RUN pip install --no-cache-dir fastapi uvicorn httpx

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]