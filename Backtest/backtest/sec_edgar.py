"""Historical SEC Form 4 purchase and 13F *new-position disclosure* ingestion.

Not a point-in-time archive: SEC's current filing metadata can be revised, and
CUSIP-to-ticker resolution is not necessarily historical. Entry eligibility is
strictly AFTER the original public filing date, never the transaction/report date.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import quote
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from .engine import Event
from .sources import record_key, SYMBOL

SEC_AGENT = os.environ.get("SEC_USER_AGENT", "PoliticalTradeAlpha/1.0 contact@extriver.com")
FORM4_CIKS = {"Berkshire Hathaway": "0001067983"}
FORM13_CIKS = {"Berkshire Hathaway": "0001067983",
               "Pershing Square": "0001336528"}
CASH = re.compile(r"^\d{9}$")


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def descendants(element: ET.Element, tag: str):
    return [child for child in element.iter() if local(child.tag) == tag]


def first_text(element: ET.Element, *path: str) -> str:
    node = element
    for name in path:
        node = next((c for c in node if local(c.tag) == name), None)
        if node is None:
            return ""
    return (node.text or "").strip()


class SecClient:
    """Throttled public filings reader, with dated on-disk JSON/XML cache."""
    def __init__(self, cache: Path, agent: str = SEC_AGENT, delay: float = 0.13):
        self.cache, self.agent, self.delay = cache, agent, delay
        self.cache.mkdir(parents=True, exist_ok=True)

    def get(self, url: str, kind: str = "text") -> str:
        import hashlib
        key = hashlib.sha256(url.encode()).hexdigest()
        path = self.cache / (key + "." + ("json" if kind == "json" else "xml"))
        if path.exists():
            return path.read_text(encoding="utf-8")
        req = Request(url, headers={"User-Agent": self.agent,
                                    "Accept-Encoding": "identity",
                                    "Accept": "application/json, application/xml, text/xml"})
        last_error = None
        for attempt in range(4):
            try:
                with urlopen(req, timeout=40) as resp:
                    body = resp.read(8_000_000).decode("utf-8-sig")
                path.write_text(body, encoding="utf-8")
                time.sleep(self.delay)
                return body
            except Exception as ex:
                last_error = ex
                time.sleep(min(8, 2 ** attempt))
        raise RuntimeError(f"SEC fetch failed: {url}: {type(last_error).__name__}")

    def submissions(self, cik: str) -> list[dict]:
        root = json.loads(self.get(f"https://data.sec.gov/submissions/CIK{cik}.json", "json"))
        arrays = [root["filings"]["recent"]]
        for archived in root["filings"].get("files", []):
            arrays.append(json.loads(self.get("https://data.sec.gov/submissions/" +
                                             quote(archived["name"]), "json")))
        filings = []
        for a in arrays:
            keys = ["form", "accessionNumber", "primaryDocument", "filingDate", "reportDate"]
            n = len(a.get("accessionNumber", []))
            for i in range(n):
                filings.append({k: (a.get(k, [""] * n)[i] or "") for k in keys})
        unique = {f["accessionNumber"]: f for f in filings}
        return sorted(unique.values(), key=lambda x: (x["filingDate"], x["accessionNumber"]))

    def filing_xml(self, cik: str, item: dict, kind: str) -> ET.Element:
        num = str(int(cik))
        base = ("https://www.sec.gov/Archives/edgar/data/" + num + "/" +
                item["accessionNumber"].replace("-", "") + "/")
        primary = item["primaryDocument"].split("/")[-1]
        candidates = [primary] if primary.lower().endswith(".xml") else []
        listing = json.loads(self.get(base + "index.json", "json"))
        candidates += [x["name"] for x in listing["directory"]["item"]
                       if x["name"].lower().endswith(".xml")
                       and x["name"] not in candidates]
        wanted = "ownershipDocument" if kind == "4" else "informationTable"
        for filename in candidates:
            try:
                raw = self.get(base + filename)
                # Some XML wrappers are encoded with declarations; ET handles Unicode.
                root = ET.fromstring(raw.lstrip("\ufeff"))
                if local(root.tag).lower() == wanted.lower():
                    return root
            except (ET.ParseError, UnicodeError):
                continue
        raise ValueError(f"No parseable {kind} data XML in {item['accessionNumber']}")


def parse_form4(root: ET.Element, filing: dict, filer: str, url: str) -> list[Event]:
    ticker = first_text(root, "issuer", "issuerTradingSymbol").upper()
    if not SYMBOL.fullmatch(ticker):
        return []
    filed = date.fromisoformat(filing["filingDate"])
    events = []
    for idx, txn in enumerate(descendants(root, "nonDerivativeTransaction")):
        code = first_text(txn, "transactionCoding", "transactionCode").upper()
        side = first_text(txn, "transactionAmounts", "transactionAcquiredDisposedCode", "value").upper()
        security = first_text(txn, "securityTitle", "value").lower()
        traded_str = first_text(txn, "transactionDate", "value")
        # Only true acquired open-market common stock, not warrants/RSUs/options.
        if code != "P" or side != "A":
            continue
        if not any(token in security for token in ("common", "class a", "class b", "cl a", "cl b")):
            continue
        try:
            traded = date.fromisoformat(traded_str)
        except ValueError:
            continue
        if traded > filed:
            continue
        key = record_key(filer, ticker, traded, filed, "SEC_FORM4", url, str(idx))
        events.append(Event(key, filer, ticker, traded, filed, url, "SEC_FORM4"))
    return events


def parse_13f(root: ET.Element) -> dict[str, float]:
    """Return security-level CUSIP holdings. Options and non-share rows excluded."""
    holdings: dict[str, float] = {}
    for row in descendants(root, "infoTable"):
        cusip = first_text(row, "cusip").upper().replace(" ", "")
        value_str = first_text(row, "value")
        typ = first_text(row, "shrsOrPrnAmt", "sshPrnamtType").upper()
        putcall = first_text(row, "putCall")
        if not CASH.fullmatch(cusip) or typ not in ("SH", "") or putcall:
            continue
        try:
            value = float(value_str)
        except ValueError:
            continue
        if value <= 0:
            continue
        holdings[cusip] = holdings.get(cusip, 0.0) + value
    return holdings


def map_cusips(cusips: set[str], cache: Path) -> tuple[dict[str, str], set[str]]:
    """Resolve with OpenFIGI; ambiguous/non-equity matches are rejected.

    Cache is not historical; caller should inspect mapping lineage before relying
    on a backtest. Never guess tickers from issuer names.
    """
    cache.parent.mkdir(parents=True, exist_ok=True)
    known = json.loads(cache.read_text()) if cache.exists() else {}
    unresolved = sorted(c for c in cusips if c not in known)
    if unresolved:
        import urllib.error
        headers = {"Content-Type": "application/json", "User-Agent": SEC_AGENT}
        if os.environ.get("OPENFIGI_API_KEY"):
            headers["X-OPENFIGI-APIKEY"] = os.environ["OPENFIGI_API_KEY"]
        for offset in range(0, len(unresolved), 5):
            batch = unresolved[offset: offset + 5]
            body = json.dumps([{"idType": "ID_CUSIP", "idValue": c,
                                "exchCode": "US"} for c in batch]).encode()
            request = Request("https://api.openfigi.com/v3/mapping", data=body,
                              headers=headers, method="POST")
            try:
                with urlopen(request, timeout=45) as response:
                    output = json.load(response)
                for c, item in zip(batch, output):
                    matches = item.get("data", [])
                    candidates = {m["ticker"].upper() for m in matches
                                  if SYMBOL.fullmatch(m.get("ticker", "").upper())
                                  and m.get("marketSector", "") == "Equity"
                                  and "ETF" not in m.get("securityType", "").upper()}
                    known[c] = next(iter(candidates)) if len(candidates) == 1 else None
            except (urllib.error.URLError, TimeoutError, ValueError):
                # Fail closed: unresolved mappings are never traded.
                for c in batch:
                    known[c] = None
            time.sleep(1.2)
        cache.write_text(json.dumps(known, indent=2, sort_keys=True), encoding="utf-8")
    return ({c: known[c] for c in cusips if known.get(c)},
            {c for c in cusips if not known.get(c)})


def collect_sec(start_year: int, end_year: int, cache: Path):
    """Two independent signal families. 13F first observed quarter is baseline."""
    client = SecClient(cache / "sec")
    form4: list[Event] = []
    new_positions: list[tuple[str, str, date, date, str, float]] = []
    quality = {"form4_filings": 0, "form4_xml_failed": 0, "form4_buy_events": 0,
               "13f_filings": 0, "13f_xml_failed": 0, "13f_new_cusips": 0}
    for name, cik in FORM4_CIKS.items():
        for f in client.submissions(cik):
            if f["form"] != "4" or not f["filingDate"].startswith(tuple(
                    str(y) for y in range(start_year, end_year + 1))):
                continue
            quality["form4_filings"] += 1
            url = ("https://www.sec.gov/Archives/edgar/data/" + str(int(cik)) + "/" +
                   f["accessionNumber"].replace("-", "") + "/")
            try:
                root = client.filing_xml(cik, f, "4")
                form4.extend(parse_form4(root, f, name, url))
            except (ValueError, RuntimeError):
                quality["form4_xml_failed"] += 1
    quality["form4_buy_events"] = len(form4)
    for name, cik in FORM13_CIKS.items():
        previous: set[str] | None = None
        sequence = []
        for f in client.submissions(cik):
            if f["form"] != "13F-HR":
                continue
            if not f["filingDate"].startswith(tuple(str(y) for y in
                                                     range(start_year, end_year + 1))):
                continue
            sequence.append(f)
        sequence.sort(key=lambda f: (f["reportDate"], f["filingDate"]))
        for f in sequence:
            quality["13f_filings"] += 1
            url = ("https://www.sec.gov/Archives/edgar/data/" + str(int(cik)) + "/" +
                   f["accessionNumber"].replace("-", "") + "/")
            try:
                holdings = parse_13f(client.filing_xml(cik, f, "13F"))
                if not holdings:
                    raise ValueError("Empty information table")
            except (ValueError, RuntimeError):
                quality["13f_xml_failed"] += 1
                # A missing quarter invalidates the immediate new-position comparison.
                previous = None
                continue
            if previous is not None:
                for cusip in sorted(set(holdings) - previous):
                    try:
                        report_date = date.fromisoformat(f["reportDate"])
                        filed_date = date.fromisoformat(f["filingDate"])
                    except ValueError:
                        continue
                    if report_date > filed_date:
                        continue
                    new_positions.append((name, cusip, report_date, filed_date, url, holdings[cusip]))
            previous = set(holdings)
    quality["13f_new_cusips"] = len(new_positions)
    mapping, missing = map_cusips({x[1] for x in new_positions}, cache / "figi.json")
    thirteen: list[Event] = []
    for name, cusip, reported, filed, url, value in new_positions:
        ticker = mapping.get(cusip)
        if not ticker:
            continue
        key = record_key(name, ticker, reported, filed, "SEC_13F_NEW", url, cusip)
        thirteen.append(Event(key, name, ticker, reported, filed, url, "SEC_13F_NEW"))
    quality["13f_mapped_events"] = len(thirteen)
    quality["13f_unmapped_cusips"] = len(missing)
    quality["limitation"] = ("SEC current submission metadata and OpenFIGI current CUSIP mapping "
                             "are not guaranteed point-in-time; do not infer actual 13F purchase dates.")
    return form4 + thirteen, quality
