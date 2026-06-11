import os
import json
import uuid
import time
import socket 
import random 
import psycopg2
from psycopg2 import pool
from psycopg2.errors import UniqueViolation
import redis
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel
from contextlib import asynccontextmanager
from collections import defaultdict

# --- KONFIGURATION ---
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
DB_HOST = os.getenv("DB_HOST", "localhost")

# --- MULTI-TENANT FAIRNESS & LOAD SHEDDING ---
# Er repräsentiert die Kapazität EINER API-Node.
GLOBAL_MAX_REQUESTS = int(os.getenv("MAX_API_CAPACITY", 50))

CLIENT_SOFT_LIMIT         = GLOBAL_MAX_REQUESTS * 0.2  # Ein Client darf immer 20% Requests haben
CLIENT_BURST_LIMIT        = GLOBAL_MAX_REQUESTS * 0.5  # Ein Client darf bis 50% haben, NUR bei weniger Gesamtlast
GLOBAL_THROTTLE_THRESHOLD = GLOBAL_MAX_REQUESTS * 0.8  # Ab 80% Requests im System werden Soft Limits erzwungen

# Little's Law: MAX_QUEUE_SIZE = Max_Age (15s) * (Workers * Throughput_per_Worker)
# Da Redis global ist, muss dieser Wert die Kapazität des gesamten Clusters spiegeln!
MAX_QUEUE_SIZE = int(os.getenv("MAX_GLOBAL_QUEUE_SIZE", 45))

# Concurrency Tracker
active_requests = 0
client_requests = defaultdict(int)

db_pool = None
r = None

def setup_db_and_pool():
    global db_pool
    
    # Deterministic Jitter (Ref: Minimizing correlated failures)
    hostname = socket.gethostname()
    random.seed(hostname)
    
    # Between 0 and 3 sec
    startup_jitter = random.uniform(0.0, 3.0)
    print(f"[{hostname}] Jittered Startup: Warte {startup_jitter:.2f}s zur Vermeidung von Race Conditions.")
    time.sleep(startup_jitter)

    max_retries = 10
    for attempt in range(max_retries):
        try:
            # 1. Normale Verbindung testen & Tabellen erstellen
            conn = psycopg2.connect(
                host=DB_HOST, database="scalability", user="postgres", password="postgres", connect_timeout=3
            )
            
            # Autocommit einschalten, damit wir DDL-Fehler sauber catchen können,
            # ohne die laufende Transaktion zu blockieren.
            conn.autocommit = True 
            cursor = conn.cursor()
            
            try:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS tasks (
                        id VARCHAR(50) PRIMARY KEY,
                        status VARCHAR(20),
                        image_url TEXT,
                        result TEXT,
                        enqueued_at FLOAT
                    )
                """)
            except UniqueViolation:
                # Falls trotz Jitter eine Kollision auftritt (weil Postgres IF NOT EXISTS intern so verarbeitet),
                # ignorieren wir diesen spezifischen Fehler einfach, da es bedeutet, dass die Tabelle existiert.
                print(f"[{hostname}] Tabelle wurde zeitgleich von einer anderen Node erstellt. Ignoriere Fehler.")
            
            cursor.close()
            conn.autocommit = False
            conn.close()

            # 2. Connection Pool erstellen
            db_pool = pool.ThreadedConnectionPool(
                minconn=1,
                maxconn=20, # Maximal 20 Verbindungen gleichzeitig offen halten
                host=DB_HOST,
                database="scalability",
                user="postgres",
                password="postgres"
            )
            print(f"[{hostname}] Datenbank-Setup und Connection Pool erfolgreich initialisiert!")
            return
        except Exception as e:
            print(f"Datenbank noch nicht bereit (Versuch {attempt + 1}/{max_retries}): {e}")
            time.sleep(2 + random.uniform(0.0, 1.0))
            
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

# --- MIDDLEWARE FÜR MULTI-TENANT FAIRNESS AND LOAD SHEDDING ---
@app.middleware("http")
async def multi_tenant_fairness_middleware(request: Request, call_next):
    global active_requests, client_requests
    
    client_id = request.headers.get("X-Client-ID", "unknown_client")
    
    # 1. Globales Hard Limit (Verhindert Node-Absturz)
    if active_requests >= GLOBAL_MAX_REQUESTS:
        return Response(content="System overloaded.", status_code=503)
    
    client_count = client_requests[client_id]
    
    # 2. Client Hard Burst Limit (Noisy Neighbor Protection)
    if client_count >= CLIENT_BURST_LIMIT:
        return Response(content="Client burst quota exceeded.", status_code=429) # 429 = Too Many Requests
        
    # 3. Client Soft Limit (Greift nur, wenn das Gesamtsystem unter Last steht!)
    if client_count >= CLIENT_SOFT_LIMIT and active_requests >= GLOBAL_THROTTLE_THRESHOLD:
        return Response(content="System under heavy load. Client soft quota exceeded.", status_code=429)

    # Request zulassen
    active_requests += 1
    client_requests[client_id] += 1

    try:
        response = await call_next(request)
        return response
    finally:
        active_requests -= 1
        client_requests[client_id] -= 1


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
        
        # WICHTIG: Timestamp hinzufügen für die Worker TTL Logik!
        task_payload = {
            "task_id": task_id, 
            "image_url": req.image_url,
            "enqueued_at": time.time()
        }
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