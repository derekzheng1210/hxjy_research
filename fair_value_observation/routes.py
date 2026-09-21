import csv
import io
import re
import sqlite3
import math
from pathlib import Path
from flask import jsonify,request,Response,session
from .storage import ROOT,clean
from .rules import VERSION
from . import query
from . import presentation


def register(app,login_required,admin_required,render):
    def root():return Path(app.config.get('FAIR_VALUE_OBSERVATION_ROOT',ROOT))
    def options():
        kind=request.args.get('kind','prospective');direction=request.args.get('direction') or None
        if kind not in {'historical','prospective','backfill'}:raise ValueError('未知样本类别')
        if direction not in {None,'widen','tighten','neutral'}:raise ValueError('未知方向')
        horizon=int(request.args.get('horizon',5));page=int(request.args.get('page',1));size=int(request.args.get('page_size',50))
        origin=request.args.get('date') or None
        if origin and not re.fullmatch(r'\d{8}',origin):raise ValueError('日期须为YYYYMMDD')
        if horizon not in {3,5,10} or page<1 or not 1<=size<=200:raise ValueError('期限或分页参数无效')
        result=dict(version=request.args.get('version',VERSION),explicit_version='version' in request.args,kind=kind,horizon=horizon,origin=origin,direction=direction,search=request.args.get('search','')[:100],page=page,page_size=size,rule=request.args.get('rule') or None)
        for field in ['rating','internal_rating','entity','ct','sub']:result[field]=request.args.getlist(field)
        result['market']=request.args.get('market','');result['sort']=request.args.get('sort','adjustment');result['order']=request.args.get('order','desc')
        if result['sort'] not in {'adjustment','term','yield'} or result['order'] not in {'asc','desc'}:raise ValueError('排序无效')
        for field in ['min_term','max_term','min_yield','max_yield']:
            value=request.args.get(field);result[field]=float(value) if value else None
            if result[field] is not None and not math.isfinite(result[field]):raise ValueError('范围无效')
        return result
    @app.route('/fair-value-observation',endpoint='fair_value_observation')
    @login_required
    def page():return render('fair_value_observation.html','fair_value_observation',is_admin=bool(session.get('admin_authenticated')))
    def handle(fn,*args):
        if not (root()/'observations.sqlite3').exists():return jsonify(ok=False,error='观测数据尚未初始化，请管理员初始化历史研究。',state='uninitialized'),503
        try:return jsonify(ok=True,**clean(fn(*args,options(),root())))
        except (ValueError,TypeError):return jsonify(ok=False,error='查询参数无效'),400
        except sqlite3.OperationalError:return jsonify(ok=False,error='观测数据库暂不可用，请稍后重试'),503
    @app.route('/api/fair-value-observation/overview')
    @login_required
    def fair_value_overview():return handle(query.overview)
    @app.route('/api/fair-value-observation/conditions')
    @login_required
    def fair_value_conditions():return handle(query.conditions)
    @app.route('/api/fair-value-observation/bonds')
    @login_required
    def fair_value_bonds():return handle(query.bond_list)
    @app.route('/api/fair-value-observation/bonds/<code>/history')
    @login_required
    def fair_value_history(code):return handle(query.bond_history,code)
    @app.route('/api/fair-value-observation/conditions/<rule>/daily')
    @login_required
    def fair_value_daily(rule):return handle(query.daily_metrics,rule)
    @app.route('/api/fair-value-observation/dates')
    @login_required
    def fair_value_dates():return handle(presentation.dates)
    @app.route('/api/fair-value-observation/facets')
    @login_required
    def fair_value_facets():return handle(presentation.facets)
    @app.route('/api/fair-value-observation/listings')
    @login_required
    def fair_value_listings():return handle(presentation.listings)
    @app.route('/api/fair-value-observation/predictions/<int:prediction>/chart')
    @login_required
    def fair_value_chart(prediction):return handle(presentation.chart,prediction)
    @app.route('/fair-value-observation/admin')
    @login_required
    @admin_required
    def fair_value_admin():return render('fair_value_observation_admin.html','fair_value_observation')
    @app.route('/api/fair-value-observation/export')
    @login_required
    def fair_value_export():
        try:result=presentation.listings(options(),root(),export=True) if request.args.get('view')=='user' else query.bond_list(options(),root(),export=True)
        except (ValueError,sqlite3.OperationalError):return jsonify(ok=False,error='导出参数无效或数据未初始化'),400
        stream=io.StringIO();writer=csv.writer(stream);fields=['origin','code','name','issuer','direction','official_yield','fair_yield','fair_spread','delta','quote_days','trade_days','signal_status','kind','created','target_date','actual_delta','correct']
        writer.writerow(['起点','债券代码','债券简称','主体','方向','官方收益率%','公允估值%','公允利差BP','调整BP','报价天数','成交天数','状态','样本类别','生成时间','目标日期','实际变化BP','方向正确'])
        for row in result['items']:
            values=[]
            for key in fields:
                v=row.get(key)
                if isinstance(v,str) and v.startswith(('=','+','-','@')):v="'"+v
                values.append(v)
            writer.writerow(values)
        return Response('\ufeff'+stream.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename="fair-value-observation.csv"'})
    @app.route('/api/fair-value-observation/retry',methods=['POST'])
    @login_required
    @admin_required
    def fair_value_retry():
        if request.headers.get('X-Requested-With')!='XMLHttpRequest':return jsonify(ok=False,error='请求来源验证失败'),403
        from .scheduler import trigger
        ok,message=trigger((request.get_json(silent=True) or {}).get('kind','predict'))
        return jsonify(ok=ok,message=message),202 if ok else 409
