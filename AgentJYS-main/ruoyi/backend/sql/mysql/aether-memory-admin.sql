-- Apply after Aether platform initialization. Preserve existing account and role data.
SET NAMES utf8mb4;
START TRANSACTION;
UPDATE system_menu SET name='记忆管理',permission='aether:content:read',visible=b'1',
 path='memories',component='aether/memories/index',component_name='AetherMemories',sort=4
 WHERE id=60004 AND deleted=b'0';
INSERT IGNORE INTO system_menu
 (id,name,permission,type,sort,parent_id,path,icon,component,component_name,status)
VALUES
 (60017,'召回管理','aether:content:read',2,5,60000,'recalls','ep:search','aether/recalls/index','AetherRecalls',0),
 (60120,'提交记忆与召回操作','aether:memories:execute',3,20,60000,'',NULL,NULL,NULL,0),
 (60121,'新增记忆','aether:memory:create',3,1,60004,'',NULL,NULL,NULL,0),
 (60122,'修改与归档记忆','aether:memory:update',3,2,60004,'',NULL,NULL,NULL,0),
 (60123,'删除记忆','aether:memory:delete',3,3,60004,'',NULL,NULL,NULL,0),
 (60124,'管理召回方案','aether:recall:manage',3,1,60017,'',NULL,NULL,NULL,0),
 (60125,'执行召回','aether:recall:execute',3,2,60017,'',NULL,NULL,NULL,0);
-- Extend only the existing Aether business package; no global package replacement.
CREATE TEMPORARY TABLE aether_memory_menu_ids(id BIGINT PRIMARY KEY);
INSERT IGNORE INTO aether_memory_menu_ids
 SELECT j.id FROM system_tenant_package p,
 JSON_TABLE(p.menu_ids,'$[*]' COLUMNS(id BIGINT PATH '$')) j WHERE p.id=53001;
INSERT IGNORE INTO aether_memory_menu_ids VALUES
 (60000),(60004),(60017),(60113),(60120),(60121),(60122),(60123),(60124),(60125);
UPDATE system_tenant_package SET menu_ids=(SELECT JSON_ARRAYAGG(id) FROM aether_memory_menu_ids)
 WHERE id=53001;
DROP TEMPORARY TABLE aether_memory_menu_ids;
-- Only existing named platform/tenant administrator roles receive these capabilities.
INSERT INTO system_role_menu(role_id,menu_id,tenant_id)
 SELECT r.id,m.id,r.tenant_id FROM system_role r CROSS JOIN system_menu m
 WHERE r.deleted=b'0' AND r.id IN(51001,51101,51102)
 AND r.code IN('aether_platform_admin','aether_tenant_admin') AND m.deleted=b'0'
 AND m.id IN(60000,60004,60017,60113,60120,60121,60122,60123,60124,60125)
 AND NOT EXISTS(SELECT 1 FROM system_role_menu rm
 WHERE rm.role_id=r.id AND rm.menu_id=m.id AND rm.deleted=b'0');
COMMIT;
-- Refresh native menu/role-menu caches before validating new permissions.
