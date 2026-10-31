"""Yahoo chart v8 指数行情直连，不做个股符号/币种识别。"""

from __future__ import annotations

from marketdata.http import market_get, record_error
from marketdata.vendors.yahoo_au import _CHART_URL, _UA


def fetch_yahoo_index_quotes(symbols: list[str], *, proxy=None) -> list[dict]:
    out: list[dict] = []
    for symbol in symbols:
        try:
            payload = market_get(
                _CHART_URL.format(sym=symbol), host_key="query2.finance.yahoo.com",
                params={"interval": "1d", "range": "1d"},
                headers={"User-Agent": _UA},
                timeout=10, retries=2, parse="json", proxy=proxy,
                log_label="Yahoo 指数行情", symbol=symbol,
            )
            results = ((payload or {}).get("chart") or {}).get("result") or []
            if not results:
                raise ValueError("empty chart payload")
            result = results[0]
            meta = result.get("meta") or {}
            closes = (((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
            prices = [float(close) for close in closes if close is not None]
            if not prices:
                raise ValueError("no closing price")
            price = prices[-1]
            previous = meta.get("chartPreviousClose")
            previous = float(previous) if previous is not None else None
            volume = (((result.get("indicators") or {}).get("quote") or [{}])[0].get("volume") or [])
            out.append({
                "symbol": symbol,
                "name": meta.get("shortName") or meta.get("longName"),
                "current_price": price,
                "change_pct": (price - previous) / previous * 100 if previous else None,
                "change_amount": price - previous if previous is not None else None,
                "volume": next((value for value in reversed(volume) if value is not None), None),
                "turnover": None,
            })
        except Exception as exc:
            record_error(f"yahoo_index: {type(exc).__name__}: {exc} for {symbol}")
            continue
    return out
