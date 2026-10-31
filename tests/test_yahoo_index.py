from marketdata.client import MarketData
from marketdata.vendors import yahoo_index


def test_yahoo_index_mapping_and_per_symbol_failure(monkeypatch):
    calls = []
    errors = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if "^AORD" in url:
            raise RuntimeError("unavailable")
        return {"chart": {"result": [{
            "meta": {"shortName": "S&P/ASX 200", "chartPreviousClose": 7950, "currency": "AUD"},
            "indicators": {"quote": [{"close": [None, 8000], "volume": [None, 12345]}]},
        }]}}

    monkeypatch.setattr(yahoo_index, "market_get", fake_get)
    monkeypatch.setattr(yahoo_index, "record_error", errors.append)
    result = yahoo_index.fetch_yahoo_index_quotes(["^AXJO", "^AORD"], proxy="http://proxy")

    assert result == [{"symbol": "^AXJO", "name": "S&P/ASX 200", "current_price": 8000,
                       "change_pct": (8000 - 7950) / 7950 * 100, "change_amount": 50,
                       "volume": 12345, "turnover": None}]
    assert calls[0][0].endswith("/^AXJO")
    assert calls[0][1]["params"] == {"interval": "1d", "range": "1d"}
    assert calls[0][1]["proxy"] == "http://proxy"
    assert len(errors) == 1
    assert "^AORD" in errors[0]


def test_client_delegates_proxy(monkeypatch):
    from marketdata.ports import SourceConfig

    class Config:
        def sources_for(self, datatype, market):
            assert (datatype, market) == ("quote", "AU")
            return [SourceConfig(vendor="yahoo_au", config={"proxy": "http://proxy"})]

    monkeypatch.setattr(yahoo_index, "fetch_yahoo_index_quotes",
                        lambda symbols, *, proxy: (symbols, proxy))
    client = MarketData(Config())
    assert client.yahoo_index_quotes(["^AXJO"]) == (["^AXJO"], "http://proxy")
