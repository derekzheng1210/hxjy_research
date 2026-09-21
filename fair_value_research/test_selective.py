import unittest
import numpy as np
import pandas as pd

from fair_value_research.selective import apply_config, metrics, qualify, snapshot_features, split_issuer


def frame():
    return pd.DataFrame([dict(q_delta=-4.,t_delta=-3.,trade_days=3,trade_count=6,last_trade_age=1,
        trade_mad_bp=.3,two_sided_days=4,last_quote_age=0,median_width_bp=2.,last_width_bp=2.,
        mean_freshness=.9,outside_delta=-2.,outside_days=3,mid_mad_bp=.2,delta_bp=-2.,level_delta=-1.5)])


class SelectiveTests(unittest.TestCase):
    def config(self,gate="trade_confirmed"):
        return dict(id="test",anchor="consensus",gate=gate,scale=.5,minimum=1.)

    def test_clear_consensus_selected(self):
        d,m=apply_config(frame(),self.config())
        self.assertTrue(m.iloc[0]);self.assertAlmostEqual(d.iloc[0],-1.75)

    def test_conflict_abstains(self):
        f=frame();f.t_delta=3
        self.assertFalse(apply_config(f,self.config())[1].iloc[0])

    def test_wide_spread_abstains(self):
        f=frame();f.median_width_bp=12
        self.assertFalse(apply_config(f,self.config())[1].iloc[0])

    def test_missing_trade_not_zero(self):
        f=frame();f.t_delta=np.nan;f.trade_days=0
        self.assertFalse(apply_config(f,self.config())[1].iloc[0])

    def test_stale_quote_or_trade_abstains(self):
        for column,value in [("last_quote_age",1),("last_trade_age",2)]:
            f=frame();f[column]=value
            self.assertFalse(apply_config(f,self.config())[1].iloc[0])

    def test_tiny_signal_abstains(self):
        f=frame();f.q_delta=-.2;f.t_delta=-.2
        self.assertFalse(apply_config(f,self.config())[1].iloc[0])

    def test_interval_inside_does_not_count_as_mispricing(self):
        q={"bid":4.,"ofr":0.,"age":0,"freshness":1}
        x=snapshot_features({"quotes":[q],"trades":[]},1.)
        self.assertEqual(x["outside_delta"],0)
        self.assertEqual(x["outside_days"],0)

    def test_historical_features_do_not_depend_on_future_label(self):
        f=frame();d1,m1=apply_config(f,self.config())
        f["actual_delta_bp"]=1000
        d2,m2=apply_config(f,self.config())
        self.assertTrue(d1.equals(d2));self.assertTrue(m1.equals(m2))

    def test_small_lucky_cohort_cannot_qualify(self):
        r=dict(n=20,issuers=15,origins=4,min_date_n=5,minimum=1,gate="trade_confirmed",
            improvement_bp=1,date_equal_improvement_bp=1,positive_dates=4,family_equal_improvement_bp=1,issuer_equal_improvement_bp=1)
        self.assertFalse(qualify(r))

    def test_matched_baseline_uses_only_selected_rows(self):
        f=pd.DataFrame([dict(date="A",code="1",issuer="I",family="F",actual_delta_bp=-2),
                        dict(date="A",code="2",issuer="J",family="G",actual_delta_bp=100)])
        r=metrics(f,pd.Series([-1.,-1.]),pd.Series([True,False]))
        self.assertEqual(r["baseline_mae_bp"],2)
        self.assertEqual(r["mae_bp"],1)
        self.assertEqual(r["coverage"],.5)

    def test_issuer_split_stable(self):
        self.assertEqual(split_issuer("same issuer"),split_issuer("same issuer"))


if __name__=="__main__":unittest.main()
