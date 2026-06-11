from __future__ import annotations

from typing import Any

import requests


class RequestsTransport:
    def __init__(self, *, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        form: dict[str, Any] | None = None,
        timeout: float = 10.0,
    ):
        return self.session.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=json_body,
            data=form,
            timeout=timeout,
            allow_redirects=False,
        )


def response_json(response) -> dict[str, Any]:
    try:
        payload = response.json()
    except (TypeError, ValueError) as exc:
        raise ValueError("Broker returned a non-JSON response.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Broker returned an unexpected JSON response.")
    return payload
