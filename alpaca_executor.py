import os
import json
import urllib.request
import urllib.error

class AlpacaClient:
    """
    Lightweight, dependency-free API client for Alpaca Paper/Live trading.
    """
    def __init__(self, key_id=None, secret_key=None, paper=True):
        self.key_id = key_id or os.environ.get("ALPACA_API_KEY_ID")
        self.secret_key = secret_key or os.environ.get("ALPACA_SECRET_KEY")
        self.base_url = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
        
    def _request(self, path, method="GET", body=None):
        if not self.key_id or not self.secret_key:
            return {"success": False, "error": "Missing Alpaca API credentials."}
            
        url = f"{self.base_url}{path}"
        headers = {
            "APCA-API-KEY-ID": self.key_id,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Content-Type": "application/json",
            "User-Agent": "AntigravityTradingBot/1.0"
        }
        
        data = None
        if body:
            data = json.dumps(body).encode('utf-8')
            
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as response:
                return {"success": True, "data": json.loads(response.read().decode('utf-8'))}
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode('utf-8')
            try:
                err_json = json.loads(err_msg)
                return {"success": False, "error": err_json.get("message", err_msg)}
            except Exception:
                return {"success": False, "error": f"HTTP {e.code}: {err_msg}"}
        except Exception as e:
            return {"success": False, "error": str(e)}
            
    def get_account(self):
        """Fetch general account metrics (cash, buying power, equity)."""
        return self._request("/v2/account")
        
    def get_positions(self):
        """Fetch current open positions."""
        return self._request("/v2/positions")
        
    def get_position(self, symbol):
        """Fetch position for a specific symbol."""
        return self._request(f"/v2/positions/{symbol}")
        
    def submit_order(self, symbol, side, amount_usd=None, qty=None):
        """
        Submit a market order.
        If amount_usd is specified, it places a fractional order using 'notional' (USD value).
        Otherwise, it places a share-based order using 'qty'.
        """
        body = {
            "symbol": symbol,
            "side": side.lower(),
            "type": "market",
            "time_in_force": "day"
        }
        
        if amount_usd is not None:
            body["notional"] = f"{amount_usd:.2f}"
        elif qty is not None:
            body["qty"] = f"{qty:.6f}"
        else:
            raise ValueError("Must specify either amount_usd or qty.")
            
        return self._request("/v2/orders", method="POST", body=body)

    def cancel_all_orders(self):
        """Cancel any outstanding open orders."""
        return self._request("/v2/orders", method="DELETE")

    def get_orders(self, status="open"):
        """Fetch orders filtered by status."""
        return self._request(f"/v2/orders?status={status}")
