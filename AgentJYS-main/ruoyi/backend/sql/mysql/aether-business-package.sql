-- Business tenants use a real limited package; only platform tenant 1 is a system tenant.
SET @aether_business_menus = (SELECT JSON_ARRAYAGG(m.id) FROM system_menu m WHERE m.deleted=0 AND (m.id IN(1,60000,60001,60002,60003,60004,60005,60009,60011,60012,60013,60102,60106) OR m.permission IN('system:user:list','system:user:query','system:user:update','system:user:create','system:role:list','system:role:query','system:permission:assign-user-role','system:login-log:query','system:operate-log:query') OR m.component IN('system/user/index','system/role/index','system/loginlog/index','system/operatelog/index')));
INSERT INTO system_tenant_package(id,name,status,remark,menu_ids) SELECT 53001,'Aether Business',0,'Scoped tenant administration and business operations',@aether_business_menus WHERE NOT EXISTS(SELECT 1 FROM system_tenant_package WHERE id=53001);
UPDATE system_tenant SET package_id=53001 WHERE id IN(501,502,503) AND package_id=0;
-- Remove only surplus initial migration grants outside the approved package.
DELETE rm FROM system_role_menu rm JOIN system_tenant_package p ON p.id=53001 WHERE rm.role_id IN(51101,51102) AND NOT JSON_CONTAINS(p.menu_ids,CAST(rm.menu_id AS JSON),'$');
