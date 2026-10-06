-- Managed namespace uniqueness: preserve legacy sample names while protecting every future insert.
-- A stored scope flag avoids MySQL's restriction on generated expressions referencing AUTO_INCREMENT columns.
SET @aether_ddl = IF(EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='system_users' AND column_name='aether_login_managed'), 'SELECT 1', 'ALTER TABLE system_users ADD COLUMN aether_login_managed TINYINT NOT NULL DEFAULT 1');
PREPARE aether_stmt FROM @aether_ddl; EXECUTE aether_stmt; DEALLOCATE PREPARE aether_stmt;
UPDATE system_users SET aether_login_managed=IF(id>=50000,1,0);
SET @aether_ddl = IF(EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='system_users' AND column_name='aether_login_name'), 'SELECT 1', 'ALTER TABLE system_users ADD COLUMN aether_login_name VARCHAR(30) GENERATED ALWAYS AS (IF(aether_login_managed=1 AND deleted=0,username,NULL)) STORED');
PREPARE aether_stmt FROM @aether_ddl; EXECUTE aether_stmt; DEALLOCATE PREPARE aether_stmt;
SET @aether_ddl = IF(EXISTS(SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='system_users' AND index_name='uk_aether_login_name'), 'SELECT 1', 'CREATE UNIQUE INDEX uk_aether_login_name ON system_users(aether_login_name)');
PREPARE aether_stmt FROM @aether_ddl; EXECUTE aether_stmt; DEALLOCATE PREPARE aether_stmt;
ALTER TABLE system_users AUTO_INCREMENT=50009;
