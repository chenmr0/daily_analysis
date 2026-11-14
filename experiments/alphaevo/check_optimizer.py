"""Reconstructed bounded optimizer smoke; uses upstream bundled US snapshot only."""
import argparse
from pathlib import Path
import time
from alphaevo.cli.demo import load_showcase_snapshot
from alphaevo.models.execution import SampleBatch
from alphaevo.optimizer import ExitOptimizer, ParamOptimizer
from alphaevo.strategy.draft import StrategyDraftBuilder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-candidates", type=int, default=12)
    args = parser.parse_args()
    if not 1 <= args.max_candidates <= 64:
        parser.error("max-candidates must be 1..64")
    args.output.mkdir(parents=True, exist_ok=False)
    data, _ = load_showcase_snapshot()
    data = {symbol: data[symbol] for symbol in ["AAPL", "MSFT", "NVDA", "AMD", "TSLA"]}
    strategy = StrategyDraftBuilder().from_text(
        "RSI oversold rebound, stop loss 5%, take profit 10%, hold 10 days",
        market="us", strategy_id="reconstructed_us_v1")
    batch = SampleBatch(batch_id="reconstructed", strategy_id=strategy.meta.id,
        symbols=list(data), date_range=(min(df.date.min() for df in data.values()),
                                       max(df.date.max() for df in data.values())))
    for label, optimizer in [("params", ParamOptimizer()), ("exit", ExitOptimizer())]:
        start = time.perf_counter()
        result = optimizer.optimize(strategy, data, batch, max_candidates=args.max_candidates)
        (args.output / f"{label}.json").write_text(result.model_dump_json(indent=2))
        print(label, "candidates", len(result.candidates), "seconds", time.perf_counter()-start,
              "best", result.best_candidate_id)
    print("Mechanism only: fixed upstream US snapshot, no independent holdout or A-share validation.")


if __name__ == "__main__":
    main()
