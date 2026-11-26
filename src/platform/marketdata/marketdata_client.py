"""PanWatch ↔ marketdata 接线:DB 配置端口 + 单例 + flag 门控的报价兼容层。

- DbConfigProvider:把 DataSource 表映射成 marketdata 的 SourceConfig(实现 ConfigProvider 端口)。
- get_market_data():进程级单例(无状态 vendor + 现查 DB 的配置端口)。
- md_quote_rows():新包 MarketData.quotes 转 dict,返回 list[dict](与旧 orchestrator 输出同形)。
- md_news()/md_news_by_keyword():新包 MarketData.news/news_by_keyword 转 host NewsItem。
"""

from __future__ import annotations

import logging

from marketdata import MarketData, Quote, SourceConfig

from src.platform.marketdata.enablement import OPTIONAL_MARKETS, disabled_optional_markets

logger = logging.getLogger(__name__)


def _optional_market_disabled(market: str) -> bool:
    """禁用的可选市场直接短路:不触达 vendor、不预热。核心市场零 DB 开销。"""
    if market not in OPTIONAL_MARKETS:
        return False
    from src.platform.persistence.database import SessionLocal

    db = SessionLocal()
    try:
        return market in disabled_optional_markets(db)
    finally:
        db.close()


class DbConfigProvider:
    """ConfigProvider 端口实现:从 DataSource 表按 priority 读某类型的启用源。"""

    def _query_rows(self, datatype: str) -> list:
        from src.platform.persistence.database import SessionLocal
        from src.platform.persistence.models import DataSource

        db = SessionLocal()
        try:
            return (
                db.query(DataSource)
                .filter(DataSource.type == datatype, DataSource.enabled == True)  # noqa: E712
                .order_by(DataSource.priority)
                .all()
            )
        finally:
            db.close()

    def sources_for(self, datatype: str, market: str | None) -> list[SourceConfig]:
        rows = self._query_rows(datatype)
        sources = []
        for row in rows:
            # 保留配置的主备链；单个环境的供应商故障应由 Engine 回退，不能永久排除。
            sources.append(
                SourceConfig(
                    vendor=row.provider,
                    priority=row.priority,
                    enabled=True,
                    config=row.config or {},
                    supports_batch=bool(row.supports_batch),
                )
            )
        return sources


_md: MarketData | None = None


def get_market_data() -> MarketData:
    """进程级单例。vendor 无状态、配置现查 DB,故无需失效钩子。"""
    global _md
    if _md is None:
        _md = MarketData(config=DbConfigProvider())
    return _md


def reset_market_data() -> None:
    """测试或热重载时重置单例。"""
    global _md
    _md = None


def _quote_to_row(q: Quote) -> dict:
    """marketdata.Quote → 旧 orchestrator 同形 dict。"""
    return {
        "symbol": q.symbol,
        "name": q.name,
        "market": q.market,
        "current_price": q.current_price,
        "change_pct": q.change_pct,
        "change_amount": q.change_amount,
        "prev_close": q.prev_close,
        "open_price": q.open_price,
        "high_price": q.high_price,
        "low_price": q.low_price,
        "volume": q.volume,
        "turnover": q.turnover,
        "turnover_rate": q.turnover_rate,
        "volume_ratio": q.volume_ratio,
        "pe_ratio": q.pe_ratio,
        "circulating_market_value": q.circulating_market_value,
        "total_market_value": q.total_market_value,
        "quote_date": q.quote_date.isoformat() if q.quote_date else None,
        "source_timestamp": q.source_timestamp.isoformat() if q.source_timestamp else None,
        "observed_at": q.timestamp.isoformat(),
        "source": q.source,
        "currency": q.currency,
    }


def _yahoo_au_quote_rows(symbols: list[str]) -> list[dict]:
    """Yahoo v8 chart fallback for AU quotes.

    The marketdata lib's quote vendors (tencent/eastmoney/sina/yfinance) have
    no working AU path — yfinance isn't installed and the rest are CN/HK/US
    only — so AU price alerts always came back no_quote. Stdlib-only, one
    request per symbol; failures skip silently.
    """
    import json as _json
    import urllib.request as _rq
    from datetime import datetime as _dt, timezone as _tz

    rows = []
    for sym in symbols:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}.AX?interval=1d&range=5d"
            req = _rq.Request(url, headers={"User-Agent": "panwatch-quote-fallback/1.0"})
            d = _json.loads(_rq.urlopen(req, timeout=10).read().decode("utf-8", "replace"))
            res = (d.get("chart") or {}).get("result") or []
            if not res:
                continue
            meta = res[0].get("meta", {})
            price = meta.get("regularMarketPrice")
            if price is None:
                continue
            prev = meta.get("chartPreviousClose") or meta.get("previousClose")
            now = _dt.now(_tz.utc)
            rows.append({
                "symbol": sym,
                "name": meta.get("symbol", sym),
                "market": "AU",
                "current_price": price,
                "change_pct": ((price - prev) / prev * 100) if prev else None,
                "change_amount": (price - prev) if prev else None,
                "prev_close": prev,
                "open_price": meta.get("regularMarketOpen"),
                "high_price": meta.get("regularMarketDayHigh"),
                "low_price": meta.get("regularMarketDayLow"),
                "volume": meta.get("regularMarketVolume"),
                "turnover": None,
                "turnover_rate": None,
                "volume_ratio": None,
                "pe_ratio": None,
                "circulating_market_value": None,
                "total_market_value": None,
                "quote_date": now.date().isoformat(),
                "source_timestamp": now.isoformat(),
                "observed_at": now.isoformat(),
                "source": "yahoo-fallback",
                "currency": meta.get("currency", "AUD"),
            })
        except Exception:
            continue
    return rows


def md_quote_rows(symbols: list[str], market: str) -> list[dict]:
    """批量报价,返回 list[dict](与旧 orchestrator 输出同形)。

    同步函数;async 调用方用 `await asyncio.to_thread(md_quote_rows, ...)`。
    """
    syms = list(symbols)
    if not syms:
        return []
    if _optional_market_disabled(market):
        return []
    quotes = get_market_data().quotes(syms, market=market)
    rows = [_quote_to_row(q) for q in quotes]
    if str(market).upper() == "AU":
        have = {r.get("symbol") for r in rows}
        missing = [s for s in syms if s not in have]
        if missing:
            rows.extend(_yahoo_au_quote_rows(missing))
    return rows


def _article_to_newsitem(a):
    """marketdata.NewsArticle → host NewsItem(同名字段直拷)。

    lazy import 避免与 news_collector 的模块级循环引用(news_collector 会
    在模块级 import 本模块的 md_news)。
    """
    from src.platform.marketdata.collectors.news_collector import NewsItem

    return NewsItem(
        source=a.source,
        external_id=a.external_id,
        title=a.title,
        content=a.content,
        publish_time=a.publish_time,
        symbols=a.symbols,
        importance=a.importance,
        url=a.url,
    )


def md_news(
    symbols: list[str], since_hours: int = 2, names: dict[str, str] | None = None
) -> list:
    """聚合新闻(个股新闻 + 公告),返回 list[NewsItem](与旧 NewsCollector.fetch_all 同形)。

    host 侧可以用 datetime.now() 做 since 过滤(包内不允许偷偷调 datetime.now(),
    必须由调用方显式传 now)。

    同步函数;async 调用方用 `await asyncio.to_thread(md_news, ...)`。
    """
    from datetime import datetime, timezone

    # 包内 news vendor 的 publish_time 是 aware(UTC);这里的 now 也必须 aware,
    # 否则 since 过滤会 "can't compare offset-naive and offset-aware datetimes"。
    arts = get_market_data().news(
        list(symbols or []), since_hours=since_hours, names=names,
        now=datetime.now(timezone.utc),
    )
    return [_article_to_newsitem(a) for a in arts]


def md_news_by_keyword(keyword: str) -> list:
    """按关键词(行业/主题词)搜中文新闻,返回 list[NewsItem]。同步。"""
    arts = get_market_data().news_by_keyword(keyword)
    return [_article_to_newsitem(a) for a in arts]


def md_stock_data(symbols: list[str], market: str) -> list:
    """返回 list[StockData](旧 AkshareCollector.get_stock_data 同形)。同步。"""
    from src.platform.marketdata.models import MarketCode, StockData

    syms = list(symbols)
    if not syms:
        return []
    if _optional_market_disabled(market):
        return []
    quotes = get_market_data().quotes(syms, market=market)
    return [StockData(
        symbol=q.symbol, name=q.name or "", market=MarketCode(q.market),
        current_price=q.current_price or 0.0, change_pct=q.change_pct or 0.0,
        change_amount=q.change_amount or 0.0, volume=q.volume or 0.0,
        turnover=q.turnover or 0.0, open_price=q.open_price or 0.0,
        high_price=q.high_price or 0.0, low_price=q.low_price or 0.0,
        prev_close=q.prev_close or 0.0) for q in quotes]
