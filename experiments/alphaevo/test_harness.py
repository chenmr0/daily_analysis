"""Offline input-contract tests; all prices here are deliberately synthetic."""
import tempfile
import unittest
from pathlib import Path
import pandas as pd
from run_trial import read_prices


class PriceInputTests(unittest.TestCase):
    def load(self, frame):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.csv"
            frame.to_csv(path, index=False)
            return read_prices(path)

    def fixture(self):
        return pd.DataFrame({"date": ["2024-01-02", "2024-01-01"],
                            "open": 10., "high": 11., "low": 9., "close": 10., "volume": 100.})

    def test_sorts_valid_rows(self):
        result = self.load(self.fixture())
        self.assertEqual(str(result.date.iloc[0]), "2024-01-01")

    def test_rejects_duplicates(self):
        frame = self.fixture()
        frame["date"] = "2024-01-01"
        with self.assertRaises(ValueError):
            self.load(frame)

    def test_rejects_invalid_ohlc(self):
        frame = self.fixture()
        frame["high"] = 8
        with self.assertRaises(ValueError):
            self.load(frame)

    def test_rejects_nonfinite_values(self):
        frame = self.fixture()
        frame["volume"] = float("inf")
        with self.assertRaises(ValueError):
            self.load(frame)


if __name__ == "__main__":
    unittest.main()
