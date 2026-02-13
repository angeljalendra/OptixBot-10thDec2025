import os
import time
import argparse
from datetime import datetime
import numpy as np
import pandas as pd
import yfinance as yf
import requests
import csv
import logging

DEFAULT_FNO = [
    "RELIANCE.NS","TCS.NS","HDFCBANK.NS","ICICIBANK.NS","INFY.NS","LT.NS","SBIN.NS","KOTAKBANK.NS","AXISBANK.NS","ITC.NS",
    "BHARTIARTL.NS","BAJFINANCE.NS","HINDUNILVR.NS","ADANIENT.NS","ADANIPORTS.NS","MARUTI.NS","SUNPHARMA.NS","WIPRO.NS","NTPC.NS","TITAN.NS",
    "ULTRACEMCO.NS","NESTLEIND.NS","ONGC.NS","POWERGRID.NS","JSWSTEEL.NS","TATASTEEL.NS","BPCL.NS","BAJAJFINSV.NS","COALINDIA.NS","HEROMOTOCO.NS",
    "M&M.NS","ASIANPAINT.NS","BRITANNIA.NS","INDUSINDBK.NS","EICHERMOT.NS","HCLTECH.NS","HDFCLIFE.NS","APOLLOHOSP.NS","CIPLA.NS","GRASIM.NS",
    "HINDALCO.NS","DIVISLAB.NS","TECHM.NS","SHREECEM.NS","UPL.NS","IOC.NS","DRREDDY.NS","SBILIFE.NS","BAJAJ-AUTO.NS","DMART.NS"
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("intraday_realtime")

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    gain_ema = pd.Series(gain, index=series.index).ewm(span=period, adjust=False).mean()
    loss_ema = pd.Series(loss, index=series.index).ewm(span=period, adjust=False).mean()
    rs = gain_ema / loss_ema.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def fetch_intraday_yahoo(symbol: str, interval: str = "1m", period: str = "1d") -> pd.DataFrame:
    s = yf.Ticker(symbol)
    df = s.history(period=period, interval=interval, auto_adjust=True)
    if df is None or df.empty:
        return pd.DataFrame()
    return df

def fetch_index_intraday(interval: str = "5m", period: str = "1d") -> pd.DataFrame:
    idx = yf.Ticker("^NSEI")
    df = idx.history(period=period, interval=interval, auto_adjust=True)
    if df is None or df.empty:
        return pd.DataFrame()
    return df

def compute_vwap(series_close: pd.Series, series_high: pd.Series, series_low: pd.Series, series_volume: pd.Series) -> pd.Series:
    tp = (series_high + series_low + series_close) / 3.0
    return (tp * series_volume).cumsum() / series_volume.replace(0, np.nan).cumsum()

def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    if df is None or df.empty or len(df) < period + 1:
        return 0.0
    prev_close = df["Close"].shift(1)
    tr1 = (df["High"] - df["Low"]).abs()
    tr2 = (df["High"] - prev_close).abs()
    tr3 = (df["Low"] - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])

def previous_day_levels(symbol: str) -> tuple:
    try:
        s = yf.Ticker(symbol)
        d = s.history(period="2d", interval="1d", auto_adjust=True)
        if d is None or len(d) < 2:
            return (None, None)
        prev = d.iloc[0]
        return (float(prev["High"]), float(prev["Low"]))
    except Exception:
        return (None, None)

def compute_indicators(df: pd.DataFrame) -> dict:
    if df is None or df.empty or len(df) < 20:
        return {}
    tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
    vwap = (tp * df["Volume"]).cumsum() / df["Volume"].replace(0, np.nan).cumsum()
    lookback = max(10, min(30, len(df)))
    support = df["Low"].rolling(lookback).min().iloc[-1]
    resistance = df["High"].rolling(lookback).max().iloc[-1]
    vol_window = max(10, min(30, len(df) - 1))
    vol_avg = df["Volume"].iloc[-vol_window-1:-1].mean() if vol_window > 0 else df["Volume"].mean()
    vol_ratio = float(df["Volume"].iloc[-1] / vol_avg) if vol_avg and vol_avg > 0 else 0.0
    r = rsi(df["Close"]).iloc[-1]
    ret_window = min(20, len(df) - 1)
    momentum = float((df["Close"].iloc[-1] - df["Close"].iloc[-ret_window]) / df["Close"].iloc[-ret_window] * 100.0) if ret_window > 0 else 0.0
    atr_val = compute_atr(df, 14)
    sigma = float(tp.rolling(lookback).std().iloc[-1]) if lookback > 1 else 0.0
    band_upper = float(vwap.iloc[-1] + sigma)
    band_lower = float(vwap.iloc[-1] - sigma)
    return {
        "current_price": float(df["Close"].iloc[-1]),
        "vwap": float(vwap.iloc[-1]),
        "support": float(support),
        "resistance": float(resistance),
        "volume_ratio": float(vol_ratio),
        "rsi": float(r),
        "momentum_pct": float(momentum),
        "volatility": float((df["High"].iloc[-1] - df["Low"].iloc[-1]) / df["Close"].iloc[-1] * 100.0),
        "atr": float(atr_val),
        "band_upper": float(band_upper),
        "band_lower": float(band_lower)
    }

def detect_signals(ind: dict) -> list:
    if not ind:
        return []
    price = ind["current_price"]
    vwap = ind["vwap"]
    support = ind["support"]
    resistance = ind["resistance"]
    vol_ratio = ind["volume_ratio"]
    r = ind["rsi"]
    bullish = price > vwap and price > resistance * 0.999 and 35 <= r <= 70 and vol_ratio >= 1.8
    bearish = price < vwap and price < support * 1.001 and r >= 50 and vol_ratio >= 1.8
    base_potential = 0.8 + max(0.0, (vol_ratio - 1.0) * 0.4)
    base_potential = float(max(0.6, min(4.0, base_potential)))
    conf = 5.0
    if vol_ratio >= 2.0:
        conf += 1.5
    if abs(ind["momentum_pct"]) >= 0.5:
        conf += 0.7
    if 45 <= r <= 65:
        conf += 0.6
    conf = float(min(9.5, conf))
    atr_buf = max(0.0, min(ind.get("atr", 0.0), price * 0.01))
    # Confluence with previous day levels
    signals_cf = []
    if bullish:
        signals_cf.append(("BULLISH", base_potential + (atr_buf / price * 100.0), conf))
    if bearish:
        signals_cf.append(("BEARISH", base_potential + (atr_buf / price * 100.0), conf))
    return signals_cf

def build_trade_plan(symbol: str, direction: str, potential: float, confidence: float, price: float, ind: dict) -> dict:
    if direction == "BULLISH":
        target = price * (1.0 + potential / 100.0)
        stop = min(ind["vwap"], price - max(ind.get("atr", 0.0) * 0.5, price * 0.008))
    else:
        target = price * (1.0 - potential / 100.0)
        stop = max(ind["vwap"], price + max(ind.get("atr", 0.0) * 0.5, price * 0.008))
    rr = abs((target - price) / (price - stop)) if price != stop else 0.0
    size = "SMALL" if confidence < 7.0 else "MEDIUM"
    return {
        "symbol": symbol.replace(".NS",""),
        "direction": direction,
        "entry": float(price),
        "target": float(target),
        "stop_loss": float(stop),
        "risk_reward": float(rr),
        "confidence": float(confidence),
        "potential": float(potential),
        "position_size": size,
        "holding_period": "INTRADAY",
        "volatility": float(ind["volatility"])
    }

def send_telegram(message: str):
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception:
        pass

def nifty_bias(interval: str = "5m", period: str = "1d") -> tuple:
    df = fetch_index_intraday(interval=interval, period=period)
    if df is None or df.empty or len(df) < 10:
        return ("SIDEWAYS", None)
    vwap = compute_vwap(df["Close"], df["High"], df["Low"], df["Volume"]).iloc[-1]
    price = float(df["Close"].iloc[-1])
    ema20 = df["Close"].ewm(span=20, adjust=False).mean()
    slope = float(ema20.iloc[-1] - ema20.iloc[-5]) if len(ema20) > 5 else 0.0
    if price > vwap and slope > 0:
        return ("BULLISH", float(vwap))
    if price < vwap and slope < 0:
        return ("BEARISH", float(vwap))
    return ("SIDEWAYS", float(vwap))

def try_confirm_ltp(symbol: str) -> float:
    broker = (os.getenv("BROKER") or "").strip().upper()
    base = symbol.replace(".NS", "").upper()
    try:
        if broker == "KITE" or broker == "":
            from trading_bot_live import UltimateFNOTrader as LiveEngine
            engine = LiveEngine(None, initial_strategy_key="SOTD_INTRADAY", request_token_override=None, allow_input=False)
            kite = getattr(engine, "kite", None)
            if not kite:
                logger.warning("Kite not initialized; skipping broker LTP")
                return None
            ins = f"NSE:{base}"
            data = kite.ltp(ins)
            if isinstance(data, dict):
                entry = next(iter(data.values()))
                logger.info(f"Kite LTP for {ins}: {entry.get('last_price')}")
                return float(entry.get("last_price"))
            return None
        return None
    except Exception:
        logger.error("Broker LTP confirm error")
        return None

def multi_tf_trend(symbol: str) -> tuple:
    s = symbol if symbol.endswith(".NS") else f"{symbol}.NS"
    df5 = fetch_intraday_yahoo(s, interval="5m", period="5d")
    df15 = None
    try:
        if df5 is not None and not df5.empty:
            close15 = df5["Close"].resample("15min").last().dropna()
            if close15 is not None and len(close15) > 0:
                df15 = pd.DataFrame({"Close": close15})
    except Exception:
        df15 = fetch_intraday_yahoo(s, interval="15m", period="5d")
    def trend(df):
        if df is None or df.empty or len(df) < 25:
            return "SIDEWAYS"
        ema = df["Close"].ewm(span=20, adjust=False).mean()
        slope = float(ema.iloc[-1] - ema.iloc[-5]) if len(ema) > 5 else 0.0
        price = float(df["Close"].iloc[-1])
        if price > float(ema.iloc[-1]) and slope > 0:
            return "BULLISH"
        if price < float(ema.iloc[-1]) and slope < 0:
            return "BEARISH"
        return "SIDEWAYS"
    return trend(df5), trend(df15)

def is_time_allowed(ts) -> bool:
    try:
        h = ts.hour
        m = ts.minute
        t = h * 60 + m
        start = 9 * 60 + 20
        lunch_start = 12 * 60 + 15
        lunch_end = 13 * 60 + 45
        close_cutoff = 15 * 60 + 10
        if t < start:
            return False
        if lunch_start <= t <= lunch_end:
            return False
        if t >= close_cutoff:
            return False
        return True
    except Exception:
        return True

def score_confidence(ind: dict, direction: str, bias: str, prev_high, prev_low) -> float:
    c = 5.0
    vr = ind.get("volume_ratio", 0.0)
    r = ind.get("rsi", 50.0)
    mom = abs(ind.get("momentum_pct", 0.0))
    price = ind.get("current_price", 0.0)
    vwap = ind.get("vwap", 0.0)
    bu = ind.get("band_upper", price)
    bl = ind.get("band_lower", price)
    if vr >= 2.0:
        c += 1.2
    elif vr >= 1.8:
        c += 0.8
    if 45 <= r <= 65:
        c += 0.6
    if mom >= 0.6:
        c += 0.6
    if direction == "BULLISH":
        if isinstance(prev_high, float) and price > prev_high:
            c += 0.6
        if price > bu:
            c += 0.4
        if bias == "BULLISH" and price > vwap:
            c += 0.4
    else:
        if isinstance(prev_low, float) and price < prev_low:
            c += 0.6
        if price < bl:
            c += 0.4
        if bias == "BEARISH" and price < vwap:
            c += 0.4
    return float(min(9.5, c))

def log_trade_csv(path: str, trade: dict, extra: dict):
    fields = [
        "time","symbol","direction","entry","target","stop_loss","risk_reward","risk_reward_net",
        "confidence","potential","potential_net","bias","volume_ratio","rsi","momentum_pct","vwap"
    ]
    new = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": trade.get("symbol"),
        "direction": trade.get("direction"),
        "entry": trade.get("entry"),
        "target": trade.get("target"),
        "stop_loss": trade.get("stop_loss"),
        "risk_reward": trade.get("risk_reward"),
        "risk_reward_net": trade.get("risk_reward_net"),
        "confidence": trade.get("confidence"),
        "potential": trade.get("potential"),
        "potential_net": trade.get("potential_net"),
        "bias": extra.get("bias"),
        "volume_ratio": extra.get("volume_ratio"),
        "rsi": extra.get("rsi"),
        "momentum_pct": extra.get("momentum_pct"),
        "vwap": extra.get("vwap"),
    }
    exists = os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            w.writeheader()
        w.writerow(new)

def scan_once(symbols: list, interval: str, period: str, max_signals: int, apply_bias: bool = True, slippage_pct: float = 0.1, confirm_ltp: bool = False, time_filter: bool = True, log_path: str = "intraday_trades.csv", tf_align: bool = False):
    bias, nifty_vwap = nifty_bias(interval="5m", period="1d")
    bullish = []
    bearish = []
    for symbol in symbols:
        try:
            df = fetch_intraday_yahoo(symbol, interval=interval, period=period)
            if df is None or df.empty:
                continue
            last_ts = df.index[-1]
            if time_filter and not is_time_allowed(last_ts):
                continue
            ind = compute_indicators(df)
            if not ind or ind["current_price"] < 10:
                continue
            prev_high, prev_low = previous_day_levels(symbol)
            if apply_bias and isinstance(nifty_vwap, float):
                if bias == "BULLISH" and not (ind["current_price"] > ind["vwap"]):
                    continue
                if bias == "BEARISH" and not (ind["current_price"] < ind["vwap"]):
                    continue
            tf5, tf15 = ("SIDEWAYS", "SIDEWAYS")
            if tf_align:
                tf5, tf15 = multi_tf_trend(symbol)
            if bias == "SIDEWAYS" and ind.get("volume_ratio", 0.0) < 2.2:
                continue
            if ind.get("volatility", 0.0) < 0.2:
                continue
            sigs = detect_signals(ind)
            for direction, potential, confidence in sigs:
                price = ind["current_price"]
                if confirm_ltp:
                    ltp = try_confirm_ltp(symbol)
                    if isinstance(ltp, float) and ltp > 0:
                        price = ltp
                confidence = score_confidence(ind, direction, bias, prev_high, prev_low)
                if tf_align:
                    if direction == "BULLISH" and not (tf5 == "BULLISH" and tf15 == "BULLISH"):
                        continue
                    if direction == "BEARISH" and not (tf5 == "BEARISH" and tf15 == "BEARISH"):
                        continue
                plan = build_trade_plan(symbol, direction, potential, confidence, price, ind)
                cost = float(slippage_pct) / 100.0
                if direction == "BULLISH":
                    target_net = plan["target"] * (1.0 - cost)
                    stop_net = plan["stop_loss"] * (1.0 + cost)
                else:
                    target_net = plan["target"] * (1.0 - cost)
                    stop_net = plan["stop_loss"] * (1.0 + cost)
                rr_net = abs((target_net - plan["entry"]) / (plan["entry"] - stop_net)) if plan["entry"] != stop_net else 0.0
                plan["risk_reward_net"] = float(rr_net)
                plan["potential_net"] = float(plan["potential"] - slippage_pct)
                if plan["risk_reward_net"] < 1.2:
                    continue
                log_trade_csv(log_path, plan, {
                    "bias": bias,
                    "volume_ratio": ind.get("volume_ratio"),
                    "rsi": ind.get("rsi"),
                    "momentum_pct": ind.get("momentum_pct"),
                    "vwap": ind.get("vwap")
                })
                if direction == "BULLISH":
                    bullish.append(plan)
                else:
                    bearish.append(plan)
        except Exception:
            continue
    bullish.sort(key=lambda x: (x["confidence"], x["potential"]), reverse=True)
    bearish.sort(key=lambda x: (x["confidence"], x["potential"]), reverse=True)
    return bullish[:max_signals], bearish[:max_signals]

def print_results(bullish: list, bearish: list):
    ts = datetime.now().strftime("%H:%M")
    print(f"INTRADAY SCAN {ts}")
    if bullish:
        print("CALLS")
        for t in bullish:
            print(f"{t['symbol']} entry {t['entry']:.2f} target {t['target']:.2f} stop {t['stop_loss']:.2f} RR {t['risk_reward']:.2f} conf {t['confidence']:.1f} pot {t['potential']:.1f}%")
    if bearish:
        print("PUTS")
        for t in bearish:
            print(f"{t['symbol']} entry {t['entry']:.2f} target {t['target']:.2f} stop {t['stop_loss']:.2f} RR {t['risk_reward']:.2f} conf {t['confidence']:.1f} pot {t['potential']:.1f}%")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", type=str, default="")
    parser.add_argument("--interval", type=str, default="5m")
    parser.add_argument("--period", type=str, default="1d")
    parser.add_argument("--max_symbols", type=int, default=25)
    parser.add_argument("--max_signals", type=int, default=6)
    parser.add_argument("--watch", type=int, default=0)
    parser.add_argument("--telegram", action="store_true")
    parser.add_argument("--no_bias", action="store_true")
    parser.add_argument("--slippage_pct", type=float, default=0.1)
    parser.add_argument("--confirm_ltp", action="store_true")
    parser.add_argument("--no_time_filter", action="store_true")
    parser.add_argument("--log_path", type=str, default="intraday_trades.csv")
    parser.add_argument("--tf_align", action="store_true")
    parser.add_argument("--broker", type=str, default=os.getenv("BROKER") or "")
    parser.add_argument("--continuous", action="store_true")
    args = parser.parse_args()
    if args.broker.strip():
        os.environ["BROKER"] = args.broker.strip().upper()
        if not args.confirm_ltp:
            args.confirm_ltp = True
    if args.continuous and args.watch <= 0:
        args.watch = 15
    if args.symbols.strip():
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
        symbols = [s if s.endswith(".NS") else f"{s}.NS" for s in symbols]
    else:
        symbols = DEFAULT_FNO[:args.max_symbols]
    first = True
    while True:
        b, s = scan_once(symbols, args.interval, args.period, args.max_signals, apply_bias=not args.no_bias, slippage_pct=args.slippage_pct, confirm_ltp=args.confirm_ltp, time_filter=not args.no_time_filter, log_path=args.log_path, tf_align=args.tf_align)
        print_results(b, s)
        if args.telegram:
            if b:
                msg = "🟢 INTRADAY CALLS\n" + "\n".join([f"{t['symbol']} {t['entry']:.1f}->{t['target']:.1f} RR {t['risk_reward_net']:.2f} Conf {t['confidence']:.1f}" for t in b])
                send_telegram(msg)
            if s:
                msg = "🔴 INTRADAY PUTS\n" + "\n".join([f"{t['symbol']} {t['entry']:.1f}->{t['target']:.1f} RR {t['risk_reward_net']:.2f} Conf {t['confidence']:.1f}" for t in s])
                send_telegram(msg)
        if args.watch <= 0:
            break
        delay = args.watch if not first else max(5, args.watch)
        first = False
        time.sleep(delay)

if __name__ == "__main__":
    main()
