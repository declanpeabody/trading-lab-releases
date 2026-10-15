import json,tempfile,unittest
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from news import News,FeedResearch,feed_items,feed_link
NOW=datetime(2026,10,15,17,tzinfo=timezone.utc)
def rss(title='$AAPL upcoming earnings',url='https://stockaholics.net/threads/aapl.1/',date='Thu, 15 Oct 2026 16:00:00 GMT'):
 return f'<rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><item><title>{title}</title><link>{url}</link><pubDate>{date}</pubDate><dc:creator>writer1</dc:creator></item></channel></rss>'.encode()
class FeedTests(unittest.TestCase):
 def test_parser(self):
  e=feed_items(rss(),'stockaholics',{'AAPL'},NOW)[0]
  self.assertEqual(e['author'],'writer1');self.assertIn('Earnings / guidance',e['categories']);self.assertIn('unverified',e['confidence'])
  self.assertIn('date not verified',e['timing'])
  for title in ('A good company','$AAPL guaranteed 1000%'):
   self.assertFalse(feed_items(rss(title),'stockaholics',{'AAPL','A'},NOW))
 def test_bad_dates_links_xml(self):
  for url in ('http://stockaholics.net/a','https://stockaholics.net.evil.com/a','https://user@bbc.com/a','https://127.0.0.1/a','javascript:alert(1)'):
   self.assertIsNone(feed_link(url));self.assertEqual(feed_items(rss(url=url),'stockaholics',{'AAPL'},NOW),[])
  for date in ('bad','Thu, 01 Oct 2026 16:00:00 GMT','Fri, 16 Oct 2026 16:00:00 GMT'):
   self.assertEqual(feed_items(rss(date=date),'stockaholics',{'AAPL'},NOW),[])
  for body in (b'<!DOCTYPE rss><rss/>',b'<!ENTITY a "b"><rss/>',b'x'*1000001):
   with self.assertRaises(ValueError):feed_items(body,'bbc',{'AAPL'},NOW)
 def test_atom_alias(self):
  body=b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Apple announces earnings</title><link href="https://www.bbc.com/news/a"/><updated>2026-10-15T16:00:00Z</updated></entry></feed>'
  self.assertEqual(feed_items(body,'bbc',{'AAPL'},NOW)[0]['symbol'],'AAPL')
 def test_restart_dedup_failure_isolation(self):
  with tempfile.TemporaryDirectory() as root,patch('news.datetime') as clock:
   clock.now.return_value=NOW;clock.fromisoformat.side_effect=datetime.fromisoformat
   observer=FeedResearch(root)
   def get(source):
    if source=='bbc':raise ValueError('HTTP 403: unavailable')
    return rss()
   account={'scanner':{'selected':['AAPL']}};original=json.dumps(account)
   with patch.object(observer,'fetch',side_effect=get) as fetch:observer.poll(account);self.assertEqual(fetch.call_count,2)
   state=observer.state();self.assertEqual(len(state['observations']),1);self.assertTrue(state['sources'][0]['error'])
   observer.db.close();observer=FeedResearch(root)
   with patch.object(observer,'fetch',side_effect=get) as fetch:observer.poll(account);fetch.assert_not_called()
   clock.now.return_value=NOW+timedelta(minutes=31)
   with patch.object(observer,'fetch',side_effect=get):observer.poll(account)
   self.assertEqual(len(observer.state()['observations']),1);self.assertEqual(json.dumps(account),original);observer.db.close()
 def test_outcomes_fresh_subsequent_quotes_only(self):
  with tempfile.TemporaryDirectory() as root,patch('news.datetime') as clock:
   clock.now.return_value=NOW;clock.fromisoformat.side_effect=datetime.fromisoformat
   observer=FeedResearch(root);account={'marks':{'AAPL':100},'last_success':(NOW-timedelta(seconds=1)).isoformat()}
   with patch.object(observer,'fetch',return_value=rss()):observer.poll(account)
   self.assertIsNone(observer.state()['observations'][0]['baseline'])
   clock.now.return_value=NOW+timedelta(seconds=60);account['last_success']=clock.now.return_value.isoformat()
   observer.poll(account);self.assertEqual(observer.state()['observations'][0]['baseline']['price_usd'],100)
   clock.now.return_value=NOW+timedelta(hours=2)
   with patch.object(observer,'fetch',return_value=rss()):observer.poll(account)
   self.assertEqual(observer.state()['observations'][0]['outcomes'],{})
   account.update(last_success=clock.now.return_value.isoformat(),marks={'AAPL':110})
   observer.poll(account);self.assertAlmostEqual(observer.state()['observations'][0]['outcomes']['1h']['change_pct'],10);observer.db.close()
 def test_independent_controls(self):
  with tempfile.TemporaryDirectory() as root:
   n=News(root);n.configure({'action':'feeds_enable'});self.assertTrue(n.state()['feeds_enabled']);self.assertFalse(n.state()['enabled'])
   n.configure({'action':'enable','contact':'test@example.com'});self.assertTrue(n.state()['feeds_enabled'])
   n.configure({'action':'feeds_disable'});self.assertTrue(n.state()['enabled']);self.assertFalse(n.state()['feeds_enabled']);n.feeds.db.close();n.db.close()
 def test_disabled_no_requests(self):
  with tempfile.TemporaryDirectory() as root:
   o=FeedResearch(root)
   with patch.object(o,'fetch') as fetch:o.poll({},lambda:False);fetch.assert_not_called()
   o.db.close()
