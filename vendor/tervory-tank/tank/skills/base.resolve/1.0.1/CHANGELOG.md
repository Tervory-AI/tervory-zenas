# base.resolve

## 1.0.1
- Eval-only patch. Adds 3 `must_refuse` cases the code really refuses: a record with no name key, a registry row with no name, and an instruction in the name are all left unmatched. Every 1.0.0 case is kept byte-for-byte. Runtime code is copied unchanged from 1.0.0. Version 1.0.0 is untouched.

## 1.0.0
- First version. RapidFuzz token-set matching within state, city bonus. Google Places / OpenCorporates come as later versions once keys exist.
