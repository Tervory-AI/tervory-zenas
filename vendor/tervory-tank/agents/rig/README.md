# Rig gold candidates

`GET /gold/candidates?tenant_id=<uuid>` is room-key only. It labels `rig.search` and `rig.fit_suggest` from the selected store's uploaded active products and explicitly approved database fits. The response uses this box's `TANK_REGION` (`us-east` or `za`). Missing tenant selection, an empty store or no approved fits produces an empty list.

Rows contain the selected `tenant_id` and `public: false`. The platform-wide collector calls without a tenant and receives an empty list. Store data stays tenant-private. Products flagged as sample/demo, inactive products and products without upload/feed provenance are excluded.

Each row's `label_family` is `fitment:{tenant}:{revision}`. The revision is a deterministic hash of the shopper-safe products and approved fit rules, so changed approved content receives a new family. Supplier amounts, embed keys, shopper contacts and retailer configuration are not candidate fields. Runtime catalogue files are not used.
