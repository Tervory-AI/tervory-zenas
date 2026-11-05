# 1.1.0

Code change (minor version, per the patch rule in tests/test_skala_eval_contract.py). A smoothed memo is used only when it keeps every original citation, adds no new
citation-like text and keeps the same legal-effect words (not, must, can, met, unclear, numbers and
so on) as the template. Otherwise the template memo is kept unchanged. Eval cases are copied
byte-for-byte from 1.0.1. Existing versions are untouched.
