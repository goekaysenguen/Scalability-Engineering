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
   # Submit an image
   curl -X POST http://localhost:8000/classify \
        -H "Content-Type: application/json" \
        -d '{"image_url": "https://upload.wikimedia.org/wikipedia/commons/c/c0/Golden_Retriever_with_tennis_ball.jpg"}'
   
   # Check the status (replace with your task_id)
   curl http://localhost:8000/status/<TASK_ID>
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
6. Use Node 1 to send request
   ```
   # Submit an image
   curl -X POST http://<externalIP>/classify \
        -H "Content-Type: application/json" \
        -d '{"image_url": "https://upload.wikimedia.org/wikipedia/commons/c/c0/Golden_Retriever_with_tennis_ball.jpg"}'
   
   # Check the status (replace with your task_id)
   curl http://<externalIP>/status/<TASK_ID>
   ```

### Teardown
To avoid unnecessary GCP costs, destroy the infrastructure after testing:
```bash
terraform destroy
```

# 2. weitere TODOs (Fokus auf das Assignment!)

#### Prio 1: Der Load-Test (K6) & Metriken (Zwingend für Req 2!)
Das Assignment sagt: *"You are required to track a performance metric [...] that you expect to scale with the increasing resources (such as throughput) and present these findings on a slide. You may use a load generator for this, such as K6"*.
*   **To-Do:** Schreibt ein kleines JavaScript-Skript für **K6**. Dieses Skript soll massenhaft Requests an euren `/classify` Endpunkt schicken.
*   **Das Ziel:** Ihr müsst beweisen, dass euer System bei 3 Nodes mehr Durchsatz (Throughput) schafft als bei 1 Node.
*   *Tipp für K6:* Baut in das K6-Skript direkt ein, dass es bei einem `HTTP 503` (eurem Load Shedding) einen **Retry mit Exponential Backoff & Jitter** macht. (Das ist der perfekte Praxis-Beweis für das Paper von Marc Brooker!).

#### Prio 2: Echtes Idempotency-Handling einbauen (Req 4 / Paper-Bezug)
Aktuell generiert eure API die `task_id` selbst. Laut dem Paper *"Making retries safe with idempotent APIs"* sollte der **Client** (also euer K6-Test oder Nutzer) die ID mitschicken, z. B. als Header `Idempotency-Key: <UUID>`. 
*   **To-Do (API):** Ändert die API so, dass sie den `Idempotency-Key` des Clients ausliest. Wenn der Client denselben Key zweimal schickt (z.B. wegen eines Timeouts), darf die API das Bild **nicht** ein zweites Mal in die Queue legen, sondern muss einfach den Status des bereits existierenden Tasks zurückgeben.
*   **Warum?** Damit habt ihr die zweite Strategie für *Requirement 4* wasserdicht umgesetzt und könnt euch wunderbar auf das Paper beziehen.

#### Prio 3: Präsentation & Slides vorbereiten (Deliverables)
Ihr habt nur **5 Minuten** für die Präsentation! Das ist extrem kurz. 
*   1 Slide: Architektur-Diagramm (ähnlich eurem Obsidian Canvas, aber mit klaren Markierungen: "Hier ist Stateless", "Hier ist Stateful", "Hier ist Load Shedding").
*   1 Slide: Skalierungs-Ergebnisse (Ein Graph aus eurem K6 Load-Test: Balkendiagramm, das zeigt `Throughput bei 1 Node vs 3 Nodes vs 5 Nodes`).
*   1 Slide: Limitations (Edge Cases diskutieren, z.B. dass Node 0 ein Single Point of Failure ist).