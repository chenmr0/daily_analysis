"""Characterization tests for AlphaEvo@712e60b3, with synthetic calendar rows.

These assertions document the existing BUG, not desired corrected behavior.
No market-data network request is made.
"""
import asyncio
from datetime import date
import json
from unittest.mock import patch
import pandas as pd
from alphaevo.data.adapter import DataAdapter, DataManager
from alphaevo.data.adapters.akshare import AkShareAdapter


class FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 11, 13)


def prices(start, end):
    return pd.DataFrame({"date": pd.date_range(start, end).date,
        "open": 10., "high": 11., "low": 9., "close": 10., "volume": 100.})


class Fallback(DataAdapter):
    calls = 0
    @property
    def name(self):
        return "synthetic_historical_fixture"
    async def get_stock_list(self, market):
        return []
    async def get_daily_data(self, symbol, days=120):
        self.calls += 1
        return prices("2024-01-01", "2024-03-31")


async def main():
    requests = []
    def fetch(**kwargs):
        requests.append(kwargs)
        return prices(kwargs["start_date"], kwargs["end_date"])
    fake = type("FakeAkshare", (), {"stock_zh_a_hist": staticmethod(fetch)})()
    results = []
    cases = [("old", "2024-01-01", "2024-03-31", False),
             ("partial", "2026-01-01", "2026-04-30", False),
             ("recent", "2026-10-01", "2026-11-13", False),
             ("fallback", "2024-01-01", "2024-03-31", True)]
    with patch("alphaevo.data.adapters.akshare.date", FixedDate), patch(
            "alphaevo.data.adapters.akshare._ensure_akshare", return_value=fake):
        for label, start, end, use_fallback in cases:
            requests.clear()
            fallback = Fallback()
            manager = DataManager([AkShareAdapter()] + ([fallback] if use_fallback else []))
            record = {"case": label, "requested": [start, end]}
            try:
                frame = await manager.get_history("000001", date.fromisoformat(start), date.fromisoformat(end))
                record.update(rows=len(frame), first=str(frame.date.min()), last=str(frame.date.max()))
            except RuntimeError as error:
                record["error"] = str(error)
            record.update(requests=requests.copy(), fallback_calls=fallback.calls,
                source_successes=manager.health_status()[0].success_count)
            results.append(record)
    assert "No historical data" in results[0]["error"]
    assert results[0]["requests"][0]["start_date"] == "20260517"
    assert (results[1]["first"], results[1]["rows"]) == ("2026-04-04", 27)
    assert (results[2]["first"], results[2]["rows"]) == ("2026-10-01", 44)
    assert results[3]["fallback_calls"] == 0 and results[3]["source_successes"] == 1
    assert "No historical data" in results[3]["error"]
    print(json.dumps({"reconstructed": True, "fixture_not_real_prices": True,
                      "cases": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
