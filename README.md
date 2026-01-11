# BotCore - AI-Led Discretionary Trading System

**Intelligent Trading System with MT5 EA Execution**

BotCore is a trading system where AI performs full discretionary analysis using both chart screenshots (visual perception) and OHLC data (precise detection), while MetaTrader 5 executes trades with hard safety limits.

## Architecture

### Core Components

1. **Python Server** (`api_server.py`) - REST API for MT5 EA communication
2. **Brain** (`brain.py`) - AI decision orchestrator
3. **Chart Service** (`chart_service.py`) - Chart-IMG.com integration
4. **OHLC Analyzer** (`ohlc_analyzer.py`) - FVG/BOS/imbalance detection
5. **GPT Vision** (`gpt_vision.py`) - Chart image analysis
6. **Market Data** (`market_data.py`) - Market context service
7. **Database** (`database.py`) - PostgreSQL for locked levels and setups
8. **MT5 EA** (`mt5_ea.mq5`) - Trade execution with safety limits

## Setup

### 1. Environment Variables

Copy `env_template.txt` to `.env` and configure:

```bash
OPENAI_API_KEY=your_key_here
CHART_IMG_API_KEY=your_key_here
DATABASE_URL=postgresql://user:pass@localhost:5432/botcore
```

### 2. Database Initialization

```python
from database import init_database
init_database()
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Run Server

```bash
python api_server.py
```

### 5. Install MT5 EA

1. Copy `mt5_ea.mq5` to MetaTrader 5 `Experts` folder
2. Configure EA inputs (ServerURL, risk limits, etc.)
3. Attach to chart and enable auto-trading

## Trading Flow

1. **Session Start**: AI identifies levels from chart, locks them in DB
2. **Monitoring**: AI checks OHLC for patterns, uses locked levels
3. **Hot Zone**: When price approaches locked level, AI monitors closely
4. **Entry**: AI detects FVG/BOS/imbalance, confirms with chart if needed
5. **Execution**: MT5 EA validates and executes if safe
6. **Management**: AI manages position, exits on TP/SL/invalidation

## Key Features

- **Dual Input Strategy**: Chart images (visual) + OHLC data (precise)
- **Locked Levels**: Levels persist in DB, don't redraw every run
- **Stateful System**: Tracks setups, phases, and trade history
- **Safety First**: EA enforces hard limits (max trades, risk, drawdown)
- **Flexible Monitoring**: Adjusts check frequency based on market state

## API Endpoints

- `POST /api/trading/snapshot` - MT5 EA requests analysis
- `POST /api/trading/execute` - MT5 EA confirms execution
- `GET /api/trading/status` - System status check
- `GET /api/health` - Health check

## Database Schema

- `locked_levels` - Persistent levels/zones per symbol/session
- `active_setups` - Current setup states and phases
- `trade_events` - Execution history and events

## Strategy

The AI uses a prompt-based strategy defined in `prompt.py`:
- Level identification from charts
- FVG/BOS/imbalance detection from OHLC
- Entry/exit criteria
- Risk management rules

## License

MIT License
