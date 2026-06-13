#!/usr/bin/env bash
set -e

build_and_push_worker() {
  docker build -t scalability-engineering/worker worker
  docker tag scalability-engineering/worker ghcr.io/goekaysenguen/scalability-engineering/worker:latest
  docker push ghcr.io/goekaysenguen/scalability-engineering/worker:latest
}

build_and_push_api() {
  docker build -t scalability-engineering/api api
  docker tag scalability-engineering/api ghcr.io/goekaysenguen/scalability-engineering/api:latest
  docker push ghcr.io/goekaysenguen/scalability-engineering/api:latest
}

case "$1" in
  api)
    build_and_push_api
    ;;
  worker)
    build_and_push_worker
    ;;
  "")
    build_and_push_worker
    build_and_push_api
    ;;
  *)
    echo "Ungültiges Argument: $1"
    echo "Verwendung: $0 [api|worker]"
    exit 1
    ;;
esac