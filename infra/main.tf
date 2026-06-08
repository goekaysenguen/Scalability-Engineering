locals {
  node_ips = [
    for i in range(var.cluster_size) : cidrhost("10.10.0.0/24", i + 10)
  ]

  api_ips = var.cluster_size == 1 ? [
    local.node_ips[0]
  ] : slice(local.node_ips, 1, var.cluster_size)

  api_servers_json = jsonencode([
    for ip in local.api_ips : {
      url = "http://${ip}:${var.api_port}"
    }
  ])
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


resource "google_compute_firewall" "allow_all" {
  # for testing purposes; should be removed later
  name    = "allow-all"
  network = google_compute_network.vpc.name

  allow {
    protocol = "all"
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
  })
}