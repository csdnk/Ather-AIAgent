-- Existing deployments: remove the duplicate overview entry. Homepage stays at /index.
-- Apply the native menu delete API online to invalidate menu/permission caches.
SET NAMES utf8mb4;
START TRANSACTION;
UPDATE system_menu SET deleted=b'1' WHERE id=60001 AND component='aether/overview/index';
UPDATE system_role_menu SET deleted=b'1' WHERE menu_id=60001;
COMMIT;
