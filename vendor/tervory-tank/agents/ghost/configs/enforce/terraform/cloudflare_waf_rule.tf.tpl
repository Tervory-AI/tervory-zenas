# Autumbra Enforce — Cloudflare WAF rule template (NOT applied)
# Product: Autumbra Enforce. Dry-run / plan only until founder arms.
# License note: HashiCorp Terraform is BSL-1.1 — product-legal review before embed.
# No remote state backend that phones home. No terraform apply automation here.
# Live edge raises stay on Autumbra Watch Shields Up (founder Shield tap).

# variable "zone_name" { type = string }          # e.g. tervory.com dogfood — operator filled
# variable "autumbra_enforce_armed" { type = bool, default = false }

# locals {
#   # Refuse to render applyable resources unless armed (template comment only).
#   armed = var.autumbra_enforce_armed
# }

# This file is a .tf.tpl stub for Autumbra Enforce plan_from_threat.
# It intentionally contains no provider block, no cloudflare_ruleset resource,
# and no backend {} — so `terraform apply` cannot succeed from this template alone.
