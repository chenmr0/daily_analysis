"""Experimental, fail-closed DSA -> CSV bridge. Not production integration."""
import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from run_trial import read_prices

COLUMNS = ['date', 'open', 'high', 'low', 'close', 'volume']


def year_windows(start, end):
    cursor = start
    while cursor <= end:
        stop = min(date(cursor.year, 12, 31), end)
        yield max(start, cursor - timedelta(days=14)), stop
        cursor = stop + timedelta(days=1)


def sessions(start, end):
    import exchange_calendars as xc
    return {stamp.date() for stamp in xc.get_calendar('XSHG').sessions_in_range(str(start), str(end))}


def source_contract(source):
    if source in {'EfinanceFetcher', 'AkshareFetcher', 'BaostockFetcher'}:
        adjustment, evidence = 'qfq', 'DSA provider requests forward adjustment'
    elif source == 'TushareFetcher':
        adjustment, evidence = 'unadjusted', 'daily/fund_daily without adjustment factors'
    else:
        adjustment, evidence = 'unknown', 'No verified response adjustment contract'
    return dict(adjustment=adjustment, evidence=evidence, volume_unit='provider-native; not independently verified')


class DSALoader:
    """Single-process script only: scoped response observation, no product edits."""
    def __init__(self, provider):
        self.manager = None
        if provider == 'manager':
            from data_provider import DataFetcherManager
            self.manager = DataFetcherManager()

    def close(self):
        if self.manager and hasattr(self.manager, 'close'):
            self.manager.close()

    def __call__(self, symbol, start, end):
        import data_provider.tencent_fetcher as module
        observed = []
        original = module._extract_kline_rows

        def capture(payload, *, symbol):
            data = payload.get('data', {}).get(symbol, {})
            observed.append('qfq' if data.get('qfqday') else 'unadjusted' if data.get('day') else 'unknown')
            return original(payload, symbol=symbol)

        with patch.object(module, '_extract_kline_rows', side_effect=capture):
            kwargs = dict(stock_code=symbol, start_date=str(start), end_date=str(end))
            if self.manager:
                frame, source = self.manager.get_daily_data(**kwargs)
            else:
                frame = module.TencentFetcher().get_daily_data(**kwargs)
                source = 'TencentFetcher'
        if source != 'TencentFetcher':
            return frame, source, source_contract(source)
        adjustment = observed[0] if observed and len(set(observed)) == 1 else 'unknown'
        return frame, source, dict(adjustment=adjustment, evidence='Observed qfqday/day response key', volume_unit='shares; DSA normalization')


def build_export(symbol, start, end, loader, expected_sessions):
    frames, chunks, identity = [], [], None
    for lower, upper in year_windows(start, end):
        frame, source, provenance = loader(symbol, lower, upper)
        if provenance['adjustment'] != 'qfq':
            raise ValueError('Unknown or unadjusted price contract; refusing export')
        current = (source, provenance['adjustment'], provenance['volume_unit'])
        if identity is not None and current != identity:
            raise ValueError('Mixed source/adjustment/volume contract')
        identity = current
        frame = frame[COLUMNS].copy()
        frame.date = pd.to_datetime(frame.date, errors='raise').dt.date
        for column in COLUMNS[1:]:
            frame[column] = pd.to_numeric(frame[column], errors='raise')
        if frame.empty or frame.isna().any().any() or frame.date.duplicated().any():
            raise ValueError('Empty, missing or duplicate rows')
        if not frame[COLUMNS[1:]].map(lambda x: float('-inf') < x < float('inf')).all().all():
            raise ValueError('Non-finite data')
        if ((frame.low <= 0) | (frame.volume < 0) | (frame.low > frame[['open','close']].min(axis=1)) | (frame.high < frame[['open','close']].max(axis=1))).any():
            raise ValueError('Invalid OHLCV')
        actual, expected = set(frame.date), expected_sessions(lower, upper)
        if actual != expected:
            raise ValueError(f'Coverage mismatch missing={sorted(expected-actual)[:12]} extra={sorted(actual-expected)[:12]}')
        frames.append(frame)
        chunks.append(dict(start=str(lower), end=str(upper), rows=len(frame), source=source, **provenance))
    merged = pd.concat(frames, ignore_index=True)
    for _, group in merged[merged.date.duplicated(keep=False)].groupby('date'):
        if ((group[COLUMNS[1:]] - group[COLUMNS[1:]].iloc[0]).abs() > 1e-8).any().any():
            raise ValueError('Overlapping rows disagree')
    merged = merged.drop_duplicates('date').sort_values('date').reset_index(drop=True)
    if set(merged.date) != expected_sessions(start, end):
        raise ValueError('Merged coverage mismatch')
    return merged, chunks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbol', required=True)
    parser.add_argument('--start', type=date.fromisoformat, required=True)
    parser.add_argument('--end', type=date.fromisoformat, required=True)
    parser.add_argument('--provider', choices=['manager', 'tencent'], default='manager')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if len(args.symbol) != 6 or not args.symbol.isdigit() or args.symbol[0] not in '036':
        parser.error('Only Shanghai/Shenzhen six-digit equity codes supported')
    if not args.start <= args.end < date.today() or (args.end-args.start).days > 3653:
        parser.error('Require completed past interval, at most ten years')
    args.output.mkdir(parents=True, exist_ok=False)
    loader = None
    try:
        loader = DSALoader(args.provider)
        frame, chunks = build_export(args.symbol, args.start, args.end, loader, sessions)
        pending = args.output / 'prices.pending'
        frame.to_csv(pending, index=False)
        read_prices(pending)
        destination = args.output / 'prices.csv'
        pending.rename(destination)
        import exchange_calendars
        manifest = dict(symbol=args.symbol, requested=[str(args.start),str(args.end)], actual=[str(frame.date.min()),str(frame.date.max())], rows=len(frame), chunks=chunks, coverage='matches_XSHG_sessions; not vendor/corporate-action audit', calendar_version=exchange_calendars.__version__, sha256=hashlib.sha256(destination.read_bytes()).hexdigest(), exporter_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), fetched_at_utc=datetime.now(timezone.utc).isoformat(), dsa_commit=subprocess.check_output(['git','-C',str(REPO),'rev-parse','HEAD'],text=True).strip(), limitations=['Suspension/new listing gaps fail closed; no filling', 'Current qfq is not point-in-time corporate-action data', 'XSHG calendar assumed for Shanghai/Shenzhen equities', 'No execution, taxes, liquidity, dividends or trading-rule validation'])
        (args.output/'manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False))
        print(json.dumps(manifest,ensure_ascii=False))
    except Exception as exc:
        (args.output/'failure.json').write_text(json.dumps({'error':str(exc)},ensure_ascii=False))
        raise
    finally:
        if loader:
            loader.close()


if __name__ == '__main__':
    main()
