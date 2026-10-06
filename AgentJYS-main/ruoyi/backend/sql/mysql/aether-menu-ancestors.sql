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
