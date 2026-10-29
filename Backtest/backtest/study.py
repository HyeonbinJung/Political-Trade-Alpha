"""Run 4 signal families x 4 exit rules; source events by public filing date."""
from __future__ import annotations
import argparse,csv,json
from pathlib import Path
from datetime import date
from dataclasses import asdict
from .engine import Config,CorporateAction,Event,backtest
from .sec_edgar import collect_sec
from .sources import download_slushfund,load_prices
ROOT=Path(__file__).resolve().parent.parent
FAMILIES={"politicians":{"HOUSE_SENATE"},"form4":{"SEC_FORM4"},
          "13f_new":{"SEC_13F_NEW"},
          "combined":{"HOUSE_SENATE","SEC_FORM4","SEC_13F_NEW"}}
RULES={"TP10":.10,"TP15":.15,"TP20":.20,"HOLD_1Y":None}
COHORT={"nancy pelosi","ro khanna","tommy tuberville"}

def actions():
    result={}
    with (ROOT/"data"/"corporate_actions.csv").open(newline="",encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            result[r["symbol"]]=CorporateAction(
                r["symbol"],date.fromisoformat(r["effective_date"]),
                r["kind"],r["source_url"],
                float(r["cash_per_share"] or 0),r["successor"],
                float(r["ratio"] or 0))
    return result

def simulation(events,prices,start,end,corp):
    result={}
    for name,types in FAMILIES.items():
        subset=[e for e in events if e.source in types]
        for rule,tp in RULES.items():
            key=name+"/"+rule
            try:
                if not subset: raise ValueError("NO_EVENTS")
                response=backtest(subset,prices,start,end,Config(
                    capital=10000,allocation=.10,max_positions=10,
                    take_profit=tp,slippage_bps=5),corp)
                result[key]={"submitted":len(subset),
                             "entered":response["entered_positions"],
                             "closed":response["closed_trades"],
                             "metrics":response["metrics"],
                             "diagnostics":response["diagnostics"]}
            except (RuntimeError,ValueError) as ex:
                result[key]={"submitted":len(subset),"error":str(ex)}
    return result

def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument("--start",type=date.fromisoformat,default=date(2023,1,1))
    p.add_argument("--end",type=date.fromisoformat,default=date(2026,10,27))
    p.add_argument("--offline",action="store_true")
    p.add_argument("--reuse-disclosures",action="store_true")
    args=p.parse_args(argv)
    folder=ROOT/"output"/"study";folder.mkdir(parents=True,exist_ok=True)
    fp=folder/"disclosures.json"
    if args.offline or args.reuse_disclosures:
        saved=json.loads(fp.read_text(encoding="utf-8"))
        records=[]
        for item in saved["events"]:
            item["transaction_date"]=date.fromisoformat(item["transaction_date"])
            item["filing_date"]=date.fromisoformat(item["filing_date"])
            records.append(Event(**item))
        provenance=saved["provenance"]
    else:
        congress,info=download_slushfund(2021,args.end.year)
        congress=[e for e in congress if e.filer.casefold() in COHORT]
        sec,sec_info=collect_sec(2021,args.end.year,ROOT/"cache")
        records=congress+sec
        provenance={"congress":info,"sec":sec_info}
        fp.write_text(json.dumps({"provenance":provenance,"events":[
            {**asdict(e),"transaction_date":e.transaction_date.isoformat(),
             "filing_date":e.filing_date.isoformat()} for e in records]},
            indent=2),encoding="utf-8")
    records=[e for e in records if e.transaction_date.year>=2021
             and e.filing_date<=args.end]
    corp=actions()
    symbols={e.symbol for e in records if e.filing_date>=args.start}
    symbols|={a.successor for a in corp.values() if a.successor}
    prices,missing=load_prices(symbols,args.start,args.end,
                               ROOT/"cache"/"prices",offline=args.offline)
    output=simulation(records,prices,args.start,args.end,corp)
    snapshot={"period":[str(args.start),str(args.end)],
              "provenance":provenance,"missing_prices":missing,
              "scenarios":output}
    (folder/"summary.json").write_text(json.dumps(
        snapshot,indent=2,ensure_ascii=False),encoding="utf-8")
    lines=["# SEC + Congress disclosure-date backtest",
           "Start $10,000; maximum 10 positions; 10% per position; 5bp slippage.",
           "Entry strictly after public filing date; exit TP10/15/20 or one-year.",
           "13F signals mean new positions on a disclosed quarter-end report, NOT actual purchases.",
           "SEC and OpenFIGI latest datasets are not immutable point-in-time archives.",
           "Intraday high touch assumes optimistic limit execution.",
           "WBA contingent DAP is excluded; merger fractional shares approximated.",
           "",
           "| Group | Exit | Portfolio | SPY | QQQ | Trades | Status |",
           "|---|---|---:|---:|---:|---:|---|"]
    rows=[]
    for key,res in output.items():
        group,rule=key.split("/")
        metrics=res.get("metrics",{})
        v=lambda n:metrics.get(n,{}).get("total_return_pct")
        fmt=lambda x:"N/A" if x is None else f"{x:+.2f}%"
        lines.append(f"| {group} | {rule} | {fmt(v('portfolio'))} | "
                     f"{fmt(v('SPY'))} | {fmt(v('QQQ'))} | "
                     f"{res.get('entered',0)} | {res.get('error','OK')} |")
        rows.append({"group":group,"rule":rule,"portfolio":v("portfolio"),
                     "SPY":v("SPY"),"QQQ":v("QQQ"),
                     "trades":res.get("entered"),"status":res.get("error","OK")})
    lines.extend(["","Missing pricing: "+", ".join(missing),
                  "Raw quality diagnostics in summary.json"])
    (folder/"report.md").write_text("\n".join(lines),encoding="utf-8")
    with (folder/"scenarios.csv").open("w",newline="",encoding="utf-8") as fh:
        w=csv.DictWriter(fh,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    print("\n".join(lines))
if __name__=="__main__":main()
