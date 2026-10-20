import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from research import DailyLearning, strategy_code, paired_scores, scorecards, passes
from research import ResearchSignals
from engine import Ledger

class LearningTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.learning=DailyLearning(self.root)
        self.ledger=Ledger(self.root/'paper.sqlite3')
        self.now=datetime(2026,10,20,23,tzinfo=timezone.utc)

    def evidence(self, start, days=30):
        rows=[]
        for i in range(days):
            at=start+timedelta(days=i)
            # Shared returns; fast model skips losing opportunities rather than
            # assigning conflicting outcomes to the same stock/snapshot.
            for symbol,result in [('WIN',2.0),('LOSS',-1.0)]:
                for model in ['trend_20_50','faster_10_30']:
                    rows.append(dict(id=f'{model}:{symbol}:{at}',symbol=symbol,module=model,source='scanner',
                        direction='HOLD' if symbol=='LOSS' and model=='faster_10_30' else 'BUY',
                        first_seen=at.isoformat(),active_action='HOLD',published_at=at.isoformat(),
                        baseline={'at':at.isoformat(),'price_usd':100},outcomes={'120':{
                            'at':(at+timedelta(hours=120)).isoformat(),'hypothetical_long_after_cost_pct':result}}))
        return rows

    def store(self, rows):
        with ResearchSignals(self.root).connect() as db:
            for r in rows:db.execute('INSERT OR REPLACE INTO signals VALUES(?,?)',(r['id'],json.dumps(r)))

    def test_schedule_idempotence_and_no_ledger_writes(self):
        before=self.ledger.read()
        self.assertFalse(self.learning.review(self.ledger,self.now.replace(hour=18)))
        self.assertTrue(self.learning.review(self.ledger,self.now))
        self.assertFalse(DailyLearning(self.root).review(self.ledger,self.now))
        self.assertEqual(before,self.ledger.read())
        self.assertIn('No change',self.learning.state()['last_review']['decision'])

    def test_code_generation_frozen_and_future_holdout(self):
        rows=self.evidence(self.now-timedelta(days=45));self.store(rows)
        self.learning.review(self.ledger,self.now)
        s=self.learning.state();self.assertEqual(s['model']['phase'],'forward_test')
        self.assertEqual(s['model']['forward_test']['measured_pairs'],0)
        path=self.root/'learning-candidates'/s['model']['candidate_file']
        self.assertEqual(path.read_text(),strategy_code('faster_10_30'))
        # Original observations cannot leak into forward evaluation.
        tomorrow=self.now+timedelta(days=1)
        self.learning.review(self.ledger,tomorrow)
        self.assertEqual(self.learning.state()['model']['forward_test']['measured_pairs'],0)
        future=self.evidence(self.now+timedelta(days=1));self.store(future)
        self.learning.review(self.ledger,self.now+timedelta(days=42))
        self.assertEqual(self.learning.state()['model']['phase'],'review_proposal')
        self.assertFalse(self.learning.state()['automatic_deployment'])

    def test_failed_holdout_never_reselects(self):
        self.store(self.evidence(self.now-timedelta(days=45)))
        self.learning.review(self.ledger,self.now)
        future=self.evidence(self.now+timedelta(days=1))
        for r in future:r['direction']='BUY'
        self.store(future);self.learning.review(self.ledger,self.now+timedelta(days=42))
        self.assertEqual(self.learning.state()['model']['phase'],'rejected')

    def test_missing_and_future_outcomes_not_losses_or_training(self):
        rows=self.evidence(self.now-timedelta(days=45))
        for r in rows[:40]:r['outcomes']={}
        stats=paired_scores(rows,self.now)
        self.assertFalse(passes(stats));self.assertLess(stats['coverage'],.9)
        cards=scorecards(rows,self.now)
        self.assertGreater(sum(g['missing'] for g in cards),0)
        future=self.evidence(self.now)
        self.assertEqual(sum(g['measured'] for g in scorecards(future,self.now)),0)

    def test_context_not_directional_and_unknown_not_missed(self):
        rows=self.evidence(self.now-timedelta(days=45),1)
        for r in rows:r['active_action']='Not evaluated'
        rows[0]['direction']='OBSERVE';rows[0]['module']='news'
        cards=scorecards(rows,self.now)
        self.assertEqual(sum(g['missed_buy_observations'] for g in cards),0)
        self.assertIsNone(next(g for g in cards if g['module']=='news')['mean_buy_after_cost_pct'])

    def test_template_matches_journal_rules(self):
        for model,(short,long) in {'trend_20_50':(20,50),'faster_10_30':(10,30)}.items():
            scope={};exec(strategy_code(model),scope)
            closes=list(range(100,150));fast=sum(closes[-short:])/short;slow=sum(closes[-long:])/long
            for price in [90,fast,200]:
                expected='BUY' if price>fast>slow else 'SELL' if price<fast else 'HOLD'
                self.assertEqual(scope['signal'](price,closes),expected)
        with self.assertRaises(KeyError):strategy_code('__import__("os")')

    def test_tampered_candidate_and_failure_isolation(self):
        self.store(self.evidence(self.now-timedelta(days=45)));self.learning.review(self.ledger,self.now)
        candidate=self.learning.state()['model']['candidate_file']
        (self.root/'learning-candidates'/candidate).write_text('changed')
        with self.assertRaises(ValueError):self.learning.review(self.ledger,self.now+timedelta(days=1))
        self.assertEqual(self.learning.state()['last_review']['day'],'2026-10-20')
        class Stop:
            stopped=False
            def is_set(self):return self.stopped
            def wait(self,n):self.stopped=True
        before=self.ledger.read()
        with patch.object(self.learning,'review',side_effect=OSError('disk')):self.learning.worker(self.ledger,Stop())
        self.assertIsNotNone(self.learning.state()['error']);self.assertEqual(before,self.ledger.read())
