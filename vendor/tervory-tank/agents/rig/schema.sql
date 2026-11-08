-- Rig's room. Additive and idempotent. Applied by the agent on start.
-- Shoppers only see rows with approved = true. Imported rows are approved.
-- Suggested rows stay hidden until approve_fit sets approved.
-- Rejected rows have source 'rejected' and approved = false, so shoppers never see them.
-- answers stores one graph or setup per product, keyed by a hash of the catalogue.
-- fit_why stores why-text for one fit, keyed by a hash of its rule and note.
-- Import, a feed pull, a price watch, approve, and reject clear answers for that tenant.
-- fit_why is kept so a known fit is not sent to a model again.
-- searches keeps each finished shopper search for that tenant.
-- A later search reads only the newest rows inside the recall window
-- (RIG_SEARCH_RECALL_DAYS, default 90), at most 500, newest first.
-- A nightly job deletes rows older than that same window.
-- A changed catalogue is not reused. A search from another tenant is never read.
-- market_prices is not the catalogue. Search does not read it.
-- Each row is one other seller's price for a sku this retailer already stocks.
-- The sheet is a separate import. A later price reader is the intended feed.
-- When a sku has no rows, the shopper is told the figure is not available.
-- retailers.name is the label on that retailer's own price.

CREATE SCHEMA IF NOT EXISTS agent_rig;

-- Durable handoff to Tank's save_pair collector. No contact details or keys.
-- Events are removed only in the same transaction that stores the training pair.
CREATE TABLE IF NOT EXISTS agent_rig.training_events (
    tenant_id uuid NOT NULL,
    region text NOT NULL,
    source text NOT NULL CHECK (source IN ('rig_no_result', 'rig_quote')),
    source_ref text NOT NULL,
    input jsonb NOT NULL,
    output jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, region, source, source_ref)
);

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS agent_rig.products (
    tenant_id   uuid NOT NULL,
    id          text NOT NULL,
    sku         text NOT NULL,
    gtin        text,
    name        text NOT NULL,
    brand       text NOT NULL,
    category    text NOT NULL,
    price       numeric NOT NULL,
    currency    text NOT NULL,
    image_url   text,
    url         text,
    family      text NOT NULL DEFAULT '',
    attributes  jsonb NOT NULL DEFAULT '{}'::jsonb,
    active      boolean NOT NULL DEFAULT true,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, sku)
);

CREATE INDEX IF NOT EXISTS products_tenant_active ON agent_rig.products (tenant_id, active);

-- Append-only event history. Consumer retries never overwrite earlier prices.
CREATE TABLE IF NOT EXISTS agent_rig.price_history (
    tenant_id uuid NOT NULL,
    product_id text NOT NULL,
    price numeric NOT NULL CHECK(price >= 0 AND price < 'Infinity'::numeric),
    currency text NOT NULL CHECK(currency ~ '^[A-Z]{3}$'),
    observed_at timestamptz NOT NULL,
    event_id uuid PRIMARY KEY
);
CREATE INDEX IF NOT EXISTS price_history_tenant_product
    ON agent_rig.price_history(tenant_id,product_id,observed_at,event_id);

-- Tiger retries are idempotent and cannot replace a newer confirmed observation.
CREATE TABLE IF NOT EXISTS agent_rig.tiger_receipts (
    tenant_id uuid NOT NULL,
    sku text NOT NULL,
    url text NOT NULL,
    observed_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, sku, url),
    FOREIGN KEY (tenant_id, sku) REFERENCES agent_rig.products(tenant_id, sku) ON DELETE CASCADE
);

-- Manual removal survives feed refresh and restart. Only a manual import clears it.
CREATE TABLE IF NOT EXISTS agent_rig.deleted_products (
    tenant_id uuid NOT NULL,
    sku text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, sku)
);

CREATE INDEX IF NOT EXISTS products_search_fts ON agent_rig.products
USING gin (to_tsvector('simple',
    coalesce(name, '') || ' ' || coalesce(brand, '') || ' ' || coalesce(sku, '') || ' ' || coalesce(attributes::text, '')
));

CREATE INDEX IF NOT EXISTS products_name_trgm ON agent_rig.products USING gin (name gin_trgm_ops);

CREATE TABLE IF NOT EXISTS agent_rig.fits (
    tenant_id     uuid NOT NULL,
    part_sku      text NOT NULL,
    fits_sku      text NOT NULL DEFAULT '',
    fits_family   text NOT NULL DEFAULT '',
    rule          jsonb NOT NULL DEFAULT '{}'::jsonb,
    source        text NOT NULL,
    confidence    numeric,
    approved      boolean NOT NULL DEFAULT false,
    note          text NOT NULL DEFAULT '',
    PRIMARY KEY (tenant_id, part_sku, fits_sku, fits_family),
    CONSTRAINT fits_one_target CHECK (
        (fits_sku <> '' AND fits_family = '') OR (fits_sku = '' AND fits_family <> '')
    )
);

CREATE INDEX IF NOT EXISTS fits_tenant_idx ON agent_rig.fits (tenant_id, part_sku);

CREATE TABLE IF NOT EXISTS agent_rig.families (
    tenant_id    uuid NOT NULL,
    family       text NOT NULL,
    member_skus  text[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (tenant_id, family)
);

CREATE TABLE IF NOT EXISTS agent_rig.setup_templates (
    tenant_id  uuid NOT NULL,
    category   text NOT NULL,
    roles      jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (tenant_id, category)
);

CREATE TABLE IF NOT EXISTS agent_rig.keys (
    tenant_id   uuid NOT NULL,
    key_hash    text NOT NULL,
    kind        text NOT NULL,
    origins     text[] NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (key_hash),
    CONSTRAINT keys_kind_chk CHECK (kind IN ('publishable', 'admin'))
);

CREATE INDEX IF NOT EXISTS keys_tenant_idx ON agent_rig.keys (tenant_id);

CREATE TABLE IF NOT EXISTS agent_rig.quotes (
    tenant_id   uuid NOT NULL,
    id          text PRIMARY KEY,
    payload     jsonb NOT NULL,
    contact     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    sent_at     timestamptz
);

CREATE INDEX IF NOT EXISTS quotes_tenant_created ON agent_rig.quotes (tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS agent_rig.retailers (
    tenant_id  uuid PRIMARY KEY,
    slug       text NOT NULL,
    email      text,
    webhook    text,
    name       text NOT NULL DEFAULT ''
);

ALTER TABLE agent_rig.retailers ADD COLUMN IF NOT EXISTS name text NOT NULL DEFAULT '';
ALTER TABLE agent_rig.retailers ADD COLUMN IF NOT EXISTS theme text NOT NULL DEFAULT 'neutral'
    CHECK (theme IN ('neutral', 'custom-nation'));
ALTER TABLE agent_rig.retailers ADD COLUMN IF NOT EXISTS quote_notice jsonb;
-- Optional cross-store fulfilment (Locate). Off for every store until it opts in.
-- Off means no supply lookup in search or quotes and no locate, confirm or accept.
ALTER TABLE agent_rig.retailers ADD COLUMN IF NOT EXISTS locate_enabled boolean NOT NULL DEFAULT false;
-- Optional blind ship: Locate orders ship from the other store straight to the shopper. Off by default.
ALTER TABLE agent_rig.retailers ADD COLUMN IF NOT EXISTS blind_ship boolean NOT NULL DEFAULT false;
-- Optional auto-order: send the PO to the approved source (sandbox outbox only). Off by default.
ALTER TABLE agent_rig.retailers ADD COLUMN IF NOT EXISTS auto_order boolean NOT NULL DEFAULT false;

-- Founder-approved carry-over. Preserve any store settings already configured.
ALTER TABLE agent_rig.retailers ADD COLUMN IF NOT EXISTS shop jsonb NOT NULL DEFAULT '{}'::jsonb;
-- Store-owned stock delivery range. Empty means the store has not supplied a time.
ALTER TABLE agent_rig.retailers ADD COLUMN IF NOT EXISTS delivery jsonb NOT NULL DEFAULT '{}'::jsonb;
UPDATE agent_rig.retailers SET shop = '{"search":true,"mail_reservations":true,"centre_sku":"ARCHON-TYPE-B","not_stocked":[{"name":"Glock","also":["glok","glock"]},{"name":"Sig","also":["sig","sigsauer"]},{"name":"CZ","also":["cz"]}]}'::jsonb
WHERE slug = 'archon' AND shop = '{}'::jsonb;

-- Preserve today's search vocabulary only where it is missing. Never overwrite store data.
UPDATE agent_rig.retailers SET shop = shop || '{"vocabulary":{"attribute_key":"caliber","carry_category":"Holsters","category_words":{"barrel":"Barrels","barrels":"Barrels","holster":"Holsters","holsters":"Holsters","light":"Lights","lights":"Lights","mag":"Magazines","magazine":"Magazines","magazines":"Magazines","mags":"Magazines","optic":"Sights","optics":"Sights","part":"Parts","parts":"Parts","pistol":"Pistols","pistols":"Pistols","sight":"Sights","sights":"Sights","trigger":"Triggers","triggers":"Triggers"},"centre_category":"Pistols","colour_words":["blue","bronze","brown","coyote","gold","gray","green","grey","od","olive","orange","pink","purple","silver","tan","white","yellow"],"loose_words":"\\b(?:left[-\\s]?handed|left|iwb|kydex|review|best|gen(?:eration)?(?:\\s+\\d+)?)\\b","model_words":{"capacity_category":"Magazines","capacity_plural":"mags","capacity_short":"mag","capacity_spec":"magazines","capacity_word":"magazine","carry_word":"holster","compared_brand":"trijicon","compound_colour_label":"OD green","compound_colour_prefix":"od","compound_colour_query":"od green","compound_colour_tail":"green","compound_prefix_label":"OD","count_attribute":"rounds","count_replacement":"\\1 round magazine","count_word":"round","default_variant":"b","extended_query":"18 round magazine","group_label":"These","length_category":"Barrels","length_spec":"standard_barrel","length_word":"barrel","light_category":"Lights","model_abbreviation":"typ","model_brand":"archon","model_prefix":"type","mount_model":"rmr","option_attribute":"optic_cut","part_feature":"threaded","primary_colour":"fde","primary_colour_label":"FDE","secondary_colour":"black","secondary_colour_label":"Black","size_alias":"9mm","size_value":"9x19","thread_long_value":"13.5x1","thread_short_value":"1/2-28","variant_feature":"ported","variants":"a b c d","view_alias":"optic","view_alias_plural":"optics","view_category":"Sights","view_colour":"red","view_plural":"sights","view_short":"dot","view_short_plural":"dots","view_word":"sight"},"modifier_words":["best","cheapest","expensive","extended","extra","gen","generation","handed","iwb","kydex","left","most","review","spare"],"part_categories":["Sights","Triggers","Magazines","Lights","Holsters","Barrels","Parts"],"part_tail":{"barrel":"Barrels","barrels":"Barrels","dot":"Sights","dots":"Sights","light":"Lights","lights":"Lights","mag":"Magazines","magazine":"Magazines","magazines":"Magazines","mags":"Magazines","optic":"Sights","optics":"Sights","rmr":"rmr","sight":"Sights","sights":"Sights","trigger":"Triggers","triggers":"Triggers"},"sentences":{"and_list":"{0} and {1}","badges":"Exact match","badges_2":"Demo item","bare_colour":"{0} isn''t a colour we list.","bare_colour_2":"{0} isn''t a colour we list. We have {1}.","blocked_build":"Doesn''t fit the {0}","brand_note":"We list an RMR-footprint dot, not a Trijicon.","build_block":"{0} does not fit the {1}.","correction_gap":"We don''t sell a suppressor. The threaded barrel is the closest.","count_gap":"We have {0} rounds for the {1}.","cut_state":"Fits your {0} cut","cut_state_2":"Needs {0} cut","expand_label":"The {0} is {1} {2}","expand_prices":"R ","fit_answer":"Yes, with the {0} cut.","fit_decision":"That part","fit_decision_2":"{0} does not fit the {1}.{2}","fit_decision_3":"{0} does not fit the {1} cut.","fit_decision_4":"{0} does not fit the {1}.","fit_state":"Does not fit","fit_state_2":"Fits your {0} cut","fit_state_3":"Needs {0} cut","honesty_gap":"No exact match. Closest: {0}.","kilo":"{0}R {1}","no_hits":"We don''t stock {0}.","no_hits_2":"We don''t stock {0}.","no_hits_3":"Nothing matches that yet.","pack_note":"We list these one at a time, not as a pack.","part_for_model":"{0} for type {1}","prepare_reservation":"That item is not in this catalogue.","prepare_reservation_2":"{0} is out of stock.","prepare_reservation_3":"{0} \u00b7 Fit to confirm","price_gap":"The {0} is {1}, which is over that.","price_gap_2":"The {0} is {1}, which is under that.","rand":"R {0}","refusal_message":"{0} does not fit the {1}.","refusal_message_2":"{0}. We don''t list a {1} for the {2}.","rewrite_fit_question":"{0} for {1}","shop_search":"We don''t stock {0}.","shop_search_2":"{0} is out of stock.","shop_search_3":"No exact match.","shop_search_4":"No exact match. Closest: {0}.","shop_search_5":"No exact match.","size_gap":"The {0} is {1}.","size_gap_2":"The {0} is {1}.","size_gap_3":"The {0} {1} are {2}.","size_miss":"No exact match for ","size_miss_2":"No exact match for ","unknown_choice":"{0} is not an option on {1}.","unknown_choice_2":"{0} is not an option on {1}.","unlink_message":"We don''t list a {0} for the {1}. The {2} is for the {3}.","unlink_message_2":"No exact match. Closest: {0}.","unoffered_colour":"OD green isn''t a colour on the {0}.","unoffered_colour_2":"{0} isn''t a colour on the {1}.","unsold_note":" for the {0}","unsold_note_10":"We don''t sell {0}.","unsold_note_11":"No exact match.","unsold_note_2":"We don''t sell a compensator{0}.","unsold_note_3":"We don''t sell a laser{0}.","unsold_note_4":"We don''t sell a sling{0}.","unsold_note_5":"We don''t sell a case{0}.","unsold_note_6":"We don''t sell a bundle{0}.","unsold_note_7":"We don''t sell night sights.","unsold_note_8":"We don''t sell a suppressor. The threaded barrel is the closest.","unsold_note_9":"We don''t sell a cleaning kit.","variant_miss":"No exact match for ","variant_miss_2":"No exact match for "},"sight_words":["dot","dots","optic","optics","sight","sights"],"size_patterns":{"count":"\\b(\\d+)\\s*rounds?\\b","count_alias":"\\b(\\d+)\\s*rounders?\\b","model_prefix":"type\\s+[a-d]\\b","named_size":"\\.\\d+\\b|\\b(?:acp|auto)\\b|\\bs\\s*&\\s*w\\b|\\bin\\s+\\.?\\d+","other_size":"\\b(?:\\.45|\\.40|\\.357|\\.38|45|40|357|38|10\\s*mm)\\b","size":"\\b(?:\\d+(?:\\.\\d+)?\\s*mm|\\d+\\s*[x\u00d7]\\s*\\d+|\\.\\d+)\\b","thread":"^\\d+x\\d+$","thread_long":"13\\s*\\.\\s*5\\s*[x\u00d7]\\s*1\\b","thread_short":"1\\s*/\\s*2\\s*[x\u00d7]\\s*28"},"spec_words":["acro","ported","rmr","rms","thread","threaded"],"unsold":{"attachment":"compensator","bundle":"bundle","care":"cleaning","consumable_full":"ammunition","consumable_short":"ammo","container":"case","correction":"suppressor","dark":"night","kit":"kit","kits":"kits","pointer":"laser","ready":"ready","strap":"sling"}}}'::jsonb
WHERE shop->>'search' = 'true' AND NOT (shop ? 'vocabulary');

CREATE TABLE IF NOT EXISTS agent_rig.feeds (
    tenant_id  uuid NOT NULL,
    format     text NOT NULL,
    url        text NOT NULL,
    currency   text NOT NULL DEFAULT '',
    PRIMARY KEY (tenant_id, url)
);

-- Legacy feed rows predate supplier_costs. Until a feed refresh records its
-- product membership, its tenant's provenance is unknown and Tiger fails closed.
ALTER TABLE agent_rig.feeds ADD COLUMN IF NOT EXISTS watch_indexed boolean NOT NULL DEFAULT false;
CREATE TABLE IF NOT EXISTS agent_rig.feed_products (
    tenant_id uuid NOT NULL, sku text NOT NULL, feed_url text NOT NULL,
    PRIMARY KEY (tenant_id, sku, feed_url),
    FOREIGN KEY (tenant_id, sku) REFERENCES agent_rig.products(tenant_id, sku) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS agent_rig.answers (
    tenant_id   uuid NOT NULL,
    kind        text NOT NULL,
    product_id  text NOT NULL,
    version     text NOT NULL,
    body        jsonb NOT NULL,
    PRIMARY KEY (tenant_id, kind, product_id)
);

CREATE TABLE IF NOT EXISTS agent_rig.fit_why (
    tenant_id   uuid NOT NULL,
    fit_id      text NOT NULL,
    rule_hash   text NOT NULL,
    why         jsonb NOT NULL,
    PRIMARY KEY (tenant_id, fit_id, rule_hash)
);

CREATE TABLE IF NOT EXISTS agent_rig.market_prices (
    tenant_id  uuid NOT NULL,
    sku        text NOT NULL,
    source     text NOT NULL,
    price      numeric NOT NULL,
    currency   text NOT NULL,
    seen_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, sku, source)
);

CREATE INDEX IF NOT EXISTS market_prices_tenant_sku ON agent_rig.market_prices (tenant_id, sku);

CREATE TABLE IF NOT EXISTS agent_rig.searches (
    tenant_id     uuid NOT NULL,
    id            text NOT NULL,
    version       text NOT NULL,
    query         text NOT NULL,
    host          text NOT NULL DEFAULT '',
    signature     text NOT NULL,
    result_limit  integer NOT NULL,
    ids           jsonb NOT NULL,
    exact_match   boolean NOT NULL DEFAULT true,
    message       text,
    parts         jsonb NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id)
);

CREATE INDEX IF NOT EXISTS searches_tenant_version ON agent_rig.searches (tenant_id, version);

CREATE INDEX IF NOT EXISTS searches_tenant_created
    ON agent_rig.searches (tenant_id, created_at DESC);

-- RIG-4: durable retailer handoff and anonymous daily widget counts.
CREATE TABLE IF NOT EXISTS agent_rig.quote_handoffs (
    tenant_id uuid NOT NULL,
    quote_id text NOT NULL REFERENCES agent_rig.quotes(id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sending', 'sent')),
    error text,
    attempts integer NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, quote_id)
);

CREATE TABLE IF NOT EXISTS agent_rig.widget_counts (
    tenant_id uuid NOT NULL,
    day date NOT NULL,
    event text NOT NULL CHECK (event IN ('searches', 'setups_opened', 'swaps', 'quotes', 'no_results')),
    count bigint NOT NULL DEFAULT 0 CHECK (count >= 0),
    PRIMARY KEY (tenant_id, day, event)
);
-- End RIG-4.

-- RIG-4b: short-lived per-client/route abuse limits, never joined to analytics.
ALTER TABLE agent_rig.quote_handoffs ADD COLUMN IF NOT EXISTS next_attempt_at timestamptz DEFAULT now();
ALTER TABLE agent_rig.quote_handoffs ADD COLUMN IF NOT EXISTS needs_attention boolean NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS quote_handoffs_due ON agent_rig.quote_handoffs(tenant_id, next_attempt_at)
    WHERE NOT needs_attention AND status <> 'sent';
CREATE TABLE IF NOT EXISTS agent_rig.widget_search_events (
    tenant_id uuid NOT NULL,
    event_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, event_id)
);
CREATE TABLE IF NOT EXISTS agent_rig.rate_windows (
    tenant_id uuid NOT NULL,
    window_id bigint NOT NULL,
    salt bytea NOT NULL,
    PRIMARY KEY (tenant_id, window_id)
);
CREATE TABLE IF NOT EXISTS agent_rig.rate_counts (
    tenant_id uuid NOT NULL,
    window_id bigint NOT NULL,
    route text NOT NULL,
    client_hash bytea NOT NULL,
    hits integer NOT NULL CHECK (hits > 0),
    PRIMARY KEY (tenant_id, window_id, route, client_hash),
    FOREIGN KEY (tenant_id, window_id) REFERENCES agent_rig.rate_windows(tenant_id, window_id) ON DELETE CASCADE
);
-- End RIG-4b.

-- RIG-3: private price inputs and per-file import receipts.
-- Public product queries never select these tables.
CREATE TABLE IF NOT EXISTS agent_rig.price_rules (
    tenant_id uuid PRIMARY KEY,
    rule jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_rig.exchange_rates (
    tenant_id uuid NOT NULL,
    currency text NOT NULL,
    rate numeric NOT NULL CHECK (rate > 0),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id,currency)
);
CREATE TABLE IF NOT EXISTS agent_rig.supplier_costs (
    tenant_id uuid NOT NULL,
    sku text NOT NULL,
    cost numeric NOT NULL CHECK (cost >= 0),
    currency text NOT NULL,
    provider text NOT NULL,
    site text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id,sku)
);
CREATE TABLE IF NOT EXISTS agent_rig.feed_receipts (
    tenant_id uuid NOT NULL,
    id uuid NOT NULL,
    provider text NOT NULL,
    site text NOT NULL,
    added integer NOT NULL CHECK (added >= 0),
    updated integer NOT NULL CHECK (updated >= 0),
    rejected integer NOT NULL CHECK (rejected >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id,id)
);
CREATE INDEX IF NOT EXISTS rig_feed_receipts_tenant_created ON agent_rig.feed_receipts(tenant_id,created_at DESC);

-- RIG-3: source provenance for per-feed pricing. Amounts stay private.
ALTER TABLE agent_rig.supplier_costs ADD COLUMN IF NOT EXISTS feed_metadata jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE agent_rig.feed_receipts ADD COLUMN IF NOT EXISTS feed_id text NOT NULL DEFAULT '';

-- COST-GUARD (#505): pricing inputs a catalogue import put in attributes
-- (Custom Nation's export_usd) live here, never in agent_rig.products.attributes.
-- Store-only: no shopper query reads this table. The import moves them on
-- write (rigbox.catalogue._product_row), and the two statements below move keys
-- already stored. Both are repeat-safe: copy first, then strip, so a second
-- run finds nothing to move. On conflict the value still in attributes wins,
-- because it came from the newest import (for example a re-import after a
-- rollback). The pattern is rigbox.shopper_attributes.PRIVATE_KEY_PATTERN
-- (a test keeps them identical). PgStore.apply_schema logs how many products
-- still carry a matching key afterwards, so a silent no-op (for example under
-- forced RLS with no tenant set) shows up in the startup log.
-- Reverse (rollback to a build that reads costs from attributes), run by hand:
--   UPDATE agent_rig.products p SET attributes = p.attributes || x.attributes
--   FROM agent_rig.product_private_attributes x
--   WHERE x.tenant_id = p.tenant_id AND x.sku = p.sku
CREATE TABLE IF NOT EXISTS agent_rig.product_private_attributes (
    tenant_id uuid NOT NULL,
    sku text NOT NULL,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, sku),
    FOREIGN KEY (tenant_id, sku) REFERENCES agent_rig.products(tenant_id, sku) ON DELETE CASCADE
);
INSERT INTO agent_rig.product_private_attributes (tenant_id, sku, attributes)
SELECT p.tenant_id, p.sku, (
    SELECT jsonb_object_agg(e.key, e.value) FROM jsonb_each(p.attributes) AS e(key, value)
    WHERE e.key ~* 'cost|duty|margin|landed|markup|profit|wholesale|(^|_)export|(^|_)(buy|purchase|dealer|trade)_?price|(^|_)vat($|_)|(^|_)fx($|_)|exchange_rate|supplier|pricing_rule|^store$|^source_id$|ship.?from|^region$|^seen_at$')
FROM agent_rig.products p
WHERE jsonb_typeof(p.attributes) = 'object'
  AND EXISTS (SELECT 1 FROM jsonb_object_keys(p.attributes) AS k(key)
              WHERE k.key ~* 'cost|duty|margin|landed|markup|profit|wholesale|(^|_)export|(^|_)(buy|purchase|dealer|trade)_?price|(^|_)vat($|_)|(^|_)fx($|_)|exchange_rate|supplier|pricing_rule|^store$|^source_id$|ship.?from|^region$|^seen_at$')
ON CONFLICT (tenant_id, sku) DO UPDATE
    SET attributes = agent_rig.product_private_attributes.attributes || EXCLUDED.attributes, updated_at = now();
UPDATE agent_rig.products p SET attributes = coalesce((
    SELECT jsonb_object_agg(e.key, e.value) FROM jsonb_each(p.attributes) AS e(key, value)
    WHERE NOT e.key ~* 'cost|duty|margin|landed|markup|profit|wholesale|(^|_)export|(^|_)(buy|purchase|dealer|trade)_?price|(^|_)vat($|_)|(^|_)fx($|_)|exchange_rate|supplier|pricing_rule|^store$|^source_id$|ship.?from|^region$|^seen_at$'), '{}'::jsonb)
WHERE jsonb_typeof(p.attributes) = 'object'
  AND EXISTS (SELECT 1 FROM jsonb_object_keys(p.attributes) AS k(key)
              WHERE k.key ~* 'cost|duty|margin|landed|markup|profit|wholesale|(^|_)export|(^|_)(buy|purchase|dealer|trade)_?price|(^|_)vat($|_)|(^|_)fx($|_)|exchange_rate|supplier|pricing_rule|^store$|^source_id$|ship.?from|^region$|^seen_at$');

-- RIG-5: immutable model revisions, reviewed anchors and a per-tenant add-on.
-- A forge result is never a shopper asset until both automatic and human checks pass.
CREATE TABLE IF NOT EXISTS agent_rig.rig_3d_entitlements (
    tenant_id uuid PRIMARY KEY,
    enabled boolean NOT NULL DEFAULT false,
    price_amount numeric CHECK (price_amount >= 0),
    currency text NOT NULL DEFAULT 'USD' CHECK (currency ~ '^[A-Z]{3}$')
);

CREATE TABLE IF NOT EXISTS agent_rig.rig_3d_jobs (
    tenant_id uuid NOT NULL,
    id uuid NOT NULL,
    product_id text NOT NULL,
    request jsonb NOT NULL,
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','running','failed','draft','approved')),
    report jsonb,
    reason text,
    review jsonb,
    reviewed_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    approved_at timestamptz,
    PRIMARY KEY (tenant_id,id),
    FOREIGN KEY (tenant_id,product_id) REFERENCES agent_rig.products (tenant_id,sku),
    CHECK (status <> 'approved' OR (report IS NOT NULL AND review IS NOT NULL AND approved_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS rig_3d_jobs_product
    ON agent_rig.rig_3d_jobs (tenant_id,product_id,status,approved_at DESC);
ALTER TABLE agent_rig.rig_3d_jobs ADD COLUMN IF NOT EXISTS progress jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE agent_rig.fits ADD COLUMN IF NOT EXISTS mount_points jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS agent_rig.rig_3d_kiosks (
    tenant_id uuid NOT NULL,
    id uuid NOT NULL,
    revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
    PRIMARY KEY (tenant_id,id)
);
-- End RIG-5.

-- Prompt2: approved store supplier network. Private inputs never selected as products.
ALTER TABLE agent_rig.products ADD COLUMN IF NOT EXISTS mpn text;
CREATE TABLE IF NOT EXISTS agent_rig.supply_sources (
    tenant_id uuid NOT NULL,
    id text NOT NULL,
    approved boolean NOT NULL DEFAULT false,
    priority integer NOT NULL DEFAULT 0,
    max_offer_age_seconds integer NOT NULL CHECK (max_offer_age_seconds > 0),
    PRIMARY KEY (tenant_id, id)
);
CREATE TABLE IF NOT EXISTS agent_rig.supply_offers (
    tenant_id uuid NOT NULL,
    source_id text NOT NULL,
    sku text NOT NULL,
    gtin text,
    brand text NOT NULL,
    mpn text,
    quantity numeric CHECK (quantity >= 0 AND quantity < 'Infinity'::numeric),
    status text NOT NULL CHECK (status IN ('available', 'unavailable', 'unknown')),
    eta text,
    ship_days integer CHECK (ship_days >= 0),
    region text NOT NULL,
    cost numeric NOT NULL CHECK (cost >= 0 AND cost < 'Infinity'::numeric),
    currency text NOT NULL,
    seen_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, source_id, sku),
    FOREIGN KEY (tenant_id, source_id) REFERENCES agent_rig.supply_sources(tenant_id, id)
);
CREATE TABLE IF NOT EXISTS agent_rig.supply_sku_map (
    tenant_id uuid NOT NULL,
    product_sku text NOT NULL,
    source_id text NOT NULL,
    offer_sku text NOT NULL,
    PRIMARY KEY (tenant_id, product_sku, source_id, offer_sku),
    FOREIGN KEY (tenant_id, product_sku) REFERENCES agent_rig.products(tenant_id, sku),
    FOREIGN KEY (tenant_id, source_id, offer_sku) REFERENCES agent_rig.supply_offers(tenant_id, source_id, sku)
);

-- Located counts mean persisted quote requests, never sales or estimates.
ALTER TABLE agent_rig.widget_counts ADD COLUMN IF NOT EXISTS located_count bigint NOT NULL DEFAULT 0 CHECK (located_count >= 0);
ALTER TABLE agent_rig.widget_counts ADD COLUMN IF NOT EXISTS kept_revenue jsonb NOT NULL DEFAULT '{}'::jsonb;
CREATE TABLE IF NOT EXISTS agent_rig.supply_sale_receipts (
    tenant_id uuid NOT NULL,
    receipt_id text NOT NULL,
    quote_id text NOT NULL,
    product_id text NOT NULL,
    amount numeric NOT NULL CHECK (amount > '-Infinity'::numeric AND amount < 'Infinity'::numeric),
    currency text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    source_ref text NOT NULL,
    received_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, receipt_id)
);
-- Distinguish a feed's zero stock from a deliberately disabled catalogue item.
ALTER TABLE agent_rig.products ADD COLUMN IF NOT EXISTS supply_eligible boolean NOT NULL DEFAULT false;
-- Preserve the existing feed pricing scope identity instead of inventing one.
ALTER TABLE agent_rig.supply_sources ADD COLUMN IF NOT EXISTS provider text NOT NULL DEFAULT '';
ALTER TABLE agent_rig.supply_sources ADD COLUMN IF NOT EXISTS site text NOT NULL DEFAULT '';
ALTER TABLE agent_rig.supply_sources ADD COLUMN IF NOT EXISTS feed_id text NOT NULL DEFAULT '';
-- Blind ship: this source must pack a neutral slip (no own branding or invoice in the parcel).
ALTER TABLE agent_rig.supply_sources ADD COLUMN IF NOT EXISTS neutral_slip_required boolean NOT NULL DEFAULT false;
-- Auto-order: the approved source's real vendor name and order inbox (not its site URL).
ALTER TABLE agent_rig.supply_sources ADD COLUMN IF NOT EXISTS vendor_name text NOT NULL DEFAULT '';
ALTER TABLE agent_rig.supply_sources ADD COLUMN IF NOT EXISTS order_email text NOT NULL DEFAULT '';

-- Locate confirm. The offer page is re-read live before any hold is issued.
ALTER TABLE agent_rig.supply_offers ADD COLUMN IF NOT EXISTS url text;
-- A hold is a short-lived, single-use price snapshot after a live check.
-- private holds the supplier evidence and is never returned to a shopper.
CREATE TABLE IF NOT EXISTS agent_rig.supply_holds (
    tenant_id uuid NOT NULL,
    id text NOT NULL,
    product_id text NOT NULL,
    public jsonb NOT NULL,
    private jsonb NOT NULL,
    expires_at timestamptz NOT NULL,
    used_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS supply_holds_expiry ON agent_rig.supply_holds (expires_at);

-- Locate orders live in Rig's own database (Tervory no longer uses Odoo).
-- One row per paid Locate Accept, keyed by its quote, so a retry never makes
-- a second order. Private store records: never returned to a shopper.
CREATE TABLE IF NOT EXISTS agent_rig.locate_orders (
    tenant_id uuid NOT NULL,
    quote_id text NOT NULL,
    number text NOT NULL,
    state text NOT NULL DEFAULT 'placed' CHECK (state IN ('placed', 'cancelled')),
    product_id text NOT NULL,
    price numeric(14, 2) NOT NULL CHECK (price > 0),
    currency text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    -- Blind ship only: the shopper's delivery address. NULL ships to the store.
    ship_to jsonb,
    cancel_reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, quote_id),
    UNIQUE (tenant_id, number)
);
-- The store's purchase order to the approved source, one per Locate order.
-- unit_cost is the source's live price: supplier-side only.
CREATE TABLE IF NOT EXISTS agent_rig.supplier_purchase_orders (
    tenant_id uuid NOT NULL,
    quote_id text NOT NULL,
    number text NOT NULL,
    state text NOT NULL DEFAULT 'draft' CHECK (state IN ('draft', 'sent', 'cancelled')),
    source_id text NOT NULL,
    vendor_name text NOT NULL DEFAULT '',
    order_email text NOT NULL DEFAULT '',
    item_sku text NOT NULL DEFAULT '',
    item_url text NOT NULL DEFAULT '',
    quantity integer NOT NULL DEFAULT 1 CHECK (quantity > 0),
    unit_cost numeric(14, 2) NOT NULL CHECK (unit_cost > 0),
    currency text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    -- Blind ship: ship straight to this address with a neutral slip if required.
    ship_to jsonb,
    neutral_slip boolean NOT NULL DEFAULT false,
    instructions text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, quote_id),
    UNIQUE (tenant_id, number),
    FOREIGN KEY (tenant_id, quote_id) REFERENCES agent_rig.locate_orders (tenant_id, quote_id)
);
-- Auto-order: the sandbox outbox record once the PO is emailed to the source.
ALTER TABLE agent_rig.supplier_purchase_orders ADD COLUMN IF NOT EXISTS sent jsonb;
ALTER TABLE agent_rig.supplier_purchase_orders ADD COLUMN IF NOT EXISTS sent_at timestamptz;
-- Pay on Accept reconciler: when it last claimed (tried) this quote. Each pass
-- lists the least recently tried first, so quotes that fail every run rotate
-- to the back instead of blocking newer ones.
ALTER TABLE agent_rig.quotes ADD COLUMN IF NOT EXISTS locate_attempt_at timestamptz;

-- Store-owned US checkout. Existing retailers remain in quote mode.
ALTER TABLE agent_rig.retailers ADD COLUMN IF NOT EXISTS commerce jsonb NOT NULL DEFAULT '{}';
CREATE TABLE IF NOT EXISTS agent_rig.commerce_state (
    tenant_id uuid NOT NULL,
    id text NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (tenant_id, id)
);

-- BEGIN GENERATED AGENT MEMORY (hermes/record_schema.py)
CREATE TABLE IF NOT EXISTS agent_rig.memory_records (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    agent text NOT NULL DEFAULT 'rig' CHECK (agent = 'rig'),
    claim text NOT NULL CHECK (length(claim) BETWEEN 1 AND 8000),
    evidence jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(evidence) = 'array'),
    confidence double precision NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
    last_verified timestamptz,
    embed_model text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    tier text NOT NULL DEFAULT 'client' CHECK (tier IN ('client','platform')),
    kind text NOT NULL DEFAULT 'fact',
    meta jsonb NOT NULL DEFAULT '{}',
    seen integer NOT NULL DEFAULT 1,
    CHECK (tier <> 'platform' OR tenant_id = '00000000-0000-0000-0000-000000000001'::uuid)
);
CREATE INDEX IF NOT EXISTS memory_records_scope ON agent_rig.memory_records (tenant_id, tier, created_at, id);
CREATE INDEX IF NOT EXISTS memory_records_encoder ON agent_rig.memory_records (tenant_id, embed_model);
CREATE TABLE IF NOT EXISTS agent_rig.memory_index_jobs (
    row_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    target_model text NOT NULL,
    operation text NOT NULL DEFAULT 'upsert' CHECK (operation IN ('upsert','delete')),
    attempts integer NOT NULL DEFAULT 0,
    run_at timestamptz NOT NULL DEFAULT now(),
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, row_id, target_model, operation)
);
CREATE INDEX IF NOT EXISTS memory_index_jobs_due ON agent_rig.memory_index_jobs (run_at);
CREATE TABLE IF NOT EXISTS agent_rig.memory_publish_policy (
    tenant_id uuid PRIMARY KEY,
    enabled boolean NOT NULL DEFAULT false,
    identifiers jsonb NOT NULL DEFAULT '{}',
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_rig.memory_publish_log (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    actor text NOT NULL,
    happened_at timestamptz NOT NULL DEFAULT now(),
    source_row uuid NOT NULL,
    result text NOT NULL CHECK (result IN ('disabled','missing','refused','published','already_published')),
    published_row uuid
);
-- END GENERATED AGENT MEMORY
