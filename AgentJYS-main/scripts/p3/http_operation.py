"""Observe durable HTTP admission without repeating the original write."""

import time


def confirmed_request(client, method, path, *, timeout=120, **kwargs):
    response = client.request(method, path, **kwargs)
    if response.status_code != 400 or response.json().get("code") != "REQUEST_IN_PROGRESS":
        response.raise_for_status()
        return response.json()
    location = response.headers.get("Location")
    if not location or not location.startswith("/p3/operations/"):
        raise RuntimeError("pending operation is missing its original result location")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(location.rstrip("/") + "/result")
        if (
            response.status_code == 400 and response.json().get("code") == "REQUEST_IN_PROGRESS"
        ) or response.status_code == 503:
            time.sleep(0.2)
            continue
        response.raise_for_status()
        return response.json()
    raise TimeoutError("original operation is still pending: " + location)
