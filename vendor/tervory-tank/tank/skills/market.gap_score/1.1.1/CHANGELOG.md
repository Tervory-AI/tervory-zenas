# market.gap_score

## 1.1.1
- Eval-only patch. Adds 4 `must_refuse` cases the code really refuses: an area with no name, a non-number demand value, an undated point, and areas that are not a list are rejected. Every 1.1.0 case is kept byte-for-byte. Runtime code is copied unchanged from 1.1.0. Version 1.1.0 is untouched.

## 1.1.0
Copy the 1.0.0 arithmetic into this versioned package; 1.0.0 is unchanged.
Reject duplicate area/segment identities, mixed population coverage, nonpositive
population, negative or non-finite inputs, missing demand/supply, and mismatched
latest dates with a clear `status: rejected` and `reason`. Earlier trend snapshots
use only dates shared by the full cohort. Keep histories and results separate for
each (area, segment), expose `by_area_segment[area][segment]`, and retain the
ranked list and existing summary fields. Add top/hot segment identifiers.
Existing valid-fixture arithmetic remains unchanged. Zero size remains zero.
Includes the original parity eval and an eval for each required rejection.
