# -*- coding: utf-8 -*-
"""公允估值观察前端回归夹具服务。

为 test_fair_value_observation_frontend.js 提供隔离的 5094 端口测试实例：
数据写入临时目录，仅注册公允估值观察路由，不触碰生产数据与调度器。
用法：.venv\\Scripts\\python.exe scripts\\fair_value_frontend_fixture.py [--port 5094]
"""
import argparse
import shutil
import sys
import tempfile
from functools import wraps
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from flask import Flask, render_template

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fair_value_observation import engine, rules
from fair_value_observation.storage import connect
from fair_value_observation.routes import register

REPO = Path(__file__).resolve().parents[1]
RULE = dict(id='fixture', family='fixture', name='夹具条件', sources=['fixture'],
            spec=dict(engine='persistent', anchor='level', gate='persistent', scale=1., minimum=1.))
CALENDAR = ['20260901', '20260902', '20260903', '20260904', '20260907', '20260908', '20260909', '20260910',
            '20260911', '20260914', '20260915', '20260916', '20260917', '20260918']
ORIGINS = CALENDAR[3:12]
BONDS = [('A.IB', '夹具债A', '发行人一', 'AA+', 8.), ('B.IB', '夹具债B', '发行人二', 'AAA', 5.), ('C.IB', '夹具债C', '发行人一', 'AA+', -6.)]


def bond_row(day, code, name, issuer, rating, q):
    return dict(date=day, code=code, name=name, issuer=issuer, family='中期票据', sub='否', ct='否', entity='地方国企',
                internal_rating='投资级', guarantor='', effective_maturity_date='20280904', official_yield=2.5,
                base_spread=50., curve_yield=1.5, curve_name='夹具曲线', valuation_date=day, term=2., rating=rating,
                quote_days=5, trade_days=0, trade_status='ok', q_delta=q, quote_level_only=q, level_delta=q * 4,
                any_quote_age=0, any_freshness=1., boundary_consistent_days=4, any_boundary=q,
                known_relevant_volume_days=0, last_trade_age=None, t_delta=None)


class FixtureData:
    bonds = [dict(code=code, effective_maturity_date='20280904') for code, *_ in BONDS]

    def __init__(self):
        self.calendar = list(CALENDAR)
        # B 从 20260916 起缺失官方值：10 日结果保持待兑现，用于前端断言。
        self.values = {day: {code: (None if code == 'B.IB' and day >= '20260916' else {'A.IB': 2.6, 'B.IB': 2.6, 'C.IB': 1.4}[code])
                             for code, *_ in BONDS} for day in CALENDAR}

    def curve(self, *args):
        return 1.5


def seed(root):
    data = FixtureData()
    with patch.object(rules, 'registry', return_value=([RULE], [])):
        engine.install(root)
        for day in ORIGINS:
            engine.record(pd.DataFrame([bond_row(day, *b) for b in BONDS]), 'historical', 'fixture', root)
        with connect(root, True) as db:
            db.execute('UPDATE rules SET selected=1')
        engine.combine(root)
        engine.evaluate(data, root)


def build_app(root):
    app = Flask(__name__, template_folder=str(REPO / 'templates'), static_folder=str(REPO / 'static'))
    app.secret_key = 'fixture'
    app.config['FAIR_VALUE_OBSERVATION_ROOT'] = str(root)

    def auth(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            return fn(*args, **kwargs)
        return wrapped

    register(app, auth, auth, lambda template, key, **ctx: render_template(template, **ctx))
    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=5094)
    parser.add_argument('--keep', action='store_true', help='退出时保留临时数据目录便于排查')
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix='fv_fixture_'))
    seed(root)
    print('fixture data at', root, 'serving on http://127.0.0.1:%d' % args.port, flush=True)
    try:
        build_app(root).run(host='127.0.0.1', port=args.port, threaded=True)
    finally:
        if not args.keep:
            shutil.rmtree(root, ignore_errors=True)
