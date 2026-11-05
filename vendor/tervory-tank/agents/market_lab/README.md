# Market Lab gold candidates

`GET /gold/candidates?limit=N` requires `X-Tank-Key` equal to this room's `TANK_KEY`. The check compares the header and the key as bytes. Limits are 1–200. The box region comes from `TANK_REGION`, not from the request.

The route reads this box's own database: `agent_market_lab.series`, `agent_market_lab.points`, and `agent_market_lab.predictions`. The query is limited to the platform tenant. Uploads, client series, and FRED are not public feeds and are skipped. Another tenant's series is skipped. This box's database is this region, so every row is stamped with `TANK_REGION`. The room image copies `gold_sources.py` next to `app.py` so this import loads. Nothing else in the room changes.

Shift and surge label a past point only after a stored value at least 28 days later is known (`WINDOW_DAYS`). The label is whether the stored values in those 28 days moved past the live percent threshold (`PCT_THRESHOLD`, 0.25). A rise is `surge` for surge detection and `bottleneck` for shift detection. A fall is `no surge` and `saturating`. A point whose 28 days have not passed is not labelled. Where a stored prediction covers that point, the row also says whether the later value met the prediction's comparator and target. The prediction's own stored outcome is not reused. The family is `later-value:{feed}:YYYY-MM`.

`market.series_fetch` labels each stored point. That stored value is the official value. The family is `official-match:{feed}:YYYY-MM`.

A series whose engine is `synthetic` uses a family whose name contains `synthetic`. These rows are candidates for a spot check. They are not trained on by themselves.
