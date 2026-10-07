-- Add the dashboard navigation after aether-platform.sql.
-- This menu grants no new action capability. Global diagnostics still checks the platform role.
SET NAMES utf8mb4;
START TRANSACTION;
INSERT IGNORE INTO system_menu
 (id,name,permission,type,sort,parent_id,path,icon,component,component_name,status)
VALUES
 (60015,'调度监测','aether:ops:read',2,2,60000,'scheduling','ep:data-analysis',
  'aether/scheduling/index','AetherScheduling',0),
 (60016,'冷热分层调度','aether:ops:read',2,3,60000,'placement','ep:sort',
  'aether/placement/index','AetherPlacement',0);
INSERT INTO system_role_menu(role_id,menu_id,tenant_id)
SELECT 51001,60015,1 WHERE NOT EXISTS
 (SELECT 1 FROM system_role_menu WHERE role_id=51001 AND menu_id=60015 AND deleted=b'0');
INSERT INTO system_role_menu(role_id,menu_id,tenant_id)
SELECT 51001,60016,1 WHERE NOT EXISTS
 (SELECT 1 FROM system_role_menu WHERE role_id=51001 AND menu_id=60016 AND deleted=b'0');

-- Scheduling monitoring is integrated into tasks. Retain the hidden route for old bookmarks.
UPDATE system_menu SET name='任务与调度', visible=b'1', update_time=NOW()
 WHERE id=60003 AND path='tasks' AND deleted=b'0';
UPDATE system_menu SET visible=b'0', keep_alive=b'0', update_time=NOW()
 WHERE id=60015 AND path='scheduling' AND deleted=b'0';
COMMIT;
-- Running deployments must refresh native menu and role-menu caches via the admin API.
