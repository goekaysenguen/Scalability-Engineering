import os

import matplotlib.pyplot as plt
import pandas as pd
from sqlalchemy import create_engine

# CONFIG
# Specify here the external IPs of your GCP Nodes (1, 3 oder 5)
NODE_IPS = ["34.179.194.89", "34.141.52.246", "34.179.214.150", "34.159.154.65", "34.159.180.20"]

MACHINE_TYPE = "e2-medium"

# Start time of the K6 test (YYYY-MM-DD HH:MM:SS) - Use Europe/Berlin timezone (local time).
# This will be converted to UTC.
START_TIME_STR = "2026-06-18 23:35:00"

DB_USER = "postgres"
DB_PASS = "postgres"
DB_NAME = "scalability"

MAX_QUEUE_AGE = 30
# ==========================================


def fetch_data_from_nodes():
    all_data = []
    start_time = pd.to_datetime(START_TIME_STR).tz_localize("Europe/Berlin").tz_convert("UTC")
    print(f"Read data from: {start_time} (UTC)")

    for ip in NODE_IPS:
        print(f"Connect to Node {ip}...")
        try:
            engine = create_engine(
                f"postgresql://{DB_USER}:{DB_PASS}@{ip}:5432/{DB_NAME}", connect_args={"connect_timeout": 5}
            )

            query = """
                SELECT id, status, created_at, finished_at
                FROM tasks
                WHERE created_at >= %(start_time)s
            """

            df = pd.read_sql_query(query, engine, params={"start_time": start_time})
            all_data.append(df)
            print(f" -> {len(df)} Loaded entry from node {ip}.")

        except Exception as e:
            print(f"Error at Node {ip}: {e}")

    if not all_data:
        print("No data found!")
        return None

    return pd.concat(all_data, ignore_index=True)


def plot_metrics(df):
    # normalise timestamp
    start_time = df["created_at"].min()

    # filter for succeeded tasks
    completed_df = df[df["status"] == "completed"].copy()

    if completed_df.empty:
        print("No finished Tasks found!")
        return

    # latency in seconds
    completed_df["latency"] = (completed_df["finished_at"] - completed_df["created_at"]).dt.total_seconds()

    # relative time in sec since test-start for x-axis
    completed_df["relative_finish_sec"] = (completed_df["finished_at"] - start_time).dt.total_seconds().astype(int)

    # Aggregate per Sec
    # 1. Goodput (Images per Ses)
    goodput = completed_df.groupby("relative_finish_sec").size()

    # 2. Latency (average latency of finished images per sec)
    latency_avg = completed_df.groupby("relative_finish_sec")["latency"].mean()
    latency_p95 = completed_df.groupby("relative_finish_sec")["latency"].quantile(0.95)

    # === PLOTTING ===
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), sharex=True)

    cluster_size = len(NODE_IPS)
    fig.suptitle(f"Scalability Test Results (Cluster Size: {cluster_size} Node(s))", fontsize=16)

    # Plot 1: Goodput
    ax1.bar(goodput.index, goodput.values, color="green", alpha=0.7, label="Processed Images / sec")
    ax1.axvline(x=30, color="red", linestyle="--")
    ax1.axvline(x=90, color="red", linestyle="--")
    ax1.axvline(x=150, color="red", linestyle="--")
    ax1.set_ylabel("Goodput (Images/s)")
    ax1.set_title("System Goodput over Time")
    ax1.legend()
    ax1.grid(True, linestyle="--", alpha=0.5)

    # Plot 2: Latency
    ax2.plot(latency_avg.index, latency_avg.values, color="blue", linewidth=2, label="Avg Latency (End-to-End)")
    ax2.plot(latency_p95.index, latency_p95.values, color="orange", linewidth=2, linestyle=":", label="p95 Latency")

    ax2.axhline(y=MAX_QUEUE_AGE, color="red", linestyle="--", label=f"Queue Timeout Threshold ({MAX_QUEUE_AGE}s)")

    ax2.axvline(x=30, color="red", linestyle="--")
    ax2.axvline(x=90, color="red", linestyle="--")
    ax2.axvline(x=150, color="red", linestyle="--")

    ax2.set_xlabel("Time since test start (seconds)")
    ax2.set_ylabel("Latency (seconds)")
    ax2.set_title("End-to-End Latency over Time")
    ax2.legend()
    ax2.grid(True, linestyle="--", alpha=0.5)

    plt.tight_layout()
    os.makedirs("results", exist_ok=True)
    plt.savefig(f"results/load_test_results_{cluster_size}_nodes_{MACHINE_TYPE}.png")
    print(f"Plot stored under 'load_test_results_{cluster_size}_nodes_{MACHINE_TYPE}.png'!")
    plt.show()


if __name__ == "__main__":
    df = fetch_data_from_nodes()
    if df is not None and not df.empty:
        # Store data as CSV for later
        cluster_size = len(NODE_IPS)
        os.makedirs("results", exist_ok=True)
        csv_filename = f"results/raw_data_{cluster_size}_nodes_{MACHINE_TYPE}.csv"
        df.to_csv(csv_filename, index=False)
        print(f"Raw data stored unter: {csv_filename}")

        plot_metrics(df)
