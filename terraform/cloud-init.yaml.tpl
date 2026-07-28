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
  # runcmd runs as root, so /opt/simpero is created root-owned by default -
  # the deploy user (via SCP in deploy.yml) needs write access to it.
  - mkdir -p /opt/simpero
  - chown deploy:deploy /opt/simpero
  # 1G swap: small droplets (e.g. s-1vcpu-1gb) have zero swap by default.
  # Without it, a brief memory spike (Docker pull + migration + container
  # restart, all during an active SSH/SCP session in deploy.yml) can
  # trigger an instant OOM kill instead of being absorbed. Persisted via
  # fstab so it's still active after a reboot, not just this first boot.
  - fallocate -l 1G /swapfile
  - chmod 600 /swapfile
  - mkswap /swapfile
  - swapon /swapfile
  - echo '/swapfile none swap sw 0 0' >> /etc/fstab

# Last: disable SSH password auth via cloud-init's native directive, not a
# hand-rolled sshd_config sed.
ssh_pwauth: false
