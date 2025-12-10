import random
import time
from typing import Dict, List, Optional


class KiteAPI:
    def __init__(self, api_key: str = None, api_secret: str = None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.access_token = None
        self.instruments = {}
        self.spot_instruments = {}
        self.symbol_to_token: Dict[str, int] = {}
        self.token_to_symbol: Dict[int, str] = {}
        self.market_data = {}
        self.history: Dict[str, List[float]] = {}
        self._kc = None
        self._kt = None
        try:
            from kiteconnect import KiteConnect, KiteTicker
            self._KiteConnect = KiteConnect
            self._KiteTicker = KiteTicker
        except Exception:
            self._KiteConnect = None
            self._KiteTicker = None

    def set_access_token(self, access_token: str):
        self.access_token = access_token
        if self._KiteConnect and self.api_key:
            try:
                self._kc = self._KiteConnect(api_key=self.api_key)
                self._kc.set_access_token(access_token)
            except Exception:
                self._kc = None

    def fetch_instruments(self):
        if self._kc:
            try:
                nfo = self._kc.instruments('NFO')
                for inst in nfo:
                    key = inst.get('tradingsymbol')
                    if key:
                        self.instruments[key] = inst
                # Also fetch NSE spot instruments
                nse = self._kc.instruments('NSE')
                for inst in nse:
                    tsym = inst.get('tradingsymbol')
                    token = inst.get('instrument_token')
                    if tsym and token:
                        self.spot_instruments[tsym] = inst
                        self.symbol_to_token[tsym.upper()] = token
                        self.token_to_symbol[token] = tsym.upper()
                return self.instruments
            except Exception:
                pass
        self.instruments = {}
        return self.instruments

    def _normalize_underlying(self, name: str) -> Optional[str]:
        try:
            n = (name or '').upper().strip()
            if not n:
                return None
            if n in ('NIFTY 50', 'NIFTY'):  
                return 'NIFTY'
            if n in ('NIFTY BANK', 'BANKNIFTY', 'BANK NIFTY'):
                return 'BANKNIFTY'
            return n
        except Exception:
            return None

    def get_nfo_underlyings(self, limit: int = 50, min_strikes: int = 20, include_indices: bool = True) -> List[str]:
        try:
            if not self.instruments:
                self.fetch_instruments()
            counts: Dict[str, int] = {}
            for inst in self.instruments.values():
                try:
                    exch = str(inst.get('exchange', ''))
                    itype = str(inst.get('instrument_type', '')).upper()
                    name = self._normalize_underlying(inst.get('name'))
                    if not name:
                        continue
                    if 'NFO' not in exch:
                        continue
                    if itype not in ('CE', 'PE', 'FUT'):
                        continue
                    if not include_indices and name in ('NIFTY', 'BANKNIFTY'):
                        continue
                    counts[name] = counts.get(name, 0) + 1
                except Exception:
                    continue
            liquid = [u for u, c in counts.items() if c >= int(min_strikes)]
            liquid.sort(key=lambda x: counts.get(x, 0), reverse=True)
            if limit and limit > 0:
                liquid = liquid[:int(limit)]
            return liquid
        except Exception:
            return []

    def build_dynamic_watchlist(self, limit: int = 50) -> List[str]:
        try:
            return self.get_nfo_underlyings(limit=limit)
        except Exception:
            return []

    def _on_connect(self, ws, response):
        return

    def _on_close(self, ws, code, reason):
        return

    def _on_message(self, ws, message):
        try:
            token = message.get('token') or message.get('instrument_token')
            last = message.get('last_price')
            if token and last:
                self.market_data[token] = {'last_price': last, 'ts': time.time()}
                sym = self.token_to_symbol.get(token)
                if sym:
                    self.history.setdefault(sym, []).append(float(last))
        except Exception:
            pass

    def start_websocket(self):
        if self._KiteTicker and self.access_token and self.api_key:
            try:
                self._kt = self._KiteTicker(api_key=self.api_key, access_token=self.access_token)
                self._kt.on_connect(self._on_connect)
                self._kt.on_close(self._on_close)
                self._kt.on_message(self._on_message)
                self._kt.connect(threaded=True)
            except Exception:
                self._kt = None

    def stop_websocket(self):
        try:
            if self._kt:
                self._kt.close()
        except Exception:
            pass

    def reset_state(self):
        try:
            self.stop_websocket()
        except Exception:
            pass
        try:
            self.market_data = {}
            self.history = {}
        except Exception:
            pass
        try:
            self.start_websocket()
        except Exception:
            pass

    def subscribe(self, tokens):
        try:
            if self._kt and tokens:
                self._kt.subscribe(tokens)
        except Exception:
            pass

    def set_mode(self, mode: str, tokens):
        try:
            if self._kt and tokens:
                self._kt.set_mode(mode, tokens)
        except Exception:
            pass

    def _map_symbol_to_quote_key(self, symbol: str) -> str:
        if symbol.upper() == 'NIFTY':
            return 'NSE:NIFTY 50'
        if symbol.upper() == 'BANKNIFTY':
            return 'NSE:NIFTY BANK'
        return f'NSE:{symbol.upper()}'

    def quote(self, key_or_keys):
        if not self._kc or not self.access_token:
            return {}
        try:
            return self._kc.quote(key_or_keys)
        except Exception:
            return {}

    def get_current_price(self, symbol: str) -> float:
        key = self._map_symbol_to_quote_key(symbol)
        data = self.quote(key)
        try:
            last = data.get(key, {}).get('last_price')
            if last:
                return float(last)
        except Exception:
            pass
        return round(100.0 + random.uniform(-2, 2), 2)

    def get_ltp_by_token(self, token: int) -> float:
        try:
            data = self.market_data.get(token)
            if data and 'last_price' in data:
                return float(data['last_price'])
        except Exception:
            pass
        return 0.0

    def get_price_series(self, symbol: str, window: int = 100) -> List[float]:
        series = self.history.get(symbol.upper(), [])
        if len(series) >= window:
            return series[-window:]
        # Fallback: seed with repeated current price
        cp = self.get_current_price(symbol)
        return [cp] * window

    def get_spot_token(self, symbol: str) -> Optional[int]:
        return self.symbol_to_token.get(symbol.upper())

    def subscribe_spot(self, symbol: str):
        token = self.get_spot_token(symbol)
        if token:
            self.subscribe([token])
            self.set_mode('ltp', [token])

    def find_option_instrument(self, symbol: str, strike: float, option_type: str,
                               expiry_preferred: Optional[str] = None) -> Optional[Dict]:
        try:
            symbol_u = symbol.upper()
            opt_u = 'CE' if option_type.upper() in ('CE', 'CALL') else 'PE'
            # Search all NFO instruments for matching name/strike/type
            candidates = []
            for inst in self.instruments.values():
                name = inst.get('name', '').upper()
                seg = inst.get('segment', '')
                exch = inst.get('exchange', '')
                inst_type = inst.get('instrument_type', '').upper()
                if 'NFO' in exch and inst_type == opt_u and name == symbol_u:
                    s = inst.get('strike')
                    if s is not None and float(s) == float(strike):
                        candidates.append(inst)
            if not candidates:
                return None
            # If expiry preferred, choose exact match; else pick nearest future expiry
            if expiry_preferred:
                for inst in candidates:
                    if str(inst.get('expiry')) == str(expiry_preferred):
                        return inst
            # Choose the earliest expiry in the future by date string
            candidates.sort(key=lambda x: str(x.get('expiry')))
            return candidates[0]
        except Exception:
            return None

    def list_option_expiries(self, symbol: str) -> List[str]:
        try:
            s = symbol.upper()
            exps = set()
            for inst in self.instruments.values():
                if inst.get('name', '').upper() == s and inst.get('instrument_type', '').upper() in ('CE', 'PE'):
                    exps.add(str(inst.get('expiry')))
            return sorted(list(exps))
        except Exception:
            return []

    def get_option_expiry(self, symbol: str, strike: float, option_type: str,
                           expiry_preferred: Optional[str] = None) -> Optional[str]:
        inst = self.find_option_instrument(symbol, strike, option_type, expiry_preferred)
        if inst:
            return str(inst.get('expiry'))
        return None

    def subscribe_option(self, symbol: str, strike: float, option_type: str):
        inst = self.find_option_instrument(symbol, strike, option_type)
        if inst:
            token = inst.get('instrument_token')
            if token:
                self.subscribe([token])
                self.set_mode('ltp', [token])
            return token
        return None

    def login_url(self) -> Optional[str]:
        try:
            if self._kc:
                return self._kc.login_url()
        except Exception:
            return None
        try:
            if self.api_key:
                return f"https://kite.zerodha.com/connect/login?api_key={self.api_key}"
        except Exception:
            pass
        return None

    def generate_session(self, request_token: str) -> Optional[Dict]:
        try:
            if self._kc:
                sess = self._kc.generate_session(request_token, api_secret=self.api_secret)
                self.set_access_token(sess.get('access_token'))
                return sess
        except Exception:
            return None
        return None

    def place_order(self, tradingsymbol: str, exchange: str, transaction_type: str,
                    order_type: str, quantity: int, price: Optional[float] = None,
                    product: str = 'MIS', validity: str = 'DAY', variety: str = 'regular') -> Optional[str]:
        if self._kc:
            try:
                resp = self._kc.place_order(
                    variety=variety,
                    tradingsymbol=tradingsymbol,
                    exchange=exchange,
                    transaction_type=transaction_type,
                    order_type=order_type,
                    quantity=quantity,
                    price=price,
                    product=product,
                    validity=validity
                )
                return resp.get('order_id')
            except Exception:
                return None
        # Fallback mock
        return f"MOCK-{int(time.time())}"

    def order_history(self, order_id: str) -> List[Dict]:
        if self._kc:
            try:
                return self._kc.order_history(order_id)
            except Exception:
                return []
        return []
