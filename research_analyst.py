import os
import datetime
import time
import json
import functions_framework
from flask import jsonify
import google.generativeai as genai
from cloud_simulator import load_portfolio_gcs, send_telegram_alert, get_historical_data, get_news_headlines

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
    
    prompt = f"""
You are a Senior Portfolio Manager and Equity Research Analyst. Your job is to analyze a list of stock market opportunities, cross-reference them with the user's current paper-trading portfolio holdings, and write a high-impact, easy-to-read weekly advisory digest for the user on Telegram.

### CURRENT PORTFOLIO STATE:
Cash Balance: ${portfolio.get('cash', 0.0):.2f}
Active Holdings (units held): {json.dumps(portfolio.get('holdings', {}), indent=2)}

### MARKET DISCOVERY WATCHLIST DATA:
{json.dumps(watchlist_data, indent=2)}

### INSTRUCTIONS FOR THE REPORT:
Write a clean, engaging Telegram report in Markdown using standard formatting (*bold* for bold, _italic_ for italic, emojis, bullet points). Do NOT use nested HTML or unsupported markdown headers like # or ## (instead use Bold text + Emojis).

Structure the report exactly like this:
1. 📊 *Weekly Market Discovery Digest ({current_date})*
   A brief, high-level summary of the macroeconomic/tech sector sentiment based on the headlines.
2. 💡 *Top 3 Investment Ideas for the Week*
   List exactly 3 stocks from the watchlist that present the most compelling risk/reward setups right now. Include:
   - Ticker, current price, and recent weekly return.
   - A bullet point detailing the fundamental or technical catalyst.
3. ⚠️ *Portfolio Optimization Advisory*
   Review the user's current portfolio holdings (e.g., BTC, ETH, TSLA, NVDA). Based on the weekly research, suggest specific strategic adjustments (e.g., if TSLA is declining and META is showing strong momentum, advise on potentially rebalancing or trimming).
4. 📅 *Catalysts to Watch*
   Highlight 2-3 critical upcoming events or earnings releases.

Keep the tone professional, objective, and analytical.
"""
    
    response = model.generate_content(prompt)
    return response.text

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
        return jsonify({
            "success": send_success,
            "timestamp": datetime.datetime.now().isoformat(),
            "report_sent": send_success,
            "errors": errors
        }), 200 if send_success else 500
    except Exception as e:
        print(f"Advisory generation failed: {e}")
        return jsonify({"success": False, "error": f"Advisory generation failed: {str(e)}"}), 500
