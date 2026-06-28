"""
Comprehensive financial model builder.

Produces a 10-year explicit FCFF DCF (with Gordon-growth terminal value),
a relative-valuation cross-check using peer multiples, and a WACC/terminal
growth sensitivity grid - using professional conventions:

  - WACC via CAPM (Ke) and post-tax cost of debt (Kd), weighted by market
    value of equity and book value of debt.
  - Revenue growth fades linearly from the recent historical CAGR down to
    the terminal growth rate over the explicit forecast window.
  - EBITDA margin held at the (user-adjustable) historical average, with
    optional linear fade to a normalized margin.
  - Maintenance + growth capex proxied off depreciation, NWC investment
    proxied off incremental revenue - both standard shortcuts used in
    initiating-coverage models when granular schedules aren't public.
  - For banks/NBFCs (where FCFF/EV is not meaningful because interest
    income/expense is the core operating line), an Excess Return / Justified
    P/B model (Ke, sustainable growth = ROE x retention, justified P/B)
    is used instead, which is the standard approach for financials.

All assumptions are returned alongside the outputs so the frontend can
show what was used and let the user override them.
"""

# ---- India market assumptions (documented, override-able) ----
DEFAULT_RISK_FREE_RATE = 7.1      # India 10Y G-Sec yield, %
DEFAULT_EQUITY_RISK_PREMIUM = 6.5  # Damodaran-style India ERP, %
DEFAULT_BETA = 0.95
DEFAULT_COST_OF_DEBT = 8.5         # fallback pre-tax Kd, %
DEFAULT_TAX_RATE = 25.0            # Indian effective corporate tax, %
DEFAULT_TERMINAL_GROWTH = 4.5      # long-run nominal GDP proxy, %
DEFAULT_CAPEX_TO_DA = 1.1          # capex slightly above D&A (growth+maintenance)
DEFAULT_NWC_PCT_OF_DELTA_SALES = 15.0
FORECAST_YEARS = 10


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _last_n(values, n):
    vals = [v for v in values if v is not None]
    return vals[-n:] if len(vals) >= n else vals


def _cagr(values, years):
    series = [v for v in values if v is not None]
    if len(series) < years + 1 or series[-(years + 1)] in (None, 0) or series[-(years + 1)] <= 0:
        return None
    start = series[-(years + 1)]
    end = series[-1]
    if start <= 0 or end <= 0:
        return None
    return ((end / start) ** (1 / years) - 1) * 100


def _get_row(table, *names):
    rows = table.get("rows", {})
    for n in names:
        for key in rows:
            if key.strip().lower() == n.lower():
                return rows[key]
    return []


def _top_ratio_value(top_ratios, name, index=0):
    entry = top_ratios.get(name)
    if not entry:
        return None
    numbers = entry.get("numbers") or []
    if len(numbers) > index:
        return numbers[index]
    return None


def build_model(data, overrides=None):
    overrides = overrides or {}
    pl = data.get("profit_loss", {"periods": [], "rows": {}})
    bs = data.get("balance_sheet", {"periods": [], "rows": {}})
    cf = data.get("cash_flow", {"periods": [], "rows": {}})
    ratios_tbl = data.get("ratios", {"periods": [], "rows": {}})
    top_ratios = data.get("top_ratios", {})
    peers_tbl = data.get("peers", {"headers": [], "peers": []})

    years = pl.get("periods", [])
    sales = _get_row(pl, "Sales", "Revenue")
    expenses = _get_row(pl, "Expenses")
    operating_profit = _get_row(pl, "Operating Profit")
    opm = _get_row(pl, "OPM %")
    other_income = _get_row(pl, "Other Income")
    interest = _get_row(pl, "Interest")
    depreciation = _get_row(pl, "Depreciation")
    pbt = _get_row(pl, "Profit before tax")
    tax_pct = _get_row(pl, "Tax %")
    net_profit = _get_row(pl, "Net Profit")
    eps = _get_row(pl, "EPS in Rs")
    payout_pct = _get_row(pl, "Dividend Payout %")

    borrowings = _get_row(bs, "Borrowings")
    investments = _get_row(bs, "Investments")
    reserves = _get_row(bs, "Reserves")
    equity_capital = _get_row(bs, "Equity Share Capital")

    cfo = _get_row(cf, "Cash from Operating Activity")

    beta_info = data.get("beta_info")
    lease_info = data.get("lease_liabilities") or {}

    market_cap = _top_ratio_value(top_ratios, "Market Cap")
    current_price = _top_ratio_value(top_ratios, "Current Price")
    book_value = _top_ratio_value(top_ratios, "Book Value")
    roe_top = _top_ratio_value(top_ratios, "ROE")
    roce_top = _top_ratio_value(top_ratios, "ROCE")
    stock_pe = _top_ratio_value(top_ratios, "Stock P/E")
    face_value = _top_ratio_value(top_ratios, "Face Value")

    shares_outstanding = None
    if market_cap and current_price:
        shares_outstanding = (market_cap * 1e7) / current_price  # crores -> absolute, shares count
    elif equity_capital and face_value:
        latest_capital = next((v for v in reversed(equity_capital) if v is not None), None)
        if latest_capital:
            shares_outstanding = (latest_capital * 1e7) / face_value

    # ---------------- assumptions ----------------
    rf = float(overrides.get("risk_free_rate", DEFAULT_RISK_FREE_RATE))
    erp = float(overrides.get("equity_risk_premium", DEFAULT_EQUITY_RISK_PREMIUM))
    regression_beta = beta_info.get("beta") if beta_info else None
    beta = float(overrides.get("beta", regression_beta if regression_beta is not None else DEFAULT_BETA))
    beta_source = "override" if "beta" in overrides else ("regression" if regression_beta is not None else "assumed_default")
    terminal_growth = float(overrides.get("terminal_growth", DEFAULT_TERMINAL_GROWTH))
    tax_rate_hist = _avg(_last_n(tax_pct, 3))
    tax_rate = float(overrides.get("tax_rate", tax_rate_hist if tax_rate_hist else DEFAULT_TAX_RATE))
    capex_to_da = float(overrides.get("capex_to_da", DEFAULT_CAPEX_TO_DA))
    nwc_pct = float(overrides.get("nwc_pct_of_delta_sales", DEFAULT_NWC_PCT_OF_DELTA_SALES))

    cagr3 = _cagr(sales, 3)
    cagr5 = _cagr(sales, 5)
    hist_growth = float(overrides.get(
        "revenue_growth_start",
        cagr3 if cagr3 is not None else (cagr5 if cagr5 is not None else 10.0)
    ))
    hist_growth = max(min(hist_growth, 30.0), -10.0)

    margin_hist = _avg(_last_n(opm, 3))
    ebitda_margin = float(overrides.get("ebitda_margin", margin_hist if margin_hist else 18.0))

    latest_borrowings = next((v for v in reversed(borrowings) if v is not None), 0) or 0
    latest_investments = next((v for v in reversed(investments) if v is not None), 0) or 0

    # Ind AS 116 leases nested inside "Borrowings" are already counted in
    # latest_borrowings above; leases screener instead nests inside "Other
    # Liabilities" are NOT, so they're added explicitly here to capitalise
    # them into the interest-bearing debt base used for Kd/WACC.
    lease_in_borrowings = next((v for v in reversed(lease_info.get("in_borrowings", [])) if v is not None), 0) or 0
    lease_in_other = next((v for v in reversed(lease_info.get("in_other_liabilities", [])) if v is not None), 0) or 0
    capitalized_debt = latest_borrowings + lease_in_other

    net_debt = capitalized_debt - latest_investments

    avg_interest = _avg(_last_n(interest, 3))
    cost_of_debt_pretax = float(overrides.get("cost_of_debt", DEFAULT_COST_OF_DEBT))
    cost_of_debt_source = "assumed_default"
    if avg_interest and capitalized_debt:
        implied_kd = (avg_interest / capitalized_debt) * 100
        if 0 < implied_kd < 25:
            cost_of_debt_pretax = implied_kd
            cost_of_debt_source = "implied_from_financials"
    if "cost_of_debt" in overrides:
        cost_of_debt_pretax = float(overrides["cost_of_debt"])
        cost_of_debt_source = "override"

    cost_of_equity = rf + beta * erp
    equity_value_for_wacc = market_cap if market_cap else (
        (book_value or 0) * (shares_outstanding or 0) / 1e7
    )
    debt_value_for_wacc = max(capitalized_debt, 0)
    total_capital = (equity_value_for_wacc or 0) + debt_value_for_wacc
    if total_capital > 0:
        we = (equity_value_for_wacc or 0) / total_capital
        wd = debt_value_for_wacc / total_capital
    else:
        we, wd = 1.0, 0.0
    wacc = we * cost_of_equity + wd * cost_of_debt_pretax * (1 - tax_rate / 100)
    wacc_source = "calculated"
    if "wacc" in overrides:
        wacc = float(overrides["wacc"])
        wacc_source = "override"

    is_banking = data.get("is_banking", False)

    assumptions = {
        "risk_free_rate": rf,
        "equity_risk_premium": erp,
        "beta": beta,
        "beta_source": beta_source,
        "beta_regression": beta_info,
        "cost_of_equity": round(cost_of_equity, 2),
        "cost_of_debt_pretax": round(cost_of_debt_pretax, 2),
        "cost_of_debt_source": cost_of_debt_source,
        "tax_rate": round(tax_rate, 2),
        "wacc": round(wacc, 2),
        "wacc_source": wacc_source,
        "capitalized_debt": round(capitalized_debt, 2),
        "lease_capitalized_in_other_liabilities": round(lease_in_other, 2),
        "lease_already_in_borrowings": round(lease_in_borrowings, 2),
        "terminal_growth": terminal_growth,
        "revenue_growth_start": round(hist_growth, 2),
        "ebitda_margin": round(ebitda_margin, 2),
        "capex_to_da": capex_to_da,
        "nwc_pct_of_delta_sales": nwc_pct,
        "forecast_years": FORECAST_YEARS,
        "model_type": "excess_return_pb" if is_banking else "fcff_dcf",
    }

    result = {
        "assumptions": assumptions,
        "historicals": {
            "years": years,
            "sales": sales,
            "operating_profit": operating_profit,
            "opm_pct": opm,
            "net_profit": net_profit,
            "eps": eps,
            "cfo": cfo,
        },
        "shares_outstanding": shares_outstanding,
        "current_price": current_price,
        "net_debt": net_debt,
    }

    if is_banking:
        result["bank_model"] = _build_bank_model(
            roe_top, payout_pct, cost_of_equity, terminal_growth, book_value,
            current_price, overrides
        )
    else:
        result["dcf"] = _build_fcff_dcf(
            sales, depreciation, hist_growth, ebitda_margin, tax_rate,
            capex_to_da, nwc_pct, terminal_growth, wacc, net_debt,
            shares_outstanding, current_price
        )
        result["sensitivity"] = _sensitivity_grid(
            sales, depreciation, hist_growth, ebitda_margin, tax_rate,
            capex_to_da, nwc_pct, terminal_growth, wacc, net_debt, shares_outstanding
        )

    result["comps"] = _comps_valuation(peers_tbl, eps, net_profit, operating_profit, stock_pe, current_price)
    result["ratios_trend"] = {
        "periods": ratios_tbl.get("periods", []),
        "roce": _get_row(ratios_tbl, "ROCE %"),
        "roe": _get_row(ratios_tbl, "ROE %"),
        "debtor_days": _get_row(ratios_tbl, "Debtor Days"),
        "working_capital_days": _get_row(ratios_tbl, "Working Capital Days"),
    }
    return result


def _project_fcff(sales, depreciation, growth_start, ebitda_margin, tax_rate,
                   capex_to_da, nwc_pct, terminal_growth, years=FORECAST_YEARS):
    last_sales = next((v for v in reversed(sales) if v is not None), None)
    last_da = next((v for v in reversed(depreciation) if v is not None), None)
    if not last_sales:
        return None

    da_pct_of_sales = (last_da / last_sales) if last_da else 0.04
    projections = []
    prev_sales = last_sales
    for yr in range(1, years + 1):
        t = (yr - 1) / max(years - 1, 1)
        growth = growth_start + (terminal_growth - growth_start) * t
        revenue = prev_sales * (1 + growth / 100)
        ebitda = revenue * ebitda_margin / 100
        da = revenue * da_pct_of_sales
        ebit = ebitda - da
        tax = max(ebit, 0) * tax_rate / 100
        nopat = ebit - tax
        capex = da * capex_to_da
        delta_sales = revenue - prev_sales
        delta_nwc = delta_sales * nwc_pct / 100
        fcff = nopat + da - capex - delta_nwc
        projections.append({
            "year": yr,
            "revenue": revenue,
            "growth_pct": growth,
            "ebitda": ebitda,
            "ebitda_margin_pct": ebitda_margin,
            "da": da,
            "ebit": ebit,
            "tax": tax,
            "nopat": nopat,
            "capex": capex,
            "delta_nwc": delta_nwc,
            "fcff": fcff,
        })
        prev_sales = revenue
    return projections


def _discount(projections, wacc, terminal_growth):
    sum_pv = 0.0
    for p in projections:
        df = 1 / ((1 + wacc / 100) ** p["year"])
        p["discount_factor"] = df
        p["pv_fcff"] = p["fcff"] * df
        sum_pv += p["pv_fcff"]

    terminal_fcff = projections[-1]["fcff"] * (1 + terminal_growth / 100)
    if wacc <= terminal_growth:
        terminal_value = None
        pv_terminal = 0.0
    else:
        terminal_value = terminal_fcff / (wacc / 100 - terminal_growth / 100)
        pv_terminal = terminal_value * projections[-1]["discount_factor"]

    enterprise_value = sum_pv + pv_terminal
    return {
        "sum_pv_fcff": sum_pv,
        "terminal_value": terminal_value,
        "pv_terminal_value": pv_terminal,
        "enterprise_value": enterprise_value,
    }


def _build_fcff_dcf(sales, depreciation, growth_start, ebitda_margin, tax_rate,
                     capex_to_da, nwc_pct, terminal_growth, wacc, net_debt,
                     shares_outstanding, current_price):
    projections = _project_fcff(sales, depreciation, growth_start, ebitda_margin,
                                 tax_rate, capex_to_da, nwc_pct, terminal_growth)
    if not projections:
        return {"error": "Insufficient historical sales data to build a DCF."}

    disc = _discount(projections, wacc, terminal_growth)
    enterprise_value = disc["enterprise_value"]
    equity_value = enterprise_value - (net_debt or 0)
    value_per_share = None
    upside_pct = None
    recommendation = None
    if shares_outstanding:
        value_per_share = (equity_value * 1e7) / shares_outstanding
        if current_price:
            upside_pct = (value_per_share / current_price - 1) * 100
            if upside_pct > 15:
                recommendation = "Undervalued"
            elif upside_pct < -15:
                recommendation = "Overvalued"
            else:
                recommendation = "Fairly valued"

    return {
        "projections": projections,
        **disc,
        "net_debt": net_debt,
        "equity_value": equity_value,
        "shares_outstanding": shares_outstanding,
        "value_per_share": value_per_share,
        "current_price": current_price,
        "upside_pct": upside_pct,
        "recommendation": recommendation,
    }


def _sensitivity_grid(sales, depreciation, growth_start, ebitda_margin, tax_rate,
                       capex_to_da, nwc_pct, terminal_growth, wacc, net_debt, shares_outstanding):
    if not shares_outstanding:
        return None
    wacc_range = [round(wacc + d, 2) for d in (-1.0, -0.5, 0, 0.5, 1.0)]
    g_range = [round(terminal_growth + d, 2) for d in (-1.0, -0.5, 0, 0.5, 1.0)]
    grid = []
    for w in wacc_range:
        row = []
        for g in g_range:
            if w <= g:
                row.append(None)
                continue
            projections = _project_fcff(sales, depreciation, growth_start, ebitda_margin,
                                         tax_rate, capex_to_da, nwc_pct, g)
            disc = _discount(projections, w, g)
            equity_value = disc["enterprise_value"] - (net_debt or 0)
            vps = (equity_value * 1e7) / shares_outstanding
            row.append(round(vps, 1))
        grid.append(row)
    return {"wacc_range": wacc_range, "g_range": g_range, "grid": grid}


def _build_bank_model(roe_top, payout_pct, cost_of_equity, terminal_growth, book_value,
                       current_price, overrides):
    roe = float(overrides.get("sustainable_roe", roe_top if roe_top else 15.0))
    avg_payout = _avg(_last_n(payout_pct, 3))
    payout = float(overrides.get("payout_ratio", avg_payout if avg_payout else 20.0))
    retention = max(1 - payout / 100, 0)
    g = min(roe * retention / 100 * 100, roe - 0.5)
    g = max(g, 0)
    ke = float(overrides.get("cost_of_equity_bank", cost_of_equity))

    if ke <= g / 100 + 0.001:
        justified_pb = None
        value_per_share = None
    else:
        justified_pb = (roe / 100 - g / 100) / (ke / 100 - g / 100)
        value_per_share = justified_pb * (book_value or 0)

    upside_pct = None
    recommendation = None
    if value_per_share and current_price:
        upside_pct = (value_per_share / current_price - 1) * 100
        if upside_pct > 15:
            recommendation = "Undervalued"
        elif upside_pct < -15:
            recommendation = "Overvalued"
        else:
            recommendation = "Fairly valued"

    return {
        "sustainable_roe_pct": roe,
        "payout_ratio_pct": payout,
        "retention_ratio_pct": retention * 100,
        "sustainable_growth_pct": g,
        "cost_of_equity_pct": ke,
        "book_value_per_share": book_value,
        "justified_pb": justified_pb,
        "value_per_share": value_per_share,
        "current_price": current_price,
        "upside_pct": upside_pct,
        "recommendation": recommendation,
    }


def _comps_valuation(peers_tbl, eps, net_profit, operating_profit, stock_pe, current_price):
    peers = peers_tbl.get("peers", []) if peers_tbl else []
    pe_values, ev_ebitda_values = [], []
    for p in peers:
        for k, v in p.items():
            kl = k.lower()
            if "p/e" in kl or kl == "pe" and isinstance(v, (int, float)) and v and v > 0:
                pe_values.append(v)
    pe_values = [v for v in pe_values if v and 0 < v < 200]
    peer_avg_pe = _avg(pe_values)

    latest_eps = next((v for v in reversed(eps) if v is not None), None)
    implied_value_pe = peer_avg_pe * latest_eps if (peer_avg_pe and latest_eps) else None

    upside_pct = None
    if implied_value_pe and current_price:
        upside_pct = (implied_value_pe / current_price - 1) * 100

    return {
        "peer_count": len(peers),
        "peer_avg_pe": peer_avg_pe,
        "subject_pe": stock_pe,
        "latest_eps": latest_eps,
        "implied_value_pe": implied_value_pe,
        "current_price": current_price,
        "upside_pct": upside_pct,
        "peers": peers[:15],
    }
