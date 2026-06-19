#!/bin/bash

NODE_INDEX='${node_index}'
CLUSTER_SIZE='${cluster_size}'
LOADBALANCER_PORT='${loadbalancer_port}'
API_PORT='${api_port}'
API_SERVERS_JSON='${api_servers_json}'
DB_HOSTS='${db_hosts}'
REDIS_IP='${redis_ip}'

install_docker() {
    echo "Installing docker" >> /var/log/startup-script.log
    # Add Docker's official GPG key:
    sudo apt update
    sudo apt install -y ca-certificates curl htop
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc

    # Add the repository to Apt sources:
    sudo tee /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/debian
Suites: $(. /etc/os-release && echo "$VERSION_CODENAME")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

    sudo apt update

    sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    echo "Finished Installing docker" >> /var/log/startup-script.log
}

start_postgres() {
    echo "Starting postgres" >> /var/log/startup-script.log
    # init-script for postgres to create table on first start
    mkdir -p /opt/scalability-engineering/db-init
    cat > /opt/scalability-engineering/db-init/init.sql <<'EOF'
CREATE TABLE IF NOT EXISTS tasks (
    id VARCHAR(50) PRIMARY KEY,
    status VARCHAR(20),
    image_url TEXT,
    result TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMPTZ
);
EOF

    # Postgres 
    docker run -d --name postgres -p 5432:5432 \
      --restart always \
      -e POSTGRES_DB=scalability \
      -e POSTGRES_USER=postgres \
      -e POSTGRES_PASSWORD=postgres \
      -v /opt/scalability-engineering/db-init:/docker-entrypoint-initdb.d:ro \
      postgres:15-alpine
}

start_loadbalancer_and_redis() {
    echo "Starting stateful components (Loadbalancer & Redis)" >> /var/log/startup-script.log

    # Redis
    docker run -d --name redis -p 6379:6379 --restart always redis:7-alpine

    # Nginx
    sudo apt install -y jq
    mkdir -p /opt/scalability-engineering

    cat > /opt/scalability-engineering/nginx.conf <<EOF
events {}

http {
    upstream backend_servers {
EOF

    echo "$API_SERVERS_JSON" \
      | jq -r '.[].url' \
      | sed -E 's#^https?://##' \
      | while read server; do
            echo "        server $server;" >> /opt/scalability-engineering/nginx.conf
        done

    cat >> /opt/scalability-engineering/nginx.conf <<'EOF'
    }

    server {
        listen 8000;

        location / {
            proxy_pass http://backend_servers;

            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }
    }
}
EOF

    docker pull nginx:1.31.1
    docker run -d --name loadbalancer -p "$LOADBALANCER_PORT":8000 \
      --restart always \
      -v /opt/scalability-engineering/nginx.conf:/etc/nginx/nginx.conf:ro \
      nginx:1.31.1
}

start_stateless() {
    echo "Starting stateless components (API & Worker)" >> /var/log/startup-script.log
    

    ## vertical scaling based on number of cores
    CORES=$(nproc)

    # leave one core for DB/API/Nginx if possible to prevent
    AI_WORKERS=$(( CORES > 1 ? CORES - 1 : 1 ))

    # TODO: 1/3 ist erstmal nur eine schätzung
    DB_MAX_CONN=$(( MAX_API_CAPACITY / 3 ))

    # use Little's law for Queue-Size
    NUM_WORKER=$(( CLUSTER_SIZE > 1 ? CLUSTER_SIZE - 1 : 1 ))
    THROUGHPUT=$(( CORES * 7 )) # TODO: erstmal eine Annahme, dass ein core 3 Bilder pro Sekunde schafft
    MAX_QUEUE_AGE=30
    MAX_GLOBAL_QUEUE_SIZE=$(( THROUGHPUT * MAX_QUEUE_AGE * NUM_WORKER))


    # TODO: einige ENV-variablen in variables.tf schreiben damit wir nicht zweimal angeben müssen?
    # API
    docker pull ghcr.io/goekaysenguen/scalability-engineering/api:latest
    docker run -d --name api -p "$API_PORT":8000 \
      --restart always \
      -e DB_HOSTS="$DB_HOSTS" \
      -e DB_USER="postgres" \
      -e DB_PASSWORD="postgres" \
      -e DB_NAME="scalability" \
      -e REDIS_HOST="$REDIS_IP" \
      -e REDIS_PORT="6379" \
      -e REDIS_QUEUE_NAME="image_tasks" \
      -e MAX_API_CAPACITY="$MAX_API_CAPACITY" \
      -e DB_MAX_CONN="$DB_MAX_CONN" \
      -e MAX_GLOBAL_QUEUE_SIZE="$MAX_GLOBAL_QUEUE_SIZE" \
      ghcr.io/goekaysenguen/scalability-engineering/api:latest

    # Worker
    docker pull ghcr.io/goekaysenguen/scalability-engineering/worker:latest
    for i in $(seq 1 $AI_WORKERS); do
        echo "Starting Worker $i..."
        docker run -d --name worker-$i \
      --restart always \
      -e DB_HOSTS="$DB_HOSTS" \
      -e DB_USER="postgres" \
      -e DB_PASSWORD="postgres" \
      -e DB_NAME="scalability" \
      -e REDIS_HOST="$REDIS_IP" \
      -e REDIS_PORT="6379" \
      -e REDIS_QUEUE_NAME="image_tasks" \
      -e MAX_QUEUE_AGE_SECONDS="$MAX_QUEUE_AGE" \
      ghcr.io/goekaysenguen/scalability-engineering/worker:latest
    done
}

install_docker

echo "Node $NODE_INDEX: Starting Postgres on EVERY node"
start_postgres

if [ "$CLUSTER_SIZE" = "1" ]; then
    echo "Starting single-node deployment"
    start_loadbalancer_and_redis
    start_stateless
elif [ "$NODE_INDEX" = "0" ]; then
    echo "Node 0 (Multi-Node): Starting Loadbalancer & Redis ONLY (No API/Worker)"
    start_loadbalancer_and_redis
else
    echo "Node $NODE_INDEX (Multi-Node): Starting Stateless (API & Worker)"
    start_stateless
fi