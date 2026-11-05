# base.normalize

## 1.0.1
- Eval-only patch. Adds 2 `must_refuse` cases the code really refuses: a name that is only legal suffixes, or an article plus a suffix, makes no match key. Every 1.0.0 case is kept byte-for-byte. Runtime code is copied unchanged from 1.0.0. Version 1.0.0 is untouched.

## 1.0.0
- First version. Pure-code normalisation; US-centric address and phone rules.
