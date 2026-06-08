#!/bin/bash

NODE_INDEX='${node_index}'
CLUSTER_SIZE='${cluster_size}'
LOADBALANCER_PORT='${loadbalancer_port}'
API_PORT='${api_port}'
API_SERVERS_JSON='${api_servers_json}'

install_docker() {
    echo "Installing docker" >> /var/log/startup-script.log
    # Add Docker's official GPG key:
    sudo apt update
    sudo apt install -y ca-certificates curl
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
    echo "Finsihed Installing docker" >> /var/log/startup-script.log
}

start_stateful() {
    echo "In start_stateful" >> /var/log/startup-script.log
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
    docker run -d -p "$LOADBALANCER_PORT":8000 -v /opt/scalability-engineering/nginx.conf:/etc/nginx/nginx.conf:ro nginx:1.31.1
}

start_stateless() {
    docker pull ghcr.io/goekaysenguen/scalability-engineering/api:latest
    docker run -d -p "$API_PORT":8000 ghcr.io/goekaysenguen/scalability-engineering/api:latest
}

install_docker

if [ "$CLUSTER_SIZE" = "1" ]; then
    echo "Starting single-node deployment"
    start_stateless
    start_stateful
elif [ "$NODE_INDEX" = "0" ]; then
    echo "Starting stateful component" >> /var/log/startup-script.log
    start_stateful
else
    echo "Starting stateless component"
    start_stateless
fi