# Per-environment values for staging. do_token, ssh_public_key, and
# environment are NOT here — they come from TF_VAR_* env vars in the
# workflow (secrets + inputs.environment), one source of truth. See
# docs/plans/do-droplet-deployment.md §1/§5.
region       = "tor1"
droplet_size = "s-1vcpu-1gb"

# Staging Postgres/Valkey cluster IDs (via DO API — see docs/PENDING_ON_VANSH.md §5).
postgres_cluster_id = "dc41daf7-3ccd-43c6-8409-9ef6b8b647e2"
valkey_cluster_id   = "bc62dad7-0e89-4323-b53b-2099b5a2fc29"
