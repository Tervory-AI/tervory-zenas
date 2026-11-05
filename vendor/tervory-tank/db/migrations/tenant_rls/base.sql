-- OPT-IN ONLY. Never included by service startup or schema.sql.
-- Run after the founder provisions distinct tenant and BYPASSRLS roles.
BEGIN;
DO $rls$
DECLARE
    target record;
    policy_name text;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'base') THEN
        RAISE EXCEPTION 'Required schema base does not exist';
    END IF;
    FOR target IN
        SELECT c.oid, c.relname, a.atttypid
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid
        WHERE n.nspname = 'base' AND c.relkind IN ('r', 'p')
          AND a.attname = 'tenant_id' AND NOT a.attisdropped
    LOOP
        IF target.atttypid <> 'uuid'::regtype THEN
            RAISE EXCEPTION 'Unsupported tenant_id type on base.%', target.relname;
        END IF;
        EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY', 'base', target.relname);
        EXECUTE format('ALTER TABLE %I.%I FORCE ROW LEVEL SECURITY', 'base', target.relname);
        -- Restrictive boundary prevents another permissive policy widening access.
        FOREACH policy_name IN ARRAY ARRAY['tenant_access', 'tenant_boundary'] LOOP
            IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid = target.oid AND polname = policy_name) THEN
                EXECUTE format($policy$CREATE POLICY %I ON %I.%I AS %s FOR ALL TO PUBLIC
                    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
                    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)$policy$,
                    policy_name, 'base', target.relname,
                    CASE WHEN policy_name = 'tenant_boundary' THEN 'RESTRICTIVE' ELSE 'PERMISSIVE' END);
            ELSE
                EXECUTE format($policy$ALTER POLICY %I ON %I.%I
                    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
                    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)$policy$,
                    policy_name, 'base', target.relname);
            END IF;
        END LOOP;
    END LOOP;
END
$rls$;
COMMIT;
