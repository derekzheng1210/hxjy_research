"""地方债选券、区域统计和接口回归。运行时使用独立 PORTAL_DATA_ROOT。"""
import tempfile
import unittest
from pathlib import Path

from flask import Flask
from interest_bond.bond_switch import bp, db, local_bonds as lb


def bond(code, province="广东", issue="20260101", deals=10, yld=2.2, rem=29, duration=22, **extra):
    return dict(code=code, province=province, issue_date=issue, avg_deals_5d=deals,
                valuation_yield=yld, remaining_years=rem, duration=duration, volume_5d=10,
                deal_count=1, trade_yield=yld, **extra)


class LocalBondTests(unittest.TestCase):
    def payload(self, bonds):
        return lb.build_dashboard(bonds, "20261028", ["20261022", "20261023", "20261026", "20261027", "20261028"], {"valuation_yield": 2.0})

    def test_region_lists_and_city_priority(self):
        self.assertEqual(lb.province_of("2026年深圳市政府专项债券"), "深圳")
        for province in lb.GOOD + lb.BAD:
            self.assertEqual(lb.province_of(f"2026年{province}政府债券"), province)
        self.assertIsNone(lb.province_of("未知发行人"))
        self.assertEqual(len(lb.PROVINCES), len(set(lb.PROVINCES)))
        self.assertIn('青海', lb.BAD)
        self.assertIn('甘肃', lb.BAD)
        self.assertNotIn('青海', dict((g[0], g[2]) for g in lb.GROUPS)['middle'])
        self.assertNotIn('甘肃', dict((g[0], g[2]) for g in lb.GROUPS)['middle'])

    def test_tax_boundary_and_activity(self):
        p = self.payload([bond("before", issue="20250807", deals=20), bond("on", issue="20250808", deals=8), bond("unknown", issue="", deals=100)])
        g = p["comparisons"][0]["details"][0]
        self.assertEqual(g["active"]["code"], "on")
        self.assertEqual(g["exempt"]["code"], "before")
        self.assertEqual(g["tax_bp"], 0)

    def test_inactive_duration_and_tax_constraints(self):
        p = self.payload([bond("active"), bond("eligible", deals=1, yld=2.3),
                          bond("far-duration", deals=0, duration=19), bond("far-term", deals=0, rem=27),
                          bond("exempt", issue="20250701", deals=0), bond("busy", deals=5)])
        g = p["comparisons"][0]
        self.assertEqual(g["details"][0]["inactive"]["code"], "eligible")
        self.assertEqual(g["liquidity_bp"], -10)
        self.assertEqual(g["liquidity_pairs"], 1)

    def test_equal_deals_prefer_size_for_each_role(self):
        small = bond("small-active", outstanding_amount=20)
        small['volume_5d'] = 100000
        large = bond("large-active", outstanding_amount=100)
        old_small = bond("small-exempt", issue="20250101", outstanding_amount=10)
        old_large = bond("large-exempt", issue="20250101", outstanding_amount=90)
        quiet_small = bond("small-inactive", deals=1, outstanding_amount=20)
        quiet_large = bond("large-inactive", deals=1, duration=23, outstanding_amount=80)
        p = self.payload([small, large, old_small, old_large, quiet_small, quiet_large])
        picks = p['comparisons'][0]['details'][0]
        self.assertEqual(picks['active']['code'], 'large-active')
        self.assertEqual(picks['exempt']['code'], 'large-exempt')
        self.assertEqual(picks['inactive']['code'], 'large-inactive')
        self.assertEqual(picks['active']['outstanding_amount'], 100)

    def test_deals_take_priority_over_size_without_rounding(self):
        p = self.payload([bond('more-deals', deals=10.4, outstanding_amount=20),
                          bond('more-size', deals=10.2, outstanding_amount=1000),
                          bond('unknown-size', deals=10.4, outstanding_amount=None)])
        self.assertEqual(p['comparisons'][0]['details'][0]['active']['code'], 'more-deals')

    def test_pairwise_mean_excludes_unmatched_provinces(self):
        p = self.payload([bond("gd"), bond("gd-old", deals=1, yld=2.4),
                          bond("js", province="江苏", yld=3), bond("zj", province="浙江", yld=2.5),
                          bond("zj-old", province="浙江", deals=1, yld=2.6)])
        self.assertEqual(p["comparisons"][0]["liquidity_bp"], -15)
        self.assertEqual(p["comparisons"][0]["liquidity_pairs"], 2)

    def test_no_trade_is_excluded_from_transaction_range(self):
        a = bond("active")
        a.update(deal_count=0, low_yield=0, high_yield=99)
        g = self.payload([a])["tables"][0]["groups"][0]
        self.assertIsNone(g["trade_range"])
        self.assertEqual(g["covered"], 1)
        self.assertEqual(g["valuation_yield"], 2.2)

    def test_transaction_range_and_bp_units(self):
        a, b = bond("gd"), bond("js", province="江苏", yld=2.25)
        a['trade_yield'], b['trade_yield'] = 2.19, 2.24
        g = self.payload([a, b])["tables"][0]["groups"][0]
        self.assertEqual(g['trade_range'], [2.19, 2.24])
        self.assertEqual(g['width_bp'], 5)
        self.assertEqual(g['vs_treasury_bp'], [20, 25])
        self.assertEqual(g['valuation_range'], [2.2, 2.25])

    def test_empty_and_cached_api(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "switch.db"
            db.initialize(path)
            app = Flask(__name__)
            app.config["BOND_SWITCH_DB"] = path
            app.register_blueprint(bp)
            client = app.test_client()
            self.assertEqual(client.get('/bond-switch/api/local-dashboard').status_code, 200)
            self.assertIsNone(client.get('/bond-switch/api/local-dashboard').json['as_of_date'])
            import json
            db.set_meta("local_dashboard", json.dumps(self.payload([])), path)
            db.set_meta("local_error", "network failed", path)
            p = client.get('/bond-switch/api/local-dashboard').json
            self.assertEqual(p['as_of_date'], '20261028')
            self.assertEqual(p['update_error'], 'network failed')
            self.assertIsNone(p['comparisons'][0]['liquidity_bp'])

    def test_history_pairs_missing_data_and_final_snapshot(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'history.db'
            db.initialize(path)
            p = self.payload([bond('gd-active', yld=2.2), bond('gd-inactive', deals=1, yld=2.3),
                              bond('gd-exempt', issue='20250101', yld=2),
                              bond('js-active', province='江苏', yld=2.4),
                              bond('js-inactive', province='江苏', deals=1, yld=2.5)])
            db.set_meta('local_dashboard', json.dumps(p), path)
            with db.transaction(path) as conn:
                conn.executemany('INSERT INTO local_valuation_points VALUES(?,?,?)', [
                    ('20261026', 'gd-active', 2.1), ('20261026', 'gd-inactive', 2.3),
                    ('20261026', 'js-active', 2.4), # 缺右侧，不能加入均值
                    ('20261027', 'gd-exempt', 2), # 缺左侧，不得向前填充
                    ('20261028', 'gd-active', 9), # 末日必须使用最新快照
                ])
            h = lb.history('good', '1m', path)
            self.assertEqual(h['dates'], ['20261026', '20261027', '20261028'])
            self.assertEqual(h['series'][0]['values'], [-20, None, -10])
            self.assertEqual(h['series'][0]['pair_counts'], [1, 0, 2])
            self.assertEqual(h['series'][1]['values'], [None, None, 20])
            self.assertEqual(h['series'][0]['values'][-1], p['comparisons'][0]['liquidity_bp'])
            app = Flask(__name__)
            app.config['BOND_SWITCH_DB'] = path
            app.register_blueprint(bp)
            client = app.test_client()
            self.assertEqual(client.get('/bond-switch/api/local-series?group=bad&range=1y').status_code, 200)
            self.assertEqual(client.get('/bond-switch/api/local-series?group=unknown').status_code, 400)
            self.assertEqual(client.get('/bond-switch/api/local-series?range=all').status_code, 400)

    def test_old_snapshot_regroups_qinghai_and_gansu(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'old.db'
            db.initialize(path)
            p = self.payload([bond('qh', province='青海'), bond('gs', province='甘肃')])
            p.pop('region_version')
            # 模拟旧快照：两个省的代表券仍存放在中地区。
            for table in p['tables']:
                table['groups'][1]['details'].extend([r for r in table['groups'][2]['details'] if r['province'] in ('青海', '甘肃')])
                table['groups'][2]['details'] = [r for r in table['groups'][2]['details'] if r['province'] not in ('青海', '甘肃')]
            db.set_meta('local_dashboard', json.dumps(p), path)
            new = lb.dashboard(path)
            bad = new['tables'][0]['groups'][2]
            self.assertEqual(bad['covered'], 2)
            self.assertEqual(bad['total'], 9)
            self.assertEqual(new['tables'][0]['groups'][1]['total'], 21)


if __name__ == '__main__':
    unittest.main()
