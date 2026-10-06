-- 这些语句只读取连接状态和数据库结构，不修改业务数据。
SELECT current_database(), current_user, inet_server_addr();
SELECT ssl, version, cipher FROM pg_stat_ssl WHERE pid = pg_backend_pid();
SELECT schema_name FROM information_schema.schemata
WHERE schema_name NOT LIKE 'pg_%' AND schema_name <> 'information_schema'
ORDER BY schema_name;
SELECT table_schema, table_name FROM information_schema.tables
WHERE table_schema IN ('public', 'p3_temporal', 'p3_temporal_visibility', 'p3_local_live_20261003')
ORDER BY table_schema, table_name;

