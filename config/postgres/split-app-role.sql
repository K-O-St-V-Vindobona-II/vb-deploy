-- Role separation: the application must not connect as the bootstrap
-- superuser. deploy.yml runs this script on every deploy as that superuser
-- inside the database container, with the password from DATABASE_URL exported
-- as VB_APP_PASSWORD. It is idempotent: it creates the role on a fresh
-- database, applies a changed password and moves ownership of any object that
-- still belongs to another role. By hand:
--   podman exec -i -e VB_APP_PASSWORD vb-api-pg \
--       psql -U vb -d vb -v ON_ERROR_STOP=1 < split-app-role.sql
\getenv app_password VB_APP_PASSWORD

SELECT format('CREATE ROLE vb_app LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB '
              'NOCREATEROLE NOREPLICATION NOBYPASSRLS', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vb_app')
\gexec

SELECT format('ALTER ROLE vb_app PASSWORD %L', :'app_password')
\gexec

SELECT format('ALTER DATABASE %I OWNER TO vb_app', current_database())
\gexec

DO $$
DECLARE
    obj record;
BEGIN
    -- Relations (tables, partitioned tables, views, materialized views,
    -- foreign tables, stand-alone sequences). Sequences that belong to a
    -- column follow their table and cannot be changed on their own.
    FOR obj IN
        SELECT format('ALTER %s %s OWNER TO vb_app',
                      CASE c.relkind
                          WHEN 'r' THEN 'TABLE' WHEN 'p' THEN 'TABLE'
                          WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW'
                          WHEN 'f' THEN 'FOREIGN TABLE' WHEN 'S' THEN 'SEQUENCE'
                      END, c.oid::regclass) AS ddl
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
          AND NOT EXISTS (SELECT 1 FROM pg_depend d
                          WHERE d.objid = c.oid AND d.deptype IN ('a', 'i', 'e'))
    LOOP
        EXECUTE obj.ddl;
    END LOOP;

    -- Enum and domain types.
    FOR obj IN
        SELECT format('ALTER TYPE %s OWNER TO vb_app', t.oid::regtype) AS ddl
        FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = 'public' AND t.typtype IN ('e', 'd')
          AND NOT EXISTS (SELECT 1 FROM pg_depend d
                          WHERE d.objid = t.oid AND d.deptype = 'e')
    LOOP
        EXECUTE obj.ddl;
    END LOOP;

    -- Functions and procedures that do not belong to an extension.
    FOR obj IN
        SELECT format('ALTER %s %s OWNER TO vb_app',
                      CASE p.prokind WHEN 'p' THEN 'PROCEDURE' ELSE 'FUNCTION' END,
                      p.oid::regprocedure) AS ddl
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.prokind IN ('f', 'p')
          AND NOT EXISTS (SELECT 1 FROM pg_depend d
                          WHERE d.objid = p.oid AND d.deptype = 'e')
    LOOP
        EXECUTE obj.ddl;
    END LOOP;
END
$$;
