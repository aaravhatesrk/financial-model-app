const state = {
  company: null,      // {url, id, name}
  companyData: null,  // raw /api/company response
  modelData: null,    // raw /api/model response
  charts: {},
};

const el = (id) => document.getElementById(id);

// ---------------- Search ----------------
let searchTimer = null;
let activeIndex = -1;

el("searchInput").addEventListener("input", (e) => {
  const q = e.target.value.trim();
  clearTimeout(searchTimer);
  if (!q) {
    el("searchResults").classList.add("hidden");
    return;
  }
  searchTimer = setTimeout(() => runSearch(q), 250);
});

el("searchInput").addEventListener("keydown", (e) => {
  const list = el("searchResults");
  const items = [...list.querySelectorAll(".search-result-item")];
  if (!items.length) return;
  if (e.key === "ArrowDown") { e.preventDefault(); activeIndex = Math.min(activeIndex + 1, items.length - 1); }
  else if (e.key === "ArrowUp") { e.preventDefault(); activeIndex = Math.max(activeIndex - 1, 0); }
  else if (e.key === "Enter") { e.preventDefault(); if (items[activeIndex]) items[activeIndex].click(); return; }
  else return;
  items.forEach((it, i) => it.classList.toggle("active", i === activeIndex));
});

document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    const q = chip.dataset.q;
    el("searchInput").value = q;
    runSearch(q, true);
  });
});

document.addEventListener("click", (e) => {
  if (!e.target.closest(".search-wrap")) el("searchResults").classList.add("hidden");
});

async function runSearch(query, autoPick = false) {
  el("searchSpinner").classList.remove("hidden");
  // case-insensitivity: normalize before sending so "TCS" / "tcs" / "Tcs" behave identically
  const normalized = query.trim().toLowerCase();
  try {
    const res = await fetch(`/api/search?q=${encodeURIComponent(normalized)}`);
    const data = await res.json();
    el("searchSpinner").classList.add("hidden");
    if (data.error) { showSearchEmpty(data.error); return; }
    renderSearchResults(data.results || []);
    if (autoPick && data.results && data.results.length) {
      selectCompany(data.results[0]);
    }
  } catch (err) {
    el("searchSpinner").classList.add("hidden");
    showSearchEmpty("Network error while searching.");
  }
}

function showSearchEmpty(msg) {
  const box = el("searchResults");
  box.innerHTML = `<div class="search-empty">${msg}</div>`;
  box.classList.remove("hidden");
}

function renderSearchResults(results) {
  const box = el("searchResults");
  activeIndex = -1;
  if (!results.length) {
    showSearchEmpty("No matching companies found on screener.in.");
    return;
  }
  box.innerHTML = results.map((r, i) => `
    <div class="search-result-item" data-idx="${i}">
      <div class="search-result-name">${escapeHtml(r.name)}</div>
      <div class="search-result-url">${escapeHtml(r.url)}</div>
    </div>
  `).join("");
  box.classList.remove("hidden");
  [...box.querySelectorAll(".search-result-item")].forEach((node, i) => {
    node.addEventListener("click", () => selectCompany(results[i]));
  });
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------------- Company selection / dashboard ----------------
async function selectCompany(company) {
  state.company = company;
  el("searchResults").classList.add("hidden");
  el("searchInput").value = company.name;

  showState("loading");
  el("loadingText").textContent = `Fetching ${company.name} data from screener.in…`;

  try {
    const res = await fetch(`/api/company?url=${encodeURIComponent(company.url)}&id=${encodeURIComponent(company.id || "")}`);
    const data = await res.json();
    if (data.error) { showState("error", data.error); return; }
    state.companyData = data;
    renderDashboard(data);
    showState("dashboard");
    await loadModel({});
  } catch (err) {
    showState("error", "Network error while loading company data.");
  }
}

function showState(which, errMsg) {
  el("emptyState").classList.add("hidden");
  el("loadingState").classList.add("hidden");
  el("errorState").classList.add("hidden");
  el("dashboard").classList.add("hidden");
  if (which === "empty") el("emptyState").classList.remove("hidden");
  if (which === "loading") el("loadingState").classList.remove("hidden");
  if (which === "error") { el("errorState").classList.remove("hidden"); el("errorText").textContent = errMsg || "Something went wrong."; }
  if (which === "dashboard") el("dashboard").classList.remove("hidden");
}

// ---------------- Tabs ----------------
document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    el(`tab-${btn.dataset.tab}`).classList.add("active");
  });
});

// ---------------- Rendering: header & overview ----------------
function renderDashboard(data) {
  el("companyName").textContent = data.name || "Unknown company";
  el("companyAbout").textContent = data.about || "";

  const keyRatios = ["Market Cap", "Current Price", "Stock P/E", "Book Value", "ROCE", "ROE", "Dividend Yield", "Face Value"];
  const ratioStrip = el("ratioStrip");
  ratioStrip.innerHTML = keyRatios.map((name) => {
    const entry = data.top_ratios[name];
    const text = entry && entry.raw ? entry.raw : "—";
    return `<div class="ratio-box"><div class="label">${name}</div><div class="value">${escapeHtml(text)}</div></div>`;
  }).join("");

  renderFinancialTables(data);
  renderOverviewCharts(data);
  renderPeers(data);
  renderShareholding(data);
}

function buildTable(container, periods, rowsObj, rowOrder, formatValue) {
  const keys = rowOrder.filter((k) => rowsObj[k]);
  Object.keys(rowsObj).forEach((k) => { if (!keys.includes(k)) keys.push(k); });
  if (!keys.length) { container.innerHTML = '<p class="muted">No data available.</p>'; return; }
  let html = '<table class="data-grid"><thead><tr><th>Particulars</th>';
  periods.forEach((p) => html += `<th>${escapeHtml(p)}</th>`);
  html += "</tr></thead><tbody>";
  keys.forEach((k) => {
    html += `<tr><td>${escapeHtml(k)}</td>`;
    const vals = rowsObj[k];
    periods.forEach((_, i) => {
      const v = vals[i];
      const text = v === null || v === undefined ? "—" : (formatValue ? formatValue(k, v) : formatNum(v));
      html += `<td>${text}</td>`;
    });
    html += "</tr>";
  });
  html += "</tbody></table>";
  container.innerHTML = html;
}

function formatNum(v) {
  if (typeof v !== "number") return v;
  return v.toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

function renderFinancialTables(data) {
  buildTable(el("tblProfitLoss"), data.profit_loss.periods, data.profit_loss.rows,
    ["Sales", "Expenses", "Operating Profit", "OPM %", "Other Income", "Interest", "Depreciation",
     "Profit before tax", "Tax %", "Net Profit", "EPS in Rs", "Dividend Payout %"]);
  buildTable(el("tblBalanceSheet"), data.balance_sheet.periods, data.balance_sheet.rows,
    ["Equity Share Capital", "Reserves", "Borrowings", "Other Liabilities", "Total Liabilities",
     "Fixed Assets", "CWIP", "Investments", "Other Assets", "Total Assets"]);
  buildTable(el("tblCashFlow"), data.cash_flow.periods, data.cash_flow.rows,
    ["Cash from Operating Activity", "Cash from Investing Activity", "Cash from Financing Activity", "Net Cash Flow"]);
}

function destroyChart(key) {
  if (state.charts[key]) { state.charts[key].destroy(); delete state.charts[key]; }
}

const CHART_COLORS = { blue: "#4f8cff", green: "#22c58b", red: "#ef5b5b", amber: "#f2b84b", purple: "#a78bfa" };

function renderOverviewCharts(data) {
  const pl = data.profit_loss;
  const sales = pl.rows["Sales"] || [];
  const netProfit = pl.rows["Net Profit"] || [];
  const opm = pl.rows["OPM %"] || [];

  destroyChart("revProfit");
  state.charts.revProfit = new Chart(el("chartRevenueProfit"), {
    type: "bar",
    data: {
      labels: pl.periods,
      datasets: [
        { label: "Sales", data: sales, backgroundColor: CHART_COLORS.blue },
        { label: "Net Profit", data: netProfit, backgroundColor: CHART_COLORS.green },
      ],
    },
    options: chartOpts(),
  });

  destroyChart("margins");
  state.charts.margins = new Chart(el("chartMargins"), {
    type: "line",
    data: { labels: pl.periods, datasets: [{ label: "OPM %", data: opm, borderColor: CHART_COLORS.amber, tension: 0.3 }] },
    options: chartOpts(),
  });

  const ratios = data.ratios;
  destroyChart("returns");
  state.charts.returns = new Chart(el("chartReturns"), {
    type: "line",
    data: {
      labels: ratios.periods,
      datasets: [
        { label: "ROCE %", data: ratios.rows["ROCE %"] || [], borderColor: CHART_COLORS.purple, tension: 0.3 },
        { label: "ROE %", data: ratios.rows["ROE %"] || [], borderColor: CHART_COLORS.red, tension: 0.3 },
      ],
    },
    options: chartOpts(),
  });

  const q = data.quarterly;
  const qSales = q.rows["Sales"] || [];
  const qProfit = q.rows["Net Profit"] || [];
  destroyChart("quarterly");
  state.charts.quarterly = new Chart(el("chartQuarterly"), {
    type: "bar",
    data: {
      labels: q.periods,
      datasets: [
        { label: "Sales", data: qSales, backgroundColor: CHART_COLORS.blue },
        { label: "Net Profit", data: qProfit, backgroundColor: CHART_COLORS.green },
      ],
    },
    options: chartOpts(),
  });
}

function chartOpts() {
  return {
    responsive: true,
    plugins: { legend: { labels: { color: "#e7ecf5" } } },
    scales: {
      x: { ticks: { color: "#8a93a6" }, grid: { color: "#232c3f" } },
      y: { ticks: { color: "#8a93a6" }, grid: { color: "#232c3f" } },
    },
  };
}

function renderPeers(data) {
  const peers = data.peers.peers || [];
  const headers = data.peers.headers || [];
  if (!peers.length) { el("tblPeers").innerHTML = '<p class="muted">No peer data available.</p>'; return; }
  const cols = headers.length ? headers : Object.keys(peers[0]);
  let html = '<table class="data-grid"><thead><tr>' + cols.map((c) => `<th>${escapeHtml(c)}</th>`).join("") + "</tr></thead><tbody>";
  peers.forEach((p) => {
    html += "<tr>" + cols.map((c) => {
      let v = p[c];
      if (v === undefined) v = p.name && c.toLowerCase() === "name" ? p.name : "—";
      return `<td>${v === null || v === undefined ? "—" : (typeof v === "number" ? formatNum(v) : escapeHtml(String(v)))}</td>`;
    }).join("") + "</tr>";
  });
  html += "</tbody></table>";
  el("tblPeers").innerHTML = html;
}

function renderShareholding(data) {
  const sh = data.shareholding;
  // Holding rows are percentages of the company - shown with a "%" so it's
  // clear they're shareholding %, not absolute counts. "No. of Shareholders"
  // is a headcount, not a holding %, so it's ordered after "Others" (any
  // unlisted holding rows are appended automatically before it).
  const pctRows = ["Promoters", "FIIs", "DIIs", "Government", "Public", "Others"];
  buildTable(el("tblShareholding"), sh.periods, sh.rows,
    [...pctRows, "No. of Shareholders"],
    (rowLabel, v) => (rowLabel === "No. of Shareholders" ? formatNum(v) : formatNum(v) + "%"));

  const labels = ["Promoters", "FIIs", "DIIs", "Government", "Public"];
  const latestValues = labels.map((k) => {
    const row = sh.rows[k];
    if (!row) return 0;
    const v = [...row].reverse().find((x) => x !== null && x !== undefined);
    return v || 0;
  });
  destroyChart("shareholding");
  state.charts.shareholding = new Chart(el("chartShareholding"), {
    type: "doughnut",
    data: {
      labels,
      datasets: [{ data: latestValues, backgroundColor: [CHART_COLORS.blue, CHART_COLORS.green, CHART_COLORS.amber, CHART_COLORS.purple, CHART_COLORS.red] }],
    },
    options: { plugins: { legend: { labels: { color: "#e7ecf5" } } } },
  });
}

// ---------------- Financial model ----------------
const ASSUMPTION_FIELDS = [
  { key: "risk_free_rate", label: "Risk-free rate (%)" },
  { key: "equity_risk_premium", label: "Equity risk premium (%)" },
  { key: "beta", label: "Beta", sourceKey: "beta_source" },
  { key: "cost_of_equity", label: "Cost of equity, Ke (%)", readonly: true },
  { key: "cost_of_debt_pretax", label: "Cost of debt, Kd pre-tax (%)", sourceKey: "cost_of_debt_source" },
  { key: "tax_rate", label: "Tax rate (%)" },
  { key: "wacc", label: "WACC (%)", sourceKey: "wacc_source" },
  { key: "terminal_growth", label: "Terminal growth (%)" },
  { key: "revenue_growth_start", label: "Revenue growth, yr 1 (%)" },
  { key: "ebitda_margin", label: "EBITDA margin (%)" },
];

const SOURCE_BADGES = {
  regression: { text: "Calculated — Beta regression", cls: "calc" },
  implied_from_financials: { text: "Calculated — from financials", cls: "calc" },
  calculated: { text: "Calculated", cls: "calc" },
  override: { text: "User override", cls: "override" },
  assumed_default: { text: "Assumed default", cls: "assumed" },
};

async function loadModel(overrides) {
  el("recalcBtn").disabled = true;
  el("recalcBtn").textContent = "Calculating…";
  try {
    const res = await fetch("/api/model", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: state.company.url, id: state.company.id, assumptions: overrides }),
    });
    const data = await res.json();
    if (data.error) { el("modelTypeBanner").textContent = data.error; return; }
    state.modelData = data;
    renderModel(data);
  } catch (err) {
    el("modelTypeBanner").textContent = "Network error while building the model.";
  } finally {
    el("recalcBtn").disabled = false;
    el("recalcBtn").textContent = "Recalculate Model";
  }
}

function currentOverrides() {
  const overrides = {};
  ASSUMPTION_FIELDS.forEach((f) => {
    if (f.readonly) return;
    const input = el(`assume_${f.key}`);
    if (input && input.value !== "") overrides[f.key] = parseFloat(input.value);
  });
  return overrides;
}

el("recalcBtn").addEventListener("click", () => {
  loadModel(currentOverrides());
});

el("exportExcelBtn").addEventListener("click", async () => {
  if (!state.company) return;
  const btn = el("exportExcelBtn");
  btn.disabled = true;
  btn.textContent = "Building Excel file…";
  try {
    const res = await fetch("/api/model/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: state.company.url, id: state.company.id, assumptions: currentOverrides() }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error || "Failed to generate the Excel model.");
    }
    const blob = await res.blob();
    const disposition = res.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="?([^";]+)"?/);
    const filename = match ? match[1] : `${state.company.name || "company"}_financial_model.xlsx`;
    const downloadUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = downloadUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(downloadUrl);
  } catch (err) {
    el("modelTypeBanner").textContent = err.message || "Network error while exporting the Excel model.";
  } finally {
    btn.disabled = false;
    btn.textContent = "Download Excel Model";
  }
});

function renderModel(data) {
  const model = data.model;
  const assumptions = model.assumptions;

  el("assumptionsForm").innerHTML = ASSUMPTION_FIELDS.map((f) => {
    const badge = f.sourceKey && SOURCE_BADGES[assumptions[f.sourceKey]];
    const badgeHtml = badge ? `<span class="source-badge ${badge.cls}">${badge.text}</span>` : "";
    return `
    <div class="assumption-field">
      <label>${f.label} ${badgeHtml}</label>
      <input type="number" step="0.1" id="assume_${f.key}" value="${assumptions[f.key] ?? ""}" ${f.readonly ? "readonly disabled" : ""}>
    </div>
  `;
  }).join("");

  const isBank = assumptions.model_type === "excess_return_pb";
  el("modelTypeBanner").textContent = isBank
    ? "Bank / NBFC detected — using an Excess-Return (Justified Price-to-Book) model instead of FCFF DCF, since interest income/expense is the core operating line for financials."
    : "Standard 10-year FCFF DCF with Gordon-growth terminal value, cross-checked against peer P/E.";

  el("dcfProjectionCard").classList.toggle("hidden", isBank);
  el("sensitivityCard").classList.toggle("hidden", isBank);

  renderValuationSummary(model, isBank);
  renderFootballField(model, isBank);

  if (!isBank && model.dcf && !model.dcf.error) {
    renderProjectionTable(model.dcf.projections);
    renderFcffChart(model.dcf.projections);
    renderSensitivityTable(model.sensitivity);
  }
  renderCompsSummary(model.comps);
}

function renderValuationSummary(model, isBank) {
  const box = el("valuationSummary");
  let rows = [];
  if (isBank) {
    const b = model.bank_model || {};
    rows = [
      ["Sustainable ROE", fmtPct(b.sustainable_roe_pct)],
      ["Payout ratio", fmtPct(b.payout_ratio_pct)],
      ["Sustainable growth (g)", fmtPct(b.sustainable_growth_pct)],
      ["Cost of equity (Ke)", fmtPct(b.cost_of_equity_pct)],
      ["Book value / share", fmtCur(b.book_value_per_share)],
      ["Justified P/B", b.justified_pb ? b.justified_pb.toFixed(2) + "x" : "—"],
      ["Fair value / share", fmtCur(b.value_per_share)],
      ["Current price", fmtCur(b.current_price)],
      ["Upside / (Downside)", fmtPct(b.upside_pct)],
    ];
    rows.push(["Recommendation", badge(b.recommendation)]);
  } else {
    const d = model.dcf || {};
    if (d.error) { box.innerHTML = `<p class="muted">${d.error}</p>`; return; }
    rows = [
      ["Enterprise value (₹ Cr)", fmtCur(d.enterprise_value)],
      ["Less: Net debt (₹ Cr)", fmtCur(d.net_debt)],
      ["Equity value (₹ Cr)", fmtCur(d.equity_value)],
      ["Shares outstanding", d.shares_outstanding ? Math.round(d.shares_outstanding).toLocaleString("en-IN") : "—"],
      ["DCF value / share", fmtCur(d.value_per_share)],
      ["Current price", fmtCur(d.current_price)],
      ["Upside / (Downside)", fmtPct(d.upside_pct)],
    ];
    rows.push(["Recommendation", badge(d.recommendation)]);
  }
  box.innerHTML = rows.map(([label, value]) => `<div class="vs-row"><span class="vs-label">${label}</span><span>${value}</span></div>`).join("");
}

function badge(rec) {
  if (!rec) return "—";
  return `<span class="vs-badge ${rec.replace(" ", ".")}">${rec}</span>`;
}
function fmtCur(v) { return v === null || v === undefined ? "—" : "₹" + Number(v).toLocaleString("en-IN", { maximumFractionDigits: 1 }); }
function fmtPct(v) { return v === null || v === undefined ? "—" : Number(v).toFixed(1) + "%"; }

function renderFootballField(model, isBank) {
  const labels = [];
  const values = [];
  if (isBank && model.bank_model) {
    if (model.bank_model.value_per_share) { labels.push("Excess Return Model"); values.push(model.bank_model.value_per_share); }
  } else if (model.dcf && !model.dcf.error) {
    if (model.dcf.value_per_share) { labels.push("DCF (FCFF)"); values.push(model.dcf.value_per_share); }
  }
  if (model.comps && model.comps.implied_value_pe) { labels.push("Peer P/E"); values.push(model.comps.implied_value_pe); }
  const cp = (model.dcf && model.dcf.current_price) || (model.bank_model && model.bank_model.current_price) || model.comps.current_price;
  if (cp) { labels.push("Current Price"); values.push(cp); }

  destroyChart("football");
  state.charts.football = new Chart(el("chartFootball"), {
    type: "bar",
    data: { labels, datasets: [{ label: "Value per share (₹)", data: values, backgroundColor: [CHART_COLORS.blue, CHART_COLORS.amber, CHART_COLORS.green] }] },
    options: { ...chartOpts(), indexAxis: "y" },
  });
}

function renderProjectionTable(projections) {
  const periods = projections.map((p) => `Yr ${p.year}`);
  const rows = {
    "Revenue": projections.map((p) => p.revenue),
    "Growth %": projections.map((p) => p.growth_pct),
    "EBITDA": projections.map((p) => p.ebitda),
    "EBIT": projections.map((p) => p.ebit),
    "NOPAT": projections.map((p) => p.nopat),
    "Capex": projections.map((p) => p.capex),
    "Δ NWC": projections.map((p) => p.delta_nwc),
    "FCFF": projections.map((p) => p.fcff),
    "PV of FCFF": projections.map((p) => p.pv_fcff),
  };
  buildTable(el("tblProjection"), periods, rows, Object.keys(rows));
}

function renderFcffChart(projections) {
  destroyChart("fcff");
  state.charts.fcff = new Chart(el("chartFcff"), {
    type: "bar",
    data: {
      labels: projections.map((p) => `Yr ${p.year}`),
      datasets: [
        { label: "FCFF", data: projections.map((p) => p.fcff), backgroundColor: CHART_COLORS.blue },
        { label: "PV of FCFF", data: projections.map((p) => p.pv_fcff), backgroundColor: CHART_COLORS.green },
      ],
    },
    options: chartOpts(),
  });
}

function renderSensitivityTable(sens) {
  if (!sens) { el("tblSensitivity").innerHTML = '<p class="muted">Sensitivity unavailable.</p>'; return; }
  let html = '<table class="data-grid"><thead><tr><th>WACC ↓ / g →</th>';
  sens.g_range.forEach((g) => html += `<th>${g}%</th>`);
  html += "</tr></thead><tbody>";
  sens.wacc_range.forEach((w, i) => {
    html += `<tr><td>${w}%</td>`;
    sens.grid[i].forEach((v) => html += `<td>${v === null ? "—" : "₹" + v.toLocaleString("en-IN")}</td>`);
    html += "</tr>";
  });
  html += "</tbody></table>";
  el("tblSensitivity").innerHTML = html;
}

function renderCompsSummary(comps) {
  const box = el("compsSummary");
  if (!comps || !comps.peer_count) { box.innerHTML = '<p class="muted">No peer multiples available on screener.in for this company.</p>'; return; }
  const rows = [
    ["Peers found", comps.peer_count],
    ["Peer average P/E", comps.peer_avg_pe ? comps.peer_avg_pe.toFixed(1) + "x" : "—"],
    ["Subject stock P/E", comps.subject_pe ? comps.subject_pe.toFixed(1) + "x" : "—"],
    ["Latest EPS (₹)", comps.latest_eps ?? "—"],
    ["Implied value (peer P/E × EPS)", fmtCur(comps.implied_value_pe)],
    ["Current price", fmtCur(comps.current_price)],
    ["Upside / (Downside)", fmtPct(comps.upside_pct)],
  ];
  box.innerHTML = '<div class="valuation-summary">' + rows.map(([l, v]) => `<div class="vs-row"><span class="vs-label">${l}</span><span>${v}</span></div>`).join("") + "</div>";
}
