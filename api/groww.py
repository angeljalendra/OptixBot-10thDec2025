import time
import hashlib
import hmac
import os
import requests
from typing import Optional, Dict, Any, List, Tuple

class GrowwAPI:
    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None, access_token: Optional[str] = None, base_url: Optional[str] = None, token_endpoint: Optional[str] = None):
        self.api_key = api_key or ""
        self.api_secret = api_secret or ""
        self.access_token = access_token or ""
        self.base_url = base_url or (os.getenv("GROWW_BASE_URL") or "")
        self.token_endpoint = token_endpoint or (os.getenv("GROWW_TOKEN_ENDPOINT") or "")
        self.live_base = os.getenv("GROWW_LIVE_BASE_URL") or "https://api.groww.in/v1"

    def _headers(self, auth_bearer: bool = True) -> Dict[str, str]:
        h = {
            "Accept": "application/json",
            "X-API-VERSION": "1.0",
        }
        if auth_bearer and self.access_token:
            h["Authorization"] = f"Bearer {self.access_token}"
        return h

    def _post(self, url: str, json_body: Dict[str, Any], headers: Optional[Dict[str, str]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        hs = headers or self._headers(auth_bearer=True)
        r = requests.post(url, json=json_body, headers=hs, timeout=timeout)
        try:
            return r.json()
        except Exception:
            return {"status": "FAILURE", "error": "invalid_json"}

    def _get(self, url: str, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        hs = headers or self._headers(auth_bearer=True)
        r = requests.get(url, params=params or {}, headers=hs, timeout=timeout)
        try:
            return r.json()
        except Exception:
            return {"status": "FAILURE", "error": "invalid_json"}

    def _checksum(self, secret: str, timestamp: int) -> str:
        raw = f"{secret}{timestamp}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def set_access_token(self, token: str):
        self.access_token = token or ""

    def generate_access_token_approval(self) -> Optional[str]:
        if not self.api_key or not self.api_secret or not self.token_endpoint:
            return None
        ts = int(time.time())
        checksum = self._checksum(self.api_secret, ts)
        headers = {
            "Authorization": self.api_key,
            "Accept": "application/json",
            "X-API-VERSION": "1.0",
        }
        body = {"key_type": "approval", "checksum": checksum, "timestamp": str(ts)}
        resp = self._post(self.token_endpoint, body, headers=headers)
        if resp.get("status") == "SUCCESS":
            token = resp.get("payload", {}).get("token") or resp.get("token")
            if token:
                self.access_token = token
                return token
        return None

    def generate_access_token_totp(self, totp_code: str) -> Optional[str]:
        if not self.api_key or not self.token_endpoint or not totp_code:
            return None
        headers = {
            "Authorization": self.api_key,
            "Accept": "application/json",
            "X-API-VERSION": "1.0",
        }
        body = {"key_type": "totp", "totp": str(totp_code)}
        resp = self._post(self.token_endpoint, body, headers=headers)
        if resp.get("status") == "SUCCESS":
            token = resp.get("payload", {}).get("token") or resp.get("token")
            if token:
                self.access_token = token
                return token
        return None

    # -------------------- LIVE DATA (per Groww Trade API docs) --------------------
    def get_ltp(self, segment: str, exchange_symbols: Tuple[str, ...]) -> Dict[str, Any]:
        url = f"{self.live_base}/live-data/ltp"
        params: Dict[str, Any] = {"segment": segment}
        params["exchange_symbols"] = list(exchange_symbols)
        return self._get(url, params=params)

    def get_ohlc(self, segment: str, exchange_symbols: Tuple[str, ...]) -> Dict[str, Any]:
        url = f"{self.live_base}/live-data/ohlc"
        params: Dict[str, Any] = {"segment": segment}
        params["exchange_symbols"] = list(exchange_symbols)
        return self._get(url, params=params)

    def get_quote(self, exchange: str, segment: str, trading_symbol: str) -> Dict[str, Any]:
        url = f"{self.live_base}/live-data/quote"
        params = {"exchange": exchange, "segment": segment, "trading_symbol": trading_symbol}
        return self._get(url, params=params)

    # -------------------- Compatibility wrappers used by trading_bot_live --------------------
    def _infer_segment(self, symbols: List[str]) -> str:
        try:
            for s in symbols:
                su = str(s).upper()
                if su.startswith("MCX_") or su.startswith("MCX-") or su.startswith("MCX"):
                    return "COMMODITY"
                if "CE" in su or "PE" in su or "FUT" in su:
                    return "FNO"
            return "CASH"
        except Exception:
            return "CASH"

    def _to_exchange_symbol(self, s: str) -> str:
        su = str(s).strip()
        if ":" in su:
            ex, ts = su.split(":", 1)
            exu = ex.strip().upper()
            return f"{exu}_{ts.strip()}"
        # Default NSE for plain equity names; special-case SENSEX
        if su.upper() == "SENSEX":
            return "BSE_SENSEX"
        return f"NSE_{su}"

    def ltp(self, symbols: List[str]) -> Dict[str, Any]:
        seg = self._infer_segment(symbols)
        ex_syms = tuple(self._to_exchange_symbol(s) for s in symbols)
        raw = self.get_ltp(seg, ex_syms)
        # Normalize into a mapping to satisfy downstream consumers
        payload_map: Dict[str, Any] = {}
        try:
            data = raw.get("data") or raw.get("payload") or raw.get("ltp") or raw
            if isinstance(data, dict):
                for k, v in data.items():
                    payload_map[k] = v if isinstance(v, dict) else {"ltp": v}
            elif isinstance(data, list):
                for item in data:
                    sym = item.get("symbol") or item.get("exchange_symbol") or item.get("trading_symbol")
                    if sym:
                        payload_map[sym] = item
            elif isinstance(data, (int, float)) and len(ex_syms) == 1:
                payload_map[ex_syms[0]] = {"ltp": float(data)}
        except Exception:
            pass
        try:
            for i, exs in enumerate(ex_syms):
                orig = symbols[i]
                val = payload_map.get(exs)
                if val is not None:
                    payload_map[orig] = val
        except Exception:
            pass
        if not payload_map and len(ex_syms) == 1:
            payload_map[ex_syms[0]] = {"ltp": 0.0}
        return {"status": raw.get("status") or "SUCCESS", "payload": payload_map, "ltp": payload_map}

    def ohlc(self, symbols: List[str]) -> Dict[str, Any]:
        seg = self._infer_segment(symbols)
        ex_syms = tuple(self._to_exchange_symbol(s) for s in symbols)
        raw = self.get_ohlc(seg, ex_syms)
        payload_map: Dict[str, Any] = {}
        try:
            data = raw.get("data") or raw.get("payload") or raw.get("ohlc") or raw
            if isinstance(data, dict):
                for k, v in data.items():
                    payload_map[k] = v if isinstance(v, dict) else {"close": v}
            elif isinstance(data, list):
                for item in data:
                    sym = item.get("symbol") or item.get("exchange_symbol") or item.get("trading_symbol")
                    if sym:
                        payload_map[sym] = item
        except Exception:
            pass
        try:
            for i, exs in enumerate(ex_syms):
                orig = symbols[i]
                val = payload_map.get(exs)
                if val is not None:
                    payload_map[orig] = val
        except Exception:
            pass
        return {"status": raw.get("status") or "SUCCESS", "payload": payload_map, "ohlc": payload_map}

    def market_quote(self, keys: List[str]) -> Dict[str, Any]:
        quotes: Dict[str, Any] = {}
        for k in keys:
            try:
                if ":" in k:
                    ex, ts = k.split(":", 1)
                    exu = ex.strip().upper()
                    trading_symbol = ts.strip()
                else:
                    exu = "NSE"
                    trading_symbol = k.strip()
                seg = "CASH"
                resp = self.get_quote(exu, seg, trading_symbol)
                last = resp.get("last_price") or resp.get("ltp") or (resp.get("payload", {}) if isinstance(resp.get("payload"), dict) else {}).get("ltp")
                ohlc = resp.get("ohlc") or {}
                quotes[k] = {"last_price": float(last or 0), "ohlc": ohlc}
            except Exception:
                quotes[k] = {"last_price": 0.0, "ohlc": {}}
        return {"status": "SUCCESS", "payload": {"quotes": quotes}}

    # -------------------- Trading endpoints (base_url expected: https://api.groww.in/trade/v1) --------------------
    def place_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        if not self.base_url:
            return {"status": "FAILURE", "error": "no_base_url"}
        url = f"{self.base_url}/orders/place"
        return self._post(url, order)

    def order_status(self, order_id: str) -> Dict[str, Any]:
        if not self.base_url:
            return {"status": "FAILURE", "error": "no_base_url"}
        url = f"{self.base_url}/orders/status"
        return self._get(url, params={"order_id": order_id})

    def positions(self) -> Dict[str, Any]:
        if not self.base_url:
            return {"status": "FAILURE", "error": "no_base_url"}
        url = f"{self.base_url}/portfolio/positions"
        return self._get(url)
