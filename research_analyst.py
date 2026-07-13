import os
import datetime
import time
import json
import functions_framework
from flask import jsonify
import google.generativeai as genai
from cloud_simulator import load_portfolio_gcs, send_telegram_alert, get_historical_data, get_news_headlines, get_usd_to_eur_rate

RESEARCH_WATCHLIST = ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AMD", "AVGO", "NFLX"]

def calculate_momentum(history):
    """Calculate 7-day, 14-day and 30-day returns from historical price data."""
    if not history or len(history) < 2:
        return {"current": 0.0, "return_7d": 0.0, "return_14d": 0.0}
        
    current = history[-1]["close"]
    
    # 7 days ago (or closest available index)
    idx_7d = max(0, len(history) - 8)
    close_7d = history[idx_7d]["close"]
    ret_7d = ((current - close_7d) / close_7d) * 100
    
    # 14 days ago
    idx_14d = max(0, len(history) - 15)
    close_14d = history[idx_14d]["close"]
    ret_14d = ((current - close_14d) / close_14d) * 100
    
    return {
        "current": current,
        "return_7d": ret_7d,
        "return_14d": ret_14d
    }

def run_gemini_advisor(api_key, portfolio, watchlist_data):
    """Use Gemini to analyze the historical trends and headlines and build a weekly advisory report."""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.5-flash')
    
    current_date = datetime.datetime.now().strftime("%Y-%m-%d")
    usd_to_eur = get_usd_to_eur_rate()
    cash_eur = portfolio.get('cash', 0.0) * usd_to_eur
    
    watchlist_data_eur = {}
    for ticker, info in watchlist_data.items():
        watchlist_data_eur[ticker] = {
            "price_eur": info["price"] * usd_to_eur,
            "return_7d_pct": info["return_7d_pct"],
            "return_14d_pct": info["return_14d_pct"],
            "headlines": info["headlines"]
        }
    
    prompt = f"""
You are a Senior Portfolio Manager and Equity Research Analyst. Your job is to analyze a list of stock market opportunities, cross-reference them with the user's current paper-trading portfolio holdings, and write a high-impact, easy-to-read weekly advisory digest for the user on Telegram.

NOTE: All asset values and stock prices have been pre-converted to Euros (€) and are in Euro currency. You must write the entire report and perform all calculations/analyses using Euro (€) currency. Do not use US Dollar ($) symbols.

### CURRENT PORTFOLIO STATE (EUROS):
Cash Balance: €{cash_eur:.2f}
Active Holdings (units held): {json.dumps(portfolio.get('holdings', {}), indent=2)}

### MARKET DISCOVERY WATCHLIST DATA (EUROS):
{json.dumps(watchlist_data_eur, indent=2)}

### INSTRUCTIONS FOR THE REPORT:
Write a clean, engaging Telegram report in Markdown using standard formatting (*bold* for bold, _italic_ for italic, emojis, bullet points). Do NOT use nested HTML or unsupported markdown headers like # or ## (instead use Bold text + Emojis).

Structure the report exactly like this:
1. 📊 *Weekly Market Discovery Digest ({current_date})*
   A brief, high-level summary of the macroeconomic/tech sector sentiment based on the headlines.
2. 💡 *Top 3 Investment Ideas for the Week*
   List exactly 3 stocks from the watchlist that present the most compelling risk/reward setups right now. Include:
   - Ticker, current price in Euros (€), and recent weekly return.
   - A bullet point detailing the fundamental or technical catalyst.
3. ⚠️ *Portfolio Optimization Advisory*
   Review the user's current portfolio holdings (e.g., BTC, ETH, TSLA, NVDA). Based on the weekly research, suggest specific strategic adjustments (e.g., if TSLA is declining and META is showing strong momentum, advise on potentially rebalancing or trimming).
4. 📅 *Catalysts to Watch*
   Highlight 2-3 critical upcoming events or earnings releases.

Keep the tone professional, objective, and analytical.
"""
    
    response = model.generate_content(prompt)
    return response.text

def extract_structured_recommendations(api_key, report_text, watchlist_data):
    """Call Gemini to extract structured BUY/SELL/HOLD recommendations from the markdown report."""
    genai.configure(api_key=api_key)
    prompt = f"""
You are a Quantitative Integration Analyst. Read this weekly equity research report and extract a structured list of actionable trading recommendations for the watchlist stocks.

### WEEKLY RESEARCH REPORT:
{report_text}

### WATCHLIST SYMBOLS:
{list(watchlist_data.keys())}

### INSTRUCTIONS:
1. Extract any concrete buy, sell, or trim recommendations mentioned in the report.
2. For each recommendation, determine:
   - "ticker": The stock symbol (must be one of the watchlist symbols, e.g. TSLA, NVDA, AAPL).
   - "action": "BUY", "SELL", or "HOLD".
   - "allocation_pct": The allocation percentage recommended (integer between 0 and 100). If not specified, default to 50 for buy/sell.
   - "reason": A short 1-sentence summary of the reasoning.
3. You must respond with a JSON array in this exact format:
[
  {{
    "ticker": "<symbol>",
    "action": "<BUY, SELL, or HOLD>",
    "allocation_pct": <int>,
    "reason": "<reasoning>"
  }}
]
Do not include any extra text, markdown formatting, or HTML. Just return the raw JSON array.
"""
    model = genai.GenerativeModel("gemini-2.5-flash")
    response = model.generate_content(prompt)
    text = response.text.strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    try:
        return json.loads(text)
    except Exception as e:
        print(f"Failed to parse recommendations JSON: {e}. Text: {text}")
        return []

def save_recommendations_gcs(bucket_name, recommendations):
    """Save recommendations JSON to Google Cloud Storage."""
    if not recommendations:
        return
    from google.cloud import storage
    with storage.Client() as client:
        bucket = client.bucket(bucket_name)
        blob = bucket.blob("recommendations.json")
        blob.upload_from_string(
            json.dumps(recommendations, indent=2),
            content_type="application/json"
        )
        print("recommendations.json saved to GCS successfully.")

@functions_framework.http
def handle_research_cycle(request):
    """
    HTTP Cloud Function entry point for the weekly Research & Advisory bot.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    bucket_name = os.environ.get("BUCKET_NAME")
    
    if not api_key or not bucket_name:
        return jsonify({"success": False, "error": "Missing required environment variables."}), 500
        
    print(f"Starting weekly research cycle for watchlist: {RESEARCH_WATCHLIST}")
    
    try:
        portfolio = load_portfolio_gcs(bucket_name)
    except Exception as e:
        print(f"Failed to load portfolio: {e}")
        portfolio = {}
        
    watchlist_data = {}
    errors = []
    
    # Iterate and fetch data for the research watchlist
    for ticker in RESEARCH_WATCHLIST:
        print(f"Scanning weekly indicators for {ticker}...")
        try:
            hist_res = get_historical_data(ticker, period="1mo", interval="1d")
            news_res = get_news_headlines(ticker)
            
            if hist_res.get("success"):
                momentum = calculate_momentum(hist_res.get("history", []))
                watchlist_data[ticker] = {
                    "price": momentum["current"],
                    "return_7d_pct": momentum["return_7d"],
                    "return_14d_pct": momentum["return_14d"],
                    "headlines": [h.get("title", "") for h in news_res.get("news", [])[:3]]
                }
            else:
                errors.append(f"{ticker}: {hist_res.get('error')}")
        except Exception as e:
            errors.append(f"{ticker}: Exception {str(e)}")
            print(f"Exception scanning {ticker}: {e}")
            
        # 10s sleep between requests to respect Gemini 5 RPM API limits
        if ticker != RESEARCH_WATCHLIST[-1]:
            time.sleep(10)
            
    if not watchlist_data:
        return jsonify({"success": False, "error": "Failed to retrieve research data.", "details": errors}), 500
        
    print("Generating Gemini advisory report...")
    try:
        report = run_gemini_advisor(api_key, portfolio, watchlist_data)
        print("Advisory report generated. Dispatching to Telegram...")
        
        send_success = send_telegram_alert(report)
        
        # Save structured recommendations if the report was sent successfully
        if send_success:
            try:
                print("Extracting structured recommendations for GCS queue...")
                recs = extract_structured_recommendations(api_key, report, watchlist_data)
                if recs:
                    print(f"Saving {len(recs)} recommendations to GCS...")
                    save_recommendations_gcs(bucket_name, recs)
            except Exception as rec_err:
                print(f"Failed to process and save structured recommendations: {rec_err}")
                
        return jsonify({
            "success": send_success,
            "timestamp": datetime.datetime.now().isoformat(),
            "report_sent": send_success,
            "errors": errors
        }), 200 if send_success else 500
    except Exception as e:
        print(f"Advisory generation failed: {e}")
        return jsonify({"success": False, "error": f"Advisory generation failed: {str(e)}"}), 500
