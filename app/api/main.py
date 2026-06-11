import os
import json
import uuid
import time
import psycopg2
from psycopg2 import pool
import redis
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel
from contextlib import asynccontextmanager

# --- KONFIGURATION ---
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
DB_HOST = os.getenv("DB_HOST", "localhost")

# --- LOAD SHEDDING / BACKPRESSURE CONFIG ---
MAX_CONCURRENT_REQUESTS = 50  # Max parallele API-Requests (Vermeidung von Thread-Exhaustion)
MAX_QUEUE_SIZE = 100          # Backpressure: Max erlaubte Bilder in der Queue
current_requests = 0

# --- GLOBALE VARIABLEN ---
db_pool = None
r = None

def setup_db_and_pool():
    global db_pool
    max_retries = 5
    for attempt in range(max_retries):
        try:
            # 1. Normale Verbindung testen & Tabellen erstellen
            conn = psycopg2.connect(
                host=DB_HOST, database="scalability", user="postgres", password="postgres", connect_timeout=3
            )
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id VARCHAR(50) PRIMARY KEY,
                    status VARCHAR(20),
                    image_url TEXT,
                    result TEXT
                )
            """)
            conn.commit()
            cursor.close()
            conn.close()

            # 2. Connection Pool erstellen (Best Practice: Wiederverwendung von DB-Verbindungen)
            db_pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=1,
                maxconn=20, # Maximal 20 Verbindungen gleichzeitig offen halten
                host=DB_HOST,
                database="scalability",
                user="postgres",
                password="postgres"
            )
            print("Datenbank-Setup und Connection Pool erfolgreich initialisiert!")
            return
        except Exception as e:
            print(f"Datenbank noch nicht bereit (Versuch {attempt + 1}/{max_retries}): {e}")
            time.sleep(2)
            
    print("FATAL: Konnte nach mehreren Versuchen keine Verbindung zur Datenbank herstellen.")

# --- LIFESPAN (Ausführen bei Start und Shutdown) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global r, db_pool
    setup_db_and_pool()
    r = redis.Redis(host=REDIS_HOST, port=6379, decode_responses=True)
    yield
    # Beim Beenden der API sauber aufräumen
    if db_pool:
        db_pool.closeall()
    if r:
        r.close()

app = FastAPI(lifespan=lifespan)

# --- MIDDLEWARE FÜR CONCURRENCY LOAD SHEDDING ---
@app.middleware("http")
async def load_shedding_middleware(request: Request, call_next):
    global current_requests
    if current_requests >= MAX_CONCURRENT_REQUESTS:
        return Response(content="System overloaded (Concurrency limit). Please retry with backoff.", status_code=503)
    
    current_requests += 1
    try:
        response = await call_next(request)
        return response
    finally:
        current_requests -= 1


# --- API ENDPUNKTE ---
class ImageRequest(BaseModel):
    image_url: str

@app.post("/classify")
def classify_image(req: ImageRequest):
    """Nimmt ein Bild an und legt es in die Queue (Asynchron)"""
    
    # BACKPRESSURE: Prüfen, ob die Queue zu voll ist ("Avoiding insurmountable queue backlogs")
    try:
        queue_length = r.llen("image_tasks")
        if queue_length >= MAX_QUEUE_SIZE:
            return Response(content="Queue is full (Backpressure limit). Please retry later.", status_code=503)
    except redis.RedisError as e:
        raise HTTPException(status_code=500, detail=f"Redis error: {str(e)}")

    task_id = str(uuid.uuid4())
    conn = None
    try:
        # Verbindung aus dem Pool holen (extrem schnell, kein Handshake nötig)
        conn = db_pool.getconn()
        cursor = conn.cursor()
        
        # In DB speichern
        cursor.execute(
            "INSERT INTO tasks (id, status, image_url) VALUES (%s, 'pending', %s)",
            (task_id, req.image_url)
        )
        conn.commit()
        cursor.close()
        
        # In die Redis Queue legen
        task_payload = {"task_id": task_id, "image_url": req.image_url}
        r.lpush("image_tasks", json.dumps(task_payload))
        
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Verbindung ZWINGEND wieder in den Pool zurückgeben
        if conn:
            db_pool.putconn(conn)

    return Response(content=json.dumps({"task_id": task_id, "message": "Task queued"}), status_code=202, media_type="application/json")

@app.get("/status/{task_id}")
def get_status(task_id: str):
    """Prüft den Status des Bildes in der Datenbank"""
    conn = None
    try:
        conn = db_pool.getconn()
        cursor = conn.cursor()
        cursor.execute("SELECT status, result FROM tasks WHERE id = %s", (task_id,))
        row = cursor.fetchone()
        cursor.close()

        if not row:
            raise HTTPException(status_code=404, detail="Task not found")
        
        return {"task_id": task_id, "status": row[0], "result": row[1]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            db_pool.putconn(conn)