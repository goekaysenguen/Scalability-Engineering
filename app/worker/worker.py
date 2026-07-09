import contextlib
import hashlib
import json
import os
import time
import uuid
from io import BytesIO

import numpy as np
import psycopg2
import redis
import requests
from PIL import Image

# TensorFlow & MobileNetV3
from tensorflow.keras.applications import MobileNetV3Small
from tensorflow.keras.applications.mobilenet_v3 import decode_predictions, preprocess_input
from tensorflow.keras.preprocessing import image as keras_image

# CONFIG
REDIS_HOST = os.getenv("REDIS_HOST", None)
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_QUEUE_NAME = os.getenv("REDIS_QUEUE_NAME", "image_tasks")

DB_HOSTS = os.getenv("DB_HOSTS", None).split(",")
NUMBER_OF_DBS = len(DB_HOSTS)
DB_NAME = os.getenv("DB_NAME", None)
DB_USER = os.getenv("DB_USER", None)
DB_PASS = os.getenv("DB_PASSWORD", None)

MAX_QUEUE_AGE_SECONDS = int(os.getenv("MAX_QUEUE_AGE_SECONDS", 15))


# initialisation
print("Load MobileNetV3 to RAM... only happens once!")
model = MobileNetV3Small(weights="imagenet")
print("Model loaded successfully.")


def get_db_index(task_id) -> int:
    """
    Calculates db index based on the id's hash.

    Args:
        task_id: The task ID used to determine the target database.

    Returns:
        The db index.
    """
    if isinstance(task_id, str):
        task_id = uuid.UUID(task_id)
    digest = hashlib.sha256(task_id.bytes).digest()
    return int.from_bytes(digest, "big") % NUMBER_OF_DBS


def get_db_connections():
    """
    Initializes db connection for all hosts in DB_HOSTS.

    Returns:
        List of db connections.
    """
    connections = []
    try:
        for db_host in DB_HOSTS:
            conn = psycopg2.connect(host=db_host, database=DB_NAME, user=DB_USER, password=DB_PASS, connect_timeout=5)
            connections.append(conn)
        # Only if all connections succeeded, we return the list
        return connections
    except Exception as e:
        # If a connection does not succeed: Close all other connections
        for c in connections:
            with contextlib.suppress(Exception):
                c.close()
        # propagate the error
        raise e


def download_image(url):
    """
    Downloads image from url.

    Args:
        url: url to image

    Returns:
        Image object
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    # Bounded Work: use strict Timeouts (Connect=3s, Read=10s)
    # Prevents the worker from hanging indefinitely and avoids queue overflow
    response = requests.get(url, headers=headers, timeout=(3, 10))
    response.raise_for_status()
    return Image.open(BytesIO(response.content))


def predict_image(img):
    """
    Runs prediction on the impage using MobileNetV3.

    - Resizes the image object to (224x224)
    - Runs MobileNetV3 prediction

    Args:
        img: Image object

    Returns:
        The best match
    """
    # Resize imagesize for MobileNetV3 (224x224)
    img = img.resize((224, 224))
    x = keras_image.img_to_array(img)
    x = np.expand_dims(x, axis=0)
    x = preprocess_input(x)

    preds = model.predict(x)
    results = decode_predictions(preds, top=1)[0]
    # results format: [(class_id, class_name, probability)]
    best_match = results[0][1]
    return best_match


def process_task(task, conns):
    """
    Processes tasks from queue and writes result to db.

    - Resizes the image object to (224x224)
    - Runs MobileNetV3 prediction

    Args:
        task: Task from redis queue
        conns: List of db connections
    """
    task_id = task.get("task_id")
    image_url = task.get("image_url")
    enqueued_at = task.get("enqueued_at", 0)

    db_index = get_db_index(task_id)
    conn = conns[db_index]

    cursor = None
    try:
        cursor = conn.cursor()

        # 1. MESSAGE AGE CHECK (Dropping old messages)
        time_in_queue = time.time() - enqueued_at
        if time_in_queue > MAX_QUEUE_AGE_SECONDS:
            print(
                f"Task {task_id} too old ({time_in_queue:.1f}s > {MAX_QUEUE_AGE_SECONDS}s). Discard task (Wasted Work Prevention)!"
            )
            cursor.execute(
                "UPDATE tasks SET status = 'failed', result = 'timeout in queue', finished_at = NOW() WHERE id = %s",
                (task_id,),
            )
            conn.commit()
            return

        # 2. IDEMPOTENCY CHECK
        # Check, if task is already completed (Prevents duplicate work)
        cursor.execute("SELECT status FROM tasks WHERE id = %s", (task_id,))
        row = cursor.fetchone()
        if not row:
            print(f"Task {task_id} not found in DB. Skip.")
            conn.rollback()
            return

        status = row[0]
        if status in ["completed", "failed"]:
            print(f"Idempotency: Task {task_id} already {status}. Skip.")
            conn.rollback()
            return

        # set status to 'processing'
        cursor.execute("UPDATE tasks SET status = 'processing' WHERE id = %s", (task_id,))
        conn.commit()

        # 3. LOAD IMAGE & PROCESS
        print(f"Task {task_id} now in progress (Time in Queue: {time_in_queue:.1f}s)...")
        img = download_image(image_url)

        print("Start Inferenz...")
        start_time = time.time()
        result_string = predict_image(img)
        print(f"Inferenz finished in {time.time() - start_time:.2f}s. Result: {result_string}")

        # 4. STORE RESULT
        cursor.execute(
            "UPDATE tasks SET status = 'completed', result = %s, finished_at = NOW() WHERE id = %s",
            (result_string, task_id),
        )
        conn.commit()
        print(f"Task {task_id} finished.")

    except psycopg2.InterfaceError:
        # Error with DB connection.
        # Propagate the error to the main loop and perform self-healing
        raise
    except Exception as e:
        print(f"Error at Task {task_id}: {e}")
        # Rollback, if transaction breakes before commit
        conn.rollback()

        # Set status to failed
        try:
            if cursor is None:
                cursor = conn.cursor()
            cursor.execute(
                "UPDATE tasks SET status = 'failed', result = %s, finished_at = NOW() WHERE id = %s", (str(e), task_id)
            )
            conn.commit()
        except Exception as inner_e:
            print(f"Could not store Error in DB: {inner_e}")
            conn.rollback()
    finally:
        if cursor is not None:
            cursor.close()


def main():
    """
    Main function containing run loop.

    - Initializes connection to redis queue
    - Initializes connection to dbs
    - Pops task from redis queue and processes them
    """
    # Timeout for Redis Connection
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True, socket_connect_timeout=5)

    print("AI Worker started and is waiting for tasks...")

    # start Initial DB-connection (Persistent Connection)
    db_conns = None
    while db_conns is None:
        try:
            db_conns = get_db_connections()
            print("Successfully connected to DB!")
        except Exception as e:
            print(f"Waiting for DB... {e}")
            time.sleep(2)

    while True:
        try:
            # Blocking pop. Waits a maximum of 5 seconds for new elements.
            # brpop returns a tuple: (queue_name, data)
            item = r.brpop(REDIS_QUEUE_NAME, timeout=5)

            if item:
                _, data = item
                task = json.loads(data)

                process_task(task, db_conns)

        except redis.ConnectionError:
            print("Lost connection to Redis. Retry in 5s...")
            time.sleep(5)
        except (psycopg2.InterfaceError, psycopg2.OperationalError):
            # IF DB-Connection breaks (e.g. Postgres-Container restarted)
            print("DB-connection lost! Retry...")
            try:
                for conn in db_conns:
                    conn.close()
            except Exception as e:
                print(f"{e}. Could not close DB connection... ignoring...")

            # Reconnect-Loop
            db_conns = None
            while db_conns is None:
                try:
                    db_conns = get_db_connections()
                    print("Reconnect to DB successfull!")
                except Exception as e:
                    print(f"Waiting for DB... {e}")
                    time.sleep(2)
        except Exception as e:
            print(f"Unkown Error in main-loop: {e}")
            time.sleep(1)


if __name__ == "__main__":
    main()
