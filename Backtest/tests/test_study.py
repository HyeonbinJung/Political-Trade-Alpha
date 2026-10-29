import unittest
from datetime import date
from backtest.engine import Bar,Config,CorporateAction,Event,backtest
from backtest.study import simulation
D=[date(2023,1,i) for i in (3,4,5)]
def bar(x):return Bar(x,x,x,x)
class ScenarioTests(unittest.TestCase):
 def setUp(self):
  self.p={"SPY":{d:bar(100) for d in D},"QQQ":{d:bar(100) for d in D},
   "AAA":{D[0]:bar(100),D[1]:Bar(100,116,100,100),D[2]:bar(125)}}
  self.e=[Event("1","Test","AAA",date(2022,12,1),date(2023,1,2),
    "https://www.sec.gov/a","SEC_FORM4")]
 def test_sweep(self):
  x=simulation(self.e,self.p,date(2023,1,1),D[-1],{})
  self.assertEqual(x["form4/TP10"]["closed"],1)
  self.assertEqual(x["form4/TP15"]["closed"],1)
  self.assertEqual(x["form4/TP20"]["closed"],1)
  self.assertEqual(x["form4/HOLD_1Y"]["closed"],0)
 def test_cash_merger(self):
  a=CorporateAction("AAA",D[1],"CASH","https://www.nasdaqtrader.com",110.)
  x=backtest(self.e,self.p,date(2023,1,1),D[-1],
    Config(take_profit=None,slippage_bps=0),{"AAA":a})
  self.assertEqual(x["trades"][0]["reason"],"CASH_MERGER")
 def test_stock_merger(self):
  self.p["FBK"]={d:bar(40) for d in D}
  a=CorporateAction("AAA",D[1],"STOCK","https://www.nasdaqtrader.com",
    successor="FBK",ratio=.8)
  x=backtest(self.e,self.p,date(2023,1,1),D[-1],
    Config(take_profit=None,slippage_bps=0),{"AAA":a})
  self.assertEqual(x["open_positions"][0]["symbol"],"FBK")
if __name__=="__main__":unittest.main()
