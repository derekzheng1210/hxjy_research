import unittest
import numpy as np
import pandas as pd
from fair_value_research.selective_followup import apply


class PersistentTests(unittest.TestCase):
    def frame(self):
        return pd.DataFrame([dict(quote_days=5,any_quote_age=0,any_freshness=1.,
            boundary_consistent_days=4,any_boundary=8.,q_delta=8.,level_delta=6.,
            known_relevant_volume_days=0,trade_days=0,last_trade_age=np.nan,t_delta=np.nan)])

    def config(self,gate="persistent"):
        return dict(anchor="level",gate=gate,scale=.25,minimum=1.)

    def test_persistent_unilateral_signal_does_not_require_trades(self):
        d,m=apply(self.frame(),self.config());self.assertTrue(m.iloc[0]);self.assertEqual(d.iloc[0],1.5)

    def test_unknown_quantity_does_not_confirm_size(self):
        self.assertFalse(apply(self.frame(),self.config("known_size"))[1].iloc[0])

    def test_trade_gate_requires_real_confirmation(self):
        f=self.frame();f.trade_days=1;f.last_trade_age=1;f.t_delta=-4
        self.assertFalse(apply(f,self.config("trade_echo"))[1].iloc[0])
        f.t_delta=4;self.assertTrue(apply(f,self.config("trade_echo"))[1].iloc[0])

    def test_stale_inconsistent_missing_and_weak_abstain(self):
        for column,value in [("any_quote_age",1),("any_freshness",.5),("quote_days",3),
            ("boundary_consistent_days",2),("any_boundary",-1),("level_delta",np.nan),("level_delta",2)]:
            f=self.frame();f[column]=value;self.assertFalse(apply(f,self.config())[1].iloc[0],column)

    def test_future_labels_never_change_selection(self):
        f=self.frame();d,m=apply(f,self.config());f["actual_delta_bp"]=-1000
        d2,m2=apply(f,self.config());self.assertTrue(d.equals(d2));self.assertTrue(m.equals(m2))

    def test_both_directions_and_cap(self):
        f=self.frame();f.level_delta=-100;f.q_delta=-100;f.any_boundary=-50
        d,m=apply(f,self.config());self.assertTrue(m.iloc[0]);self.assertEqual(d.iloc[0],-10.)


if __name__=="__main__":unittest.main()
