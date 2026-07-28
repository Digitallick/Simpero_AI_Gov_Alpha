variable "environment" {
  description = "Deployment target. Suffixes every DO resource name so staging/production don't collide account-wide. Supplied via TF_VAR_environment."
  type        = string
  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "environment must be \"staging\" or \"production\"."
  }
}

variable "do_token" {
  description = "DigitalOcean API token (write scope). Supplied via TF_VAR_do_token."
  type        = string
  sensitive   = true
}

variable "ssh_public_key" {
  description = "Raw public key content of the dedicated deploy keypair. Supplied via TF_VAR_ssh_public_key."
  type        = string
}

variable "region" {
  description = "DigitalOcean region. Matches the existing Postgres cluster host (db-pgsql-tor1-...)."
  type        = string
  default     = "tor1"
}

variable "droplet_size" {
  description = "Droplet size slug. s-2vcpu-2gb is sized for a single low-traffic dev droplet."
  type        = string
  default     = "s-1vcpu-1gb"
}

variable "postgres_cluster_id" {
  description = "ID of the existing DO managed Postgres cluster (from `doctl databases list`). No default — must be supplied explicitly."
  type        = string
}

variable "valkey_cluster_id" {
  description = "ID of the existing DO managed Valkey cluster (from `doctl databases list`). No default — must be supplied explicitly."
  type        = string
}

variable "do_project_name" {
  description = "Name of the existing DigitalOcean Project this environment's droplet is assigned into (shared with the frontend and services repos — not created by this repo's Terraform). No default — must be supplied explicitly."
  type        = string
}
