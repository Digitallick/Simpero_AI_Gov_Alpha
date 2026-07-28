# Per-environment values for production. do_token, ssh_public_key, and
# environment are NOT here — they come from TF_VAR_* env vars in the
# workflow (secrets + inputs.environment), one source of truth. See
# docs/plans/do-droplet-deployment.md §1/§5.
region       = "tor1"
droplet_size = "s-2vcpu-2gb"

# REPLACE_ME: production Postgres/Valkey clusters are not yet provisioned
# (plan §5 item 7) — Vansh to fill in the real cluster IDs from
# `doctl databases list` before the first real apply.
postgres_cluster_id = "REPLACE_ME"
valkey_cluster_id   = "REPLACE_ME"
