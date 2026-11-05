# ffl.profile

## 1.1.1
- Eval-only patch. Adds 2 `must_refuse` cases the code really refuses: an expiry code with a skipped month letter (I) is not guessed, and site text never sets SOT status or compliance flags. Every 1.1.0 case is kept byte-for-byte. Runtime code is copied unchanged from 1.1.0. Version 1.1.0 is untouched.

## 1.1.0
- Adds `data/site.json`: the name words, dealer-site hosts, and inbox names that `base.find_site` and `base.contacts` accept from the caller. That includes sporting, goods, outdoors, pawn, defense, precision, fishing, bait, tackle, farm, ranch, auction, jewelry, and loan. The profile result is the same as 1.0.0.
- Not approved. 1.0.0 is unchanged.

## 1.0.0
- First version. Licence type/label, expiry from the ATF code (year digit + month letter, I skipped), renewal window ≤120 days, lapsed flag, segment, inventory signal. SOT/violations null by design.
