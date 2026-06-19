import pandas as pd
import matplotlib.pyplot as plt
import os

# ==========================================
# 1. KONFIGURATION
# ==========================================
# Welche Dateien sollen verglichen werden? 

HORIZONTAL_OR_VERTICAL = "Horizontal" # "Horizontal" or "Vertical"

if HORIZONTAL_OR_VERTICAL == "Horizontal":
    FILES_TO_COMPARE = {
        "1 Node": "results/raw_data_1_nodes_e2-medium.csv",
        "3 Nodes": "results/raw_data_3_nodes_e2-medium.csv",
        "5 Nodes": "results/raw_data_5_nodes_e2-medium.csv"
    }

    COLORS = {
        "1 Node": "#e74c3c",   # Rot
        "3 Nodes": "#3498db",  # Blau
        "5 Nodes": "#2ecc71"   # Grün
    }
else:
    FILES_TO_COMPARE = {
        "e2-standard-2": "results/raw_data_1_nodes_e2-standard-2.csv",
        "e2-standard-4": "results/raw_data_1_nodes_e2-standard-4.csv",
        "e2-standard-8": "results/raw_data_1_nodes_e2-standard-8.csv"
    }

    COLORS = {
        "e2-standard-2": "#e74c3c",   # Rot
        "e2-standard-4": "#3498db",  # Blau
        "e2-standard-8": "#2ecc71"   # Grün
    }

# Timeout Limit, das wir im Worker eingestellt haben (für die rote Hilfslinie)
MAX_QUEUE_AGE_SECONDS = 30 
# ==========================================

def process_csv(file_path):
    df = pd.read_csv(file_path)
    
    # Strings zurück in Datetime-Objekte umwandeln
    df['created_at'] = pd.to_datetime(df['created_at'])
    df['finished_at'] = pd.to_datetime(df['finished_at'])
    
    start_time = df['created_at'].min()
    
    # Nur erfolgreiche Tasks betrachten
    completed_df = df[df['status'] == 'completed'].copy()
    if completed_df.empty:
        return None, None, None

    completed_df['latency'] = (completed_df['finished_at'] - completed_df['created_at']).dt.total_seconds()
    completed_df['relative_finish_sec'] = (completed_df['finished_at'] - start_time).dt.total_seconds().astype(int)

    # Aggregieren
    goodput = completed_df.groupby('relative_finish_sec').size()
    latency_avg = completed_df.groupby('relative_finish_sec')['latency'].mean()
    latency_p95 = completed_df.groupby('relative_finish_sec')['latency'].quantile(0.95)

    return goodput, latency_avg, latency_p95

def main():
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), sharex=True)
    fig.suptitle(f"{HORIZONTAL_OR_VERTICAL} Scaling Comparison", fontsize=18, fontweight='bold')

    for label, file_path in FILES_TO_COMPARE.items():
        if not os.path.exists(file_path):
            print(f"WARNUNG: Datei {file_path} nicht gefunden. Überspringe {label}...")
            continue
            
        goodput, latency_avg, latency_p95 = process_csv(file_path)
        if goodput is None:
            continue
            
        color = COLORS[label]

        # Plot 1: Goodput (als Linie, damit es bei 3 Vergleichen lesbar bleibt)
        # rolling(3).mean() glättet die Linie leicht (Moving Average über 3 Sekunden)
        smoothed_goodput = goodput.reindex(range(goodput.index.max() + 1), fill_value=0).rolling(window=3, min_periods=1).mean()
        
        ax1.plot(smoothed_goodput.index, smoothed_goodput.values, color=color, linewidth=2.5, label=f'{label} (Goodput)')
        
        # Plot 2: Latency
        ax2.plot(latency_avg.index, latency_avg.values, color=color, linewidth=2.5, label=f'{label} (Avg)')
        # P95 Latenz gestrichelt hinzufügen
        ax2.plot(latency_p95.index, latency_p95.values, color=color, linestyle=':', alpha=0.6, label=f'{label} (p95)')

    # Styling Goodput Plot
    ax1.axvline(x=30, color='red', linestyle='--' )
    ax1.axvline(x=90, color='red', linestyle='--' )
    ax1.axvline(x=150, color='red', linestyle='--')
    ax1.set_ylabel("Goodput (Images/s)", fontsize=12)
    ax1.set_title("System Goodput over Time (3s Moving Avg)", fontsize=14)
    ax1.legend(loc="upper left")
    ax1.grid(True, linestyle='--', alpha=0.5)

    # Styling Latency Plot
    ax2.axhline(y=MAX_QUEUE_AGE_SECONDS, color='black', linestyle='--', alpha=0.8, label=f'max_queue_age ({MAX_QUEUE_AGE_SECONDS}s)')
    ax2.set_xlabel("Time since test start (seconds)", fontsize=12)
    ax2.axvline(x=30, color='red', linestyle='--' )
    ax2.axvline(x=90, color='red', linestyle='--' )
    ax2.axvline(x=150, color='red', linestyle='--')
    ax2.set_ylabel("Latency (seconds)", fontsize=12)
    ax2.set_title("End-to-End Latency over Time", fontsize=14)
    # Legende außerhalb oder klein halten
    ax2.legend(loc="upper left", ncol=2, fontsize=10)
    ax2.grid(True, linestyle='--', alpha=0.5)

    plt.tight_layout()
    os.makedirs("results", exist_ok=True)
    output_file = f"results/comparison_plot_{HORIZONTAL_OR_VERTICAL}.png"
    plt.savefig(output_file, dpi=300)
    print(f"✅ Vergleichs-Plot erfolgreich gespeichert unter: {output_file}")
    plt.show()

if __name__ == "__main__":
    main()