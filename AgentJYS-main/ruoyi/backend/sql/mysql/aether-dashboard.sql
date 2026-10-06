-- Add the dashboard navigation after aether-platform.sql.
-- This menu grants no new action capability. Global diagnostics still checks the platform role.
SET NAMES utf8mb4;
START TRANSACTION;
INSERT IGNORE INTO system_menu
 (id,name,permission,type,sort,parent_id,path,icon,component,component_name,status)
VALUES
 (60015,'调度监测','aether:ops:read',2,2,60000,'scheduling','ep:data-analysis',
  'aether/scheduling/index','AetherScheduling',0);
INSERT INTO system_role_menu(role_id,menu_id,tenant_id)
SELECT 51001,60015,1 WHERE NOT EXISTS
 (SELECT 1 FROM system_role_menu WHERE role_id=51001 AND menu_id=60015 AND deleted=b'0');
COMMIT;
-- Running deployments must refresh native menu and role-menu caches via the admin API.
