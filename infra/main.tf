locals {
  node_ips = [
    for i in range(var.cluster_size) : cidrhost("10.10.0.0/24", i + 10)
  ]

  # Bei Cluster-Size = 1: API läuft auf Node 0
  # Bei Cluster-Size > 1: APIs laufen NUR auf Node 1 bis N (Node 0 bleibt Loadbalancer/Redis vorbehalten)
  api_ips = var.cluster_size == 1 ? [
    local.node_ips[0]
  ] : slice(local.node_ips, 1, var.cluster_size)

  api_servers_json = jsonencode([
    for ip in local.api_ips : {
      url = "http://${ip}:${var.api_port}"
    }
  ])

  redis_ip = local.node_ips[0]

  # Die Datenbank skaliert immer über ALLE verfügbaren Nodes (inkl. Node 0)
  db_ips = local.node_ips
  db_hosts = join(",", local.db_ips)
}


provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}


resource "google_compute_network" "vpc" {
  name                    = "vpc"
  auto_create_subnetworks = false
}


resource "google_compute_subnetwork" "vpc_subnet" {
  name          = "vpc-subnet"
  ip_cidr_range = "10.10.0.0/24"
  region        = var.region
  network       = google_compute_network.vpc.id
}


resource "google_compute_firewall" "allow_internal" {
  name    = "allow-internal"
  network = google_compute_network.vpc.name

  allow {
    protocol = "all"
  }

  source_ranges = ["10.10.0.0/24"]
}


# allow http for loadbalancer (port can be changed, but is likely port 80)
resource "google_compute_firewall" "allow_external_http" {
  name    = "allow-external-http"
  network = google_compute_network.vpc.name

  allow {
    protocol = "tcp"
    ports    = [var.loadbalancer_port] 
  }

  source_ranges = ["0.0.0.0/0"]
}

# Allow SSH
resource "google_compute_firewall" "allow_ssh" {
  name    = "allow-ssh"
  network = google_compute_network.vpc.name

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = ["0.0.0.0/0"]
}


resource "google_compute_instance" "vm" {
  count        = var.cluster_size
  name         = "node-${count.index + 1}"
  machine_type = var.machine_type

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 20
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.vpc_subnet.id
    network_ip = local.node_ips[count.index]

    access_config {
      # Gives the VM an external IP
    }
  }

  metadata_startup_script = templatefile("${path.module}/startup.sh.tpl", {
    node_index   = count.index
    cluster_size = var.cluster_size
    loadbalancer_port = var.loadbalancer_port
    api_port     = var.api_port
    api_servers_json = local.api_servers_json
    db_hosts = local.db_hosts
    redis_ip = local.redis_ip
  })
}