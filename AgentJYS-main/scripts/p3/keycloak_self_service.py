"""Enable native account self-service on the existing local Keycloak demo.

Incremental configuration only: does not recreate users, organizations or P3 identities.
The SMTP sink is local Mailpit; no outbound email is configured.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path

import httpx
import yaml
from keycloak_organizations_demo import Admin

MAILPIT_IMAGE = "axllent/mailpit:v1.31.3"
REALM_OPTIONS = {
    "registrationAllowed": True,
    "resetPasswordAllowed": True,
    "verifyEmail": True,
    "bruteForceProtected": True,
    "permanentLockout": False,
    "failureFactor": 5,
    "waitIncrementSeconds": 60,
    "maxFailureWaitSeconds": 900,
    "passwordPolicy": "length(12) and upperCase(1) and lowerCase(1) and digits(1)",
    "eventsEnabled": True,
    "eventsExpiration": 604800,
    "adminEventsEnabled": True,
    "adminEventsDetailsEnabled": False,
}
SMTP = {
    "host": "mailpit",
    "port": "1025",
    "from": "no-reply@p3.demo.test",
    "fromDisplayName": "P3 本地身份服务",
    "auth": "false",
    "ssl": "false",
    "starttls": "false",
}


def local_mail_compose():
    return {
        "name": "aether-p3-identity-mail",
        "services": {
            "mailpit": {
                "image": MAILPIT_IMAGE,
                "ports": ["127.0.0.1:18025:8025", "127.0.0.1:11025:1025"],
                "environment": {"MP_MAX_MESSAGES": "200"},
                "networks": ["identity"],
            }
        },
        "networks": {"identity": {"external": True, "name": "aether-p3-identity-demo_default"}},
    }


def apply(directory: Path, start_mail: bool = True):
    directory = directory.resolve()
    repository = Path(__file__).resolve().parents[2]
    if directory.is_relative_to(repository):
        raise ValueError("private configuration must stay outside the repository")
    admin = Admin(directory)
    try:
        realm = admin.get("")
        old_smtp = realm.get("smtpServer") or {}
        if old_smtp and old_smtp != SMTP:
            raise ValueError(
                "Existing SMTP configuration differs; refusing to replace a mail service"
            )
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = directory / "backups" / ("self-service-" + stamp + ".json")
        backup.parent.mkdir(parents=True, exist_ok=True)
        tracked = {*REALM_OPTIONS, "smtpServer"}
        backup.write_text(
            json.dumps({key: realm.get(key) for key in tracked}, ensure_ascii=False, indent=2),
            "utf-8",
        )
        compose_path = directory / "compose.self-service-mail.yaml"
        compose_path.write_text(yaml.safe_dump(local_mail_compose(), sort_keys=False), "utf-8")
        if start_mail:
            subprocess.run(
                ["docker", "compose", "-f", str(compose_path), "up", "-d", "mailpit"], check=True
            )
            deadline = time.monotonic() + 30
            while True:
                try:
                    response = httpx.get("http://127.0.0.1:18025/api/v1/info", timeout=2)
                    if response.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError("Local mail sink did not become ready; realm is unchanged")
                time.sleep(0.5)
        updated = {**realm, **REALM_OPTIONS, "smtpServer": SMTP}
        admin.request("PUT", "", json=updated)
        current = admin.get("")
        if current.get("smtpServer") != SMTP or any(
            current.get(key) != value for key, value in REALM_OPTIONS.items()
        ):
            raise RuntimeError(
                "Keycloak did not retain the requested realm options; inspect the backup"
            )
        # Preserve existing imports and private values; initialization is not rerun.
        import_path = directory / "realm.json"
        if import_path.exists():
            imported = json.loads(import_path.read_text("utf-8"))
            imported.update(REALM_OPTIONS)
            imported["smtpServer"] = SMTP
            import_path.write_text(json.dumps(imported, ensure_ascii=False, indent=2), "utf-8")
        print(
            json.dumps(
                {
                    "status": "configured",
                    "mailbox": "http://127.0.0.1:18025",
                    "backup": str(backup),
                    "registration": True,
                    "password_reset": True,
                    "email_verification": True,
                    "audit_events": True,
                },
                ensure_ascii=False,
            )
        )
    finally:
        admin.http.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument(
        "--no-start-mail", action="store_true", help="Use an already-running local mail sink"
    )
    args = parser.parse_args()
    apply(args.directory, start_mail=not args.no_start_mail)
