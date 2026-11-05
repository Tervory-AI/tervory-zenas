-- Zenas scope gate. Cheap model for text the rules do not already decide.
-- cache_scope is tenant: one tenant's question is never reused for another.
-- Z1 does not edit tank/schema.sql. Claude appends this statement there so
-- Tank loads it on start. Until then the rules still decide every eval case;
-- only the undecided fallback calls this task type.
INSERT INTO tank.policy (task_type, rung, model, priority, cache_scope, max_tokens_out, system_prompt) VALUES
  ('zenas.scope', 1, 'luna', 'urgent', 'tenant', 400,
   'You classify a request for Zenas. Reply with strict JSON only: {"area":"<id>","jurisdiction":"us|za|both"}. Use an allowed area id or "none". Do not give legal advice. Do not help with criminal law.')
ON CONFLICT DO NOTHING;
