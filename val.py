import json
import urllib.request
import urllib.error

WATCHLIST = ["BTC-USD", "ETH-USD", "TSLA", "NVDA"]

class UI:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[38;2;46;204;113m"
    RED = "\033[38;2;231;76;60m"
    YELLOW = "\033[38;2;241;196;15m"
    BLUE = "\033[38;2;52;152;219m"
    MAGENTA = "\033[38;2;155;89;182m"
    CYAN = "\033[38;2;26;188;156m"
    WHITE = "\033[38;2;236;240;241m"
    DARK_GRAY = "\033[38;2;127;140;141m"

def get_current_price(ticker):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1d"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req) as response:
            chart_data = json.loads(response.read().decode())
        price = chart_data['chart']['result'][0]['meta']['regularMarketPrice']
        return price, True
    except Exception:
        return 0.0, False

def main():
    try:
        with open("portfolio.json", "r") as f:
            portfolio = json.load(f)
    except FileNotFoundError:
        with open("portfolio_current.json", "r") as f:
            portfolio = json.load(f)
        
    cash = portfolio['cash']
    holdings = portfolio['holdings']
    
    total_holdings_val = 0.0
    assets_table = []
    
    print(f"{UI.DIM}Valuing cloud portfolio at real-time market prices...{UI.RESET}")
    for ticker, units in holdings.items():
        price, success = get_current_price(ticker)
        value = units * price
        total_holdings_val += value
        price_str = f"${price:,.2f}" if success else "CONNECTION ERROR"
        value_str = f"${value:,.2f}"
        status_icon = "🟢" if success else "🔴"
        assets_table.append((ticker, units, price_str, value_str, status_icon))
        
    net_asset_value = cash + total_holdings_val
    
    print(f"\n{UI.BOLD}{UI.WHITE}┌────────────────────────────────────────────────────────┐{UI.RESET}")
    print(f"{UI.BOLD}{UI.WHITE}│ {UI.GREEN}PORTFOLIO PERFORMANCE & CASH BALANCES{UI.WHITE}                  │{UI.RESET}")
    print(f"{UI.BOLD}{UI.WHITE}├────────────────────────────────────────────────────────┤{UI.RESET}")
    print(f"{UI.BOLD}{UI.WHITE}│ {UI.BOLD}Net Asset Value (NAV): {UI.GREEN}${net_asset_value:,.2f}{UI.WHITE}             │{UI.RESET}")
    print(f"{UI.BOLD}{UI.WHITE}│ {UI.BOLD}Available Cash:        {UI.CYAN}${cash:,.2f}{UI.WHITE}             │{UI.RESET}")
    print(f"{UI.BOLD}{UI.WHITE}│ {UI.BOLD}Total Assets Value:    {UI.MAGENTA}${total_holdings_val:,.2f}{UI.WHITE}             │{UI.RESET}")
    print(f"{UI.BOLD}{UI.WHITE}└────────────────────────────────────────────────────────┘{UI.RESET}")
    
    print(f"\n{UI.BOLD}{UI.BLUE}⏵ CURRENT PORTFOLIO POSITION BREAKDOWN{UI.RESET}")
    print(f"{UI.BOLD}{UI.WHITE}{'Ticker':<10} {'Holdings':<14} {'Market Price':<16} {'Market Value':<16} {'Status':<6}{UI.RESET}")
    print(f"{UI.DARK_GRAY}{'-' * 66}{UI.RESET}")
    for ticker, units, price_str, value_str, icon in assets_table:
        ticker_fmt = f"{UI.BOLD}{ticker:<10}{UI.RESET}"
        print(f"{ticker_fmt} {units:<14.6f} {price_str:<16} {value_str:<16} {icon:<6}")

if __name__ == "__main__":
    main()
