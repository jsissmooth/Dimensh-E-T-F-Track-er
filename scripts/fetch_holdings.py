import json
import os
import sys
import requests
import pandas as pd
from datetime import date, timedelta
from io import StringIO

BASE_URL = "https://tools-blob.dimensional.com/etf/{date}/{ticker}.csv"
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

ETFS = [
    "DFAC", "DEXC", "DEHP", "DFMC", "DFEV", "DFUV", "DFSV",
    "DFAT", "DFAS", "DFLV", "DFAR", "DUSG", "DFAW", "DXUV",
    "DFGR", "DFIV", "DUHP", "DIHP", "DFGP", "DFNM", "DFAL"
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.dimensional.com/",
    "Accept": "text/csv,*/*",
}


def find_latest_date():
    """Try dates going back from today until we find an available file."""
    d = date.today()
    for _ in range(14):
        date_str = d.strftime("%Y%m%d")
        url = BASE_URL.format(date=date_str, ticker="DFAC")
        try:
            resp = requests.head(url, headers=HEADERS, timeout=10, allow_redirects=True)
            if resp.status_code == 200:
                print("Latest available date: {} ({})".format(d.isoformat(), date_str), file=sys.stderr)
                return d.isoformat(), date_str
        except Exception:
            pass
        d -= timedelta(days=1)
    return None, None


def download_csv(ticker, date_str):
    url = BASE_URL.format(date=date_str, ticker=ticker)
    resp = requests.get(url, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    return resp.text


def parse_holdings(csv_text):
    df = pd.read_csv(StringIO(csv_text))
    records = []

    def safe_float(val):
        try:
            v = float(str(val).strip())
            return None if pd.isna(v) else v
        except (ValueError, TypeError):
            return None

    for _, row in df.iterrows():
        ticker = str(row.get("ticker", "")).strip()
        name   = str(row.get("description", "")).strip()
        if not ticker or ticker.lower() in ("nan", "ticker"):
            continue

        weight       = safe_float(row.get("weight"))
        pct_of_fund  = round(weight * 100, 6) if weight is not None else None
        quantity     = safe_float(row.get("shares"))
        market_value = safe_float(row.get("market_value"))
        identifier   = str(row.get("identifier", "")).strip()
        if identifier.lower() == "nan":
            identifier = ""

        records.append({
            "ticker":       ticker,
            "name":         name,
            "identifier":   identifier,
            "pct_of_fund":  pct_of_fund,
            "quantity":     quantity,
            "market_value": market_value,
            "sector":       "",
        })
    return records


def get_etf_data_dir(etf_ticker):
    d = os.path.join(DATA_DIR, etf_ticker)
    os.makedirs(d, exist_ok=True)
    return d


def save_snapshot(records, today_str, etf_ticker):
    data_dir = get_etf_data_dir(etf_ticker)
    payload = {"date": today_str, "ticker": etf_ticker, "holdings": records}
    with open(os.path.join(data_dir, "{}.json".format(today_str)), "w") as f:
        json.dump(payload, f, indent=2)
    with open(os.path.join(data_dir, "latest.json"), "w") as f:
        json.dump(payload, f, indent=2)


def find_prior_snapshot(today_str, etf_ticker):
    data_dir = get_etf_data_dir(etf_ticker)
    files = sorted(
        f for f in os.listdir(data_dir)
        if f.endswith(".json") and f not in ("latest.json", "diff.json", "history.json")
    )
    prior = [f for f in files if f.replace(".json", "") < today_str]
    return os.path.join(data_dir, prior[-1]) if prior else None


def compute_diff(today_records, prior_records, today_str, prior_date_str, etf_ticker):
    today_map = {r["ticker"]: r for r in today_records}
    prior_map = {r["ticker"]: r for r in prior_records}
    all_keys  = sorted(set(today_map) | set(prior_map))
    rows = []
    for key in all_keys:
        t = today_map.get(key)
        p = prior_map.get(key)
        if t and p:
            q_today   = t["quantity"] or 0
            q_prior   = p["quantity"] or 0
            pct_today = t["pct_of_fund"] or 0
            pct_prior = p["pct_of_fund"] or 0
            qty_chg   = ((q_today - q_prior) / q_prior * 100) if q_prior != 0 else 0
            rows.append({
                "ticker":              t["ticker"],
                "name":                t.get("name") or p.get("name") or "",
                "identifier":          t.get("identifier") or "",
                "sector":              t.get("sector") or "",
                "status":              "changed" if round(qty_chg, 6) != 0 else "unchanged",
                "quantity_today":      q_today,
                "quantity_prior":      q_prior,
                "quantity_pct_change": round(qty_chg, 4),
                "pct_of_fund_today":   pct_today,
                "pct_of_fund_prior":   pct_prior,
                "pct_of_fund_change":  round(pct_today - pct_prior, 6),
                "market_value_today":  t.get("market_value"),
            })
        elif t:
            rows.append({
                "ticker": t["ticker"], "name": t.get("name") or "",
                "identifier": t.get("identifier") or "", "sector": t.get("sector") or "",
                "status": "added",
                "quantity_today": t["quantity"] or 0, "quantity_prior": None,
                "quantity_pct_change": None,
                "pct_of_fund_today": t["pct_of_fund"] or 0, "pct_of_fund_prior": None,
                "pct_of_fund_change": None, "market_value_today": t.get("market_value"),
            })
        else:
            rows.append({
                "ticker": p["ticker"], "name": p.get("name") or "",
                "identifier": p.get("identifier") or "", "sector": p.get("sector") or "",
                "status": "removed",
                "quantity_today": None, "quantity_prior": p["quantity"] or 0,
                "quantity_pct_change": None, "pct_of_fund_today": None,
                "pct_of_fund_prior": p["pct_of_fund"] or 0,
                "pct_of_fund_change": None, "market_value_today": None,
            })
    return {"date": today_str, "ticker": etf_ticker, "prior_date": prior_date_str, "diff": rows}


def append_history(today_str, diff, etf_ticker):
    data_dir = get_etf_data_dir(etf_ticker)
    history_path = os.path.join(data_dir, "history.json")
    history = []
    if os.path.exists(history_path):
        with open(history_path) as f:
            history = json.load(f)
    entry = {"date": today_str, "prior_date": diff["prior_date"]}
    if entry not in history:
        history.append(entry)
        history.sort(key=lambda x: x["date"], reverse=True)
    with open(history_path, "w") as f:
        json.dump(history, f, indent=2)


def process_etf(etf_ticker, today_str, date_str):
    print("Fetching {}...".format(etf_ticker), file=sys.stderr)
    try:
        csv_text = download_csv(etf_ticker, date_str)
        records  = parse_holdings(csv_text)
        if not records:
            print("  No holdings parsed.", file=sys.stderr)
            return
        print("  {} holdings found.".format(len(records)), file=sys.stderr)
        save_snapshot(records, today_str, etf_ticker)

        prior_path = find_prior_snapshot(today_str, etf_ticker)
        if not prior_path:
            diff_rows = []
            for r in records:
                diff_rows.append({
                    "ticker":              r["ticker"],
                    "name":                r.get("name") or "",
                    "identifier":          r.get("identifier") or "",
                    "sector":              r.get("sector") or "",
                    "status":              "unchanged",
                    "quantity_today":      r["quantity"] or 0,
                    "quantity_prior":      r["quantity"] or 0,
                    "quantity_pct_change": 0,
                    "pct_of_fund_today":   r["pct_of_fund"] or 0,
                    "pct_of_fund_prior":   r["pct_of_fund"] or 0,
                    "pct_of_fund_change":  0,
                    "market_value_today":  r.get("market_value"),
                })
            diff = {"date": today_str, "ticker": etf_ticker, "prior_date": None, "diff": diff_rows}
        else:
            with open(prior_path) as f:
                prior_data = json.load(f)
            if prior_data["date"] == today_str:
                print("  Already have data for {} -- skipping.".format(today_str), file=sys.stderr)
                return
            diff = compute_diff(records, prior_data["holdings"], today_str, prior_data["date"], etf_ticker)

        data_dir = get_etf_data_dir(etf_ticker)
        with open(os.path.join(data_dir, "diff.json"), "w") as f:
            json.dump(diff, f, indent=2)

        append_history(today_str, diff, etf_ticker)

        changed = sum(1 for r in diff["diff"] if r["status"] == "changed")
        added   = sum(1 for r in diff["diff"] if r["status"] == "added")
        removed = sum(1 for r in diff["diff"] if r["status"] == "removed")
        print("  Done -- {} changed | {} added | {} removed".format(
            changed, added, removed), file=sys.stderr)

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            print("  No file available for {} on {}.".format(etf_ticker, today_str), file=sys.stderr)
        else:
            print("  HTTP ERROR: {}".format(e), file=sys.stderr)
    except Exception as e:
        print("  ERROR for {}: {}".format(etf_ticker, e), file=sys.stderr)


def main():
    print("Finding latest available date...", file=sys.stderr)
    today_str, date_str = find_latest_date()

    if not today_str:
        print("Could not find any available date in the last 14 days.", file=sys.stderr)
        sys.exit(1)

    print("Running for {}...".format(today_str), file=sys.stderr)
    for etf_ticker in ETFS:
        process_etf(etf_ticker, today_str, date_str)
    print("All done.", file=sys.stderr)


if __name__ == "__main__":
    main()
