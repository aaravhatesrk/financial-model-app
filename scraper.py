"""
screener.in scraper.

Pulls public, non-login-gated data from screener.in: top ratios, quarterly
results, P&L, balance sheet, cash flow, key ratios history, peer comparison
and shareholding pattern. All numbers are parsed into plain floats (crores
for absolute figures unless noted).
"""
import re
import time
import requests
from bs4 import BeautifulSoup

BASE = "https://www.screener.in"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
NIFTY50_TICKER = "%5ENSEI"  # ^NSEI, used as the market proxy for beta regression
BETA_LOOKBACK_RANGE = "5y"
BETA_MIN_DATA_POINTS = 52  # require >=1y of weekly observations before trusting a regression beta

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

_session = requests.Session()
_session.headers.update(HEADERS)

# very small in-memory cache so repeated dashboard/model requests for the
# same company don't hammer screener.in
_cache = {}
_CACHE_TTL = 600  # seconds

# Nifty 50 weekly returns are the same for every company in a given run -
# cache them once instead of refetching per request.
_index_returns_cache = {}


def _get(url, params=None):
    r = _session.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r


def search_companies(query):
    """Case-insensitive company name/ticker search using screener's own
    autocomplete API."""
    query = (query or "").strip()
    if not query:
        return []
    resp = _get(f"{BASE}/api/company/search/", params={"q": query})
    try:
        data = resp.json()
    except ValueError:
        return []
    out = []
    for item in data:
        out.append({
            "id": item.get("id"),
            "name": item.get("name"),
            "url": item.get("url"),
        })
    return out


def _clean_number(text):
    if text is None:
        return None
    t = text.strip().replace(",", "").replace("₹", "").replace("%", "")
    t = t.replace(" ", "").replace("\xa0", "")
    if t in ("", "-", "--"):
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    try:
        val = float(t)
    except ValueError:
        return None
    return -val if neg else val


def _parse_top_ratios(soup):
    ratios = {}
    ul = soup.find("ul", id="top-ratios")
    if not ul:
        return ratios
    for li in ul.find_all("li"):
        name_el = li.find("span", class_="name")
        val_el = li.find("span", class_="value")
        if not name_el or not val_el:
            continue
        name = " ".join(name_el.get_text(strip=True).split())
        numbers = [n.get_text(strip=True) for n in val_el.find_all("span", class_="number")]
        raw_text = " ".join(val_el.get_text(" ", strip=True).split())
        ratios[name] = {
            "raw": raw_text,
            "numbers": [_clean_number(n) for n in numbers],
        }
    return ratios


def _parse_data_table(section):
    """Parse a screener `data-table` inside a <section>: returns
    {"periods": [...], "rows": {row_label: [values...]}}"""
    table = section.find("table", class_="data-table")
    if not table:
        return {"periods": [], "rows": {}}

    periods = []
    thead = table.find("thead")
    if thead:
        for th in thead.find_all("th")[1:]:
            label = " ".join(th.get_text(" ", strip=True).split())
            if label:
                periods.append(label)

    rows = {}
    tbody = table.find("tbody")
    if not tbody:
        return {"periods": periods, "rows": rows}

    for tr in tbody.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue
        label_cell = tds[0]
        label = " ".join(label_cell.get_text(" ", strip=True).split())
        label = label.replace("+", "").strip()
        if not label:
            continue
        values = [_clean_number(td.get_text(strip=True)) for td in tds[1:]]
        rows[label] = values
    return {"periods": periods, "rows": rows}


def _th_label(th):
    full = " ".join(th.get_text(" ", strip=True).split())
    span = th.find("span")
    if span:
        unit = span.get_text(strip=True)
        if unit:
            full = full.replace(unit, "").strip()
    return full


def _parse_peers(soup):
    """Peer comparison is loaded client-side via
    /api/company/{warehouseId}/peers/ - fetch it separately using the
    warehouse id embedded in the main page."""
    info_div = soup.find(id="company-info")
    warehouse_id = info_div.get("data-warehouse-id") if info_div else None
    if not warehouse_id:
        return {"headers": [], "peers": []}

    try:
        resp = _get(f"{BASE}/api/company/{warehouse_id}/peers/")
    except requests.RequestException:
        return {"headers": [], "peers": []}

    peer_soup = BeautifulSoup(resp.text, "html.parser")
    table = peer_soup.find("table")
    if not table:
        return {"headers": [], "peers": []}

    rows = table.find_all("tr")
    if not rows:
        return {"headers": [], "peers": []}

    headers = [_th_label(th) for th in rows[0].find_all("th")]

    peers = []
    for tr in rows[1:]:
        tds = tr.find_all("td")
        if not tds:
            continue
        row = {}
        for i, td in enumerate(tds):
            col = headers[i] if i < len(headers) else f"col{i}"
            a = td.find("a")
            if a and a.get_text(strip=True):
                row["name"] = a.get_text(strip=True)
                row["url"] = a.get("href")
                row[col] = row["name"]
            else:
                text = " ".join(td.get_text(" ", strip=True).split())
                val = _clean_number(text)
                row[col] = val if val is not None else text
        peers.append(row)
    return {"headers": headers, "peers": peers}


def _parse_exchange_codes(soup):
    """NSE/BSE tickers are rendered as plain text inside `<span class="upper">`
    links to nseindia.com / bseindia.com - needed to map a screener company
    to a Yahoo Finance ticker for beta."""
    codes = {"nse": None, "bse": None}
    for span in soup.find_all("span", class_="upper"):
        text = " ".join(span.get_text(" ", strip=True).split())
        if text.upper().startswith("NSE:"):
            codes["nse"] = text.split(":", 1)[1].strip()
        elif text.upper().startswith("BSE:"):
            codes["bse"] = text.split(":", 1)[1].strip()
    return codes


def _get_schedule(company_id, parent, section, consolidated):
    """Screener loads breakups of aggregated rows (e.g. what's inside
    "Borrowings") on demand via this endpoint - used here to find the
    Lease Liabilities sub-line so leases can be confirmed as part of debt."""
    if not company_id:
        return {}
    params = {"parent": parent, "section": section}
    if consolidated:
        params["consolidated"] = ""
    try:
        resp = _get(f"{BASE}/api/company/{company_id}/schedules/", params=params)
        return resp.json()
    except (requests.RequestException, ValueError):
        return {}


def _extract_lease_row(schedule_json, periods):
    """Sum any sub-line in a schedule breakup whose label mentions "lease"
    (e.g. "Lease Liabilities"), aligned to the parent table's periods."""
    lease_keys = [k for k in schedule_json if "lease" in k.lower()]
    if not lease_keys:
        return [None] * len(periods)
    totals = [0.0] * len(periods)
    found = [False] * len(periods)
    for k in lease_keys:
        series = schedule_json.get(k) or {}
        for i, p in enumerate(periods):
            v = _clean_number(series.get(p))
            if v is not None:
                totals[i] += v
                found[i] = True
    return [t if f else None for t, f in zip(totals, found)]


def get_lease_liabilities(company_id, periods, is_consolidated):
    """Under Ind AS 116, lease liabilities are capitalised on balance sheet
    and screener nests them as a sub-line inside either "Borrowings" or
    "Other Liabilities". Surface them explicitly (summed across wherever
    they show up) so the financial model can confirm/disclose that the debt
    figure used for Kd/WACC already capitalises leases."""
    try:
        in_borrowings = _extract_lease_row(
            _get_schedule(company_id, "Borrowings", "balance-sheet", is_consolidated), periods)
        in_other = _extract_lease_row(
            _get_schedule(company_id, "Other Liabilities", "balance-sheet", is_consolidated), periods)
    except requests.RequestException:
        return {"in_borrowings": [None] * len(periods), "in_other_liabilities": [None] * len(periods)}
    return {"in_borrowings": in_borrowings, "in_other_liabilities": in_other}


def _yahoo_weekly_closes(ticker, range_=BETA_LOOKBACK_RANGE):
    resp = _get(YAHOO_CHART_URL.format(ticker=ticker), params={"range": range_, "interval": "1wk"})
    payload = resp.json()
    result = payload["chart"]["result"][0]
    timestamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    return [(t, c) for t, c in zip(timestamps, closes) if c is not None]


def _to_weekly_returns(series):
    by_ts = dict(series)
    times = sorted(by_ts)
    returns = {}
    prev = None
    for t in times:
        c = by_ts[t]
        if prev and prev > 0:
            returns[t] = (c / prev) - 1
        prev = c
    return returns


def _index_returns(range_=BETA_LOOKBACK_RANGE):
    cached = _index_returns_cache.get(range_)
    if cached and (time.time() - cached[0]) < _CACHE_TTL:
        return cached[1]
    returns = _to_weekly_returns(_yahoo_weekly_closes(NIFTY50_TICKER, range_))
    _index_returns_cache[range_] = (time.time(), returns)
    return returns


def get_beta(nse_code, bse_code, range_=BETA_LOOKBACK_RANGE):
    """Calculate beta via OLS slope of weekly stock returns vs Nifty 50
    weekly returns, using Yahoo Finance's public chart endpoint - rather than
    assuming a flat default beta for every company."""
    ticker = f"{nse_code}.NS" if nse_code else (f"{bse_code}.BO" if bse_code else None)
    if not ticker:
        return None
    try:
        stock_returns = _to_weekly_returns(_yahoo_weekly_closes(ticker, range_))
        index_returns = _index_returns(range_)
    except (requests.RequestException, ValueError, KeyError, IndexError):
        return None

    common = sorted(set(stock_returns) & set(index_returns))
    if len(common) < BETA_MIN_DATA_POINTS:
        return None

    x = [index_returns[t] for t in common]
    y = [stock_returns[t] for t in common]
    n = len(x)
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y)) / n
    var_x = sum((xi - mean_x) ** 2 for xi in x) / n
    if var_x == 0:
        return None

    return {
        "beta": round(cov / var_x, 2),
        "ticker": ticker,
        "index": "NIFTY 50 (^NSEI)",
        "lookback": range_,
        "data_points": n,
    }


def _company_page_url(company_url):
    if not company_url.startswith("http"):
        company_url = BASE + company_url
    return company_url


def get_company(company_url, company_id=None, name_hint=None):
    """Fetch and parse the full screener.in company page."""
    cache_key = company_url
    cached = _cache.get(cache_key)
    if cached and (time.time() - cached[0]) < _CACHE_TTL:
        return cached[1]

    url = _company_page_url(company_url)
    resp = _get(url)
    soup = BeautifulSoup(resp.text, "html.parser")

    h1 = soup.find("h1")
    company_name = h1.get_text(strip=True) if h1 else (name_hint or "")

    data = {
        "name": company_name,
        "url": url,
        "id": company_id,
        "top_ratios": _parse_top_ratios(soup),
    }

    section_map = {
        "quarters": "quarterly",
        "profit-loss": "profit_loss",
        "balance-sheet": "balance_sheet",
        "cash-flow": "cash_flow",
        "ratios": "ratios",
        "shareholding": "shareholding",
    }
    for section_id, key in section_map.items():
        section = soup.find("section", id=section_id)
        data[key] = _parse_data_table(section) if section else {"periods": [], "rows": {}}

    # Banks/NBFCs present "Financing Profit" / "Deposits" instead of the
    # standard "Sales" / "Operating Profit" - that's the most reliable
    # structural signal that FCFF/EV is not a meaningful valuation lens.
    pl_rows = data["profit_loss"]["rows"]
    bs_rows = data["balance_sheet"]["rows"]
    data["is_banking"] = "Financing Profit" in pl_rows or "Deposits" in bs_rows

    data["peers"] = _parse_peers(soup)

    about_el = soup.find("p", class_="sub")
    data["about"] = about_el.get_text(" ", strip=True) if about_el else ""

    # Real, company-specific beta (regression of weekly returns vs Nifty 50)
    # instead of a single flat assumption shared by every company.
    exchange_codes = _parse_exchange_codes(soup)
    data["exchange_codes"] = exchange_codes
    data["beta_info"] = get_beta(exchange_codes.get("nse"), exchange_codes.get("bse"))

    # screener.in's standalone vs consolidated company pages are distinguished
    # by "/consolidated/" in the path - needed so the lease schedule lookup
    # (and Kd/WACC capital structure) matches the same basis as the P&L/BS data.
    is_consolidated = "/consolidated" in url.lower()
    data["is_consolidated"] = is_consolidated
    data["lease_liabilities"] = get_lease_liabilities(
        company_id, data["balance_sheet"].get("periods", []), is_consolidated
    )

    _cache[cache_key] = (time.time(), data)
    return data
