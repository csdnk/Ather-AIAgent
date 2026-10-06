-- Aether candidate initialization. Run after upstream native schema import.
-- Caller must bind eight independent BCrypt hashes as @aether_password_hash_50001 through _50008.
-- Existing accounts/passwords are preserved on rerun; no credentials are stored here.
SET NAMES utf8mb4;
CREATE TABLE IF NOT EXISTS aether_notification_delivery (alert_id VARCHAR(128) NOT NULL,state VARCHAR(32) NOT NULL,payload_hash CHAR(64) NOT NULL,message_ids TEXT NULL,created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,completed_at TIMESTAMP NULL,PRIMARY KEY(alert_id,state));
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
START TRANSACTION;
INSERT IGNORE INTO system_tenant(id,name,contact_name,status,package_id,expire_time,account_count) VALUES (501,'Aether Tenant A','Aether',0,53001,'2099-01-01',1000),(502,'Aether Tenant B','Aether',0,53001,'2099-01-01',1000),(503,'Aether Tenant C','Aether',1,53001,'2099-01-01',1000);
UPDATE system_tenant SET name='Aether Platform',contact_name='Aether',contact_mobile='',websites='',expire_time='2099-01-01' WHERE id=1;
INSERT IGNORE INTO system_role(id,name,code,sort,status,type,data_scope,tenant_id) VALUES (51001,'平台管理员','aether_platform_admin',1,0,2,1,1);
INSERT IGNORE INTO system_role(id,name,code,sort,status,type,data_scope,tenant_id) VALUES (51101,'租户管理员','aether_tenant_admin',1,0,2,1,501);
INSERT IGNORE INTO system_role(id,name,code,sort,status,type,data_scope,tenant_id) VALUES (51102,'租户管理员','aether_tenant_admin',1,0,2,1,502);
INSERT IGNORE INTO system_role(id,name,code,sort,status,type,data_scope,tenant_id) VALUES (52101,'业务用户','aether_user',1,0,2,1,501);
INSERT IGNORE INTO system_role(id,name,code,sort,status,type,data_scope,tenant_id) VALUES (52102,'业务用户','aether_user',1,0,2,1,502);
INSERT IGNORE INTO system_role(id,name,code,sort,status,type,data_scope,tenant_id) VALUES (52103,'业务用户','aether_user',1,0,2,1,503);
INSERT INTO system_users(id,username,password,nickname,status,tenant_id,remark) SELECT 50001,'aetherplatform',@aether_password_hash_50001,'平台管理员',0,1,'Controlled Aether migration' WHERE NOT EXISTS(SELECT 1 FROM system_users WHERE id=50001);
INSERT INTO system_user_role(user_id,role_id,tenant_id) SELECT 50001,51001,1 WHERE NOT EXISTS(SELECT 1 FROM system_user_role WHERE user_id=50001 AND role_id=51001 AND deleted=0);
INSERT INTO system_users(id,username,password,nickname,status,tenant_id,remark) SELECT 50002,'aetheradmina',@aether_password_hash_50002,'A 租户管理员',0,501,'Controlled Aether migration' WHERE NOT EXISTS(SELECT 1 FROM system_users WHERE id=50002);
INSERT INTO system_user_role(user_id,role_id,tenant_id) SELECT 50002,51101,501 WHERE NOT EXISTS(SELECT 1 FROM system_user_role WHERE user_id=50002 AND role_id=51101 AND deleted=0);
INSERT INTO system_users(id,username,password,nickname,status,tenant_id,remark) SELECT 50003,'aetheradminb',@aether_password_hash_50003,'B 租户管理员',0,502,'Controlled Aether migration' WHERE NOT EXISTS(SELECT 1 FROM system_users WHERE id=50003);
INSERT INTO system_user_role(user_id,role_id,tenant_id) SELECT 50003,51102,502 WHERE NOT EXISTS(SELECT 1 FROM system_user_role WHERE user_id=50003 AND role_id=51102 AND deleted=0);
INSERT INTO system_users(id,username,password,nickname,status,tenant_id,remark) SELECT 50004,'aetherusera',@aether_password_hash_50004,'A 用户',0,501,'Controlled Aether migration' WHERE NOT EXISTS(SELECT 1 FROM system_users WHERE id=50004);
INSERT INTO system_user_role(user_id,role_id,tenant_id) SELECT 50004,52101,501 WHERE NOT EXISTS(SELECT 1 FROM system_user_role WHERE user_id=50004 AND role_id=52101 AND deleted=0);
INSERT INTO system_users(id,username,password,nickname,status,tenant_id,remark) SELECT 50005,'aetherusera2',@aether_password_hash_50005,'A 用户 2',0,501,'Controlled Aether migration' WHERE NOT EXISTS(SELECT 1 FROM system_users WHERE id=50005);
INSERT INTO system_user_role(user_id,role_id,tenant_id) SELECT 50005,52101,501 WHERE NOT EXISTS(SELECT 1 FROM system_user_role WHERE user_id=50005 AND role_id=52101 AND deleted=0);
INSERT INTO system_users(id,username,password,nickname,status,tenant_id,remark) SELECT 50006,'aetheruserb',@aether_password_hash_50006,'B 用户',0,502,'Controlled Aether migration' WHERE NOT EXISTS(SELECT 1 FROM system_users WHERE id=50006);
INSERT INTO system_user_role(user_id,role_id,tenant_id) SELECT 50006,52102,502 WHERE NOT EXISTS(SELECT 1 FROM system_user_role WHERE user_id=50006 AND role_id=52102 AND deleted=0);
INSERT INTO system_users(id,username,password,nickname,status,tenant_id,remark) SELECT 50007,'aetheruserc',@aether_password_hash_50007,'C 用户',0,503,'Controlled Aether migration' WHERE NOT EXISTS(SELECT 1 FROM system_users WHERE id=50007);
INSERT INTO system_user_role(user_id,role_id,tenant_id) SELECT 50007,52103,503 WHERE NOT EXISTS(SELECT 1 FROM system_user_role WHERE user_id=50007 AND role_id=52103 AND deleted=0);
INSERT INTO system_users(id,username,password,nickname,status,tenant_id,remark) SELECT 50008,'aetherdisabled',@aether_password_hash_50008,'禁用用户',1,501,'Controlled Aether migration' WHERE NOT EXISTS(SELECT 1 FROM system_users WHERE id=50008);
INSERT INTO system_user_role(user_id,role_id,tenant_id) SELECT 50008,52101,501 WHERE NOT EXISTS(SELECT 1 FROM system_user_role WHERE user_id=50008 AND role_id=52101 AND deleted=0);
INSERT IGNORE INTO system_menu(id,name,permission,type,sort,parent_id,path,icon,component,component_name,status) VALUES (60000,'Aether 运维','',1,0,0,'/aether','ep:monitor',NULL,NULL,0);
INSERT IGNORE INTO system_menu(id,name,permission,type,sort,parent_id,path,icon,component,component_name,status) VALUES (60001,'运行总览','aether:ops:read',2,1,60000,'overview','ep:document','aether/overview/index','AetherOverview',0);
INSERT IGNORE INTO system_menu(id,name,permission,type,sort,parent_id,path,icon,component,component_name,status) VALUES (60002,'请求诊断','aether:ops:read',2,2,60000,'requests','ep:document','aether/requests/index','AetherRequests',0);
INSERT IGNORE INTO system_menu(id,name,permission,type,sort,parent_id,path,icon,component,component_name,status) VALUES (60003,'任务与积压','aether:ops:read',2,3,60000,'tasks','ep:document','aether/tasks/index','AetherTasks',0);
INSERT IGNORE INTO system_menu(id,name,permission,type,sort,parent_id,path,icon,component,component_name,status) VALUES (60004,'记忆状态','aether:ops:read',2,4,60000,'memories','ep:document','aether/memories/index','AetherMemories',0);
INSERT IGNORE INTO system_menu(id,name,permission,type,sort,parent_id,path,icon,component,component_name,status) VALUES (60005,'故障与告警','aether:ops:read',2,5,60000,'incidents','ep:document','aether/incidents/index','AetherIncidents',0);
INSERT IGNORE INTO system_menu(id,name,permission,type,sort,parent_id,path,icon,component,component_name,status) VALUES (60006,'告警规则','aether:ops:read',2,6,60000,'rules','ep:document','aether/rules/index','AetherRules',0);
INSERT IGNORE INTO system_menu(id,name,permission,type,sort,parent_id,path,icon,component,component_name,status) VALUES (60007,'配置与发布','aether:ops:read',2,7,60000,'configuration','ep:document','aether/configuration/index','AetherConfiguration',0);
INSERT IGNORE INTO system_menu(id,name,permission,type,sort,parent_id,path,icon,component,component_name,status) VALUES (60008,'备份与恢复','aether:ops:read',2,8,60000,'backups','ep:document','aether/backups/index','AetherBackups',0);
INSERT IGNORE INTO system_menu(id,name,permission,type,sort,parent_id,path,icon,component,component_name,status) VALUES (60009,'容量与用量','aether:ops:read',2,9,60000,'usage','ep:document','aether/usage/index','AetherUsage',0);
INSERT IGNORE INTO system_menu(id,name,permission,type,sort,parent_id,path,icon,component,component_name,status) VALUES (60010,'资源台账','aether:ops:read',2,10,60000,'resources','ep:document','aether/resources/index','AetherResources',0);
INSERT IGNORE INTO system_menu(id,name,permission,type,sort,parent_id,path,icon,component,component_name,status) VALUES (60011,'运营支持','aether:ops:read',2,11,60000,'support','ep:document','aether/support/index','AetherSupport',0);
INSERT IGNORE INTO system_menu(id,name,permission,type,sort,parent_id,path,icon,component,component_name,status) VALUES (60012,'运维审计','aether:ops:read',2,12,60000,'audit','ep:document','aether/audit/index','AetherAudit',0);
INSERT IGNORE INTO system_menu(id,name,permission,type,sort,parent_id,path,icon,component,component_name,status) VALUES (60013,'命令记录','aether:ops:read',2,13,60000,'commands','ep:document','aether/commands/index','AetherCommands',0);
INSERT IGNORE INTO system_menu(id,name,permission,type,sort,parent_id,path,status) VALUES (60101,'tasks 操作','aether:tasks:execute',3,1,60000,'',0);
INSERT IGNORE INTO system_menu(id,name,permission,type,sort,parent_id,path,status) VALUES (60102,'incidents 操作','aether:incidents:execute',3,2,60000,'',0);
INSERT IGNORE INTO system_menu(id,name,permission,type,sort,parent_id,path,status) VALUES (60103,'configuration 操作','aether:configuration:execute',3,3,60000,'',0);
INSERT IGNORE INTO system_menu(id,name,permission,type,sort,parent_id,path,status) VALUES (60104,'backups 操作','aether:backups:execute',3,4,60000,'',0);
INSERT IGNORE INTO system_menu(id,name,permission,type,sort,parent_id,path,status) VALUES (60105,'resources 操作','aether:resources:execute',3,5,60000,'',0);
INSERT IGNORE INTO system_menu(id,name,permission,type,sort,parent_id,path,status) VALUES (60106,'support 操作','aether:support:execute',3,6,60000,'',0);
INSERT IGNORE INTO system_menu(id,name,permission,type,sort,parent_id,path,status) VALUES (60107,'rules 操作','aether:rules:execute',3,7,60000,'',0);
INSERT IGNORE INTO system_menu(id,name,permission,type,sort,parent_id,path,icon,component,component_name,status) VALUES (60014,'配额管理','aether:ops:read',2,14,60000,'quotas','ep:scale-to-original','aether/quotas/index','AetherQuotas',0),(60108,'配额操作','aether:quotas:execute',3,8,60000,'',NULL,NULL,NULL,0);
INSERT INTO system_role_menu(role_id,menu_id,tenant_id) SELECT 51001,m.id,1 FROM system_menu m WHERE m.deleted=0 AND (m.id IN (1,2,60000) OR m.permission LIKE 'system:%' OR m.permission LIKE 'infra:%' OR m.permission LIKE 'aether:%' OR m.component LIKE 'system/%' OR m.component LIKE 'infra/%') AND NOT EXISTS(SELECT 1 FROM system_role_menu rm WHERE rm.role_id=51001 AND rm.menu_id=m.id AND rm.deleted=0);
INSERT INTO system_role_menu(role_id,menu_id,tenant_id) SELECT 51101,m.id,501 FROM system_menu m WHERE m.deleted=0 AND (m.id IN(1,60000,60001,60002,60003,60004,60005,60009,60011,60012,60013,60102,60106) OR m.permission IN('system:user:list','system:user:query','system:user:update','system:user:create','system:role:list','system:role:query','system:permission:assign-user-role','system:login-log:query','system:operate-log:query') OR m.component IN('system/user/index','system/role/index','system/loginlog/index','system/operatelog/index')) AND NOT EXISTS(SELECT 1 FROM system_role_menu rm WHERE rm.role_id=51101 AND rm.menu_id=m.id AND rm.deleted=0);
INSERT INTO system_role_menu(role_id,menu_id,tenant_id) SELECT 51102,m.id,502 FROM system_menu m WHERE m.deleted=0 AND (m.id IN(1,60000,60001,60002,60003,60004,60005,60009,60011,60012,60013,60102,60106) OR m.permission IN('system:user:list','system:user:query','system:user:update','system:user:create','system:role:list','system:role:query','system:permission:assign-user-role','system:login-log:query','system:operate-log:query') OR m.component IN('system/user/index','system/role/index','system/loginlog/index','system/operatelog/index')) AND NOT EXISTS(SELECT 1 FROM system_role_menu rm WHERE rm.role_id=51102 AND rm.menu_id=m.id AND rm.deleted=0);
INSERT IGNORE INTO system_notify_template(id,name,code,nickname,content,type,params,status,remark) VALUES(160001,'Aether 运维告警','aether_ops_alert','Aether','告警 {alert_id}：{metric}，状态 {state}，当前值 {value}，阈值 {threshold}，租户 {tenant_id}',1,'["alert_id","metric","state","value","threshold","tenant_id"]',0,'Native in-app notification, transactional deduplication');
-- Business tenants use a real limited package; only platform tenant 1 is a system tenant.
SET @aether_business_menus = (SELECT JSON_ARRAYAGG(m.id) FROM system_menu m WHERE m.deleted=0 AND (m.id IN(1,60000,60001,60002,60003,60004,60005,60009,60011,60012,60013,60102,60106) OR m.permission IN('system:user:list','system:user:query','system:user:update','system:user:create','system:role:list','system:role:query','system:permission:assign-user-role','system:login-log:query','system:operate-log:query') OR m.component IN('system/user/index','system/role/index','system/loginlog/index','system/operatelog/index')));
INSERT INTO system_tenant_package(id,name,status,remark,menu_ids) SELECT 53001,'Aether Business',0,'Scoped tenant administration and business operations',@aether_business_menus WHERE NOT EXISTS(SELECT 1 FROM system_tenant_package WHERE id=53001);
UPDATE system_tenant SET package_id=53001 WHERE id IN(501,502,503) AND package_id=0;
-- Remove only surplus initial migration grants outside the approved package.
DELETE rm FROM system_role_menu rm JOIN system_tenant_package p ON p.id=53001 WHERE rm.role_id IN(51101,51102) AND NOT JSON_CONTAINS(p.menu_ids,CAST(rm.menu_id AS JSON),'$');
-- Complete navigation ancestry without granting any additional executable permission or page component.
INSERT INTO system_role_menu(role_id,menu_id,tenant_id)
WITH RECURSIVE ancestors AS (
 SELECT rm.role_id,rm.tenant_id,m.parent_id AS id FROM system_role_menu rm JOIN system_menu m ON m.id=rm.menu_id WHERE rm.role_id IN(51001,51101,51102) AND rm.deleted=0 AND m.deleted=0
 UNION DISTINCT
 SELECT a.role_id,a.tenant_id,m.parent_id FROM ancestors a JOIN system_menu m ON m.id=a.id WHERE a.id<>0 AND m.deleted=0
)
SELECT a.role_id,a.id,a.tenant_id FROM ancestors a JOIN system_menu m ON m.id=a.id WHERE a.id<>0 AND m.deleted=0 AND COALESCE(m.permission,'')='' AND COALESCE(m.component,'')='' AND NOT EXISTS(SELECT 1 FROM system_role_menu r WHERE r.role_id=a.role_id AND r.menu_id=a.id AND r.deleted=0);
CREATE TEMPORARY TABLE aether_package_menu_ids(id BIGINT PRIMARY KEY);
INSERT INTO aether_package_menu_ids(id)
WITH RECURSIVE ancestors AS (
 SELECT j.id FROM system_tenant_package p, JSON_TABLE(p.menu_ids,'$[*]' COLUMNS(id BIGINT PATH '$')) j WHERE p.id=53001
 UNION DISTINCT
 SELECT m.parent_id FROM ancestors a JOIN system_menu m ON m.id=a.id WHERE a.id<>0 AND m.deleted=0
)
SELECT a.id FROM ancestors a JOIN system_menu m ON m.id=a.id JOIN system_tenant_package p ON p.id=53001 WHERE a.id<>0 AND m.deleted=0 AND (JSON_CONTAINS(p.menu_ids,CAST(a.id AS JSON),'$') OR (COALESCE(m.permission,'')='' AND COALESCE(m.component,'')=''));
UPDATE system_tenant_package SET menu_ids=(SELECT JSON_ARRAYAGG(id) FROM aether_package_menu_ids) WHERE id=53001;
DROP TEMPORARY TABLE aether_package_menu_ids;
-- Fresh installations use database-backed files, never an upstream cloud bucket.
-- Deployment may SET @aether_file_domain to its public native API base before import.
-- INSERT IGNORE preserves an existing operator-managed adapter and its configuration.
SET @aether_file_domain=COALESCE(NULLIF(@aether_file_domain,''),'http://localhost:48080');
INSERT IGNORE INTO infra_file_config(id,name,storage,remark,master,config,creator,updater,deleted)
SELECT 4,'Aether database files',1,'Owned database storage',
 CASE WHEN EXISTS(SELECT 1 FROM infra_file_config f WHERE f.master=b'1' AND f.deleted=b'0') THEN b'0' ELSE b'1' END,
 JSON_OBJECT('@class','cn.iocoder.yudao.module.infra.framework.file.core.client.db.DBFileClientConfig','domain',@aether_file_domain),
 'aether','aether',b'0';
COMMIT;
