#!/usr/bin/env python3
import sys
import os
import json
import urllib.request
import urllib.error
import urllib.parse
import datetime
import re
import math
from google.cloud import storage
import google.generativeai as genai

# Configuration
WATCHLIST = ["BTC-USD", "ETH-USD", "TSLA", "NVDA"]
PORTFOLIO_BLOB = "portfolio.json"
HISTORY_BLOB = "history.csv"

def get_current_price(ticker):
    """Fetch current price and metadata from Yahoo Finance."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1d"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'})
    try:
        with urllib.request.urlopen(req) as response:
            chart_data = json.loads(response.read().decode())
        
        result = chart_data['chart']['result'][0]
        meta = result['meta']
        price = meta['regularMarketPrice']
        
        day_high = meta.get('regularMarketDayHigh', price)
        day_low = meta.get('regularMarketDayLow', price)
        volume = meta.get('regularMarketVolume', 0)
        prev_close = meta.get('chartPreviousClose', price)
        
        return {
            'success': True,
            'price': price,
            'day_high': day_high,
            'day_low': day_low,
            'volume': volume,
            'prev_close': prev_close,
            'currency': meta.get('currency', 'USD')
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}

def get_news_headlines(ticker):
    """Fetch recent news headlines from Yahoo Finance Search API."""
    url = f"https://query2.finance.yahoo.com/v1/finance/search?q={ticker}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'})
    try:
        with urllib.request.urlopen(req) as response:
            search_data = json.loads(response.read().decode())
        news = search_data.get('news', [])
        return {
            'success': True,
            'news': [{'title': n.get('title', ''), 'publisher': n.get('publisher', 'Unknown')} for n in news]
        }
    except Exception as e:
        return {'success': False, 'error': str(e), 'news': []}

def run_gemini_analyst(api_key, ticker, price_data, headlines):
    """Use Gemini 1.5 Flash via Google AI Studio API as the strict Quantitative Analyst."""
    genai.configure(api_key=api_key)
    
    prompt = f"""
You are a strict Quantitative Risk Analyst. You are evaluating the asset '{ticker}' for a virtual paper trading portfolio.

Real-Time Market Data:
- Ticker: {ticker}
- Current Price: ${price_data['price']:,.2f}
- Daily Range: ${price_data['day_low']:,.2f} - ${price_data['day_high']:,.2f}
- Daily Volume: {price_data['volume']:,}

Recent News Headlines (Today's top stories):
"""
    for i, h in enumerate(headlines, 1):
        prompt += f"{i}. [{h['publisher']}] {h['title']}\n"
        
    prompt += """
Your Task:
1. Perform a thorough financial risk assessment of these headlines and daily price movements. Note that this bot runs on a daily schedule (once every 24 hours).
2. The portfolio strategy is a medium-term swing-trading and DCA strategy. The user contributes €100 (simulated as $108 USD) monthly, targeting a stable 3% monthly return.
3. To achieve this, you must prioritize stable, high-conviction medium-term trends and minimize active trading turnover. Avoid high-frequency trading or reactive day-trading to prevent commission fee drag.
4. Determine a dynamic Risk Rating on a strict scale of 1 to 10 (1 = lowest risk, 10 = highest risk).
5. Decide on a trading action: BUY, SELL, or HOLD.
6. Recommend an allocation percentage (1 to 100).
   - If action is BUY, this is the percentage of the maximum allowed trade size (which is 15% of total portfolio NAV) to use.
   - If action is SELL, this is the percentage of your holdings to liquidate.
   - If action is HOLD, allocation percentage must be 0.
7. Provide a detailed, professional, and quantitative executive summary explaining your reasoning.

You must respond with a JSON object in this exact format:
{
  "risk_score": <int between 1 and 10>,
  "action": "<BUY, SELL, or HOLD>",
  "allocation_percentage": <int between 0 and 100>,
  "reasoning": "<Executive summary of your risk analysis>"
}
Do not include any extra text, markdown formatting, or HTML. Just return the raw JSON object.
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
    except Exception as parse_error:
        # Fallback in case of parsing issues
        return {
            "risk_score": 5,
            "action": "HOLD",
            "allocation_percentage": 0,
            "reasoning": f"Failed to parse Gemini output: {text}. Error: {str(parse_error)}"
        }

def load_portfolio_gcs(bucket_name):
    """Load portfolio state from Google Cloud Storage."""
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(PORTFOLIO_BLOB)
    
    if not blob.exists():
        portfolio = {
            "cash": 10000.0,
            "holdings": {ticker: 0.0 for ticker in WATCHLIST},
            "history": []
        }
        save_portfolio_gcs(bucket_name, portfolio)
        return portfolio
        
    data = blob.download_as_text()
    return json.loads(data)

def save_portfolio_gcs(bucket_name, portfolio):
    """Save portfolio state to Google Cloud Storage."""
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(PORTFOLIO_BLOB)
    blob.upload_from_string(json.dumps(portfolio, indent=2))

def get_portfolio_valuation_gcs(portfolio, active_ticker=None, active_price=0.0):
    """Calculate the real-time valuation of the portfolio in the cloud."""
    total_holdings_val = 0.0
    holding_values = {}
    holding_prices = {}
    
    for t, units in portfolio['holdings'].items():
        if t == active_ticker and active_price > 0:
            p = active_price
        else:
            p_data = get_current_price(t)
            p = p_data['price'] if p_data['success'] else 0.0
            
        val = units * p
        total_holdings_val += val
        holding_values[t] = val
        holding_prices[t] = p
        
    net_asset_value = portfolio['cash'] + total_holdings_val
    return net_asset_value, total_holdings_val, holding_values, holding_prices

def log_to_csv_gcs(bucket_name, ticker, action, price, allocation_pct, shares_traded, trade_value, cash, portfolio_value, reasoning):
    """Log the simulation run and portfolio value over time to history.csv in GCS."""
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(HISTORY_BLOB)
    
    clean_reasoning = reasoning.replace('"', '""').replace('\n', ' ')
    
    row = [
        datetime.datetime.now().isoformat(),
        ticker,
        action,
        f"{price:.6f}" if price else "0.0",
        f"{allocation_pct:.2f}",
        f"{shares_traded:.6f}",
        f"{trade_value:.2f}",
        f"{cash:.2f}",
        f"{portfolio_value:.2f}",
        f'"{clean_reasoning}"'
    ]
    
    csv_line = ",".join(row) + "\n"
    
    if blob.exists():
        current_csv = blob.download_as_text()
        new_csv = current_csv + csv_line
    else:
        header = "timestamp,ticker,action,price,allocation_percentage,shares_traded,value,available_cash,total_portfolio_value,reasoning\n"
        new_csv = header + csv_line
        
    blob.upload_from_string(new_csv)

def execute_trade_gcs(bucket_name, portfolio, ticker, action, allocation_pct, price, reasoning):
    """
    Executes trades with hardcoded risk boundaries:
    - 15% cash ceiling for any single trade.
    - 30% concentration ceiling for any single asset position.
    """
    cash = portfolio['cash']
    holdings = portfolio['holdings']
    
    if ticker not in holdings:
        holdings[ticker] = 0.0
        
    current_holding = holdings[ticker]
    trade_executed = False
    shares_traded = 0.0
    trade_value = 0.0
    old_cash = cash
    old_holding = current_holding
    safety_log = []
    
    nav, total_holdings_val, holding_values, holding_prices = get_portfolio_valuation_gcs(portfolio, ticker, price)
    
    if action == "BUY" and allocation_pct > 0:
        # Safety Overlay 1: Max trade size is 15% of portfolio NAV
        max_trade_cash = nav * 0.15
        
        # Calculate cash to use based on allocation percentage of the max allowed trade cash
        cash_to_use = max_trade_cash * (allocation_pct / 100.0)
        
        # Ensure we don't exceed actual available cash
        if cash_to_use > cash:
            original_cash_to_use = cash_to_use
            cash_to_use = cash
            safety_log.append(f"BUY trade size capped at available cash of ${cash:,.2f} (originally ${original_cash_to_use:,.2f}).")
        else:
            safety_log.append(f"BUY trade size set to ${cash_to_use:,.2f} (which represents {allocation_pct}% of the 15% NAV limit of ${max_trade_cash:,.2f}).")
            
        # Safety Overlay 2: 30% NAV maximum exposure limit
        max_allowed_val = nav * 0.30
        current_val = holding_values.get(ticker, 0.0)
        
        if current_val + cash_to_use > max_allowed_val:
            allowed_additional_cash = max(0.0, max_allowed_val - current_val)
            if allowed_additional_cash < 0.01:
                safety_log.append(f"BUY trade aborted. Holdings in {ticker} are already at or exceed 30% NAV concentration limit.")
                action = "HOLD"
                allocation_pct = 0.0
                cash_to_use = 0.0
            else:
                original_cash = cash_to_use
                cash_to_use = allowed_additional_cash
                # Recalculate allocation_pct relative to max_trade_cash for logging
                allocation_pct = (cash_to_use / max_trade_cash) * 100.0 if max_trade_cash > 0 else 0.0
                safety_log.append(f"BUY trade value capped at ${cash_to_use:,.2f} (originally ${original_cash:,.2f}) to respect the 30% concentration limit.")
                
        if cash_to_use >= 0.01 and action == "BUY":
            shares_bought = cash_to_use / price
            portfolio['cash'] = cash - cash_to_use
            portfolio['holdings'][ticker] = current_holding + shares_bought
            shares_traded = shares_bought
            trade_value = cash_to_use
            trade_executed = True
            
    elif action == "SELL" and allocation_pct > 0 and current_holding > 0:
        shares_to_sell = current_holding * (allocation_pct / 100.0)
        revenue = shares_to_sell * price
        portfolio['cash'] = cash + revenue
        portfolio['holdings'][ticker] = current_holding - shares_to_sell
        shares_traded = -shares_to_sell
        trade_value = revenue
        trade_executed = True
        
    if trade_executed:
        log_entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "ticker": ticker,
            "action": action,
            "price": price,
            "allocation_percentage": allocation_pct,
            "shares_traded": shares_traded,
            "value": trade_value,
            "reasoning": reasoning
        }
        portfolio['history'].append(log_entry)
        save_portfolio_gcs(bucket_name, portfolio)
        
    return {
        'executed': trade_executed,
        'shares_traded': shares_traded,
        'trade_value': trade_value,
        'old_cash': old_cash,
        'new_cash': portfolio['cash'],
        'old_holding': old_holding,
        'new_holding': portfolio['holdings'][ticker],
        'action_taken': action if trade_executed else "HOLD",
        'allocation_pct_final': allocation_pct if trade_executed else 0.0,
        'safety_log': safety_log
    }

def run_cloud_simulation_cycle(ticker, bucket_name, api_key):
    """Executes a single simulation cycle for the given ticker using cloud state and Gemini API."""
    ticker = ticker.upper()
    if ticker not in WATCHLIST:
        return {'success': False, 'error': f"Ticker {ticker} is not in watchlist."}
        
    # 1. Fetch real-time market data
    price_data = get_current_price(ticker)
    if not price_data['success']:
        return {'success': False, 'error': f"Failed to fetch market price: {price_data.get('error')}"}
        
    # 2. Fetch recent headlines
    news_data = get_news_headlines(ticker)
    
    # 3. Ask Gemini to act as strict Risk Analyst
    analyst_decision = run_gemini_analyst(api_key, ticker, price_data, news_data.get('news', []))
    
    portfolio = load_portfolio_gcs(bucket_name)
    
    # Auto-deposit: Inject €100 (simulated as $108 USD) at the start of every month
    current_date = datetime.datetime.now().strftime("%Y-%m")
    last_deposit = portfolio.get("last_deposit_date", "")
    if last_deposit != current_date:
        deposit_amount = 108.00
        old_cash = portfolio.get("cash", 0.0)
        portfolio["cash"] = old_cash + deposit_amount
        portfolio["last_deposit_date"] = current_date
        
        deposit_entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "ticker": "USD",
            "action": "DEPOSIT",
            "price": 1.0,
            "allocation_percentage": 0.0,
            "shares_traded": deposit_amount,
            "value": deposit_amount,
            "reasoning": f"Automated monthly capital injection of €100 (simulated as ${deposit_amount:.2f} USD)."
        }
        if "history" not in portfolio:
            portfolio["history"] = []
        portfolio["history"].append(deposit_entry)
        
        print(f"Injecting monthly deposit of ${deposit_amount:.2f}. New cash balance: ${portfolio['cash']:.2f}")
        save_portfolio_gcs(bucket_name, portfolio)
        
        nav, _, _, _ = get_portfolio_valuation_gcs(portfolio)
        log_to_csv_gcs(
            bucket_name=bucket_name,
            ticker="USD",
            action="DEPOSIT",
            price=1.0,
            allocation_pct=0.0,
            shares_traded=deposit_amount,
            trade_value=deposit_amount,
            cash=portfolio["cash"],
            portfolio_value=nav,
            reasoning=deposit_entry["reasoning"]
        )
    
    action = analyst_decision.get('action', 'HOLD').upper()
    alloc = analyst_decision.get('allocation_percentage', 0)
    reasoning = analyst_decision.get('reasoning', '')
    
    # Dry-run checks
    if action == "SELL" and portfolio['holdings'].get(ticker, 0.0) <= 0:
        action = "HOLD"
        alloc = 0
        reasoning = "[Adjusted to HOLD due to no holdings] " + reasoning
    if action == "BUY" and portfolio['cash'] < 1.0:
        action = "HOLD"
        alloc = 0
        reasoning = "[Adjusted to HOLD due to insufficient cash] " + reasoning
        
    # 5. Execute trade with safety limits
    trade_result = execute_trade_gcs(bucket_name, portfolio, ticker, action, alloc, price_data['price'], reasoning)
    
    # 6. Fetch final NAV and log to CSV on Cloud Storage
    final_nav, total_holdings_val, holding_values, holding_prices = get_portfolio_valuation_gcs(portfolio, ticker, price_data['price'])
    
    log_action = trade_result.get('action_taken', "HOLD")
    log_alloc = trade_result.get('allocation_pct_final', 0.0)
    
    log_to_csv_gcs(
        bucket_name=bucket_name,
        ticker=ticker,
        action=log_action,
        price=price_data['price'],
        allocation_pct=log_alloc,
        shares_traded=trade_result['shares_traded'],
        trade_value=trade_result['trade_value'],
        cash=portfolio['cash'],
        portfolio_value=final_nav,
        reasoning=reasoning
    )
    
    return {
        'success': True,
        'ticker': ticker,
        'price': price_data['price'],
        'analyst_decision': {
            'suggested_action': analyst_decision.get('action'),
            'suggested_allocation': analyst_decision.get('allocation_percentage'),
            'risk_rating': analyst_decision.get('risk_score'),
            'reasoning': reasoning
        },
        'trade_result': {
            'executed': trade_result['executed'],
            'action_taken': trade_result['action_taken'],
            'allocation_pct_final': trade_result['allocation_pct_final'],
            'shares_traded': trade_result['shares_traded'],
            'trade_value': trade_result['trade_value'],
            'safety_warnings': trade_result['safety_log']
        },
        'portfolio': {
            'cash': portfolio['cash'],
            'net_asset_value': final_nav,
            'holdings': portfolio['holdings']
        }
    }
