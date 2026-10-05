"""Bounded, revocable token lease shared by requests and long-running P3 calls."""

import time
from collections.abc import Callable
from threading import RLock
from typing import Any

from aether_platform.p3 import P3Error


class SessionTokens:
    def __init__(
        self,
        session: dict[str, Any],
        *,
        active: Callable[[], bool],
        refresh: Callable[[str], dict[str, Any]],
        introspect: Callable[[str], dict[str, Any]],
        version: Callable[[], int],
        clock: Callable[[], float] = time.time,
    ):
        self.session, self.active = session, active
        self.refresh, self.introspect, self.version, self.clock = (
            refresh,
            introspect,
            version,
            clock,
        )
        self.lock = RLock()

    def check(self) -> None:
        if (
            not self.active()
            or self.session["expires"] <= self.clock()
            or self.version() != self.session["version"]
        ):
            raise P3Error("AUTHENTICATION_REQUIRED")

    def get(self) -> str:
        with self.lock:
            self.check()
            session = self.session
            if session["access_expires"] <= self.clock() + 75:
                if not session.get("refresh_token"):
                    raise P3Error("AUTHENTICATION_REQUIRED")
                tokens = self.refresh(session["refresh_token"])
                token = tokens["access_token"]
            else:
                tokens, token = None, session["access_token"]
            proof = self.introspect(token)
            if (
                not proof.get("active")
                or proof.get("sub") != session["subject"]
                or proof.get("client_id") != "platform-bff"
            ):
                raise P3Error("AUTHENTICATION_REQUIRED")
            self.check()
            if tokens is not None:
                session.update(
                    access_token=token,
                    access_expires=self.clock() + int(tokens["expires_in"]),
                    refresh_token=tokens.get("refresh_token", session["refresh_token"]),
                )
            return str(token)
