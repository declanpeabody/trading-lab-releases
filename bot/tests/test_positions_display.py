"""Check displayed returns against independent GBP/USD examples."""
import shutil
import subprocess
import unittest
from pathlib import Path

class PositionDisplayTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'Node is required for dashboard calculation verification')
    def test_cost_fx_split_and_missing_data(self):
        html=(Path(__file__).resolve().parents[1]/'dashboard.html').read_text()
        function=html[html.index('function positionMetrics('):html.index('function renderPositions(')]
        checks=r'''
const assert=require('node:assert/strict');
const near=(x,y)=>assert.ok(Math.abs(x-y)<1e-9,`${x} != ${y}`);
// Two shares at $90: GBP book cost £150, including purchase costs.
let m=positionMetrics({qty:2,entry_usd:90,cost_gbp:150},100,.8);
near(m.value,160);near(m.profit,10);near(m.returnPercent,100/15);
near(m.priceChange,10);near(m.pricePercent,100/9);
// Same USD price gain but an adverse FX move creates a GBP loss.
m=positionMetrics({qty:2,entry_usd:90,cost_gbp:150},100,.7);
near(m.profit,-10);assert.ok(m.pricePercent>0);assert.ok(m.returnPercent<0);
// A confirmed 2-for-1 split preserves position value and percentage returns.
const before=positionMetrics({qty:2,entry_usd:90,cost_gbp:150},100,.8);
const after=positionMetrics({qty:4,entry_usd:45,cost_gbp:150},50,.8);
near(before.value,after.value);near(before.profit,after.profit);near(before.pricePercent,after.pricePercent);
// No quote/FX/cost means no invented return. USD prices can still be shown without FX.
m=positionMetrics({qty:2,entry_usd:90,cost_gbp:150},undefined,.8);
assert.equal(m.profit,null);assert.equal(m.pricePercent,null);
m=positionMetrics({qty:2,entry_usd:90,cost_gbp:150},100,null);
assert.equal(m.value,null);near(m.latest,100);
m=positionMetrics({qty:2,entry_usd:90},100,.8);
assert.equal(m.returnPercent,null);near(m.value,160);
'''
        subprocess.run(['node','-e',function+checks],check=True,capture_output=True,text=True)
