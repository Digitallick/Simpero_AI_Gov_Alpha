#cloud-config
# Rendered via Terraform's templatefile() (main.tf) so the deploy keypair's
# public half can be injected - cloud-init can't reference a TF variable
# directly. Ordering matters: the deploy user + its authorized_keys must be
# confirmed written before password auth is disabled (last step below), so a
# partial run never locks out SSH entirely (digitalocean_droplet.ssh_keys is
# the redundant fallback login path for that same reason).
users:
  - name: deploy
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    groups: docker
    ssh_authorized_keys:
      - ${ssh_public_key}

runcmd:
  - mkdir -p /opt/simpero

# Last: disable SSH password auth via cloud-init's native directive, not a
# hand-rolled sshd_config sed.
ssh_pwauth: false
