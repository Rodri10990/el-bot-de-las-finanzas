import os
import datetime
import time
import functions_framework
from flask import jsonify
from cloud_simulator import run_cloud_simulation_cycle, WATCHLIST

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
        
    results = {}
    errors = []
    
    print(f"Starting hourly trading cycle for watchlist: {WATCHLIST}")
    
    # 2. Iterate and run the cycle for each watchlist ticker
    for ticker in WATCHLIST:
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
        if ticker != WATCHLIST[-1]:
            print("Sleeping for 15 seconds to respect Gemini API rate limits...")
            time.sleep(15)
            
    print(f"Completed hourly trading cycle. Successes: {len(results) - len(errors)}, Errors: {len(errors)}")
    
    status_code = 200 if not errors else 207  # 207 Multi-Status if there are minor errors
    return jsonify({
        "success": len(errors) == 0,
        "timestamp": datetime.datetime.now().isoformat(),
        "watchlist_results": results
    }), status_code
