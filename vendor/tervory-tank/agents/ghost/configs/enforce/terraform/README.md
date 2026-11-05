# Autumbra Enforce — Terraform templates

**Product:** Autumbra Enforce (Watch / Enforce naming only).  
**Status:** Templates only. Flag **OFF**. Never auto-apply.  
**License:** Upstream HashiCorp Terraform is **BSL-1.1** — product-legal review before any hosted competitive embed. These `.tf.tpl` stubs do not vendor the Terraform binary.

## Rules

1. `plan_from_threat` returns a dry plan dict — adapters never call `terraform apply` / `destroy`.
2. No remote state backend that phones home in packaged templates.
3. Live Cloudflare edge changes remain on **Autumbra Watch Shields Up** (propose → founder Shield tap → health → undo).
4. Arming requires `ENFORCE_OSS_ENABLED` **and** `ENFORCE_OSS_ARMED` / `autumbra_enforce_armed` — still no apply path in this slice.

## Modules (allowlisted)

| Template | Maps from ThreatPayload.kind |
|----------|------------------------------|
| `cloudflare_waf_rule.tf.tpl` | `waf_managed` |
| `cloudflare_rate_limit.tf.tpl` | `rate_limit` |

Vault pin: `hashicorp/terraform` @ `55988303ba5651e41e2c9097e208468c8eb3da4b`  
Primary vault: `ammosync@2.25.250.28:/home/ammosync/code-vault/autumbra-oss/terraform/`  
Box cache: `/workspace/autumbra-oss-vault/terraform/`
