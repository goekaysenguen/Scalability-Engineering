import os
import json
import uuid
import time
import socket 
import random 
from psycopg2 import pool
from psycopg2.errors import UniqueViolation
import redis
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel
from contextlib import asynccontextmanager
from collections import defaultdict
import threading
import hashlib

# --- KONFIGURATION ---
REDIS_HOST = os.getenv("REDIS_HOST", None)
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_QUEUE_NAME = os.getenv("REDIS_QUEUE_NAME", "image_tasks")
MAX_QUEUE_SIZE = int(os.getenv("MAX_GLOBAL_QUEUE_SIZE", 45))

DB_HOSTS = os.getenv("DB_HOSTS", None).split(",")
NUMBER_OF_DBS = len(DB_HOSTS)
DB_MAX_CONN = int(os.getenv("DB_MAX_CONN", 20))

# For fairness & load shedding
GLOBAL_MAX_REQUESTS = int(os.getenv("MAX_API_CAPACITY", 50))

CLIENT_SOFT_LIMIT         = GLOBAL_MAX_REQUESTS * 0.2  # Ein Client darf immer 20% Requests haben
CLIENT_BURST_LIMIT        = GLOBAL_MAX_REQUESTS * 0.5  # Ein Client darf bis 50% haben, NUR bei weniger Gesamtlast
GLOBAL_THROTTLE_THRESHOLD = GLOBAL_MAX_REQUESTS * 0.8  # Ab 80% Requests im System werden Soft Limits erzwungen

# Concurrency Tracker
active_requests = 0
client_requests = defaultdict(int)

db_pools = []
db_semaphores = [threading.BoundedSemaphore(DB_MAX_CONN) for _ in range(NUMBER_OF_DBS)]
r = None


def get_db_index(task_id) -> int:
    if isinstance(task_id, str):
        task_id = uuid.UUID(task_id)
    digest = hashlib.sha256(task_id.bytes).digest()
    return int.from_bytes(digest, "big") % NUMBER_OF_DBS

def get_db_pool_and_semaphore(task_id):
    db_index = get_db_index(task_id)
    db_pool = db_pools[db_index]
    db_semaphore = db_semaphores[db_index]
    return db_pool, db_semaphore

def setup_db_and_pool():
    global db_pools
    
    # Deterministic Jitter (Ref: Minimizing correlated failures)
    hostname = socket.gethostname()
    random.seed(hostname)
    
    # Between 0 and 3 sec
    startup_jitter = random.uniform(0.0, 3.0)
    print(f"[{hostname}] Jittered Startup: Warte {startup_jitter:.2f}s um DB nicht zu überlasten.")
    time.sleep(startup_jitter)

    max_retries = 10
    for db_host in DB_HOSTS:
        for attempt in range(max_retries):
            try:
                # Versuche einfach direkt den Pool zu erstellen.
                # Schlägt das fehl (weil Postgres noch hochfährt), landen wir im except-Block.
                db_pool = pool.ThreadedConnectionPool(
                    minconn=1,
                    maxconn=DB_MAX_CONN,
                    host=db_host,
                    database=os.getenv("DB_NAME", None),
                    user=os.getenv("DB_USER", None),
                    password=os.getenv("DB_PASSWORD", None)
                )
                db_pools.append(db_pool)
                break
            except Exception as e:
                print(f"Datenbank noch nicht bereit (Versuch {attempt + 1}/{max_retries}): {e}")
                time.sleep(2 + random.uniform(0.0, 1.0))
        else:
            raise RuntimeError(f"Konnte keine Verbindung zu {db_host} herstellen.")

def acquire_db_conn(task_id):
    """
    Wartet kontrolliert auf eine freie DB-Verbindung.
    Gibt 503 zurück, wenn innerhalb des Timeouts keine Verbindung verfügbar ist.
    """
    db_pool, db_semaphore = get_db_pool_and_semaphore(task_id)
    acquired = db_semaphore.acquire(timeout=2)

    if not acquired:
        raise HTTPException(
            status_code=503,
            detail="Database busy. Please retry later."
        )

    try:
        conn = db_pool.getconn()
        return conn
    except pool.PoolError:
        db_semaphore.release()
        raise HTTPException(
            status_code=503,
            detail="Database connection pool exhausted. Please retry later."
        )
    except Exception:
        db_semaphore.release()
        raise

def release_db_conn(task_id, conn):
    """
    Gibt Connection und Semaphore-Slot sauber zurück.
    """
    db_pool, db_semaphore = get_db_pool_and_semaphore(task_id)
    try:
        if conn:
            db_pool.putconn(conn)
    finally:
        db_semaphore.release()


# --- LIFESPAN (Ausführen bei Start und Shutdown) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global r
    setup_db_and_pool()
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    yield
    # Beim Beenden der API sauber aufräumen
    for db_pool in db_pools:
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
        queue_length = r.llen(REDIS_QUEUE_NAME)
        if queue_length >= MAX_QUEUE_SIZE:
            return Response(content="Queue is full (Backpressure limit). Please retry later.", status_code=503)
    except redis.RedisError as e:
        raise HTTPException(status_code=500, detail=f"Redis error: {str(e)}")

    task_id = str(uuid.uuid4())
    conn = None
    try:
        # Verbindung aus dem Pool holen (extrem schnell, kein Handshake nötig)
        conn = acquire_db_conn(task_id)
        
        # In DB speichern
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO tasks (id, status, image_url, created_at) VALUES (%s, 'pending', %s, NOW())",
                (task_id, req.image_url)
            )
        conn.commit()
        
        # WICHTIG: Timestamp hinzufügen für die Worker TTL Logik!
        task_payload = {
            "task_id": task_id, 
            "image_url": req.image_url,
            "enqueued_at": time.time()
        }
        r.lpush(REDIS_QUEUE_NAME, json.dumps(task_payload))

    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Verbindung ZWINGEND wieder in den Pool zurückgeben
        if conn:
            release_db_conn(task_id, conn)

    return Response(content=json.dumps({"task_id": task_id, "message": "Task queued"}), status_code=202, media_type="application/json")

@app.get("/status/{task_id}")
def get_status(task_id: str):
    """Prüft den Status des Bildes in der Datenbank"""
    conn = None
    try:
        conn = acquire_db_conn(task_id)
        
        with conn.cursor() as cursor:
            cursor.execute("SELECT status, result, created_at, finished_at FROM tasks WHERE id = %s", (task_id,))
            row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Task not found")
        
        return {"task_id": task_id, "status": row[0], "result": row[1], "created_at": row[2].isoformat() if row[2] else None, "finished_at": row[3].isoformat() if row[3] else None}
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            release_db_conn(task_id, conn)