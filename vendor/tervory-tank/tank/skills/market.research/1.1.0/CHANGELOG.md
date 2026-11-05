# 1.1.0

- Stage code-first series lookups and gap scoring before summary generation.
- Room supplies existing series adapter inputs in `series`. Areas may reference
  their `demand_series_id` and `supply_series_id`; unresolved or duplicate IDs fail.
- Structured citations and arithmetic cannot be replaced by model output.
- Preserve passage-only and missing-source behavior. No paid route changes.
- Depends on the existing bundled market.series_fetch 1.0.0 pure module (same
  resolver as research 1.0.0); supports its existing adapter input contracts.
- FRED observations remain transient and are omitted from returned series.
- Gap calculations now call the versioned market.gap_score 1.1.0 package.
  Invalid comparisons return its rejection reason without a model call.
- Summary prompt forbids numbers absent from the evidence. Code compares numeric
  values (including signed decimals, percentages, scientific notation, grouped
  digits, magnitudes, dates and common English number words), excluding question
  text and object keys. Retry once on unsupported numbers, then raise
  `summary added numbers`. Fixture prose goes through the same check.
- Evals reuse repository gold fixtures for citations, series and gap math.
  `model_text` supplies fixture prose, following the existing skill convention.
  These fixtures are not claimed to be 100 checked real-world examples or a
  live grok-top comparison. Founder approval and shadow evidence remain required.
