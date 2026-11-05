# 1.2.1

- Code is unchanged from 1.2.0. Only the eval cases change.
- The 12 cases from 1.2.0 are kept byte-identical as the first lines.
- Added refusal evidence. Four `must_refuse` cases cover a South African bail period, a United States sentencing period, an explicit `criminal` flag, and a service-triggered period with no return of service date. 1.2.0 had no `must_refuse` case, so it could never pass the Skala approval gate.
- Added six cases from real public law:
  - Three South African judgments, each stating the date the court counted to:
    - Minister of Rural Development and Land Reform v Poit (LCC205/2021, Land Court, 6 February 2023): a notice of bar served on 12 May 2022 required the affidavit by 19 May 2022.
    - Minister of Rural Development and Land Reform v Jacobs (LCC19/2022, Land Court, 1 March 2023): 15 days from papers filed on 10 May 2022 ran to 31 May 2022.
    - ADCORP Fulfilment Services v Prodigy Human Capital Architects [2023] ZAGPJHC 579: ten days from complete service on 9 September 2021 ran to 23 September 2021.
  - Three United States periods from the rule text in the Boston pool: FRCP 12(a)(1)(A)(i) (21 days), FRCP 59(b) (28 days) and FRCP 72(a) (14 days). Each one ends on a legal holiday named in FRCP 6(a)(6).
