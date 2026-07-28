output "droplet_ip" {
  description = "Droplet public IPv4 address — fill DROPLET_HOST with this after apply."
  value       = digitalocean_droplet.app.ipv4_address
}
