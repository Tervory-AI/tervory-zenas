# video-shop

Internal room for store-owned videos and catalogues, composed onto the existing
Rig host. No provider APIs, paid services or credentials are used for analysis.
The founder explicitly requested these local CPU engines for gate 1 (4 Nov).

`POST /v1/rig/video-shop/admin/videos` accepts exactly one of `video_base64` or a
public HTTPS `url`, plus `rights_confirmed: true`. The store does **not** supply a
transcript. Nonempty supplied transcripts are rejected rather than recorded as
automatic evidence. Catalogue image URLs are fetched automatically through the
existing guarded HTTPS transport (up to 100). Optional `references` map catalogue
SKUs to replacement base64 image bytes when a photo is inaccessible.

FFmpeg extracts mono 16 kHz PCM and samples one frame every five seconds. Pinned
MIT faster-whisper 1.2.1 transcribes locally with the multilingual Whisper tiny
weights, CPU/int8, speech activity detection and timestamped segments. Pinned MIT
OpenCLIP 3.3.0 embeds each frame and catalogue image using ViT-B-32-quickgelu,
CPU/fp32. L2-normalized embeddings match at cosine similarity >= 0.85. Rig search
proposes transcript candidates; a complete product name or SKU must occur in the
recognized text. The union of both sources produces one suggestion per SKU per
five-second window, with both evidence fields when applicable. The generated
transcript is saved in the private draft. All suggestions require store review.

Receipts include model and engine identifiers, SHA-256 for every staged speech/image
asset, CPU device, threshold, sampling interval, detected language, source digest,
image coverage/errors and `provider_calls=0`. `model_calls` counts the local
speech/image analysis stages (two with audio, one without). Silent videos use
vision alone; uncertain speech or images can yield no suggestions. No RGB-distance
fallback or provider fallback exists.

The image build uses `requirements.lock` (Python 3.12, Linux x86_64 CPU wheels,
including the Rig chassis) with required hashes. `models.json` pins immutable
Hub revisions and asset checksums. `prepare_models.py` downloads and verifies
those public assets at build time; serving only loads local paths and verifies
them before first use. It never downloads missing models. Missing/modified assets
fail closed with HTTP 503. Container model files are root-owned/read-only to the
room user. See `THIRD_PARTY_NOTICES.md` for engine and weight licenses.

GET the admin video route to reopen a draft. PUT it with `revision` and `moments`
to replace/edit/approve suggestions. Every moment has `start`, `end`,
`product_id` (catalogue SKU), and boolean `approved`. Stale edits conflict.

No founder-facing product name is assigned. Screen wording follows the Proposed
`docs/figma/video-shop/REGISTER.md` from the parallel design checkout, commit
be87b92e.

## Overlay

Load `/v1/rig/video-shop/widget.js` from the Rig host, then call
`VideoShop.mount(element, {rigBase: 'https://rig-host.example', key: publishableKey,
videoId, storeCartUrl, cartSession, labels: {add: 'Add to cart'}})`.
The key must be a Rig publishable key restricted to the store's origin. Never
embed an admin key. The player fetches authenticated media as a local blob and
revokes it on `destroy()`. Approved moments alone reach the browser. Seeks update
products; inactive/deleted catalogue products are omitted on the next load.

Product detail uses `/v1/rig/products/{sku}`. Adds and checkout use Rig's existing
`/cart/session`, `/cart/add`, and `/cart/checkout`; no separate checkout or payment
service exists here. Listen for `video-shop:cart` to update the store UI and
`video-shop:state` for loading/error states. Labels follow the Proposed register (design commit be87b92e) and remain
overridable. Open store cart stays disabled unless the store supplies its own
HTTPS cart URL. The returned `checkout()` method uses Rig’s existing checkout
route and returns its store-owned checkout URL for the host integration. Call the returned `destroy()` on unmount.

The deployable entry point is `video_shop.video_asgi:app`. Build with `agents/`
as context using this room’s Dockerfile. It composes the existing Rig host and
uses its existing database, keys and cart settings. Persist `DATA_DIR/video-shop`.
Admin GET `/admin/videos` lists recent videos; GET `/admin/videos/{id}/media`
allows private draft playback during review. See `docs/acceptance/video_shop.md`
for limits and the offline acceptance recipe.

Pass the host’s existing Rig `cartSession` so video products join the same cart.
If omitted, the embed creates a Rig shopper session on the first add. The host
can read it with `getSession()` and persist it using the existing store-scoped
cart session policy. `checkout()` uses that same session.
