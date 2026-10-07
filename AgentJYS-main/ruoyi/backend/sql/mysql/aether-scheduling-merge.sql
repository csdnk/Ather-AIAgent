SET NAMES utf8mb4;
START TRANSACTION;

-- Scheduling monitoring is integrated into tasks. Retain the hidden route for old bookmarks.
UPDATE system_menu SET name='任务与调度', visible=b'1', update_time=NOW()
 WHERE id=60003 AND path='tasks' AND deleted=b'0';
UPDATE system_menu SET visible=b'0', keep_alive=b'0', update_time=NOW()
 WHERE id=60015 AND path='scheduling' AND deleted=b'0';
COMMIT;
