# rig.fit_suggest

## 1.1.0
- The fit list comes from the catalogue. Exact product fits come first, then a family the catalogue already names.
- A part is included only when that list supports it. The model cannot add one.
- The model writes one sentence of explanation, on the existing `rig.fit_suggest` route. Evals pass `model_text` so they do not call it.
- A suggestion that a person has not accepted is left out, and so is a row marked not accepted.
- The sentence stops at a mark that is followed by a space or the end of the text, not inside a number or "e.g.", and it is at most 200 characters.
- If that sentence names a product the fit list does not return, it is replaced with a catalogue line.
- 1.0.0 is unchanged. This version is not approved.

## 1.0.0
- First version. Output is always source suggested and unapproved.
