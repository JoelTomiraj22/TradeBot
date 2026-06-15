"""Lightweight async Binance Futures client for the trade bot."""

import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlencode

import httpx


class BinanceClient:
    def __init__(self, api_key: str, api_secret: str, base_url: str) -> None:
        self._api_key = api_key
        self._api_secret = api_secret.encode()
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"X-MBX-APIKEY": api_key},
            timeout=10.0,
        )

    async def close(self) -> None:
        await self._http.aclose()

    def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = 5000
        query = urlencode(params)
        sig = hmac.new(self._api_secret, query.encode(), hashlib.sha256).hexdigest()
        params["signature"] = sig
        return params

    def _parse(self, response: httpx.Response) -> Any:
        data = response.json()
        if isinstance(data, dict) and "code" in data and int(data["code"]) not in (200, 0):
            raise Exception(f"Binance error {data['code']}: {data.get('msg', '')}")
        if not response.is_success:
            raise Exception(f"HTTP {response.status_code}: {response.text}")
        return data

    async def get(self, path: str, params: dict | None = None) -> Any:
        r = await self._http.get(path, params=params or {})
        return self._parse(r)

    async def get_signed(self, path: str, params: dict | None = None) -> Any:
        r = await self._http.get(path, params=self._sign(params or {}))
        return self._parse(r)

    async def post_signed(self, path: str, params: dict | None = None) -> Any:
        r = await self._http.post(path, data=self._sign(params or {}))
        return self._parse(r)

    async def delete_signed(self, path: str, params: dict | None = None) -> Any:
        r = await self._http.request("DELETE", path, data=self._sign(params or {}))
        return self._parse(r)
