"""Explicit local/cloud configuration; public identities never come from headers."""

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit


@dataclass(frozen=True)
class RuntimeSettings:
    origin: str
    issuer: str
    connect_issuer: str
    management_url: str
    prefix: str
    secure_cookie: bool

    @property
    def cookie_path(self) -> str:
        return self.prefix + "/"

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "RuntimeSettings":
        mode = config.get("mode", "lab")
        database = urlsplit(config["database_dsn"])
        if config.get("auth_provider") == "ruoyi":
            from aether_platform.auth.ruoyi import validate_ruoyi_config

            authority = validate_ruoyi_config(config)
            origin = config["public_origin"]
            public = urlsplit(origin)
            management = urlsplit(config["management_url"])
            prefix = config.get("path_prefix", "/agent" if mode == "cloud" else "")
            if (
                mode not in {"lab", "cloud"}
                or public.path
                or public.query
                or public.fragment
                or public.username
                or not public.hostname
                or (mode == "cloud" and public.scheme != "https")
                or (
                    mode == "lab"
                    and (
                        public.scheme != "http"
                        or public.hostname not in {"localhost", "127.0.0.1", "::1"}
                    )
                )
                or management.scheme != public.scheme
                or management.username
                or management.hostname != public.hostname
                or (mode == "cloud" and management.netloc != public.netloc)
                or database.scheme != "postgresql"
                or not database.hostname
                or database.fragment
                or (mode == "cloud" and database.hostname in {"localhost", "127.0.0.1", "::1"})
                or (prefix and not re.fullmatch(r"/[a-z][a-z0-9-]*", prefix))
            ):
                raise ValueError("Invalid Ruoyi runtime origins or database")
            return cls(
                origin,
                authority["issuer"],
                authority["base_url"],
                config["management_url"],
                prefix,
                mode == "cloud",
            )
        if config.get("auth_provider", "keycloak") != "keycloak":
            raise ValueError("Unknown identity provider")
        issuer = config["issuer"]
        if mode == "lab":
            expected = "http://localhost:19080/realms/aether-lab"
            if (
                issuer != expected
                or database.hostname != "127.0.0.1"
                or database.port != 19432
                or database.path != "/aether_platform_lab"
                or database.query
                or database.fragment
                or database.scheme != "postgresql"
            ):
                raise ValueError("This session adapter is restricted to the isolated local lab")
            transport = config.get(
                "identity_connect_issuer", expected.replace("localhost", "127.0.0.1")
            )
            if transport not in {expected, expected.replace("localhost", "127.0.0.1")}:
                raise ValueError("Identity transport must address the configured local realm")
            return cls(
                "http://localhost:19010",
                issuer,
                transport,
                "http://localhost:19000/app/default%20workspace/aether-admin",
                "",
                False,
            )
        if mode != "cloud":
            raise ValueError("Unknown runtime mode")
        origin = config["public_origin"]
        public = urlsplit(origin)
        identity = urlsplit(issuer)
        transport = config["identity_connect_issuer"]
        internal = urlsplit(transport)
        management = urlsplit(config["management_url"])
        prefix = config.get("path_prefix", "/agent")
        if (
            public.scheme != "https"
            or not public.hostname
            or public.path
            or public.query
            or public.fragment
            or public.username
            or identity.scheme != "https"
            or identity.netloc != public.netloc
            or identity.query
            or identity.fragment
            or not identity.path.endswith("/realms/aether-lab")
            or internal.scheme not in {"http", "https"}
            or not internal.hostname
            or internal.username
            or internal.query
            or internal.fragment
            or internal.path != identity.path
            or management.scheme != "https"
            or management.netloc != public.netloc
            or database.scheme != "postgresql"
            or not database.hostname
            or database.hostname in {"localhost", "127.0.0.1", "::1"}
            or database.fragment
            or not re.fullmatch(r"/[a-z][a-z0-9-]*", prefix)
        ):
            raise ValueError("Cloud settings require explicit HTTPS origins and internal services")
        return cls(origin, issuer, transport, config["management_url"], prefix, True)
