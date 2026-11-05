# Published page assets

Each CSS/JavaScript object here is named `<full SHA256>-rig.css` or
`<full SHA256>-rig.js` and contains exactly the bytes named by that digest.
The page route references these retained objects. Keep published objects when
updating the canonical files: an older page can still request its original URL
across a deployment. Never change the bytes of an existing object.

When changing `../rig.css` or `../rig.js`, add a new snapshot with its full SHA256
in the filename in the same PR. `test_current_assets_have_retained_published_objects`
checks that each current asset has an exact retained object; the deployment
transition tests verify old and new objects remain readable with their original
bytes. Archives are repository/build inputs only; the running app never writes
or creates them. The route restricts filenames to these two assets, confines
resolved paths to this directory, verifies the digest, and only then permits
immutable caching.
