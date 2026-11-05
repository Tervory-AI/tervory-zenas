# 1.2.0

- South African Rule 19(1), Rule 19(2), and generic `after_service` periods require `service_date` from the return of service.
- Rule 22 pleas and Rule 26 notices of bar require `delivery_date`. Neither service nor delivery dates fall back to an order date or generic trigger. Other generic periods use `trigger`.
- Accept caller-supplied `proclaimed_holidays` as ISO dates, exclude them as public holidays, and list them in the steps. Invalid dates are refused.
- United States trigger handling and court-day counting are unchanged.
- Added eval coverage for service and delivery dates, missing delivery dates, and a proclaimed election holiday. Pending founder approval after staging evals.
