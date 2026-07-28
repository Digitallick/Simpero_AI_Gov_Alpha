provider "digitalocean" {
  token = var.do_token
}

resource "digitalocean_ssh_key" "deploy" {
  name       = "simpero-deploy-${var.environment}"
  public_key = var.ssh_public_key
}

resource "digitalocean_droplet" "app" {
  name = "simpero-app-${var.environment}"
  # DO's "Docker on Ubuntu" marketplace image — ships Docker Engine + compose
  # plugin preinstalled. Slug has stayed "docker-20-04" even as DO updated the
  # image's underlying base to Ubuntu 22.04; verified against DO's own
  # marketplace listing rather than guessed.
  image  = "docker-20-04"
  region = var.region
  size   = var.droplet_size

  # Real fallback login path if the cloud-init user-creation step fails
  # partway — independent of the ssh_authorized_keys install in user_data.
  ssh_keys = [digitalocean_ssh_key.deploy.id]

  # templatefile(), not file() — cloud-init can't reference a TF variable
  # directly, so the deploy public key is rendered in here. Any edit to this
  # file (or the template it renders) forces droplet replacement.
  user_data = templatefile("${path.module}/cloud-init.yaml.tpl", {
    ssh_public_key = var.ssh_public_key
  })
}

resource "digitalocean_firewall" "app" {
  name = "simpero-app-firewall-${var.environment}"

  droplet_ids = [digitalocean_droplet.app.id]

  inbound_rule {
    protocol         = "tcp"
    port_range       = "22"
    source_addresses = ["0.0.0.0/0", "::/0"]
  }

  inbound_rule {
    protocol         = "tcp"
    port_range       = "80"
    source_addresses = ["0.0.0.0/0", "::/0"]
  }

  inbound_rule {
    protocol         = "tcp"
    port_range       = "443"
    source_addresses = ["0.0.0.0/0", "::/0"]
  }

  # DO Cloud Firewalls deny-by-default in both directions — without these the
  # droplet can't reach GHCR, Let's Encrypt, the DB clusters, or DNS.
  # Allow-all egress is fine at this scale, no need to scope it down.
  outbound_rule {
    protocol              = "tcp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "udp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "icmp"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }
}

# Full-replace, not additive — mirrors DO's underlying firewall API. Existing
# trusted sources must be audited by hand (doctl databases firewalls list)
# before the first real apply; see docs/plans/do-droplet-deployment.md §1/§5.
resource "digitalocean_database_firewall" "postgres" {
  cluster_id = var.postgres_cluster_id

  rule {
    type  = "droplet"
    value = digitalocean_droplet.app.id
  }
}

resource "digitalocean_database_firewall" "valkey" {
  cluster_id = var.valkey_cluster_id

  rule {
    type  = "droplet"
    value = digitalocean_droplet.app.id
  }
}

# Assigns this environment's droplet into a pre-existing DO Project (shared
# with the frontend and services repos, each assigning their own resources —
# not created or owned by this repo's Terraform, same pattern as the shared
# Spaces state buckets). Only the droplet: DO Projects support a fixed set of
# resource types (App Platform App, Database, Domain, Droplet, Floating IP,
# Kubernetes Cluster, Load Balancer, Space, Volume) — confirmed against DO's
# own OpenAPI spec — and neither Firewalls nor SSH keys are on that list, so
# digitalocean_firewall.app and digitalocean_ssh_key.deploy simply can't be
# assigned to a project at all; they stay account-wide regardless.
data "digitalocean_project" "target" {
  name = var.do_project_name
}

resource "digitalocean_project_resources" "app" {
  project   = data.digitalocean_project.target.id
  resources = [digitalocean_droplet.app.urn]
}
