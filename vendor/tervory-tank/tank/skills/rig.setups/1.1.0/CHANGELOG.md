# 1.1.0

Minor release for a code change. `pure.py` now passes a product row's `availability` (only `status` and `eta`) through to the shopper result, which Locate (cross-store fulfilment) uses for out-of-stock items. Nothing else in the result changes.

- `pure.py` is byte-identical to `agents/rig/rigbox/logic.py`.
- `run.py` and the eval cases are copied from 1.0.1. rig.search adds one case for a row that carries availability.
- 1.0.0 and 1.0.1 are untouched (approved versions stay immutable). 1.1.0 needs Skala evals and founder approval on its own.
