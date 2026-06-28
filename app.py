from flask import Flask, jsonify, request, render_template, send_file
import requests

import scraper
import financial_model
import excel_export

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/search")
def api_search():
    query = request.args.get("q", "")
    try:
        results = scraper.search_companies(query.strip().lower())
    except requests.RequestException as e:
        return jsonify({"error": f"Could not reach screener.in: {e}"}), 502
    return jsonify({"results": results})


@app.route("/api/company")
def api_company():
    url = request.args.get("url")
    company_id = request.args.get("id")
    if not url:
        return jsonify({"error": "url query param required"}), 400
    try:
        data = scraper.get_company(url, company_id=company_id)
    except requests.RequestException as e:
        return jsonify({"error": f"Could not fetch company data: {e}"}), 502
    return jsonify(data)


@app.route("/api/model", methods=["POST"])
def api_model():
    payload = request.get_json(force=True, silent=True) or {}
    url = payload.get("url")
    company_id = payload.get("id")
    overrides = payload.get("assumptions") or {}
    if not url:
        return jsonify({"error": "url is required"}), 400
    try:
        data = scraper.get_company(url, company_id=company_id)
        model = financial_model.build_model(data, overrides)
    except requests.RequestException as e:
        return jsonify({"error": f"Could not fetch company data: {e}"}), 502
    except Exception as e:
        return jsonify({"error": f"Failed to build model: {e}"}), 500
    return jsonify({
        "company": {"name": data.get("name"), "url": data.get("url"), "about": data.get("about")},
        "top_ratios": data.get("top_ratios"),
        "model": model,
    })


@app.route("/api/model/export", methods=["POST"])
def api_model_export():
    payload = request.get_json(force=True, silent=True) or {}
    url = payload.get("url")
    company_id = payload.get("id")
    overrides = payload.get("assumptions") or {}
    if not url:
        return jsonify({"error": "url is required"}), 400
    try:
        data = scraper.get_company(url, company_id=company_id)
        model = financial_model.build_model(data, overrides)
        workbook_buf = excel_export.build_workbook(data, model)
    except requests.RequestException as e:
        return jsonify({"error": f"Could not fetch company data: {e}"}), 502
    except Exception as e:
        return jsonify({"error": f"Failed to build Excel model: {e}"}), 500

    safe_name = "".join(c if c.isalnum() or c in (" ", "-", "_") else "" for c in (data.get("name") or "company"))
    filename = f"{safe_name.strip().replace(' ', '_') or 'company'}_financial_model.xlsx"
    return send_file(
        workbook_buf,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
