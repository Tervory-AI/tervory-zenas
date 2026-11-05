# zenas.scope

## 1.2.1
- Eval-only patch. The 23 criminal-refusal cases are marked `must_refuse: true`, so the candidate-to-staging gate sees passing refusal evidence. Runtime code, rules, areas and policy are copied unchanged from 1.2.0. Version 1.2.0 is untouched.

## 1.2.0
- Add South African criminal-procedure and prosecution terms from the Criminal Procedure Act 51 of 1977 and National Prosecuting Authority Act 32 of 1998. Keep the existing refusal and civil rules.
- Retain all earlier evals; add ten SA criminal and ten SA civil regression cases. No model is needed for these decisions.

## 1.1.0
- Adds "bail", "theft" and "thefts" to the criminal phrases, so "How do I get bail for theft?" is refused with the criminal-lawyer referral instead of falling to the cheap model. Single words match on word boundaries, so bailment, bailee, bailiff and bailout are not caught. Runtime code is copied unchanged from 1.0.0. Version 1.0.0 is untouched.

## 1.0.0
- First version. Rules refuse criminal law, flag regulatory work that carries criminal penalties, and return "not offered yet" for areas that are not on the launch list. The list is areas.json. The cheap model runs only when no rule hits, through task type zenas.scope with cache scope tenant.
