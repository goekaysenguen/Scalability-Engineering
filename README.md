# Scalability Engineering: AI Image Classification System

This project is part of the Scalability Engineering assignment. It implements a highly scalable, event-driven AI image classification system. 

The architecture strictly separates stateless components (FastAPI, AI-Worker using MobileNetV3) from stateful components (PostgreSQL, Redis). It includes scalability patterns such as **Load Shedding** (to avoid overload), **Bounded Work/Timeouts**, and **Idempotency**.

## 🏗️ Local Development (Docker Compose)

For local testing, the entire stack (Load Balancer, API, Worker, Database, Queue) can be started using Docker Compose.

1. Navigate to the `app/` directory:
   ```bash
   cd app
   ```
2. Start the stack (uses loadbalancer with two API-services):
   ```bash
   docker compose up --build -d
   ```
3. Test the API:
   ```bash
   # generate Client TaskID for this Image (Idempotency)
   MY_TASK_ID=$(uuidgen) 

   # Submit an image
   curl -X POST http://localhost:8001/classify \
        -H "Content-Type: application/json" \
        -d "{\"image_url\": \"https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcS2FUZMIkoOnHgembMF9InnnlEXenXekksJrA&s\", \"task_id\": \"$MY_TASK_ID\"}"

   # Check the status (replace with your task_id)
   curl http://localhost:8001/status/$MY_TASK_ID
   ```
4. Stop the stack:
   ```bash
   docker compose down
   ```

## ☁️ Cloud Deployment (GCP via Terraform)

The infrastructure is deployed to Google Cloud Platform (GCP) using Terraform. The deployment supports different cluster sizes (1, 3, or 5 nodes) as required by the assignment.

### Prerequisites
* [Google Cloud CLI (`gcloud`)](https://cloud.google.com/sdk/docs/install) installed and authenticated.
* A GCP Project with the **Compute Engine API** enabled.
* [Terraform](https://developer.hashicorp.com/terraform/downloads) installed.
* Docker images must be built and pushed to a public registry (e.g., `ghcr.io`).

### Deployment Steps

1. Authenticate with Google Cloud:
   ```bash
   gcloud auth application-default login
   ```
2. Navigate to the `infra/` directory:
   ```bash
   cd infra
   ```
3. Initialize Terraform:
   ```bash
   terraform init
   ```
4. Plan and Apply the deployment (choose your configuration):
   ```bash
   terraform plan  # see what will be changed
   terraform apply # apply the changes
   ```
5. Once applied, find out the external IP of  `Node 1` 
   ```bash
   gcloud compute instances list
   ```
6. Use Node 1 to send request from you local PC
   ```bash
   # generate Client TaskID for this Image (Idempotency)
   MY_TASK_ID=$(uuidgen) 

   # Submit an image
   curl -X POST http://34.141.116.140/classify \
        -H "Content-Type: application/json" \
        -d "{\"image_url\": \"https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcS2FUZMIkoOnHgembMF9InnnlEXenXekksJrA&s\", \"task_id\": \"$MY_TASK_ID\"}"

   # Check the status (replace with your task_id)
   curl http://<externalIP>/status/$MY_TASK_ID
   ```

### Teardown
To avoid unnecessary GCP costs, destroy the infrastructure after testing:
```bash
terraform destroy
```

### Run K6 load-test

```sh
docker run --rm -i \
  -e BASE_URL=http://<external-node1-IP> \
  -v $(pwd):/app -w /app \
  grafana/k6 run k6_load_test.js
```



# TODO: nächsten ToDos

- [x] TODO: 1. zweite Strategie aus paper / VL implementieren
   - wir haben schon: Client bursting (als Erweiterung zur MAX_API_CAPACITY für Load Shedding)
   - Jitter bei API startup, um DB nicht zu überlasten (aber nicht sicher ob das ausreichend ist)
   - **NEU**: Idempotency (haben wir schon fast, aber die task_id müsste vom Client generiert werden... siehe Paper Making retries safe with idempotent APIs)
- [x] TODO: 2. Bonus Points: Evaluate the impact that using more performant machines has on your application for the previous configurations a, b and c, and also display these results. also einfach nochmal den Test mit e2-standard-4 als machine-type und vielleicht noch einen anderen Plot um die beiden horizontal-scaling besser zu vergleichen.
- [x] TODO: (Am Ende) alles auf englisch und sauberer Code + nochmal testen
- [ ] TODO: sauberes README schreiben. Siehe assignment Abschnitt **Deliverables** für Anforderungen. - README TUTORIAL NOCHMAL TESTEN
- [ ] TODO: Slides / Presentation entwerfen (siehe assignment für Anforderungen)

---

## Author

Authors: Gökay Sengün, Lorenz Pusch

## Acknowledgments

Parts of this project were developed with the assistance of LLMs.
