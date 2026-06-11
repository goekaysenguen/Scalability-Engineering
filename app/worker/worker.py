import os
import json
import time
import redis
import psycopg2
import requests
from io import BytesIO
from PIL import Image
import numpy as np

# TensorFlow & MobileNetV3
from tensorflow.keras.applications import MobileNetV3Small
from tensorflow.keras.applications.mobilenet_v3 import preprocess_input, decode_predictions
from tensorflow.keras.preprocessing import image as keras_image

# --- KONFIGURATION ---
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_QUEUE_NAME = "image_tasks"

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "scalability")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "postgres")

# Wie lange darf ein Task maximal in der Queue liegen? (in Sekunden)
MAX_QUEUE_AGE_SECONDS = 15


# --- INITIALISIERUNG (Einmalig beim Start) ---
print("Lade MobileNetV3 Modell in den Speicher... Das passiert nur einmal!")
# WICHTIG: Modell außerhalb der Schleife laden, um Memory Leaks und Latenz zu vermeiden (Constant Work)
model = MobileNetV3Small(weights='imagenet')
print("Modell erfolgreich geladen.")

def get_db_connection():
    """Baut eine Verbindung zur Datenbank auf."""
    return psycopg2.connect(
        host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS,
        connect_timeout=5 # Bounded Work: Timeout setzen!
    )

def download_image(url):
    """Lädt das Bild herunter. In Produktion wäre das z.B. Google Cloud Storage."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    # Bounded Work: Setze strikte Timeouts (Connect=3s, Read=10s)
    # Verhindert, dass der Worker ewig hängt und die Queue überläuft ("insurmountable queue backlogs")
    response = requests.get(url, headers=headers, timeout=(3, 10))
    response.raise_for_status()
    return Image.open(BytesIO(response.content))

def predict_image(img):
    """Führt die Klassifizierung mit MobileNetV3 durch."""
    # Bildgröße für MobileNetV3 anpassen (224x224)
    img = img.resize((224, 224))
    x = keras_image.img_to_array(img)
    x = np.expand_dims(x, axis=0)
    x = preprocess_input(x)

    preds = model.predict(x)
    results = decode_predictions(preds, top=1)[0]
    # results format: [(class_id, class_name, probability)]
    best_match = results[0][1] 
    return best_match

def process_task(task):
    task_id = task.get("task_id")
    image_url = task.get("image_url")
    enqueued_at = task.get("enqueued_at", 0)

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # 1. MESSAGE AGE CHECK (Dropping old messages)
        # Ref: Amazon Builders' Library - "Avoiding insurmountable queue backlogs"
        time_in_queue = time.time() - enqueued_at
        if time_in_queue > MAX_QUEUE_AGE_SECONDS:
            print(f"Task {task_id} ist zu alt ({time_in_queue:.1f}s > {MAX_QUEUE_AGE_SECONDS}s). Verwerfe Task (Wasted Work Prevention)!")
            cursor.execute("UPDATE tasks SET status = 'failed', result = 'timeout in queue' WHERE id = %s", (task_id,))
            conn.commit()
            return # Bricht ab, KI wird nicht ausgeführt!

        # 2. IDEMPOTENCY CHECK

        # Prüfen, ob der Task schon bearbeitet wurde (verhindert doppelte Arbeit bei Retries)
        cursor.execute("SELECT status FROM tasks WHERE id = %s", (task_id,))
        row = cursor.fetchone()
        if not row:
            print(f"Task {task_id} nicht in DB gefunden. Überspringe.")
            return
        
        status = row[0]
        if status in ['completed', 'failed']:
            print(f"Idempotency greift: Task {task_id} ist bereits {status}. Überspringe.")
            return

        # Status auf 'processing' setzen
        cursor.execute("UPDATE tasks SET status = 'processing' WHERE id = %s", (task_id,))
        conn.commit()

        # 3. BILD LADEN & VERARBEITEN
        print(f"Bearbeite Task {task_id} (Zeit in Queue: {time_in_queue:.1f}s)...")
        img = download_image(image_url)
        
        print("Starte Inferenz...")
        start_time = time.time()
        result_string = predict_image(img)
        print(f"Inferenz beendet in {time.time() - start_time:.2f}s. Ergebnis: {result_string}")

        # 4. ERGEBNIS SPEICHERN
        cursor.execute(
            "UPDATE tasks SET status = 'completed', result = %s WHERE id = %s",
            (result_string, task_id)
        )
        conn.commit()
        print(f"Task {task_id} erfolgreich abgeschlossen.")

    except Exception as e:
        print(f"Fehler bei Task {task_id}: {e}")
        # Bei Fehler Status auf 'failed' setzen, damit die API weiß, was los ist
        cursor.execute("UPDATE tasks SET status = 'failed', result = %s WHERE id = %s", (str(e), task_id))
        conn.commit()
    finally:
        cursor.close()
        conn.close()

def main():
    # Timeout für Redis Connection
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True, socket_connect_timeout=5)
    
    print("AI Worker ist gestartet und wartet auf Aufgaben...")
    while True:
        try:
            # Blockierendes Pop aus der Liste. Wartet maximal 5 Sekunden auf neue Elemente.
            # brpop gibt ein Tuple zurück: (queue_name, data)
            item = r.brpop(REDIS_QUEUE_NAME, timeout=5)
            
            if item:
                _, data = item
                task = json.loads(data)
                process_task(task)
                
        except redis.ConnectionError:
            print("Verbindung zu Redis verloren. Versuche es in 5 Sekunden erneut...")
            time.sleep(5)
        except Exception as e:
            print(f"Unerwarteter Fehler in der Main-Loop: {e}")
            time.sleep(1)

if __name__ == "__main__":
    main()