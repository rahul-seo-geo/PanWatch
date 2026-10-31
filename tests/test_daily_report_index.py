"""daily_report 大盘指数取数测试。

覆盖:
- CN 市场走 marketdata 新包 index_quotes,产出正确的 IndexData 列表。
- AU 市场走 Yahoo；HK/US 市场返回空 list，且不调用 md。
"""

from __future__ import annotations

import asyncio

from src.modules.automation import daily_report
from src.platform.marketdata.models import IndexData, MarketCode


def _fake_index_items() -> list[dict]:
    return [
        {
            "symbol": "000001",
            "name": "上证指数",
            "current_price": 3123.45,
            "change_pct": 1.23,
            "change_amount": 12.3,
            "prev_close": 3111.15,
            "volume": 100000.0,
            "turnover": 999999999.0,
        },
        {
            "symbol": "399001",
            "name": "深证成指",
            "current_price": 10234.5,
            "change_pct": -0.5,
            "change_amount": -51.2,
            "prev_close": 10285.7,
            "volume": 200000.0,
            "turnover": 888888888.0,
        },
    ]


class _FakeMarketData:
    def __init__(self, items: list[dict]):
        self.items = items
        self.calls: list[list[str]] = []

    def index_quotes(self, tencent_symbols: list[str]) -> list[dict]:
        self.calls.append(list(tencent_symbols))
        return self.items

    def yahoo_index_quotes(self, symbols: list[str]) -> list[dict]:
        self.calls.append(list(symbols))
        return self.items


def test_uses_marketdata_index_quotes(monkeypatch):
    """CN 指数走 md.index_quotes,产出正确的 IndexData 列表。"""
    fake_md = _FakeMarketData(_fake_index_items())
    monkeypatch.setattr(daily_report, "get_market_data", lambda: fake_md)

    agent = daily_report.DailyReportAgent()
    indices = asyncio.run(agent._fetch_index_for_market(MarketCode.CN))

    assert fake_md.calls == [["sh000001", "sz399001", "sz399006"]]
    assert len(indices) == 2
    assert all(isinstance(i, IndexData) for i in indices)
    assert indices[0].symbol == "000001"
    assert indices[0].name == "上证指数"
    assert indices[0].market == MarketCode.CN
    assert indices[0].current_price == 3123.45
    assert indices[0].change_pct == 1.23
    assert indices[0].change_amount == 12.3
    assert indices[0].volume == 100000.0
    assert indices[0].turnover == 999999999.0


def test_hk_us_markets_return_empty(monkeypatch):
    """HK/US 市场返回空 list，且不调用 md。"""
    fake_md = _FakeMarketData(_fake_index_items())
    monkeypatch.setattr(daily_report, "get_market_data", lambda: fake_md)

    agent = daily_report.DailyReportAgent()
    indices_hk = asyncio.run(agent._fetch_index_for_market(MarketCode.HK))
    indices_us = asyncio.run(agent._fetch_index_for_market(MarketCode.US))

    assert indices_hk == []
    assert indices_us == []
    assert fake_md.calls == []


def test_au_indices_and_fallback_name(monkeypatch):
    items = [
        {"symbol": "^AXJO", "name": "Yahoo ASX", "current_price": 8000,
         "change_pct": 1.0, "change_amount": 80, "volume": 1234, "turnover": None},
        {"symbol": "^AORD", "name": None, "current_price": 8100,
         "change_pct": -1.0, "change_amount": -81, "volume": None, "turnover": None},
    ]
    fake_md = _FakeMarketData(items)
    monkeypatch.setattr(daily_report, "get_market_data", lambda: fake_md)

    indices = asyncio.run(daily_report.DailyReportAgent()._fetch_index_for_market(MarketCode.AU))

    assert fake_md.calls == [["^AXJO", "^AORD", "^AXKO"]]
    assert [idx.name for idx in indices] == ["Yahoo ASX", "All Ordinaries"]
    assert all(idx.market == MarketCode.AU for idx in indices)
    assert indices[0].turnover is None


def test_au_index_malformed_item_does_not_drop_valid_indices(monkeypatch):
    items = [
        {"symbol": "^AXJO", "name": "Yahoo ASX", "current_price": 8000},
        {"symbol": "^AORD", "name": None},
        {"symbol": "^UNKNOWN", "name": "Yahoo Other", "current_price": 9000},
        {"symbol": "^MISSING", "current_price": 9100},
        None,
    ]
    fake_md = _FakeMarketData(items)
    monkeypatch.setattr(daily_report, "get_market_data", lambda: fake_md)

    indices = asyncio.run(daily_report.DailyReportAgent()._fetch_index_for_market(MarketCode.AU))

    assert [(idx.symbol, idx.name) for idx in indices] == [
        ("^AXJO", "Yahoo ASX"), ("^UNKNOWN", "Yahoo Other"),
    ]
    assert all(idx.market == MarketCode.AU for idx in indices)


def test_index_prompt_uses_volume_when_turnover_absent():
    from types import SimpleNamespace
    from src.modules.automation.base import AgentContext

    agent = daily_report.DailyReportAgent()
    context = AgentContext(ai_client=None, notifier=None, config=SimpleNamespace(watchlist=[]))
    cn = IndexData(symbol="000001", name="Shanghai", market=MarketCode.CN,
                   current_price=3000, change_pct=1, change_amount=30,
                   volume=100, turnover=300000000)
    au = IndexData(symbol="^AXJO", name="ASX", market=MarketCode.AU,
                   current_price=8000, change_pct=1, change_amount=80,
                   volume=12345, turnover=None)
    _, content = agent.build_prompt({"indices": [cn, au], "signal_packs": {}}, context)
    assert "成交额:3亿" in content
    assert "ASX: 8000.00 ↑ +1.00% Volume:12,345" in content
    assert "成交额:0亿" not in content
