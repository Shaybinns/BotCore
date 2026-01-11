"""
BotCore API Server - MT5 EA Communication Endpoint

Provides REST API endpoints for MetaTrader 5 EA to:
- Request trading analysis snapshots
- Submit execution confirmations
- Check system status
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import os
from dotenv import load_dotenv
from brain import process_trading_snapshot

load_dotenv()

app = Flask(__name__)
CORS(app)

app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'botcore-secret-key-change-in-production')


@app.route("/api/health", methods=["GET"])
def health_check():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy",
        "service": "BotCore API",
        "version": "1.0.0"
    }), 200


@app.route("/api/trading/snapshot", methods=["POST"])
def trading_snapshot():
    """
    Main endpoint for MT5 EA to request trading analysis.
    
    Expected payload:
    {
        "symbol": "EURUSD",
        "ohlc_data": {
            "H1": [...],
            "M15": [...],
            "M5": [...],
            "M1": [...],
            "current_price": 1.0850,
            "primary_timeframe": "H1"
        },
        "account_state": {
            "balance": 10000.0,
            "equity": 10000.0,
            "drawdown": 0.0,
            "open_positions": [...],
            "pending_orders": [...],
            "max_trades_per_day": 10,
            "max_risk_per_trade": 0.02,
            "daily_drawdown_limit": 0.05
        },
        "session_context": "London"  # Optional
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
        
        # Validate required fields
        symbol = data.get("symbol")
        ohlc_data = data.get("ohlc_data")
        account_state = data.get("account_state")
        
        if not symbol or not ohlc_data or not account_state:
            return jsonify({
                "error": "Missing required fields: symbol, ohlc_data, account_state"
            }), 400
        
        # Get requested timeframes (optional - for tracking)
        requested_timeframes = data.get("requested_timeframes", [])
        
        # Process trading snapshot
        result = process_trading_snapshot(
            symbol=symbol,
            ohlc_data=ohlc_data,
            account_state=account_state,
            session_context=data.get("session_context"),
            requested_timeframes=requested_timeframes
        )
        
        return jsonify(result), 200
        
    except Exception as e:
        return jsonify({
            "error": f"Internal server error: {str(e)}",
            "action": "ERROR",
            "next_run_at_utc": None
        }), 500


@app.route("/api/trading/execute", methods=["POST"])
def trading_execute():
    """
    Endpoint for MT5 EA to confirm trade execution.
    
    Expected payload:
    {
        "setup_id": "uuid-here",
        "symbol": "EURUSD",
        "order_type": "BUY",
        "price": 1.0850,
        "stop_loss": 1.0800,
        "take_profit": 1.0900,
        "lot_size": 0.1,
        "execution_time": "2024-01-01T12:00:00Z"
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
        
        # Log execution to database
        from database import save_trade_event
        save_trade_event(
            setup_id=data.get("setup_id"),
            symbol=data.get("symbol"),
            event_type="EXECUTION",
            event_data=data
        )
        
        return jsonify({
            "status": "executed",
            "setup_id": data.get("setup_id")
        }), 200
        
    except Exception as e:
        return jsonify({
            "error": f"Failed to log execution: {str(e)}"
        }), 500


@app.route("/api/trading/status", methods=["GET"])
def trading_status():
    """Get system status and pending instructions."""
    return jsonify({
        "status": "operational",
        "version": "1.0.0"
    }), 200


@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "error": "Endpoint not found",
        "available_endpoints": [
            "/api/health",
            "/api/trading/snapshot",
            "/api/trading/execute",
            "/api/trading/status"
        ]
    }), 404


@app.errorhandler(500)
def internal_error(error):
    return jsonify({
        "error": "Internal server error"
    }), 500


if __name__ == "__main__":
    port = int(os.getenv('PORT', 5000))
    host = os.getenv('HOST', '0.0.0.0')
    
    print(f"🚀 Starting BotCore API on {host}:{port}")
    print(f"📊 Trading endpoints available at http://{host}:{port}/api/trading/")
    
    app.run(host=host, port=port, debug=False)
