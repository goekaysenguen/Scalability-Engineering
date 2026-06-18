import psycopg2
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime
import pytz
from sqlalchemy import create_engine

# ==========================================
# 1. KONFIGURATION
# ==========================================
# Trage hier die externen IPs deiner GCP Nodes ein (1, 3 oder 5)
NODE_IPS = ["35.198.155.171", "34.159.57.49", "34.40.68.54"]

# Startzeitpunkt des K6 Tests (YYYY-MM-DD HH:MM:SS) - Nutze TimeZone Europe/Berlin, also lokale uhrzeit. wird dann in UTC umgerechnet
# Achte darauf, dass du etwas Puffer nach hinten lässt (z.B. 10 Sek vor K6 Start)
START_TIME_STR = "2026-06-18 23:35:00"

DB_USER = "postgres"
DB_PASS = "postgres"
DB_NAME = "scalability"

MAX_QUEUE_AGE=30
# ==========================================

def fetch_data_from_nodes():
    all_data = []
    start_time = pd.to_datetime(START_TIME_STR).tz_localize('Europe/Berlin').tz_convert('UTC')
    print(f"Lese Daten ab: {start_time} (UTC)")

    for ip in NODE_IPS:
        print(f"Verbinde zu Node {ip}...")
        try:
            # NEU: SQLAlchemy Engine nutzen
            engine = create_engine(f'postgresql://{DB_USER}:{DB_PASS}@{ip}:5432/{DB_NAME}', connect_args={'connect_timeout': 5})
            
            query = """
                SELECT id, status, created_at, finished_at 
                FROM tasks 
                WHERE created_at >= %(start_time)s
            """
            
            df = pd.read_sql_query(query, engine, params={"start_time": start_time})
            all_data.append(df)
            print(f" -> {len(df)} Einträge von Node {ip} geladen.")
            
        except Exception as e:
            print(f"Fehler bei Node {ip}: {e}")

    if not all_data:
        print("Keine Daten gefunden!")
        return None
        
    return pd.concat(all_data, ignore_index=True)

def plot_metrics(df):
    # Zeitstempel normalisieren
    start_time = df['created_at'].min()
    
    # Filtere nur die erfolgreich abgeschlossenen Tasks für Latenz & Goodput
    completed_df = df[df['status'] == 'completed'].copy()
    
    if completed_df.empty:
        print("Keine abgeschlossenen Tasks gefunden!")
        return

    # Latenz in Sekunden berechnen
    completed_df['latency'] = (completed_df['finished_at'] - completed_df['created_at']).dt.total_seconds()
    
    # Relative Zeit in Sekunden seit Testbeginn für die X-Achse
    completed_df['relative_finish_sec'] = (completed_df['finished_at'] - start_time).dt.total_seconds().astype(int)

    # Aggregieren pro Sekunde
    # 1. Goodput (Bilder pro Sekunde)
    goodput = completed_df.groupby('relative_finish_sec').size()
    
    # 2. Latenz (Durchschnittliche Latenz der fertiggestellten Bilder pro Sekunde)
    latency_avg = completed_df.groupby('relative_finish_sec')['latency'].mean()
    latency_p95 = completed_df.groupby('relative_finish_sec')['latency'].quantile(0.95)

    # === PLOTTING ===
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), sharex=True)
    
    cluster_size = len(NODE_IPS)
    fig.suptitle(f"Scalability Test Results (Cluster Size: {cluster_size} Node(s))", fontsize=16)

    # Plot 1: Goodput
    ax1.bar(goodput.index, goodput.values, color='green', alpha=0.7, label='Processed Images / sec')
    #ax1.axhline(y=cluster_size, color='r', linestyle='--', label=f'Expected Max Capacity (~{cluster_size} img/s)')
    ax1.set_ylabel("Goodput (Images/s)")
    ax1.set_title("System Goodput over Time")
    ax1.legend()
    ax1.grid(True, linestyle='--', alpha=0.5)

    # Plot 2: Latency
    ax2.plot(latency_avg.index, latency_avg.values, color='blue', linewidth=2, label='Avg Latency (End-to-End)')
    ax2.plot(latency_p95.index, latency_p95.values, color='orange', linewidth=2, linestyle=':', label='p95 Latency')
    
    ax2.axhline(y=MAX_QUEUE_AGE, color='red', linestyle='--', label=f'Queue Timeout Threshold ({MAX_QUEUE_AGE}s)')
    
    ax2.set_xlabel("Time since test start (seconds)")
    ax2.set_ylabel("Latency (seconds)")
    ax2.set_title("End-to-End Latency over Time")
    ax2.legend()
    ax2.grid(True, linestyle='--', alpha=0.5)

    plt.tight_layout()
    plt.savefig(f"load_test_results_{cluster_size}_nodes.png")
    print(f"Plot wurde als 'load_test_results_{cluster_size}_nodes.png' gespeichert!")
    plt.show()

if __name__ == "__main__":
    df = fetch_data_from_nodes()
    if df is not None and not df.empty:
        plot_metrics(df)