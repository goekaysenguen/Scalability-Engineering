variable "project_id" {
  description = "Google Cloud Console Project Id"
  type        = string
  default     = "scalability-engineering"
}

variable "cluster_size" {
  description = "Number of VMs in the deployment, choose between 1, 3, 5."
  type        = number

  validation {
    condition     = contains([1, 3, 5], var.cluster_size)
    error_message = "cluster_size must be one of: 1, 3, 5."
  }
}

variable "machine_type" {
  description = "GCP machine type for all VMs"
  type        = string
  default     = "e2-medium" # vertical-scaling test: e2-standard-2, e2-standard-4, e2-standard-8
                            # horizontal testing: e2-medium (cheaper), e2-standard-4 (bonus-task)
}


variable "loadbalancer_port" {
  description = "On what port the loadbalancer should run"
  type        = number
  default     = 80
}


variable "api_port" {
  description = "On what port the api should run"
  type        = number
  default     = 8001
}


variable "region" {
  type    = string
  default = "europe-west3"
}

variable "zone" {
  type    = string
  default = "europe-west3-a"
}