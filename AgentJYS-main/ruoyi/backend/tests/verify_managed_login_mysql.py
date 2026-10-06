"""Exercise managed-login uniqueness with two real concurrent MySQL sessions.
Run with AETHER_TEST_MYSQL_CONTAINER set to an isolated MySQL Docker container.
The root password is read inside that container, never printed or copied to the host.
"""
import concurrent.futures
import os
from pathlib import Path
import subprocess
import uuid

container = os.environ["AETHER_TEST_MYSQL_CONTAINER"]
database = "aether_login_test_" + uuid.uuid4().hex[:12]

def sql(statement):
    return subprocess.run(["docker", "exec", "-i", container, "sh", "-c",
        'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot --batch --skip-column-names'],
        input=statement.encode(), capture_output=True)

def ok(statement):
    result = sql(statement)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    return result.stdout.decode().strip()

created = False
try:
    ok(f"CREATE DATABASE {database} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
    created = True
    ok(f"USE {database}; CREATE TABLE system_users(id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,username VARCHAR(30) NOT NULL,deleted BIT NOT NULL DEFAULT 0); INSERT INTO system_users(id,username) VALUES(1,'legacy'),(2,'legacy'),(50001,'managed');")
    ddl = (Path(__file__).parents[1] / "sql/mysql/aether-login-uniqueness.sql").read_text(encoding="utf-8")
    ok(f"USE {database}; " + ddl)
    ok(f"USE {database}; " + ddl)  # idempotent migration
    assert ok(f"SELECT COUNT(*) FROM {database}.system_users WHERE username='legacy'") == "2"
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: sql(f"USE {database}; INSERT INTO system_users(username) VALUES('concurrent');"), range(2)))
    assert sorted(result.returncode for result in results) == [0, 1]
    assert any(b"1062" in result.stderr for result in results)
    assert int(ok(f"SELECT id FROM {database}.system_users WHERE username='concurrent'")) >= 50009
    assert sql(f"USE {database}; INSERT INTO system_users(username) VALUES('MANAGED');").returncode != 0
    print("PASS: concurrent duplicate denied, case-insensitive uniqueness, legacy rows preserved, future IDs fenced, migration repeatable")
finally:
    if created:
        ok(f"DROP DATABASE {database};")
