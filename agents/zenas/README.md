# Zenas gold candidates

`GET /gold/candidates` is room-key only. It labels public worked matters and statute sections already stored on this box. It does not call a model, and it does not read another tenant's rows.

Every auto row has `label_family`:

- Worked matters: `converted:{source_id}:YYYY-MM`. The month is the matter's `as_at`, then the day it was saved, then when the court text was first seen, then the court's own date.
- Statute sections: `statute_section:{source_id}:YYYY-MM`. The month is when the section was first seen, then when it was captured, then its effective date, then the document date.

A row with no usable date is not emitted. Two months are two families. Held-out rows stay `holdout: true` and are not trained on. A row marked synthetic uses a family whose name contains `synthetic`.

`label_source` stays per row, as before.

## Docassemble raw compose

`docker-compose.docassemble.yml` (Boston, Coolify app `zenas-docassemble`, uuid
`iumzytsbbvjn2ah2wmlvuiyk`, server `localhost`) and `docker-compose.docassemble.za.yml`
(ZA, Coolify app `zenas-docassemble-za`, uuid `r1rogjhhxryoh770asmjvcm3`, server
`za-afrihost`) start with `# coolify: raw compose`. In Coolify's normal mode the
`docassemble` service would also join Coolify's internet-facing app network, which breaks
the seal on the internal `tervory-zenas-assembly` network (SEAL-1).

Merging these files does not redeploy either Docassemble app: deploy-on-merge only deploys
apps named `agent-*` plus the main and ZA Tank stacks. A manual redeploy with Raw Compose
still off runs normal mode as today. Coolify keeps using the volume it already has a
storage entry for (`<uuid>_docassemble-…-backup`), so no backup data moves.

**Order matters:** turn Raw Compose on only after these files are on `main`. In raw mode
the older file (with no external volume) would start Docassemble on a new, empty
`<uuid>_docassemble_…_backup` volume.

Live steps, once per app, after the merge's deploy-on-merge run has finished:

1. Boston, box shell (read only): `docker volume ls --format '{{.Name}}' | grep iumzytsbbvjn2ah2wmlvuiyk`
   must list `iumzytsbbvjn2ah2wmlvuiyk_docassemble-us-backup`, and `docker ps --filter
   volume=iumzytsbbvjn2ah2wmlvuiyk_docassemble-us-backup --format '{{.Names}}'` must show
   the running docassemble and docassemble-backup containers. If the name differs, fix
   `volumes.docassemble_us_backup.name` in a PR first.
2. Boston, Coolify UI: `zenas-docassemble` > **Advanced** > turn on **Raw Compose
   Deployment** > Redeploy. Docassemble writes a backup on its graceful stop (10 min grace).
3. ZA, box shell (read only): `docker volume ls --format '{{.Name}}' | grep -E 'r1rogjhhxryoh770asmjvcm3|docassemble-za-backup'`
   shows the normal-mode volume, expected `r1rogjhhxryoh770asmjvcm3_docassemble-za-backup`
   (OLD below). `tervory-zenas-docassemble-za-backup` must not exist yet.
4. ZA, Coolify UI: `zenas-docassemble-za` > **Stop**. Wait until it shows stopped.
5. ZA, box shell: confirm nothing uses OLD. `docker ps --filter volume=OLD -q` must print
   nothing, and `docker ps -a --filter label=coolify.applicationUuid=r1rogjhhxryoh770asmjvcm3 --format '{{.Names}} {{.Status}}'`
   shows only `Exited`. Never copy while a container has OLD mounted.
6. ZA, box shell: copy once.
   `docker volume create tervory-zenas-docassemble-za-backup && docker run --rm -v OLD:/from:ro -v tervory-zenas-docassemble-za-backup:/to alpine cp -a /from/. /to/`
7. ZA, box shell: check that the copy is complete. This must print `COPY-OK`:
   `docker run --rm -v OLD:/from:ro -v tervory-zenas-docassemble-za-backup:/to:ro alpine sh -c 'cd /from && find . -type f -exec sha256sum {} + | sort -k2 >/tmp/a; cd /to && find . -type f -exec sha256sum {} + | sort -k2 >/tmp/b; diff /tmp/a /tmp/b && echo COPY-OK; find /from | wc -l; find /to | wc -l'`.
   Keep OLD as the rollback copy; do not delete it.
8. ZA, Coolify UI: `zenas-docassemble-za` > **Advanced** > turn on **Raw Compose
   Deployment** > Redeploy. Do not Start it in normal mode between steps 4 and 8. A normal
   start writes new backups to OLD, not the copy.
9. Both, box shell: `docker inspect -f '{{json .NetworkSettings.Networks}}' <docassemble container>`
   lists only `tervory-zenas-assembly`. The docassemble mount is the fixed volume name
   (`docker inspect -f '{{range .Mounts}}{{.Name}} {{end}}' <container>`), and
   `http://127.0.0.1/health_check?ready=1` answers inside it.

Raw Compose Deployment is set in the Coolify UI. Repo tooling never sets it through the API
(docs/cursor/ROOMS.md).
