import json
import math
from datetime import datetime, timezone
from pathlib import Path
import yfinance as yf

CAPITAL = 100.0
RISK_PER_TRADE = 0.01
STOP_PCT = 0.05
TAKE_PCT = 0.10
SYMBOLS = ['NVDA', 'TSLA', 'AMD', 'PLTR', 'AAPL', 'QQQ']
STATE = Path('paper_state.json')
REPORT = Path('report.json')

def save(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')

def analyze(symbol):
    df = yf.download(symbol, period='3mo', interval='1d', auto_adjust=True, progress=False)
    if df.empty or len(df) < 50:
        raise ValueError('Insufficient data')
    close = df['Close'].squeeze()
    volume = df['Volume'].squeeze()
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rsi = 100 - 100 / (1 + gain / loss.replace(0, float('nan')))
    price, rsi_now = float(close.iloc[-1]), float(rsi.iloc[-1])
    if not all(math.isfinite(x) for x in [price, rsi_now]):
        raise ValueError('Invalid price/RSI')
    bar_date = close.index[-1].date()
    if not 0 <= (datetime.now(timezone.utc).date() - bar_date).days <= 5:
        raise ValueError('Stale market data')
    previous = float(close.iloc[-2])
    if previous <= 0 or abs(price / previous - 1) > 0.25:
        raise ValueError('Large daily price jump; verify market data')
    bearish = (price < float(ema20.iloc[-1]) < float(ema50.iloc[-1])
               and rsi_now < 45 and float(volume.iloc[-1]) > float(volume.tail(20).mean()))
    return {'symbol': symbol, 'price': round(price, 4), 'rsi': round(rsi_now, 2),
            'signal': 'SHORT' if bearish else 'WAIT', 'bar_date': str(bar_date)}

def main():
    print('NO PITY. NO REMORSE. NO FEAR. ONLY IN THE NAME OF SEVGİ. FOR THE EMPEROR!', flush=True)
    now = datetime.now(timezone.utc).isoformat()
    state = json.loads(STATE.read_text()) if STATE.exists() else {
        'starting_capital_usd': CAPITAL, 'realized_pnl_usd': 0.0,
        'position': None, 'trades': [], 'last_processed_bar': {}}
    results, errors, events = [], {}, []
    for symbol in SYMBOLS:
        try:
            item = analyze(symbol)
            results.append(item)
            print(item, flush=True)
        except Exception as exc:
            errors[symbol] = str(exc)
            print(f'{symbol}: ERROR - {exc}', flush=True)
    quotes = {item['symbol']: item for item in results}
    pos = state['position']
    if pos and pos['symbol'] in quotes:
        price = quotes[pos['symbol']]['price']
        pnl = (pos['entry_price'] - price) * pos['quantity']
        if price >= pos['stop_price'] or price <= pos['take_price']:
            reason = 'STOP' if price >= pos['stop_price'] else 'TAKE_PROFIT'
            trade = {**pos, 'exit_price': price, 'exit_time': now,
                     'realized_pnl_usd': round(pnl, 4), 'exit_reason': reason}
            state['trades'].append(trade)
            state['realized_pnl_usd'] = round(state['realized_pnl_usd'] + pnl, 4)
            state['position'] = None
            events.append({'action': 'CLOSE_SHORT', **trade})
            print(f'CLOSE PAPER SHORT {pos["symbol"]}: {reason}, PnL={pnl:.2f}', flush=True)
    if state['position'] is None:
        equity = CAPITAL + state['realized_pnl_usd']
        candidates = [item for item in results if item['signal'] == 'SHORT'
                      and state['last_processed_bar'].get(item['symbol']) != item['bar_date']]
        if equity > 0 and candidates:
            item = min(candidates, key=lambda x: x['rsi'])
            price = item['price']
            notional = min(equity, equity * RISK_PER_TRADE / STOP_PCT)
            qty = round(notional / price, 8)
            if qty > 0:
                pos = {'symbol': item['symbol'], 'entry_price': price, 'quantity': qty,
                       'entry_time': now, 'stop_price': round(price * (1 + STOP_PCT), 4),
                       'take_price': round(price * (1 - TAKE_PCT), 4),
                       'entry_bar_date': item['bar_date']}
                state['position'] = pos
                state['last_processed_bar'][item['symbol']] = item['bar_date']
                events.append({'action': 'OPEN_SHORT', **pos})
                print(f'OPEN PAPER SHORT {item["symbol"]}: qty={qty}', flush=True)
    pos = state['position']
    unrealized = None
    if pos and pos['symbol'] in quotes:
        unrealized = round((pos['entry_price'] - quotes[pos['symbol']]['price']) * pos['quantity'], 4)
    report = {'timestamp': now, 'mode': 'PAPER_ONLY', 'starting_capital_usd': CAPITAL,
              'realized_pnl_usd': state['realized_pnl_usd'], 'unrealized_pnl_usd': unrealized,
              'estimated_equity_usd': round(CAPITAL + state['realized_pnl_usd'] + unrealized, 4) if unrealized is not None else None,
              'position': pos, 'events': events, 'results': results, 'errors': errors,
              'assumptions': 'Daily closing-price simulation, fractional shares, no borrow fees, slippage, commissions or intraday stops. No real broker orders.'}
    save(STATE, state)
    save(REPORT, report)
    if not results:
        raise RuntimeError('All market data downloads failed; see report.json')

if __name__ == '__main__':
    main()
