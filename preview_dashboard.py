import sys,json
from pathlib import Path
sys.path.insert(0,'bot')
from app import top_navigation
s=Path('bot/dashboard.html').read_text().replace('__TOP_NAV__',top_navigation('/','preview')).replace('__NONCE__','preview')
Path('/tmp/overview18.html').write_text(s)
state=dict(equity=9964.27,cash=9000,contributed_capital=10000,paused=False,error=None,fx=.75,last_success='2026-10-16T19:43:55Z',data_configured=True,events=[{'at':'2026-10-16T19:43:55Z','message':'Paper holdings checked; all five slots occupied.'}],positions={},marks={},accounting={'executions':[],'funding':[],'performance':{'start':'2026-10-12T15:00:00Z','return_pct':-.36,'max_drawdown_pct':.52,'daily':[]}},worker={'healthy':True},components={'dashboard_version':'2026.10.18.1','worker_version':'2026.10.18.1','started_at':'2026-10-18T13:00:00Z'},no_trade={'reasons':['All five holding slots are occupied.'],'decisions':[]},csrf='preview-only')
for sym,qty,price in [('MSFT',.484311,525.45),('ABBV',.935654,285.34),('AAPL',.759508,343.57),('LOPE',1.641979,160.56),('URBN',2.760475,86.59)]:
 state['positions'][sym]={'qty':qty,'cost_gbp':200,'entry_usd':price*1.02};state['marks'][sym]=price
for day,ret in [(12,0),(13,.12),(14,-.21),(15,-.16),(16,-.36)]:state['accounting']['performance']['daily'].append({'at':f'2026-10-{day}T19:43:55Z','return_pct':ret,'equity_gbp':10000*(1+ret/100),'cash_gbp':9000})
Path('/tmp/overview18-fixture.json').write_text(json.dumps(state))
state['components']['dashboard_version']=state['components']['worker_version']='2026.10.18.2'
state['session_scorecard']={'note':'Synthetic session fixture','sessions':[{'day':'2026-10-16','attempts':12,'successful':10,'success_pct':83.33,'quote_failures':1,'unfinished':1,'longest_success_gap_minutes':10}]}
state['accounting']['closed_reviews']=[{'symbol':'TEST','reason':'Trend exit','realised_pnl_gbp':5,'at':'2026-10-16T15:00:00Z','entry_at':'2026-10-15T15:00:00Z','entry_basis_usd':100,'entry_evidence':{'quote_usd':99.75,'sma20':98,'sma50':96},'trade_review':{'low_usd':99,'high_usd':110,'observations':12,'partial':False}}]
Path('/tmp/overview18-fixture.json').write_text(json.dumps(state))
Path('/tmp/research18.html').write_text(Path('bot/research.html').read_text().replace('__TOP_NAV__',top_navigation('/research','preview')).replace('__NONCE__','preview'))
