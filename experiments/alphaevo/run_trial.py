"""Reconstructed, offline research harness; not a daily_analysis integration."""
import argparse
import asyncio
import dataclasses
import hashlib
import json
from datetime import date
from pathlib import Path
import time

import pandas as pd
from alphaevo.core.config import AppConfig, DataConfig, EvolutionConfig
from alphaevo.data.adapter import DataAdapter, DataManager
from alphaevo.models.enums import EvolutionMethod, MarketType
from alphaevo.models.market import StockInfo
from alphaevo.orchestrator.evolution import EvolutionPipeline
from alphaevo.strategy.draft import StrategyDraftBuilder
from alphaevo.strategy.dsl.serializer import StrategySerializer

PIN = "712e60b3a79fa3835ac725e91e4f5b2c5811f652"


def encode(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value):
        return {f.name: getattr(value, f.name) for f in dataclasses.fields(value)
                if f.name != "research_log"}
    return str(value)


def read_prices(path):
    frame = pd.read_csv(path)
    columns = ["date", "open", "high", "low", "close", "volume"]
    if not set(columns).issubset(frame):
        raise ValueError(f"CSV requires {columns}")
    frame = frame[columns].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.date
    for column in columns[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    if frame.empty or frame.isna().any().any() or frame.date.duplicated().any():
        raise ValueError("Empty, missing or duplicate price rows")
    if not frame[columns[1:]].map(lambda x: float('-inf') < x < float('inf')).all().all():
        raise ValueError("Non-finite price data")
    if ((frame.low <= 0) | (frame.volume < 0)
            | (frame.low > frame[["open", "close"]].min(axis=1))
            | (frame.high < frame[["open", "close"]].max(axis=1))).any():
        raise ValueError("Invalid OHLCV data")
    return frame.sort_values("date").reset_index(drop=True)


class FrozenAdapter(DataAdapter):
    def __init__(self, frame, symbol, market, lower, upper):
        self.frame, self.symbol, self.market = frame, symbol, market
        self.lower, self.upper = lower, upper

    @property
    def name(self):
        return "explicit_local_csv"

    async def get_daily_data(self, symbol, days=120):
        if symbol != self.symbol:
            return pd.DataFrame()
        return self.frame[self.frame.date.between(self.lower, self.upper)].copy()

    async def get_stock_list(self, market):
        return [StockInfo(symbol=self.symbol, name=self.symbol, market=self.market)]


async def run(args):
    frame = read_prices(args.csv)
    if not args.train_start <= args.train_end < args.test_start <= args.test_end:
        raise ValueError("Require ordered, disjoint train and test windows")
    for start, end in [(args.train_start, args.train_end), (args.test_start, args.test_end)]:
        if len(frame[frame.date.between(start, end)]) < 30:
            raise ValueError("Each window needs at least 30 supplied rows")
    # Never overwrite previous experiments. No .env / DSA config is loaded.
    args.output.mkdir(parents=True, exist_ok=False)
    def save(name, value):
        (args.output / name).write_text(json.dumps(value, default=encode, ensure_ascii=False, indent=2))
    config = AppConfig(db_path=args.output / "research.db",
                       strategies_dir=args.output / "strategies",
                       data=DataConfig(cache_dir=args.output / "cache"),
                       evolution=EvolutionConfig(auto_expand_samples=False,
                           max_sample_expansions=0, min_signal_count=args.min_signals))
    pipeline = EvolutionPipeline(config)
    adapter = FrozenAdapter(frame, args.symbol, MarketType(args.market), args.train_start, args.train_end)
    pipeline._run_pipeline._data_manager = DataManager([adapter])
    strategy = StrategyDraftBuilder().from_text(args.idea, market=args.market, strategy_id="isolated_trial_v1")
    strategy.universe.filters = []  # Explicit single-security experiment, no invented fundamentals.
    pipeline.store.save(strategy)
    serializer = StrategySerializer()
    serializer.to_file(strategy, args.output / "initial.yaml")
    begin = time.perf_counter()
    result = await pipeline.evolve_async(strategy.meta.id, rounds=args.rounds,
        method=EvolutionMethod.PARAM_SEARCH, max_symbols=1,
        date_range=(args.train_start, args.train_end))
    save("evolution.json", result)
    for item in result.rounds:
        serializer.to_file(item.strategy, args.output / f"round-{item.round_num}.yaml")
    champion = pipeline.store.get(result.champion_id or strategy.meta.id)
    adapter.lower, adapter.upper = args.test_start, args.test_end
    holdout = {}
    for label, sid in [("initial", strategy.meta.id), ("champion", champion.meta.id)]:
        value = await pipeline._run_pipeline.run(sid, max_symbols=1,
            date_range=(args.test_start, args.test_end))
        holdout[label] = {"strategy_id": sid, "evaluation": value.evaluation,
                          "backtest": value.backtest_result}
    save("holdout.json", holdout)
    save("manifest.json", {"reconstructed_harness": True, "expected_alphaevo_commit": PIN,
        "csv_sha256": hashlib.sha256(args.csv.read_bytes()).hexdigest(),
        "source_label": args.source_label, "adjustment": args.adjustment,
        "train": [args.train_start, args.train_end], "test": [args.test_start, args.test_end],
        "min_signals": args.min_signals, "mechanism_only": args.min_signals < 30,
        "warmup_policy": "strict window clipping; first rows reserved implicitly by engine",
        "seconds": time.perf_counter() - begin, "rounds": len(result.rounds),
        "champion": champion.meta.id, "improvement": result.improvement,
        "method": "param_search", "llm_requested": False})
    print(f"Completed {len(result.rounds)} rounds; champion={champion.meta.id}; output={args.output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--market", choices=["a_share", "us", "hk"], default="a_share")
    parser.add_argument("--source-label", required=True)
    parser.add_argument("--adjustment", required=True, help="Describe actual adjustment, or unknown")
    parser.add_argument("--idea", default="RSI低位反弹，止损5%，止盈10%，持有10天")
    for name in ["train-start", "train-end", "test-start", "test-end"]:
        parser.add_argument("--" + name, type=date.fromisoformat, required=True)
    parser.add_argument("--min-signals", type=int, default=30)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.rounds <= 5 or args.min_signals < 1:
        parser.error("rounds must be 1..5 and min-signals must be positive")
    asyncio.run(run(args))
