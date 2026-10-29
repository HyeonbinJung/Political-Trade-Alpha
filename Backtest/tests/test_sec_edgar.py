import unittest
from datetime import date
from xml.etree import ElementTree as ET
from backtest.sec_edgar import parse_form4, parse_13f, local

FILING = {"filingDate": "2023-01-02"}

class SECTest(unittest.TestCase):
    def test_only_open_market_common_stock_acquisitions(self):
        xml = """<ownershipDocument><issuer><issuerTradingSymbol>ABC</issuerTradingSymbol></issuer>
        <nonDerivativeTable>
        <nonDerivativeTransaction><securityTitle><value>Common Stock</value></securityTitle>
          <transactionDate><value>2022-12-31</value></transactionDate>
          <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
          <transactionAmounts><transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
        </nonDerivativeTransaction>
        <nonDerivativeTransaction><securityTitle><value>Common Stock</value></securityTitle>
          <transactionDate><value>2023-01-01</value></transactionDate>
          <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
          <transactionAmounts><transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode></transactionAmounts>
        </nonDerivativeTransaction>
        <nonDerivativeTransaction><securityTitle><value>Stock Option</value></securityTitle>
          <transactionDate><value>2022-12-31</value></transactionDate>
          <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
          <transactionAmounts><transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
        </nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>"""
        records = parse_form4(ET.fromstring(xml), FILING, "Example", "https://www.sec.gov/Archives/edgar/data/1/2/")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].filing_date, date(2023, 1, 2))
        self.assertEqual(records[0].source, "SEC_FORM4")

    def test_namespaced_13f_and_options_filter(self):
        xml = """<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
          <infoTable><cusip>123456789</cusip><value>1200</value><shrsOrPrnAmt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable>
          <infoTable><cusip>123456789</cusip><value>300</value><shrsOrPrnAmt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable>
          <infoTable><cusip>987654321</cusip><value>150</value><putCall>CALL</putCall></infoTable>
        </informationTable>"""
        records = parse_13f(ET.fromstring(xml))
        self.assertEqual(records, {"123456789": 1500.0})

    def test_date_anti_lookahead(self):
        xml = """<ownershipDocument><issuer><issuerTradingSymbol>ABC</issuerTradingSymbol></issuer><nonDerivativeTransaction>
          <securityTitle><value>Common Stock</value></securityTitle>
          <transactionDate><value>2023-01-03</value></transactionDate>
          <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
          <transactionAmounts><transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
          </nonDerivativeTransaction></ownershipDocument>"""
        self.assertEqual(parse_form4(ET.fromstring(xml), FILING, "Example", "https://www.sec.gov/a"), [])

if __name__ == "__main__":
    unittest.main()
