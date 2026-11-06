import json
import logging
import math
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

CAPITAL = 100.0
RISK_PER_TRADE = 0.01
STOP_PCT = 0.05
TAKE_PCT = 0.10
SYMBOLS = ('NVDA', 'TSLA', 'AMD', 'PLTR', 'AAPL', 'QQQ')
INTERVAL_SECONDS = 300
DATA_DIR = Path(os.environ.get('DATA_DIR', '/data'))
STATE = DATA_DIR / 'paper_state_v3.json'
REPORT = DATA_DIR / 'report_v3.json'
NY = ZoneInfo('America/New_York')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')


def atomic_save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')
    temp.replace(path)


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {'starting_capital_usd': CAPITAL, 'realized_pnl_usd': 0.0,
            'position': None, 'trades': [], 'last_entry_bar': {}}


def market_session_now():
    now = datetime.now(NY)
    # US exchange holidays are not encoded here; unavailable/stale quotes block trading.
    return now.weekday() < 5 and (9, 30) <= (now.hour, now.minute) < (16, 0)


def analyze(symbol):
    df = yf.download(symbol, period='10d', interval='5m', auto_adjust=True,
                     progress=False, threads=False)
    if df.empty or len(df) < 55:
        raise ValueError('Insufficient 5-minute data')
    close = df['Close'].squeeze().dropna()
    volume = df['Volume'].squeeze().reindex(close.index)
    if len(close) < 55:
        raise ValueError('Insufficient valid bars')
    last_bar = pd.Timestamp(close.index[-1])
    if last_bar.tzinfo is None:
        raise ValueError('Quote timestamp has no timezone')
    age = datetime.now(timezone.utc) - last_bar.to_pydatetime().astimezone(timezone.utc)
    if not timedelta(minutes=-1) <= age <= timedelta(minutes=20):
        raise ValueError(f'Stale or future quote: {age}')
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    avg_gain, avg_loss = float(gain.iloc[-1]), float(loss.iloc[-1])
    rsi = 100.0 if avg_loss == 0 and avg_gain > 0 else (50.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss))
    price = float(close.iloc[-1])
    if not math.isfinite(price) or price <= 0 or not math.isfinite(rsi):
        raise ValueError('Invalid price/RSI')
    prev = float(close.iloc[-2])
    if prev <= 0 or abs(price / prev - 1) > 0.25:
        raise ValueError('Unusual price jump')
    bearish = (price < float(ema20.iloc[-1]) < float(ema50.iloc[-1])
               and rsi < 45 and float(volume.iloc[-1]) > float(volume.tail(20).mean()))
    return {'symbol': symbol, 'price': round(price, 4), 'rsi': round(rsi, 2),
            'signal': 'SHORT' if bearish else 'WAIT', 'bar_time': last_bar.isoformat()}


def cycle():
    state = load_state()
    now = datetime.now(timezone.utc).isoformat()
    results, errors, events = [], {}, []
    for symbol in SYMBOLS:
        try:
            results.append(analyze(symbol))
        except Exception as exc:
            errors[symbol] = str(exc)
            logging.warning('%s: %s', symbol, exc)
    quotes = {item['symbol']: item for item in results}
    pos = state['position']
    if pos and pos['symbol'] in quotes:
        price = quotes[pos['symbol']]['price']
        if price >= pos['stop_price'] or price <= pos['take_price']:
            reason = 'STOP' if price >= pos['stop_price'] else 'TAKE_PROFIT'
            pnl = (pos['entry_price'] - price) * pos['quantity']
            trade = {**pos, 'exit_price': price, 'exit_time': now,
                     'realized_pnl_usd': round(pnl, 4), 'exit_reason': reason}
            state['trades'].append(trade)
            state['realized_pnl_usd'] = round(state['realized_pnl_usd'] + pnl, 4)
            state['position'] = None
            events.append({'action': 'CLOSE_SHORT', **trade})
    if state['position'] is None:
        equity = CAPITAL + state['realized_pnl_usd']
        candidates = [item for item in results if item['signal'] == 'SHORT'
                      and state['last_entry_bar'].get(item['symbol']) != item['bar_time']]
        if equity > 0 and candidates:
            item = min(candidates, key=lambda x: x['rsi'])
            price = item['price']
            notional = min(equity, equity * RISK_PER_TRADE / STOP_PCT)
            qty = round(notional / price, 8)
            if qty > 0:
                pos = {'symbol': item['symbol'], 'entry_price': price,
                       'quantity': qty, 'entry_time': now,
                       'stop_price': round(price * (1 + STOP_PCT), 4),
                       'take_price': round(price * (1 - TAKE_PCT), 4),
                       'entry_bar_time': item['bar_time']}
                state['position'] = pos
                state['last_entry_bar'][item['symbol']] = item['bar_time']
                events.append({'action': 'OPEN_SHORT', **pos})
    pos = state['position']
    unrealized = None
    if pos and pos['symbol'] in quotes:
        unrealized = round((pos['entry_price'] - quotes[pos['symbol']]['price']) * pos['quantity'], 4)
    report = {'timestamp': now, 'mode': 'PAPER_ONLY_SHORT_V3',
              'starting_capital_usd': CAPITAL,
              'realized_pnl_usd': state['realized_pnl_usd'],
              'unrealized_pnl_usd': unrealized,
              'estimated_equity_usd': round(CAPITAL + state['realized_pnl_usd'] + unrealized, 4) if unrealized is not None else None,
              'position': pos, 'events': events, 'results': results, 'errors': errors,
              'assumptions': '5-minute Yahoo Finance bar simulation; fractional shares; no borrow fees, slippage, commissions, or guaranteed intrabar stops. No broker orders.'}
    atomic_save(STATE, state)
    atomic_save(REPORT, report)
    logging.info('Cycle: %s valid quotes, %s errors, %s events', len(results), len(errors), len(events))


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.info('V3 paper bot started; data directory=%s', DATA_DIR)
    while True:
        try:
            if market_session_now():
                cycle()
            else:
                logging.info('US regular session closed; waiting')
        except Exception:
            logging.exception('Cycle failed; will retry')
        time.sleep(INTERVAL_SECONDS)


if __name__ == '__main__':
    main()
