import unittest
import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from fair_value_research.model import Parameters, evidence, predict, realization, volume_imbalance
from fair_value_research.run import Inputs, peer_average


class FairValueTests(unittest.TestCase):
    def setUp(self):
        self.p = Parameters()

    def q(self, age=0, bid=None, ofr=None, imbalance=None):
        return {"age":age,"day":str(age),"bid":bid,"ofr":ofr,"imbalance":imbalance,"freshness":1}

    def test_offer_only_persistence_saturates(self):
        one=evidence({"quotes":[self.q(ofr=5)]},0,self.p)
        many=evidence({"quotes":[self.q(i,ofr=5) for i in range(5)]},0,self.p)
        self.assertGreater(one["signal"],0)
        self.assertGreater(many["signal"],one["signal"])
        self.assertLessEqual(many["signal"],5)

    def test_bid_demand_and_mid_below_official(self):
        result=evidence({"quotes":[self.q(i,bid=-1,ofr=-3,imbalance=-.6) for i in range(5)]},0,self.p)
        self.assertLess(result["signal"],0)

    def test_offer_falling_gives_negative_trend(self):
        ev=evidence({"quotes":[self.q(i,ofr=i) for i in range(5)]},0,self.p)
        self.assertLess(ev["components"]["quote_trend"],0)

    def test_peer_pressure_does_not_create_evidence(self):
        ev=evidence({},0,self.p)
        self.assertIsNone(predict(0,ev,10,self.p)[0])
        ev=evidence({"quotes":[self.q(bid=2,ofr=0)]},0,self.p)
        self.assertGreater(predict(0,ev,10,self.p)[0],predict(0,ev,None,self.p)[0])

    def test_offer_volume_growth_without_bid_size(self):
        quotes=[{**self.q(i,ofr=0),"ofr_volume":1000*(5-i)} for i in range(5)]
        result=evidence({"quotes":quotes},0,self.p)
        self.assertGreater(result["components"]["size_trend"],0)

    def test_trade_confirms_or_opposes_quote(self):
        q=[self.q(i,bid=6,ofr=4) for i in range(5)]
        plus={"quotes":q,"trades":[{"age":1,"spread":8,"num":10}]}
        minus={"quotes":q,"trades":[{"age":1,"spread":-8,"num":10}]}
        self.assertGreater(evidence(plus,0,self.p)["signal"],evidence(minus,0,self.p)["signal"])

    def test_unknown_volume_is_not_zero_demand(self):
        self.assertIsNone(volume_imbalance(None,5000))
        self.assertLess(volume_imbalance(10000,1000),0)

    def test_repeated_polling_does_not_create_states_or_size(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            (root/"quotes").mkdir()
            row={"code":"A.IB","observed_at":"2026-09-01 10:00:00","bid_yield":2,"ofr_yield":1.9,
                 "bid_time":"2026-09-01 09:59:00","ofr_time":"2026-09-01 09:59:00",
                 "bid_volume_value":0,"bid_volume_text":"--","ofr_volume_value":5000,"ofr_volume_text":"5000"}
            (root/"quotes/20260901.json").write_text(json.dumps([row]*10),encoding="utf8")
            inp=Inputs.__new__(Inputs)
            inp.root=root;inp.days=["20260901"];inp.quotes={};inp.audit=defaultdict(int)
            inp._quote_daily()
            q=inp.quotes["20260901"]["A.IB"]
            self.assertEqual(q["states"],1)
            self.assertEqual(q["ofr_volume"],5000)
            self.assertIsNone(q["imbalance"])
            self.assertEqual(q["bid"],2)
            self.assertEqual(inp.audit["duplicate_quote_states"],9)

    def test_peer_self_and_subordination_excluded(self):
        own={"code":"A","sub":"否","guarantor":""}
        base={"term":2}
        ev={"has_evidence":True,"quality":1,"signal":10}
        self.assertEqual(peer_average(own,base,[(own,base,ev)]),(None,0))
        self.assertEqual(peer_average(own,base,[({**own,"code":"B","sub":"是"},base,ev)]),(None,0))

    def test_cross_market_family_excluded(self):
        own={"code":"A.IB","issuer":"Issuer","issue_date":"2020-01-01","effective_maturity_date":"2027-01-01","sub":"否","guarantor":""}
        other={**own,"code":"A.SH"}
        ev={"has_evidence":True,"quality":1,"signal":10}
        self.assertEqual(peer_average(own,{"term":2},[(other,{"term":2},ev)]),(None,0))

    def test_touch_not_terminal_realization(self):
        r=realization(0,-5,2,[-5,2])
        self.assertEqual(r["touched"],1)
        self.assertEqual(r["target_reached"],0)
        self.assertEqual(r["max_adverse_bp"],2)

    def test_missing_path_is_missing_not_zero(self):
        self.assertIsNone(realization(0,5,3,[])["touched"])

    def test_rating_no_future_leak(self):
        inp=Inputs.__new__(Inputs)
        inp.ratings={"S":[["20260901","AA"],["20260910","AAA"]]}
        self.assertEqual(inp.rating({"secode":"S"},"20260910"),"AA")
        self.assertEqual(inp.rating({"secode":"S"},"20260911"),"AAA")

    def test_trade_origin_day_excluded(self):
        inp=Inputs.__new__(Inputs)
        inp.calendar=["20260901","20260902","20260903"]
        inp.quotes={}
        inp.trades={"A":{"20260902":{"yield":2,"trading_num":1},"20260903":{"yield":20,"trading_num":100}}}
        inp.curve=lambda *a:1
        inp.curve_name=lambda *a:"curve"
        inp.term=lambda *a:1
        f=inp.features({"code":"A.IB"},"20260903",2)
        self.assertEqual(len(f["trades"]),1)
        self.assertEqual(f["trades"][0]["spread"],100)

    def test_total_adjustment_cap_and_contribution_reconcile(self):
        own={"has_evidence":True,"quality":1,"components":{"x":100}}
        fair,parts=predict(0,own,10,self.p)
        self.assertEqual(fair,self.p.total_cap_bp)
        self.assertAlmostEqual(sum(parts.values()),fair)


if __name__=="__main__":
    unittest.main()
