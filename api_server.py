"""
BotCore API Server - MT5 EA Communication Endpoint

Provides REST API endpoints for MetaTrader 5 EA to:
- Receive Start of Day (SOD) OHLC data
- Request trading analysis snapshots
- Submit execution confirmations
- Check system status
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import os
from dotenv import load_dotenv
from brain import sod_action, intraday_action
from database import store_current_positions, get_current_positions

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


@app.route("/api/trading/sod", methods=["POST"])
def trading_sod():
    """
    Start of Day (SOD) endpoint for MT5 EA to send initial OHLC data.
    
    Expected payload:
    {
        "symbol": "GBPUSD",
        "1h_DATA": [
            {
                "time": 1704067200,
                "open": 1.08500,
                "high": 1.08600,
                "low": 1.08400,
                "close": 1.08550,
                "volume": 1234
            },
            ...
        ],
        "4h_DATA": [...],
        "1D_DATA": [...],
        "1W_DATA": [...]
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
        
        # Validate required fields
        symbol = data.get("symbol")
        h1_data = data.get("1h_DATA")
        h4_data = data.get("4h_DATA")
        d1_data = data.get("1D_DATA")
        w1_data = data.get("1W_DATA")
        
        if not symbol:
            return jsonify({"error": "Missing required field: symbol"}), 400
        
        if not h1_data or not h4_data or not d1_data or not w1_data:
            return jsonify({
                "error": "Missing required timeframe data: 1h_DATA, 4h_DATA, 1D_DATA, 1W_DATA"
            }), 400
        
        # Log the SOD data received
        print("=" * 50)
        print(f"📊 Start of Day Data Received")
        print(f"Symbol: {symbol}")
        print(f"1h candles: {len(h1_data)}")
        print(f"4h candles: {len(h4_data)}")
        print(f"1D candles: {len(d1_data)}")
        print(f"1W candles: {len(w1_data)}")
        print("=" * 50)
        
        # Prepare OHLC data in format expected by sod_action
        ohlc_data = {
            "1h_DATA": h1_data,
            "4h_DATA": h4_data,
            "1D_DATA": d1_data,
            "1W_DATA": w1_data
        }
        
        # Extract positions (optional)
        positions = data.get("positions", [])
        
        # Call sod_action in brain.py to perform comprehensive SOD analysis
        # This includes: OHLC analysis, chart analysis with GPT Vision, market data, and final GPT analysis
        result = sod_action(symbol=symbol, ohlc_data=ohlc_data, positions=positions)
        
        # Return the AI analysis result directly to the EA
        # The EA will receive the complete SOD analysis including decision, bias, and order_details
        return jsonify(result), 200
        
    except Exception as e:
        return jsonify({
            "error": f"Internal server error: {str(e)}"
        }), 500


@app.route("/api/trading/intraday", methods=["POST"])
def trading_intraday():
    """
    Intraday endpoint for active trading analysis during the day.
    
    Expected payload:
    {
        "symbol": "GBPUSD",
        "H1_DATA": [...],
        "M15_DATA": [...],
        "M5_DATA": [...],
        "M1_DATA": [...]
    }
    
    Note: Timeframe keys can vary - send whichever timeframes are needed
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
        
        # Validate symbol
        symbol = data.get("symbol")
        if not symbol:
            return jsonify({"error": "Missing required field: symbol"}), 400
        
        # Extract OHLC data (flexible timeframes)
        ohlc_data = {}
        for key, value in data.items():
            if key != "symbol" and key != "positions" and key.endswith("_DATA"):
                ohlc_data[key] = value
        
        if not ohlc_data:
            return jsonify({
                "error": "No OHLC data provided. Include at least one timeframe (e.g., H1_DATA, M15_DATA)"
            }), 400
        
        # Extract positions (optional)
        positions = data.get("positions", [])
        
        # Log the intraday request
        print("=" * 50)
        print(f"📈 Intraday Analysis Request Received")
        print(f"Symbol: {symbol}")
        print(f"Timeframes: {list(ohlc_data.keys())}")
        print(f"Positions: {len(positions)} open")
        print("=" * 50)
        
        # Call intraday_action in brain.py
        result = intraday_action(symbol=symbol, ohlc_data=ohlc_data, positions=positions)
        
        # Return the AI analysis result directly to the EA
        return jsonify(result), 200
        
    except Exception as e:
        return jsonify({
            "error": f"Internal server error: {str(e)}"
        }), 500


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
        
        # Process trading snapshot (placeholder - will be implemented later)
        # from brain import process_trading_snapshot
        # result = process_trading_snapshot(
        #     symbol=symbol,
        #     ohlc_data=ohlc_data,
        #     account_state=account_state,
        #     session_context=data.get("session_context"),
        #     requested_timeframes=requested_timeframes
        # )
        
        # Placeholder response
        result = {
            "action": "WAIT",
            "next_run_at_utc": None,
            "message": "Trading snapshot processing not yet implemented"
        }
        
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


@app.route("/api/trading/store_positions", methods=["POST"])
def store_positions():
    """
    Store current positions from EA to database.
    
    Expected payload:
    {
        "symbol": "GBPUSD",
        "positions": [
            {
                "ticket": 12345,
                "asset": "GBPUSD",
                "direction": "BUY",
                "entry_price": 1.34205,
                "current_price": 1.34305,
                "stop_loss": 1.34100,
                "take_profit": 1.34500,
                "lot_size": 0.5,
                "entry_time": "2024-01-15T08:00:00Z"
            }
        ]
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
        
        symbol = data.get("symbol")
        positions = data.get("positions", [])
        
        if not symbol:
            return jsonify({"error": "Missing required field: symbol"}), 400
        
        # Store positions in database
        success = store_current_positions(symbol, positions)
        
        if success:
            return jsonify({
                "status": "success",
                "positions_stored": len(positions),
                "symbol": symbol
            }), 200
        else:
            return jsonify({
                "error": "Failed to store positions"
            }), 500
        
    except Exception as e:
        return jsonify({
            "error": f"Internal server error: {str(e)}"
        }), 500


@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "error": "Endpoint not found",
        "available_endpoints": [
            "/api/health",
            "/api/trading/sod",
            "/api/trading/intraday",
            "/api/trading/snapshot",
            "/api/trading/execute",
            "/api/trading/status",
            "/api/trading/store_positions"
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
