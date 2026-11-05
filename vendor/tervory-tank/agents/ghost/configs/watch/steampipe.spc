# Autumbra Watch — Steampipe options (stronger-than-stock)
# Vault pin: 103201b1aad459788fb7d798637295600ed82179
# Read-only inventory. No write plugins.

options "general" {
  update_check = false
  telemetry    = "none"
}

# Allowlisted read plugins only (connections still operator-supplied).
  plugin "aws" { source = "aws" }
  plugin "azure" { source = "azure" }
  plugin "cloudflare" { source = "cloudflare" }
  plugin "gcp" { source = "gcp" }
  plugin "github" { source = "github" }
  plugin "kubernetes" { source = "kubernetes" }
  plugin "terraform" { source = "terraform" }
