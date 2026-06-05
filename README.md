# El Bot de las Finanzas 🤖📈

An autonomous, 24/7 paper trading bot deployed serverless on **Google Cloud Platform (GCP)**. It leverages Google's **Gemini 3.5 Flash** model as a strict Quantitative Risk Analyst to evaluate real-time market data and news, executing paper trades while respecting robust, hardcoded safety boundaries.

---

## 🏗️ Architecture & Stack

- **Runtime:** Python 3.10
- **Compute:** Google Cloud Functions (2nd Gen)
- **Trigger:** Google Cloud Scheduler (Hourly Cron: `0 * * * *`)
- **Database / Storage:** Google Cloud Storage (GCS) holding `portfolio.json` and `history.csv`
- **AI Analyst:** Gemini 3.5 Flash (via Google AI Studio)
- **Market Feed:** Yahoo Finance API (real-time price metrics & headlines)

---

## 🛡️ Risk & Safety Controls

The bot has hardcoded, non-bypassable safety overlays designed to protect virtual capital:
1. **Trade Sizing Cap:** No single trade can ever allocate more than **15% of available cash**.
2. **Asset Concentration Cap:** Holdings of any single asset cannot exceed **30% of the total Net Asset Value (NAV)**. Buy actions are aborted or scaled down if they would breach this ceiling.

---

## 📁 Repository Structure

- `cloud_simulator.py` - Core trade evaluation, GCS integration, Gemini analyst prompt, and execution logic.
- `main.py` - HTTP Cloud Function entry point triggered by Cloud Scheduler.
- `deploy.sh` - Automated bash script to spin up the GCP infrastructure, create the storage bucket, and deploy the function.
- `requirements.txt` - Python package dependencies.
- `val.py` - Local utility to fetch real-time prices and value the portfolio.
- `portfolio.json` - Active virtual portfolio state (snapshot of cash & holdings).
- `history.csv` - Chronological log of all simulated trades.

---

## 🚀 How it Works

1. **Trigger:** Every hour (UTC), Cloud Scheduler sends an authenticated HTTP POST request to the Cloud Function.
2. **Analyze:** For each asset in the watchlist (`BTC-USD`, `ETH-USD`, `TSLA`, `NVDA`), the bot fetches:
   - Real-time price, day high/low, and daily volume.
   - Latest publisher headlines from Yahoo Finance.
3. **Inference:** The data is compiled and sent to Gemini with a structured schema, returning a dynamic Risk Rating (1-10), a trade decision (`BUY`, `SELL`, `HOLD`), and recommended sizing.
4. **Safety Verification:** The bot loads the active portfolio state from GCS, tests the decision against safety limits, and adjusts or cancels trades as needed.
5. **Execution & Log:** The portfolio is updated and saved back to GCS, and a transaction row is appended to `history.csv`.

---

## 💻 Local Portfolio Valuation

You can value the portfolio at any time using the local script:
```bash
python3 val.py
```

This retrieves current market prices for all holdings and displays a formatted Net Asset Value (NAV) balance sheet.
