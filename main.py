import os
import datetime
import time
import functions_framework
from flask import jsonify
from cloud_simulator import run_cloud_simulation_cycle, WATCHLIST, load_portfolio_gcs, send_telegram_alert, get_portfolio_valuation_gcs, get_usd_to_eur_rate
from research_analyst import handle_research_cycle

@functions_framework.http
def handle_trading_cycle(request):
    """
    HTTP Cloud Function entry point.
    Triggered by Google Cloud Scheduler to run the simulation cycle hourly.
    """
    # 1. Retrieve API key and Bucket Name from Environment Variables
    api_key = os.environ.get("GEMINI_API_KEY")
    bucket_name = os.environ.get("BUCKET_NAME")
    
    if not api_key:
        return jsonify({
            "success": False, 
            "error": "Environment variable GEMINI_API_KEY is not set. Please configure it in your Cloud Function."
        }), 500
        
    if not bucket_name:
        return jsonify({
            "success": False, 
            "error": "Environment variable BUCKET_NAME is not set. Please configure it in your Cloud Function."
        }), 500
        
    # 2. Load portfolio and check for already-processed tickers today
    try:
        portfolio = load_portfolio_gcs(bucket_name)
    except Exception as e:
        print(f"Failed to load portfolio for idempotency check: {e}")
        portfolio = {}
        
    current_date = datetime.datetime.now().strftime("%Y-%m-%d")
    processed_today = portfolio.get("processed_today", {})
    processed_tickers = processed_today.get("tickers", []) if processed_today.get("date") == current_date else []
    
    results = {}
    errors = []
    skipped_tickers = []
    
    print(f"Starting trading cycle for watchlist: {WATCHLIST}. Already processed today: {processed_tickers}")
    
    # 3. Iterate and run the cycle for each watchlist ticker
    for ticker in WATCHLIST:
        if ticker in processed_tickers:
            print(f"Skipping {ticker}: Already successfully processed today.")
            skipped_tickers.append(ticker)
            continue
            
        print(f"Running simulation cycle for {ticker}...")
        try:
            res = run_cloud_simulation_cycle(ticker, bucket_name, api_key)
            if res.get('success'):
                results[ticker] = res
                print(f"Successfully processed {ticker}. Decision: {res['trade_result']['action_taken']} ({res['trade_result']['allocation_pct_final']}% final allocation)")
            else:
                results[ticker] = {"success": False, "error": res.get('error')}
                errors.append(f"{ticker}: {res.get('error')}")
                print(f"Failed to process {ticker}: {res.get('error')}")
        except Exception as e:
            results[ticker] = {"success": False, "error": str(e)}
            errors.append(f"{ticker}: Exception {str(e)}")
            print(f"Critical exception processing {ticker}: {str(e)}")
            
        # Add rate-limiting delay between requests to avoid exceeding Gemini API 5 RPM free quota
        # Only sleep if there are other active, non-skipped tickers remaining in the watchlist
        remaining_tickers = [t for t in WATCHLIST if t not in processed_tickers and WATCHLIST.index(t) > WATCHLIST.index(ticker)]
        if remaining_tickers:
            print("Sleeping for 10 seconds to respect Gemini API rate limits...")
            time.sleep(10)
            
    print(f"Completed hourly trading cycle. Successes: {len(results) - len(errors)}, Errors: {len(errors)}")
    
    # 4. Compile and send Telegram notification report
    if results or errors:
        try:
            usd_to_eur = get_usd_to_eur_rate()
            final_portfolio = load_portfolio_gcs(bucket_name)
            cash = final_portfolio.get("cash", 0.0) * usd_to_eur
            nav_usd, holdings_val_usd, holding_values, holding_prices = get_portfolio_valuation_gcs(final_portfolio)
            
            nav = nav_usd * usd_to_eur
            holdings_val = holdings_val_usd * usd_to_eur
            
            # Format message
            status_emoji = "🔴" if errors else "🟢"
            status_title = f"{status_emoji} *Trading Bot Daily Report* ({current_date})\n\n"
            
            summary_section = f"*Portfolio Summary (Euros):*\n"
            summary_section += f"• Net Asset Value: `€{nav:.2f}`\n"
            summary_section += f"• Cash Balance: `€{cash:.2f}`\n"
            summary_section += f"• Assets Value: `€{holdings_val:.2f}`\n\n"
            
            trades_section = f"*Today's Actions (Euros):*\n"
            for t in WATCHLIST:
                if t in skipped_tickers:
                    trades_section += f"• {t}: _Skipped (already run)_\n"
                elif t in results:
                    res = results[t]
                    if res.get('success'):
                        t_res = res['trade_result']
                        action = t_res['action_taken']
                        shares = t_res['shares_traded']
                        val = t_res['trade_value'] * usd_to_eur
                        price = res['price'] * usd_to_eur
                        if action == "HOLD":
                            trades_section += f"• {t}: `HOLD` (Price: `€{price:.2f}`)\n"
                        else:
                            trades_section += f"• {t}: `{action}` {abs(shares):.6f} shares (Value: `€{val:.2f}` at `€{price:.2f}`)\n"
                    else:
                        trades_section += f"• {t}: ❌ *FAILED*: {res.get('error')}\n"
                else:
                    trades_section += f"• {t}: ❌ *UNPROCESSED*\n"
                    
            if errors:
                trades_section += f"\n🚨 *Errors Detected:*\n"
                for err in errors:
                    trades_section += f"• {err}\n"
            
            send_telegram_alert(status_title + summary_section + trades_section)
        except Exception as alert_err:
            print(f"Failed to compile and send Telegram alert: {alert_err}")
            
    status_code = 200 if not errors else 500  # Return 500 on execution errors to trigger Cloud Scheduler retries
    return jsonify({
        "success": len(errors) == 0,
        "timestamp": datetime.datetime.now().isoformat(),
        "watchlist_results": results
    }), status_code
