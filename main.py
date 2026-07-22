import os
import datetime
import time
import functions_framework
from flask import jsonify
from cloud_simulator import run_cloud_simulation_cycle, WATCHLIST, load_portfolio_gcs, send_telegram_alert, get_portfolio_valuation_gcs, get_usd_to_eur_rate, load_recommendations_gcs, delete_recommendations_gcs, save_portfolio_gcs, get_historical_data, sync_portfolio_with_alpaca
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
        
    # 2. Load portfolio, sync with Alpaca (if live), and check for already-processed tickers today
    try:
        portfolio = load_portfolio_gcs(bucket_name)
        portfolio = sync_portfolio_with_alpaca(bucket_name, portfolio)
    except Exception as e:
        print(f"Failed to load portfolio for idempotency check: {e}")
        portfolio = {}
        
    current_date = datetime.datetime.now().strftime("%Y-%m-%d")
    processed_today = portfolio.get("processed_today", {})
    processed_tickers = processed_today.get("tickers", []) if processed_today.get("date") == current_date else []
    
    results = {}
    errors = []
    skipped_tickers = []
    analyst_trades = []
    execution_warnings = []
    
    # 2.5 Ingest Analyst recommendations if recommendations.json is present on GCS
    try:
        recs = load_recommendations_gcs(bucket_name)
        if recs:
            print(f"Found Analyst recommendations in queue: {recs}")
            p_state = load_portfolio_gcs(bucket_name)
            
            for rec in recs:
                ticker = rec.get("ticker")
                action = rec.get("action")
                alloc_pct = rec.get("allocation_pct", 50)
                reason = rec.get("reason", "No reason provided.")
                
                if not ticker or action not in ["BUY", "SELL", "HOLD"]:
                    print(f"Skipping invalid recommendation: {rec}")
                    continue
                
                print(f"Processing Analyst recommendation: {action} {ticker} ({alloc_pct}%)...")
                
                # Fetch current price via yfinance
                price_res = get_historical_data(ticker, period="1d", interval="1d")
                if not price_res.get("success") or not price_res.get("history"):
                    print(f"Failed to fetch price for {ticker}: {price_res.get('error')}. Skipping.")
                    execution_warnings.append(f"Analyst {ticker}: Failed to fetch price.")
                    continue
                price = price_res["history"][-1]["close"]
                
                # Execute Trade Simulation
                nav_usd, holdings_val_usd, values, prices = get_portfolio_valuation_gcs(p_state)
                
                if action == "BUY":
                    # Trade size: alloc_pct * 15% of NAV
                    trade_value = (alloc_pct / 100.0) * 0.15 * nav_usd
                    if trade_value > p_state["cash"]:
                        print(f"Trimming trade size to available cash. Requested: ${trade_value:.2f}, Cash: ${p_state['cash']:.2f}")
                        trade_value = p_state["cash"]
                    
                    if trade_value <= 0.01:
                        print(f"Insufficient cash to buy {ticker}. Skipping.")
                        execution_warnings.append(f"Analyst {ticker}: Insufficient cash to BUY.")
                        continue
                        
                    # Execute Live Broker Order if enabled
                    live_trading = os.environ.get("LIVE_TRADING", "false").lower() == "true"
                    if live_trading:
                        from alpaca_executor import AlpacaClient
                        client = AlpacaClient()
                        order_res = client.submit_order(ticker, "buy", amount_usd=trade_value)
                        if not order_res.get("success"):
                            print(f"Alpaca Analyst Order Rejected for {ticker}: {order_res.get('error')}")
                            execution_warnings.append(f"Analyst {ticker}: Alpaca execution failed: {order_res.get('error')}")
                            continue
                        else:
                            print(f"Alpaca Analyst Order Submitted successfully: {order_res.get('data')}")
                            
                    shares = trade_value / price
                    p_state["cash"] -= trade_value
                    p_state["holdings"][ticker] = p_state["holdings"].get(ticker, 0.0) + shares
                    
                    p_state["history"].append({
                        "timestamp": datetime.datetime.now().isoformat(),
                        "ticker": ticker,
                        "action": "BUY",
                        "price": price,
                        "allocation_percentage": float(alloc_pct),
                        "shares_traded": float(shares),
                        "value": float(trade_value),
                        "reasoning": f"Analyst Recommendation: {reason}"
                    })
                    analyst_trades.append({"ticker": ticker, "action": "BUY", "shares": shares, "value": trade_value, "price": price})
                    
                elif action == "SELL":
                    held_shares = p_state["holdings"].get(ticker, 0.0)
                    if held_shares <= 0.0:
                        print(f"No holdings of {ticker} to sell. Skipping.")
                        continue
                        
                    shares_to_sell = held_shares * (alloc_pct / 100.0)
                    trade_value = shares_to_sell * price
                    # Execute Live Broker Order if enabled
                    live_trading = os.environ.get("LIVE_TRADING", "false").lower() == "true"
                    if live_trading:
                        from alpaca_executor import AlpacaClient
                        client = AlpacaClient()
                        order_res = client.submit_order(ticker, "sell", qty=shares_to_sell)
                        if not order_res.get("success"):
                            print(f"Alpaca Analyst Order Rejected for {ticker}: {order_res.get('error')}")
                            execution_warnings.append(f"Analyst {ticker}: Alpaca execution failed: {order_res.get('error')}")
                            continue
                        else:
                            print(f"Alpaca Analyst Order Submitted successfully: {order_res.get('data')}")
                            
                    p_state["cash"] += trade_value
                    p_state["holdings"][ticker] = held_shares - shares_to_sell
                    
                    if p_state["holdings"][ticker] <= 0.000001:
                        p_state["holdings"].pop(ticker, None)
                        
                    p_state["history"].append({
                        "timestamp": datetime.datetime.now().isoformat(),
                        "ticker": ticker,
                        "action": "SELL",
                        "price": price,
                        "allocation_percentage": float(alloc_pct),
                        "shares_traded": float(-shares_to_sell),
                        "value": float(trade_value),
                        "reasoning": f"Analyst Recommendation: {reason}"
                    })
                    analyst_trades.append({"ticker": ticker, "action": "SELL", "shares": shares_to_sell, "value": trade_value, "price": price})
                    
                elif action == "HOLD":
                    p_state["history"].append({
                        "timestamp": datetime.datetime.now().isoformat(),
                        "ticker": ticker,
                        "action": "HOLD",
                        "price": price,
                        "allocation_percentage": 0.0,
                        "shares_traded": 0.0,
                        "value": 0.0,
                        "reasoning": f"Analyst Recommendation: {reason}"
                    })
                    analyst_trades.append({"ticker": ticker, "action": "HOLD", "shares": 0.0, "value": 0.0, "price": price})
                
                # Mark as processed today so standard scan skips it
                if "processed_today" not in p_state or p_state["processed_today"].get("date") != current_date:
                    p_state["processed_today"] = {"date": current_date, "tickers": []}
                if ticker not in p_state["processed_today"]["tickers"]:
                    p_state["processed_today"]["tickers"].append(ticker)
                    
            # Save updated state
            save_portfolio_gcs(bucket_name, p_state)
            # Remove queue file
            delete_recommendations_gcs(bucket_name)
            # Refresh portfolio reference
            portfolio = p_state
            
    except Exception as queue_err:
        print(f"Error processing recommendations queue: {queue_err}")
        errors.append(f"Analyst Queue: Error processing: {str(queue_err)}")

    processed_today = portfolio.get("processed_today", {})
    processed_tickers = processed_today.get("tickers", []) if processed_today.get("date") == current_date else []

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
            
            # Track and save daily NAV history in portfolio
            daily_nav = final_portfolio.get("daily_nav_history", {})
            daily_nav[current_date] = nav_usd
            final_portfolio["daily_nav_history"] = daily_nav
            save_portfolio_gcs(bucket_name, final_portfolio)
            
            # Calculate USD Trading Performance metrics
            total_invested_usd = sum(tx.get("value", 0.0) for tx in final_portfolio.get("history", []) if tx.get("action") == "DEPOSIT")
            if total_invested_usd <= 0.0:
                total_invested_usd = 300.00
                
            # 1. Daily Return (compared to previous recorded cycle/day)
            past_dates = sorted([d for d in daily_nav.keys() if d < current_date])
            if past_dates:
                prev_date = past_dates[-1]
                prev_nav_usd = daily_nav[prev_date]
                daily_return_pct = ((nav_usd - prev_nav_usd) / prev_nav_usd) * 100.0 if prev_nav_usd > 0 else 0.0
                daily_diff_usd = nav_usd - prev_nav_usd
                daily_diff_eur = daily_diff_usd * usd_to_eur
            else:
                daily_return_pct = 0.0
                daily_diff_usd = 0.0
                daily_diff_eur = 0.0
                
            # 2. Monthly Return (MTD)
            current_month = datetime.datetime.now().strftime("%Y-%m")
            month_dates = sorted([d for d in daily_nav.keys() if d.startswith(current_month)])
            if month_dates and month_dates[0] in daily_nav:
                month_start_nav = daily_nav[month_dates[0]]
            else:
                month_start_nav = total_invested_usd
            monthly_return_pct = ((nav_usd - month_start_nav) / month_start_nav) * 100.0 if month_start_nav > 0 else 0.0

            # 3. Year-to-Date (YTD) Return
            current_year = datetime.datetime.now().strftime("%Y")
            ytd_dates = sorted([d for d in daily_nav.keys() if d.startswith(current_year)])
            if ytd_dates and ytd_dates[0] in daily_nav:
                ytd_start_nav = daily_nav[ytd_dates[0]]
            else:
                ytd_start_nav = total_invested_usd
            ytd_return_pct = ((nav_usd - ytd_start_nav) / ytd_start_nav) * 100.0 if ytd_start_nav > 0 else 0.0

            # 4. All-Time Total Return
            total_return_pct = ((nav_usd - total_invested_usd) / total_invested_usd) * 100.0 if total_invested_usd > 0 else 0.0
            
            # Format message
            status_emoji = "🔴" if errors else "🟢"
            status_title = f"{status_emoji} *Trading Bot Daily Report* ({current_date})\n\n"
            
            summary_section = f"*Portfolio Summary (Euros):*\n"
            summary_section += f"• Net Asset Value: `€{nav:.2f}`\n"
            summary_section += f"• Cash Balance: `€{cash:.2f}`\n"
            summary_section += f"• Assets Value: `€{holdings_val:.2f}`\n\n"
            
            summary_section += f"*Performance & Returns (USD Basis):*\n"
            summary_section += f"• Today's Return: `{daily_return_pct:+.2f}%` (`{daily_diff_eur:+.2f}€` / `{daily_diff_usd:+.2f}$`)\n"
            summary_section += f"• Monthly Return ({current_month}): `{monthly_return_pct:+.2f}%`\n"
            summary_section += f"• Year-to-Date ({current_year}): `{ytd_return_pct:+.2f}%`\n"
            summary_section += f"• All-Time Total Return: `{total_return_pct:+.2f}%`\n\n"
            
            trades_section = ""
            if analyst_trades:
                trades_section += f"*Weekly Analyst Actions (Euros):*\n"
                for trade in analyst_trades:
                    ticker = trade["ticker"]
                    action = trade["action"]
                    val = trade["value"] * usd_to_eur
                    price = trade["price"] * usd_to_eur
                    shares = trade["shares"]
                    if action == "HOLD":
                        trades_section += f"• {ticker}: `HOLD` (Price: `€{price:.2f}`)\n"
                    else:
                        trades_section += f"• {ticker}: `{action}` {abs(shares):.6f} shares (Value: `€{val:.2f}` at `€{price:.2f}`)\n"
                trades_section += "\n"
                
            trades_section += f"*Today's Actions (Euros):*\n"
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
                        trades_section += f"• {t}: ❌ *FAILED*\n"
                else:
                    trades_section += f"• {t}: ❌ *UNPROCESSED*\n"
                    
            if execution_warnings:
                trades_section += f"\n⚠️ *Warnings:*\n"
                for warn in execution_warnings:
                    trades_section += f"• {warn}\n"
                    
            if errors:
                trades_section += f"\n🚨 *Errors Detected:*\n```\n"
                for err in errors:
                    safe_err = str(err).replace("`", "'")
                    trades_section += f"• {safe_err}\n"
                trades_section += "```\n"
            
            send_telegram_alert(status_title + summary_section + trades_section)
        except Exception as alert_err:
            print(f"Failed to compile and send Telegram alert: {alert_err}")
            
    status_code = 200  # Always return 200 on execution warnings to prevent unsafe Cloud Scheduler retries
    return jsonify({
        "success": True,
        "timestamp": datetime.datetime.now().isoformat(),
        "watchlist_results": results
    }), status_code
