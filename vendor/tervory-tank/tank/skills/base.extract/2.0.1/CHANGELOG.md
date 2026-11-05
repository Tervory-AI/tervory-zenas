# base.extract

## 2.0.1
- Eval-only patch. Adds 2 `must_refuse` cases the code really refuses: a fixture name that walks out of the package reads nothing, and an instruction in short page text invents no people. Every 2.0.0 case is kept byte-for-byte. Runtime code is copied unchanged from 2.0.0. Version 2.0.0 is untouched.

## 2.0.0
- Pure code. No model call and no task type.
- Each field keeps the first value it finds: structured data, then page structure, then the engine vocabulary.
- Adds method and confidence on every field. Empty fields stay empty.
- Industry words stay in the engine data files. This package only matches them.

## 1.0.0
- First version. See that folder. Not changed here.
