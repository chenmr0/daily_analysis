import unittest
from datetime import date
from unittest.mock import patch
import pandas as pd
from export_dsa_prices import build_export, DSALoader, source_contract, year_windows

START, END = date(2023,12,20), date(2024,1,10)

def calendar(a,b):
    return set(pd.date_range(a,b).date)

def fixture(symbol,a,b):
    return pd.DataFrame([dict(date=d,open=2,high=3,low=1,close=2,volume=100) for d in sorted(calendar(a,b))]), 'TencentFetcher', dict(adjustment='qfq',volume_unit='shares')

class ExportTests(unittest.TestCase):
    def run_export(self, loader=fixture):
        return build_export('000001',START,END,loader,calendar)
    def test_overlap(self):
        frame,chunks=self.run_export()
        self.assertEqual(len(frame),22)
        self.assertEqual(len(chunks),2)
        self.assertEqual(list(year_windows(START,END))[1][0],START)
    def mutated(self, change):
        def load(s,a,b):
            f,source,p=fixture(s,a,b)
            return change(f,source,p,a)
        with self.assertRaises(ValueError): self.run_export(load)
    def test_unknown(self):
        self.mutated(lambda f,s,p,a:(f,s,{**p,'adjustment':'unknown'}))
    def test_unadjusted(self):
        self.mutated(lambda f,s,p,a:(f,s,{**p,'adjustment':'unadjusted'}))
    def test_missing(self):
        self.mutated(lambda f,s,p,a:(f.iloc[1:],s,p))
    def test_duplicate(self):
        self.mutated(lambda f,s,p,a:(pd.concat([f,f.iloc[:1]]),s,p))
    def test_invalid(self):
        self.mutated(lambda f,s,p,a:(f.assign(low=5),s,p))
    def test_nonfinite(self):
        self.mutated(lambda f,s,p,a:(f.assign(volume=float('inf')),s,p))
    def test_mixed_sources(self):
        calls=[]
        def load(s,a,b):
            calls.append(1); f,source,p=fixture(s,a,b)
            return f,source if len(calls)==1 else 'Other',p
        with self.assertRaisesRegex(ValueError,'Mixed'): self.run_export(load)
    def test_overlap_disagreement(self):
        calls=[]
        def load(s,a,b):
            calls.append(1); f,source,p=fixture(s,a,b)
            return f.assign(volume=len(calls)*100),source,p
        with self.assertRaisesRegex(ValueError,'Overlapping'): self.run_export(load)
    def test_contracts(self):
        self.assertEqual(source_contract('TushareFetcher')['adjustment'],'unadjusted')
        self.assertEqual(source_contract('TencentFetcher')['adjustment'],'unknown')
    def test_response_observation(self):
        import data_provider.tencent_fetcher as m
        for key,expected in [('qfqday','qfq'),('day','unadjusted')]:
            def fetch(**kwargs):
                m._extract_kline_rows({'data':{'sz000001':{key:[['2024-01-02','2','2','3','1','100']]}}},symbol='sz000001')
                return pd.DataFrame()
            with patch.object(m.TencentFetcher,'get_daily_data',side_effect=fetch):
                _,_,meta=DSALoader('tencent')('000001',START,END)
                self.assertEqual(meta['adjustment'],expected)
    def test_unobserved_response(self):
        import data_provider.tencent_fetcher as m
        with patch.object(m.TencentFetcher,'get_daily_data',return_value=pd.DataFrame()):
            self.assertEqual(DSALoader('tencent')('000001',START,END)[2]['adjustment'],'unknown')

if __name__=='__main__': unittest.main()
