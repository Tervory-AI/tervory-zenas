# base.contacts

## 1.1.0
- Extra generic inbox names come from the caller. The default list is ordinary inboxes (info, sales, support, and the like).
- An address is personal unless its name is on that default list or the caller added it.
- Not approved. 1.0.0 is unchanged.

## 1.0.0
- First version. Generic vs personal inboxes, pattern learning (first, first.last, firstlast, flast, first_last, last, first.l, firstl), pattern-derived addresses at confidence 55, decision maker by role, MX check via DNS-over-HTTPS.
