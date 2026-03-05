"""
BotCore API Server - MT5 EA Communication Endpoint

Provides REST API endpoints for MetaTrader 5 EA to:
- Receive Start of Day (SOD) OHLC data
- Request trading analysis snapshots
- Submit execution confirmations
- Check system status
"""

from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
import os
import json
from dotenv import load_dotenv
from brain import sod_action, intraday_action
from database import store_current_positions, get_current_positions

load_dotenv()

app = Flask(__name__)
CORS(app)

app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'botcore-secret-key-change-in-production')


@app.route("/api/health", methods=["GET"])
def health_check():
    """
    Health check endpoint — also initialises DB tables on first boot.
    Railway hits this after deploy; if DATABASE_URL is set the tables
    are created automatically (CREATE TABLE IF NOT EXISTS — safe to repeat).
    """
    db_status = "not_configured"
    if os.getenv("DATABASE_URL"):
        try:
            from database import init_database
            init_database()
            db_status = "ok"
        except Exception as e:
            db_status = f"error: {e}"

    return jsonify({
        "status":  "healthy",
        "service": "BotCore API",
        "version": "1.0.0",
        "database": db_status
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
        import traceback
        print(f"[sod] ERROR: {e}")
        print(traceback.format_exc())
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
        import traceback
        print(f"[intraday] ERROR: {e}")
        print(traceback.format_exc())
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
            symbol=data.get("symbol", "UNKNOWN"),
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


def _build_chat_context(symbol: str, user_id: str = None) -> str:
    """
    Load all available context from the DB and assemble it into a single
    context string for the chat assistant.

    Reads (never fetches live):
      - market_data_note  — synthesized market intelligence
      - sod_note          — today's SOD analysis and trading plan
      - last_run_note     — most recent intraday analysis
      - current_positions — live open positions
      - recent_messages   — user's conversation history (if user_id provided)
    """
    from database import get_analysis_note, get_current_positions
    from datetime import datetime, timezone

    parts = [
        f"CURRENT TIME: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"SYMBOL IN FOCUS: {symbol}",
        ""
    ]

    market_intel = get_analysis_note('GLOBAL', 'market_data_note')
    if market_intel:
        fetched_at = market_intel.get('_fetched_at') or market_intel.get('_db_created_at', 'unknown')
        parts += [
            f"=== MARKET INTELLIGENCE (as of {fetched_at}) ===",
            json.dumps({k: v for k, v in market_intel.items()
                        if not k.startswith('_')}, indent=2),
            ""
        ]
    else:
        parts += ["=== MARKET INTELLIGENCE ===", "Not yet available — SOD has not run today.", ""]

    sod_note = get_analysis_note(symbol, 'sod_note')
    if sod_note:
        parts += [
            "=== TODAY'S SOD ANALYSIS (trading plan and bias) ===",
            json.dumps({k: v for k, v in sod_note.items()
                        if not k.startswith('_')}, indent=2),
            ""
        ]
    else:
        parts += ["=== TODAY'S SOD ANALYSIS ===", "Not yet available — SOD has not run today.", ""]

    last_run = get_analysis_note(symbol, 'last_run_note')
    if last_run:
        parts += [
            "=== LAST INTRADAY ANALYSIS ===",
            json.dumps({k: v for k, v in last_run.items()
                        if not k.startswith('_')}, indent=2),
            ""
        ]
    else:
        parts += ["=== LAST INTRADAY ANALYSIS ===", "No intraday runs yet today.", ""]

    positions = get_current_positions(symbol)
    if positions:
        parts += [
            "=== CURRENT OPEN POSITIONS ===",
            json.dumps(positions, indent=2),
            ""
        ]
    else:
        parts += ["=== CURRENT OPEN POSITIONS ===", "No open positions.", ""]

    if user_id:
        try:
            from user_tracking import get_messages
            history = get_messages(user_id)
            if history:
                history_lines = [
                    f"{m['role'].upper()}: {m['content']}" for m in history
                ]
                parts += [
                    "=== CONVERSATION HISTORY ===",
                    "\n".join(history_lines),
                    ""
                ]
        except Exception as e:
            print(f"[chat] Could not load message history: {e}")

    return "\n".join(parts)


@app.route("/api/chat", methods=["POST"])
def chat():
    """
    BotCore conversational chat endpoint.

    Request JSON:
      {
        "message": "What's the current market regime?",
        "symbol":  "GBPUSD",   (optional, defaults to GBPUSD)
        "user_id": "<uuid>"    (optional — enables message history)
      }

    Returns the full response in one payload.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400

        message = data.get("message", "").strip()
        symbol  = data.get("symbol", "GBPUSD").upper()
        user_id = data.get("user_id")

        if not message:
            return jsonify({"error": "Missing message"}), 400

        openai_key = os.getenv("OPENAI_API_KEY")
        if not openai_key:
            return jsonify({"error": "OPENAI_API_KEY not configured"}), 503

        from openai import OpenAI
        from prompt import get_botcore_prompt

        context = _build_chat_context(symbol, user_id)
        client  = OpenAI(api_key=openai_key)

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": get_botcore_prompt()},
                {"role": "user",   "content": f"{context}\n\n---\n\nUSER: {message}"}
            ],
            max_tokens=2000,
            temperature=0.4
        )

        reply = response.choices[0].message.content

        if user_id:
            try:
                from user_tracking import add_message
                add_message(user_id, "user",      message)
                add_message(user_id, "assistant", reply)
            except Exception as e:
                print(f"[chat] Failed to save message history: {e}")

        return jsonify({
            "success":  True,
            "symbol":   symbol,
            "message":  message,
            "response": reply
        })

    except Exception as e:
        print(f"[chat] Error: {e}")
        return jsonify({"error": str(e), "success": False}), 500


@app.route("/api/chat/stream", methods=["POST"])
def chat_stream():
    """
    BotCore streaming chat endpoint (newline-delimited JSON).

    Request JSON:
      {
        "message": "Explain the current DXY outlook",
        "symbol":  "GBPUSD",  (optional, defaults to GBPUSD)
        "user_id": "<uuid>"   (optional — enables message history)
      }

    Streams chunks as newline-delimited JSON:
      {"type": "chunk",  "content": "The DXY is..."}
      {"type": "done",   "content": ""}
      {"type": "error",  "content": "error message"}
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400

        message = data.get("message", "").strip()
        symbol  = data.get("symbol", "GBPUSD").upper()
        user_id = data.get("user_id")

        if not message:
            return jsonify({"error": "Missing message"}), 400

        openai_key = os.getenv("OPENAI_API_KEY")
        if not openai_key:
            return jsonify({"error": "OPENAI_API_KEY not configured"}), 503

        context = _build_chat_context(symbol, user_id)

        def generate():
            full_reply = []
            try:
                from openai import OpenAI
                from prompt import get_botcore_prompt

                client = OpenAI(api_key=openai_key)
                stream = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": get_botcore_prompt()},
                        {"role": "user",   "content": f"{context}\n\n---\n\nUSER: {message}"}
                    ],
                    max_tokens=2000,
                    temperature=0.4,
                    stream=True
                )

                for chunk in stream:
                    delta = chunk.choices[0].delta.content
                    if delta:
                        full_reply.append(delta)
                        yield json.dumps({"type": "chunk", "content": delta}) + "\n"

                # Save history after the full response is streamed
                if user_id and full_reply:
                    try:
                        from user_tracking import add_message
                        add_message(user_id, "user",      message)
                        add_message(user_id, "assistant", "".join(full_reply))
                    except Exception as hist_err:
                        print(f"[chat/stream] Failed to save history: {hist_err}")

                yield json.dumps({"type": "done", "content": ""}) + "\n"

            except Exception as e:
                print(f"[chat/stream] Error: {e}")
                yield json.dumps({"type": "error", "content": str(e)}) + "\n"

        return Response(
            stream_with_context(generate()),
            mimetype="application/x-ndjson"
        )

    except Exception as e:
        print(f"[chat/stream] Error: {e}")
        return jsonify({"error": str(e)}), 500


# =============================================================================
# AUTH ENDPOINTS
# =============================================================================

@app.route("/api/auth/register", methods=["POST"])
def auth_register():
    """
    Register a new user for the BotCore chat interface.

    Request JSON:
      { "email": "you@example.com", "password": "...", "full_name": "Your Name" }

    Returns:
      { "success": true, "user_id": "...", "email": "...", "full_name": "..." }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400

        email     = (data.get("email")     or "").strip()
        password  =  data.get("password")  or ""
        full_name = (data.get("full_name") or "").strip()

        if not email:
            return jsonify({"error": "Missing email"}), 400
        if not password:
            return jsonify({"error": "Missing password"}), 400
        if not full_name:
            return jsonify({"error": "Missing full_name"}), 400

        from user_tracking import create_user

        try:
            user = create_user(email=email, password=password, full_name=full_name)
        except ValueError as ve:
            return jsonify({"error": str(ve), "success": False}), 409

        if not user:
            return jsonify({"error": "Failed to create user", "success": False}), 500

        return jsonify({
            "success":   True,
            "user_id":   user["user_id"],
            "email":     user["email"],
            "full_name": user["full_name"],
            "created_at": user["created_at"]
        }), 201

    except Exception as e:
        print(f"[auth/register] Error: {e}")
        return jsonify({"error": str(e), "success": False}), 500


@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    """
    Authenticate an existing user.

    Request JSON:
      { "email": "you@example.com", "password": "..." }

    Returns:
      { "success": true, "user_id": "...", "email": "...", "full_name": "..." }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400

        email    = (data.get("email")    or "").strip()
        password =  data.get("password") or ""

        if not email:
            return jsonify({"error": "Missing email"}), 400
        if not password:
            return jsonify({"error": "Missing password"}), 400

        from user_tracking import get_user_by_email, verify_password

        user = get_user_by_email(email)
        if not user:
            return jsonify({"error": "Email not found", "success": False}), 404

        if not verify_password(password, user.get("password", "")):
            return jsonify({"error": "Invalid password", "success": False}), 401

        return jsonify({
            "success":    True,
            "user_id":    user["user_id"],
            "email":      user["email"],
            "full_name":  user["full_name"],
            "created_at": user["created_at"]
        })

    except Exception as e:
        print(f"[auth/login] Error: {e}")
        return jsonify({"error": str(e), "success": False}), 500


@app.route("/api/auth/me", methods=["GET"])
def auth_me():
    """
    Return basic profile info for a user_id.
    Query param: ?user_id=<uuid>
    """
    try:
        user_id = request.args.get("user_id", "").strip()
        if not user_id:
            return jsonify({"error": "Missing user_id"}), 400

        from user_tracking import get_user_by_id
        user = get_user_by_id(user_id)

        if not user:
            return jsonify({"error": "User not found", "success": False}), 404

        return jsonify({"success": True, **user})

    except Exception as e:
        print(f"[auth/me] Error: {e}")
        return jsonify({"error": str(e), "success": False}), 500


@app.route("/api/auth/history", methods=["GET"])
def auth_history():
    """
    Return the recent message history for a user.
    Query param: ?user_id=<uuid>
    """
    try:
        user_id = request.args.get("user_id", "").strip()
        if not user_id:
            return jsonify({"error": "Missing user_id"}), 400

        from user_tracking import get_messages, is_user_active
        if not is_user_active(user_id):
            return jsonify({"error": "User not found", "success": False}), 404

        messages = get_messages(user_id)
        return jsonify({"success": True, "user_id": user_id, "messages": messages})

    except Exception as e:
        print(f"[auth/history] Error: {e}")
        return jsonify({"error": str(e), "success": False}), 500


@app.route("/api/auth/history", methods=["DELETE"])
def auth_history_clear():
    """
    Clear the message history for a user.
    Request JSON: { "user_id": "<uuid>" }
    """
    try:
        data    = request.get_json() or {}
        user_id = data.get("user_id", "").strip()
        if not user_id:
            return jsonify({"error": "Missing user_id"}), 400

        from user_tracking import clear_messages, is_user_active
        if not is_user_active(user_id):
            return jsonify({"error": "User not found", "success": False}), 404

        clear_messages(user_id)
        return jsonify({"success": True, "user_id": user_id, "message": "History cleared"})

    except Exception as e:
        print(f"[auth/history/clear] Error: {e}")
        return jsonify({"error": str(e), "success": False}), 500


if __name__ == "__main__":
    port = int(os.getenv('PORT', 5000))
    host = os.getenv('HOST', '0.0.0.0')

    print(f"Starting BotCore API on {host}:{port}")
    print(f"Trading endpoints: http://{host}:{port}/api/trading/")
    print(f"Chat endpoints:    http://{host}:{port}/api/chat")
    print(f"Auth endpoints:    http://{host}:{port}/api/auth/")

    app.run(host=host, port=port, debug=False)
