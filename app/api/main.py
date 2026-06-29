import hashlib
import json
import os
import random
import socket
import threading
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager

import redis
from fastapi import FastAPI, HTTPException, Request, Response
from psycopg2 import pool
from psycopg2.errors import UniqueViolation
from pydantic import BaseModel

# CONFIG
REDIS_HOST = os.getenv("REDIS_HOST", None)
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_QUEUE_NAME = os.getenv("REDIS_QUEUE_NAME", "image_tasks")
MAX_QUEUE_SIZE = int(os.getenv("MAX_GLOBAL_QUEUE_SIZE", 45))

DB_HOSTS = os.getenv("DB_HOSTS", None).split(",")
NUMBER_OF_DBS = len(DB_HOSTS)
DB_MAX_CONN = int(os.getenv("DB_MAX_CONN", 20))

# For fairness & load shedding
GLOBAL_MAX_REQUESTS = int(os.getenv("MAX_API_CAPACITY", 50))
# fmt: off
CLIENT_SOFT_LIMIT         = GLOBAL_MAX_REQUESTS * 0.2  # A client may always have 20% of requests
CLIENT_BURST_LIMIT        = GLOBAL_MAX_REQUESTS * 0.5  # A client may have up to 50%, but only when overall load is low
GLOBAL_THROTTLE_THRESHOLD = GLOBAL_MAX_REQUESTS * 0.8  # When system reaches 80% of requests, soft limits are enforced
# fmt: on

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
    """
    Initializes database connection pools for all hosts in DB_HOSTS with
    retry logic and startup jitter.

    - Applies a deterministic jitter (0–3 seconds) to avoid overloading
      databases during simultaneous startup.
    - Attempts to connect to each database host with retries (max_retries = 10).
    - Raises a RuntimeError if any host cannot be reached after all retries.

    Global:
        db_pools (list): List of database connection pools.
    """

    global db_pools

    # Deterministic Jitter
    hostname = socket.gethostname()
    random.seed(hostname)

    # Jitter between 0 and 3 sec
    startup_jitter = random.uniform(0.0, 3.0)
    print(f"[{hostname}] jittered startup: wait {startup_jitter:.2f}s to avoid overloading DBs.")
    time.sleep(startup_jitter)

    # Attempts to establish a connection to each database with retries
    max_retries = 10
    for db_host in DB_HOSTS:
        for attempt in range(max_retries):
            try:
                db_pool = pool.ThreadedConnectionPool(
                    minconn=1,
                    maxconn=DB_MAX_CONN,
                    host=db_host,
                    database=os.getenv("DB_NAME", None),
                    user=os.getenv("DB_USER", None),
                    password=os.getenv("DB_PASSWORD", None),
                )
                db_pools.append(db_pool)
                break
            except Exception as e:
                print(f"DB not ready (attempt {attempt + 1}/{max_retries}): {e}")
                time.sleep(2 + random.uniform(0.0, 1.0))
        else:
            raise RuntimeError(f"Could not establish connection to {db_host}.")


def acquire_db_conn(task_id):
    """
    Acquires a database connection for a given task ID using a semaphore
    to enforce concurrency limits per database.

    Args:
        task_id: The task ID used to determine the target database.

    Returns:
        The acquired database connection.

    Raises:
        HTTPException (503): If the database is busy or the connection
        pool is exhausted
    """
    db_pool, db_semaphore = get_db_pool_and_semaphore(task_id)
    acquired = db_semaphore.acquire(timeout=2)

    if not acquired:
        raise HTTPException(status_code=503, detail="Database busy. Please retry later.")

    try:
        conn = db_pool.getconn()
        return conn
    except pool.PoolError as e:
        db_semaphore.release()
        raise HTTPException(status_code=503, detail="Database connection pool exhausted. Please retry later.") from e
    except Exception:
        db_semaphore.release()
        raise


def release_db_conn(task_id, conn):
    db_pool, db_semaphore = get_db_pool_and_semaphore(task_id)
    try:
        if conn:
            db_pool.putconn(conn)
    finally:
        db_semaphore.release()


# executed at start and shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    global r
    setup_db_and_pool()
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    yield
    # clean up at API shutdown
    for db_pool in db_pools:
        if db_pool:
            db_pool.closeall()
    if r:
        r.close()


app = FastAPI(lifespan=lifespan)


# MIDDLEWARE
@app.middleware("http")
async def multi_tenant_fairness_middleware(request: Request, call_next):
    """
    Middleware to enforce fairness and rate limiting in a multi-tenant system.

    Implements three layers of protection:
    1. **Global Hard Limit**: Rejects requests if the total active requests exceed `GLOBAL_MAX_REQUESTS`.
    2. **Client Hard Burst Limit**: Enforces a strict per-client limit (`CLIENT_BURST_LIMIT`) to prevent noisy neighbors.
    3. **Client Soft Limit**: Only activates when the system is under heavy load (`active_requests >= GLOBAL_THROTTLE_THRESHOLD`).
       If the client's request count exceeds `CLIENT_SOFT_LIMIT`, further requests are throttled.

    Tracks active requests globally and per client, ensuring fair resource distribution.

    Returns:
        Response: A response indicating overload, throttling, or the result of the request.
    """
    global active_requests, client_requests

    client_id = request.headers.get("X-Client-ID", "unknown_client")

    # 1. Global Hard Limit
    if active_requests >= GLOBAL_MAX_REQUESTS:
        return Response(content="System overloaded.", status_code=503)

    client_count = client_requests[client_id]

    # 2. Client Hard Burst Limit (Noisy Neighbor Protection)
    if client_count >= CLIENT_BURST_LIMIT:
        return Response(content="Client burst quota exceeded.", status_code=429)  # 429 = Too Many Requests

    # 3. Client Soft Limit (Only engages when the overall system is under load!)
    if client_count >= CLIENT_SOFT_LIMIT and active_requests >= GLOBAL_THROTTLE_THRESHOLD:
        return Response(content="System under heavy load. Client soft quota exceeded.", status_code=429)

    active_requests += 1
    client_requests[client_id] += 1

    try:
        response = await call_next(request)
        return response
    finally:
        active_requests -= 1
        client_requests[client_id] -= 1


# API ENDPOINT
class ImageRequest(BaseModel):
    image_url: str
    task_id: str


@app.post("/classify")
def classify_image(req: ImageRequest):
    """ "
    Accepts an image, stores it in the queue asynchronously, and ensures idempotency.

    Implements backpressure by checking if the Redis queue is full. If so, returns a 503 response.
    Uses an idempotency key (`task_id`) to prevent duplicate processing of the same request.
    Stores the task in the database and adds it to the Redis queue for workers to process.

    Args:
        req (ImageRequest): The request containing the image URL and task ID.

    Returns:
        Response: A JSON response with the task ID and a message indicating the task was queued.
                 Returns 202 (Accepted) on success, 503 (Service Unavailable) if the queue is full,
                 or 409 (Conflict) if the task already exists (idempotency).

    Raises:
        HTTPException (500): If a Redis or database error occurs.
    """

    # BACKPRESSURE: Check, if Queue is full
    try:
        queue_length = r.llen(REDIS_QUEUE_NAME)
        if queue_length >= MAX_QUEUE_SIZE:
            return Response(content="Queue is full. Please retry later.", status_code=503)
    except redis.RedisError as e:
        raise HTTPException(status_code=500, detail=f"Redis error: {str(e)}") from e

    # Take Idempotency Key from Client
    task_id = req.task_id

    conn = None
    try:
        # Get connection from pool
        conn = acquire_db_conn(task_id)

        # Store in DB
        with conn.cursor() as cursor:
            # If task is allready in the DB, Postgres will raise an error
            cursor.execute(
                "INSERT INTO tasks (id, status, image_url, created_at) VALUES (%s, 'pending', %s, NOW())",
                (task_id, req.image_url),
            )
        conn.commit()

        # Add Timestamp For Worker TTL Logic!
        task_payload = {"task_id": task_id, "image_url": req.image_url, "enqueued_at": time.time()}
        r.lpush(REDIS_QUEUE_NAME, json.dumps(task_payload))

    except UniqueViolation:
        # Idempotency-Logic
        if conn:
            conn.rollback()
        # Do not do the job again. Just return the same answer.
        return Response(
            content=json.dumps({"task_id": task_id, "message": "Task already queued (idempotent response)"}),
            status_code=202,
            media_type="application/json",
        )
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        # return the connection to the pool
        if conn:
            release_db_conn(task_id, conn)

    return Response(
        content=json.dumps({"task_id": task_id, "message": "Task queued"}),
        status_code=202,
        media_type="application/json",
    )


@app.get("/status/{task_id}")
def get_status(task_id: str):
    """
    Retrieves the status and result of an image classification task from the database.

    Queries the database for the task's status, result, creation time, and completion time.
    Returns a structured response with the task details. If the task does not exist, raises a 404 error.
    Handles database connection acquisition, cleanup, and error scenarios.

    Args:
        task_id (str): The unique identifier of the task.

    Returns:
        dict: A dictionary containing the task's status, result, and timestamps.

    Raises:
        HTTPException (404): If the task is not found in the database.
        HTTPException (500): If a database error occurs.
    """

    conn = None
    try:
        conn = acquire_db_conn(task_id)

        with conn.cursor() as cursor:
            cursor.execute("SELECT status, result, created_at, finished_at FROM tasks WHERE id = %s", (task_id,))
            row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Task not found")

        return {
            "task_id": task_id,
            "status": row[0],
            "result": row[1],
            "created_at": row[2].isoformat() if row[2] else None,
            "finished_at": row[3].isoformat() if row[3] else None,
        }
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        if conn:
            release_db_conn(task_id, conn)
