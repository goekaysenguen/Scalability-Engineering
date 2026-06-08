import os
import json
import uuid
import time
import psycopg2
import redis
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

app = FastAPI()

# --- KONFIGURATION ---
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
DB_HOST = os.getenv("DB_HOST", "localhost")

# --- LOAD SHEDDING CONFIG (Mitigation Strategy) ---
MAX_CONCURRENT_REQUESTS = 50  # Maximal 50 parallele Requests erlaubt
current_requests = 0

# Middleware für Load Shedding
@app.middleware("http")
async def load_shedding_middleware(request: Request, call_next):
    global current_requests
    if current_requests >= MAX_CONCURRENT_REQUESTS:
        # Fail Fast! Wir schützen den Server vor Überlastung (Avoiding Overload)
        return Response(content="System overloaded. Please try again later with backoff.", status_code=503)
    
    current_requests += 1
    try:
        response = await call_next(request)
        return response
    finally:
        current_requests -= 1

# --- DATENBANK & REDIS SETUP ---
def get_db():
    return psycopg2.connect(
        host=DB_HOST, database="scalability", user="postgres", password="postgres", connect_timeout=3
    )

def setup_db():
    max_retries = 5
    for attempt in range(max_retries):
        try:
            conn = get_db()
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
            print("Datenbank-Setup erfolgreich!")
            return
        except Exception as e:
            print(f"Datenbank noch nicht bereit (Versuch {attempt + 1}/{max_retries}): {e}")
            time.sleep(2)  # 2 Sekunden warten vor dem nächsten Versuch (Backoff)
            
    print("FATAL: Konnte nach mehreren Versuchen keine Verbindung zur Datenbank herstellen.")


# Führe Setup beim Start aus
setup_db()
r = redis.Redis(host=REDIS_HOST, port=6379, decode_responses=True)

# --- API ENDPUNKTE ---
class ImageRequest(BaseModel):
    image_url: str

@app.post("/classify")
def classify_image(req: ImageRequest):
    """Nimmt ein Bild an und legt es in die Queue (Asynchron)"""
    task_id = str(uuid.uuid4())
    
    try:
        conn = get_db()
        cursor = conn.cursor()
        # In die DB speichern
        cursor.execute(
            "INSERT INTO tasks (id, status, image_url) VALUES (%s, 'pending', %s)",
            (task_id, req.image_url)
        )
        conn.commit()
        
        # In die Redis Queue legen
        task_payload = {"task_id": task_id, "image_url": req.image_url}
        r.lpush("image_tasks", json.dumps(task_payload))
        
        cursor.close()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # WICHTIG: 202 Accepted. Wir warten NICHT auf die KI!
    return Response(content=json.dumps({"task_id": task_id, "message": "Task queued"}), status_code=202, media_type="application/json")

@app.get("/status/{task_id}")
def get_status(task_id: str):
    """Prüft den Status des Bildes in der Datenbank"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT status, result FROM tasks WHERE id = %s", (task_id,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if not row:
            raise HTTPException(status_code=404, detail="Task not found")
        
        return {"task_id": task_id, "status": row[0], "result": row[1]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))