#!/bin/bash

NODE_INDEX='${node_index}'
CLUSTER_SIZE='${cluster_size}'
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
    mkdir -p /opt/scalability-engineering
    cd /opt/scalability-engineering
    echo "$API_SERVERS_JSON" > servers.json

    docker pull ghcr.io/goekaysenguen/scalability-engineering/loadbalancer:latest
    docker run -v /opt/scalability-engineering/servers.json:/app/servers.json:ro -d -p 80:8000 ghcr.io/goekaysenguen/scalability-engineering/loadbalancer:latest
}

start_stateless() {
    docker pull ghcr.io/goekaysenguen/scalability-engineering/api:latest
    docker run -d -p 8001:8000 ghcr.io/goekaysenguen/scalability-engineering/api:latest
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