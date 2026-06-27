import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import os

# ==========================================
# 1. KONFIGURATION
# ==========================================
# Die Dateinamen eurer 6 Tests
FILES = {
    ("1 Node", "e2-medium"): "results/raw_data_1_nodes_e2-medium.csv",
    ("1 Node", "e2-standard-4"): "results/raw_data_1_nodes_e2-standard-4.csv",
    ("3 Nodes", "e2-medium"): "results/raw_data_3_nodes_e2-medium.csv",
    ("3 Nodes", "e2-standard-4"): "results/raw_data_3_nodes_e2-standard-4.csv",
    ("5 Nodes", "e2-medium"): "results/raw_data_5_nodes_e2-medium.csv",
    ("5 Nodes", "e2-standard-4"): "results/raw_data_5_nodes_e2-standard-4.csv"
}

NODES = ["1 Node", "3 Nodes", "5 Nodes"]
MACHINE_TYPES = ["e2-medium", "e2-standard-4"]
COLORS = ["#3498db", "#2ecc71"] # Blau für Medium, Grün für Standard-4
# ==========================================

def calculate_metrics(file_path):
    if not os.path.exists(file_path):
        print(f"Warnung: Datei {file_path} fehlt.")
        return 0, 0

    df = pd.read_csv(file_path)
    df['created_at'] = pd.to_datetime(df['created_at'])
    df['finished_at'] = pd.to_datetime(df['finished_at'])
    
    completed_df = df[df['status'] == 'completed'].copy()
    if completed_df.empty:
        return 0, 0

    # Latenz berechnen
    completed_df['latency'] = (completed_df['finished_at'] - completed_df['created_at']).dt.total_seconds()
    
    # Relative Zeit für Goodput/Sekunde
    start_time = completed_df['created_at'].min()
    completed_df['sec'] = (completed_df['finished_at'] - start_time).dt.total_seconds().astype(int)
    
    goodput_per_sec = completed_df.groupby('sec').size()
    
    # Wir nehmen das 90. Perzentil des Goodputs pro Sekunde.
    # Das ignoriert Ramp-Up/Ramp-Down von K6 und liefert den echten "Max Sustained Goodput" 
    # (also das Plateau, das das System unter Volllast stabil halten konnte).
    sustained_goodput = goodput_per_sec.quantile(0.90)

    # Die p95 Latenz über den gesamten Test
    p95_latency = completed_df['latency'].quantile(0.95)
    
    return sustained_goodput, p95_latency

def main():
    goodput_data = {mt: [] for mt in MACHINE_TYPES}
    latency_data = {mt: [] for mt in MACHINE_TYPES}

    # Daten extrahieren
    for node in NODES:
        for mt in MACHINE_TYPES:
            file_path = FILES[(node, mt)]
            goodput, latency = calculate_metrics(file_path)
            goodput_data[mt].append(goodput)
            latency_data[mt].append(latency)

    # === PLOTTING ===
    x = np.arange(len(NODES))  # Label Locations (0, 1, 2)
    width = 0.35  # Breite der Balken

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10))
    fig.suptitle("Horizontal & Vertical Scaling Comparison", fontsize=18, fontweight='bold')

    # Plot 1: Sustained Goodput
    bars1 = ax1.bar(x - width/2, goodput_data["e2-medium"], width, label='e2-medium (2 vCPUs)', color=COLORS[0], edgecolor='black', alpha=0.8)
    bars2 = ax1.bar(x + width/2, goodput_data["e2-standard-4"], width, label='e2-standard-4 (4 vCPUs)', color=COLORS[1], edgecolor='black', alpha=0.8)

    ax1.set_ylabel('Sustained Goodput (Images/sec)', fontsize=12)
    ax1.set_title('Maximum Sustained Throughput Capacity', fontsize=14)
    ax1.set_xticks(x)
    ax1.set_xticklabels(NODES, fontsize=12)
    ax1.legend(loc="upper left")
    ax1.grid(axis='y', linestyle='--', alpha=0.7)

    # Werte über den Balken anzeigen
    max_heigth = 0
    for bar in bars1 + bars2:
        height = bar.get_height()
        if height > max_heigth:
            max_heigth = height
        if height > 0:
            ax1.annotate(f'{height:.1f}/s',
                        xy=(bar.get_x() + bar.get_width() / 2, height),
                        xytext=(0, 3), textcoords="offset points",
                        ha='center', va='bottom', fontsize=10, fontweight='bold')
    ax1.set_ylim(0, max_heigth+max_heigth*0.08)


    # Plot 2: p95 Latency
    bars3 = ax2.bar(x - width/2, latency_data["e2-medium"], width, label='e2-medium', color=COLORS[0], edgecolor='black', alpha=0.8)
    bars4 = ax2.bar(x + width/2, latency_data["e2-standard-4"], width, label='e2-standard-4', color=COLORS[1], edgecolor='black', alpha=0.8)

    ax2.set_ylabel('p95 Latency (seconds)', fontsize=12)
    ax2.set_title('95th Percentile End-to-End Latency under Load', fontsize=14)
    ax2.set_xticks(x)
    ax2.set_xticklabels(NODES, fontsize=12)
    ax2.grid(axis='y', linestyle='--', alpha=0.7)

    # Markierung für Wasted Work Timeout (die 15s oder 30s Grenze)
    # Passe den Wert hier an, falls ihr 15 oder 30 genutzt habt
    ax2.axhline(y=30, color='red', linestyle='--', linewidth=2, label='Queue Timeout')
    ax2.legend(loc="lower left")

    # Werte über den Balken anzeigen
    max_heigth = 0
    for bar in bars3 + bars4:
        height = bar.get_height()
        if height > max_heigth:
            max_heigth = height
        if height > 0:
            ax2.annotate(f'{height:.1f}s',
                        xy=(bar.get_x() + bar.get_width() / 2, height),
                        xytext=(0, 3), textcoords="offset points",
                        ha='center', va='bottom', fontsize=10, fontweight='bold')
    ax2.set_ylim(0, max_heigth+max_heigth*0.08)

    plt.tight_layout()
    output_file = "results/bonus_scaling_comparison.png"
    plt.savefig(output_file, dpi=300)
    print(f"✅ Bonus Plot erfolgreich gespeichert unter: {output_file}")
    plt.show()

if __name__ == "__main__":
    main()