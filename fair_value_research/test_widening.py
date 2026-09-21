import unittest
import pandas as pd
import numpy as np
from fair_value_research.widening import quote_features,apply,qualifies
from fair_value_research.widening_events import apply as event_apply


class WideningTests(unittest.TestCase):
    def frame(self):
        return pd.DataFrame([dict(offer_days=5,offer_age=0,offer_freshness=1.,offer_above_days=4,
            last_offer_gap=8.,offer_only_above_days=4,offer_slope=.5,known_offer_size_days=0,
            sell_imbalance=np.nan,offer_size_growth=np.nan,peer_n=2,peer_gap=3.,peer_rising_share=.5,
            trade_days=0,last_trade_age=np.nan,t_delta=np.nan,quote_level_only=6.)])
    def config(self,gate='persistent_offer'):return dict(gate=gate,scale=.25)
    def test_trade_never_changes_anchor(self):
        f=self.frame();d,_=apply(f,self.config());f.t_delta=100;f.trade_days=3;f.last_trade_age=1
        d2,_=apply(f,self.config());self.assertTrue(d.equals(d2))
    def test_unknown_volume_not_sell_pressure(self):
        self.assertFalse(apply(self.frame(),self.config('known_sell_size'))[1].iloc[0])
    def test_trade_conflict_veto(self):
        f=self.frame();f.t_delta=-2;f.trade_days=1;f.last_trade_age=1
        self.assertFalse(apply(f,self.config('no_trade_conflict'))[1].iloc[0])
    def test_stale_and_nonpersistent_refused(self):
        for k,v in [('offer_age',1),('offer_freshness',.2),('offer_above_days',2),('last_offer_gap',-1),('quote_level_only',-4)]:
            f=self.frame();f[k]=v;self.assertFalse(apply(f,self.config())[1].iloc[0])
    def test_yield_offer_rising_is_positive(self):
        qs=[dict(age=4,ofr=3.,bid=None,freshness=1),dict(age=0,ofr=7.,bid=None,freshness=1)]
        f=quote_features(qs,1.);self.assertEqual(f['offer_slope'],1.);self.assertEqual(f['offer_only_above_days'],2)
        self.assertTrue(np.isnan(f['offer_size_growth']))
    def test_missing_latest_offer_does_not_reuse_old_offer(self):
        qs=[dict(age=1,ofr=5.,bid=None),dict(age=0,ofr=None,bid=8.)]
        self.assertTrue(np.isnan(quote_features(qs,1.)['last_offer_gap']))
    def test_peer_requires_two_other_families(self):
        f=self.frame();f.peer_n=1;self.assertFalse(apply(f,self.config('peer_confirmed'))[1].iloc[0])
    def test_lucky_small_group_not_validated(self):
        self.assertFalse(qualifies(dict(n=20,issuers=20,origins=4,direction_accuracy=1.,improvement_bp=2)))
    def test_labels_cannot_change_prediction(self):
        f=self.frame();d,m=apply(f,self.config());f['actual_delta_bp']=100
        d2,m2=apply(f,self.config());self.assertTrue(d.equals(d2));self.assertTrue(m.equals(m2))
    def test_event_does_not_require_existing_positive_gap(self):
        f=self.frame();f['two_sided_days']=3;f['last_width_bp']=2.;f['offer_only_days']=3;f['past5_official_delta']=0
        f.last_offer_gap=-5
        d,m=event_apply(f,dict(gate='any',scale=.5));self.assertTrue(m.iloc[0]);self.assertEqual(d.iloc[0],1.)
        f['actual_delta_bp']=100;d2,m2=event_apply(f,dict(gate='any',scale=.5))
        self.assertTrue(d.equals(d2));self.assertTrue(m.equals(m2))
    def test_event_rejects_falling_offer(self):
        f=self.frame();f['two_sided_days']=3;f['last_width_bp']=2.;f['offer_only_days']=3;f['past5_official_delta']=0
        f.offer_slope=-.5
        self.assertFalse(event_apply(f,dict(gate='any',scale=.5))[1].iloc[0])


if __name__=='__main__':unittest.main()
