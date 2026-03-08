# BotCore API Reference

Base URL: `https://botcore-production.up.railway.app`

---

## Contents

- [Strategies](#strategies) — create and manage named trading strategy prompts
- [Authentication](#authentication) — user accounts for the chat interface
- [Chat](#chat) — conversational interface to the trading system

> **Trading endpoints** (`/api/trading/sod`, `/api/trading/intraday`, etc.) are called directly by the MT5 EA and are documented separately in the EA source code.

---

## Strategies

Strategies are named trading rule sets stored in the database. Each strategy has a `strategy_prompt` — a plain-text (or structured text) description of exactly how a specific approach should be traded: entry conditions, session filters, risk rules, setup criteria, etc.

When a strategy is active, its prompt is appended to the AI's system prompt at analysis time. The AI is then instructed to apply those rules when evaluating setups.

**Prompt composition per analysis type:**

| Analysis | System prompt = |
|---|---|
| SOD | General + SOD methodology + **Strategy** |
| Intraday | General + Intraday methodology + **Strategy** |
| BotCore chat | General + BotCore chat instructions _(no strategy)_ |

Strategies are selected **per EA instance** via the `StrategyName` input parameter in MetaTrader. The strategy name is sent with every SOD and Intraday request. **A strategy is required** — if the field is missing, empty, or does not match a name in the database, the request is **rejected with a 400 error** and the analysis will not run. This is intentional: all trading must be rule-based and linked to a defined strategy.

---

### Create / Update a Strategy
`POST /api/strategies`

Create a new strategy or overwrite an existing one. If `strategy_name` already exists in the database the prompt and `uploaded_by` fields are updated and `updated_at` is refreshed.

**Request body**
```json
{
  "strategy_name":   "London Liquidity Sweep",
  "strategy_prompt": "Only look for entries during the London session (07:00–10:00 UTC).\nTarget liquidity sweeps above/below the Asian session range.\nEntry trigger: a clear break and retest of the Asian high or low on M15 or lower.\nMinimum R:R 1:2. Maximum risk per trade: 1%.\nAvoid trading within 15 minutes of a high-impact news event.",
  "uploaded_by":     "john@example.com"
}
```

| Field | Required | Notes |
|---|---|---|
| `strategy_name` | Yes | Unique identifier. Case-sensitive. This exact string must be entered as `StrategyName` in the EA. |
| `strategy_prompt` | Yes | Free-form text describing the strategy rules. Can be as long as needed — it becomes part of the GPT system prompt. |
| `uploaded_by` | Yes | Name or email of the person uploading. Stored for audit purposes only. |

**Success response** `201`
```json
{
  "success": true,
  "strategy": {
    "strategy_name": "London Liquidity Sweep",
    "uploaded_by":   "john@example.com",
    "created_at":    "2026-03-08T09:00:00+00:00",
    "updated_at":    "2026-03-08T09:00:00+00:00"
  }
}
```

> The `strategy_prompt` text is **not** returned on create/update — use `GET /api/strategies/<name>` to retrieve it.

**Error responses**
| Status | Meaning |
|--------|---------|
| 400 | Missing `strategy_name`, `strategy_prompt`, or `uploaded_by` |
| 500 | Database error |

---

### List All Strategies
`GET /api/strategies`

Returns all strategies (metadata only — no prompt text). Use this to discover available strategy names before configuring an EA instance.

**Success response** `200`
```json
{
  "success": true,
  "strategies": [
    {
      "strategy_name": "London Liquidity Sweep",
      "uploaded_by":   "john@example.com",
      "created_at":    "2026-03-08T09:00:00+00:00",
      "updated_at":    "2026-03-08T09:00:00+00:00"
    },
    {
      "strategy_name": "NY Open Momentum",
      "uploaded_by":   "sarah@example.com",
      "created_at":    "2026-03-07T14:30:00+00:00",
      "updated_at":    "2026-03-07T14:30:00+00:00"
    }
  ]
}
```

Returns an empty array `[]` if no strategies have been uploaded yet.

---

### Get a Strategy (with prompt)
`GET /api/strategies/<strategy_name>`

Returns the full strategy record including the prompt text.

**Example**
```
GET /api/strategies/London%20Liquidity%20Sweep
```

**Success response** `200`
```json
{
  "success": true,
  "strategy": {
    "strategy_name":   "London Liquidity Sweep",
    "strategy_prompt": "Only look for entries during the London session (07:00–10:00 UTC)...",
    "uploaded_by":     "john@example.com",
    "created_at":      "2026-03-08T09:00:00+00:00",
    "updated_at":      "2026-03-08T09:00:00+00:00"
  }
}
```

**Error responses**
| Status | Meaning |
|--------|---------|
| 404 | Strategy name not found |

---

### Delete a Strategy
`DELETE /api/strategies/<strategy_name>`

Permanently deletes a strategy. Any EA instance configured with this strategy name will fail validation until the name is updated or the strategy is re-created.

**Example**
```
DELETE /api/strategies/London%20Liquidity%20Sweep
```

**Success response** `200`
```json
{
  "success": true,
  "message": "Strategy 'London Liquidity Sweep' deleted"
}
```

**Error responses**
| Status | Meaning |
|--------|---------|
| 404 | Strategy name not found |

---

### How to Write a Strategy Prompt

The `strategy_prompt` field is injected directly into the GPT system prompt under the heading **"ACTIVE TRADING STRATEGY"**. The AI is instructed to apply these rules when evaluating setups and making decisions.

Write it as a set of clear, specific trading rules. Example structure:

```
SETUP TYPE: Liquidity sweep reversals at key HTF levels

SESSION FILTER:
- Only enter during London (07:00–10:00 UTC) or New York (13:00–16:00 UTC) sessions
- No trades during Asian session or within 15 minutes of a high-impact news event

ENTRY CONDITIONS (all must be met):
1. Price sweeps a significant swing high/low visible on H1 or H4
2. Followed by a strong rejection candle (engulfing or pinbar) on M15 or M5
3. A Fair Value Gap (FVG) is created on entry timeframe
4. Entry direction aligns with the D1/W1 structural bias from the SOD analysis

ENTRY:
- Enter at the 50% retracement of the rejection candle or FVG fill
- Stop loss: above/below the sweep wick (add 5 pips buffer)
- Take profit: next structural level with minimum 1:2 R:R
- Risk: 1% per trade

MANAGEMENT:
- Move stop to breakeven once 1R in profit
- Take 50% partial at 1.5R, trail the remainder
- Exit fully if price closes beyond the invalidation level on M15

FILTERS TO AVOID:
- Ranging markets with no clear structure
- Setups where spread exceeds 2 pips
- More than one active trade at a time
```

The more specific and rule-based the prompt, the more consistently the AI will apply the strategy.

---

### EA Configuration

In MetaTrader 5, open the EA inputs and set:

```
StrategyName = London Liquidity Sweep
```

The name must **exactly match** a strategy in the database (case-sensitive). If the field is empty or the name is not found in the database, the SOD and Intraday calls are rejected with a `400` error — the EA must always be configured with a valid strategy before it will run.

---

### Typical Strategy Workflow

```
1. POST /api/strategies          → upload your strategy prompt
2. GET  /api/strategies          → confirm it appears in the list
3. Set StrategyName in MT5 EA    → EA now sends the strategy name on every call
4. POST /api/trading/sod         → AI analyses using General + SOD + your strategy
5. POST /api/trading/intraday    → AI analyses using General + Intraday + your strategy
6. POST /api/strategies (again)  → update the prompt any time — takes effect on next EA call
7. DELETE /api/strategies/<name> → remove a strategy when no longer needed
```

---

## Authentication

### Register
`POST /api/auth/register`

Create a new user account.

**Request body**
```json
{
  "email":     "you@example.com",
  "password":  "yourpassword",
  "full_name": "Your Name"
}
```

**Success response** `201`
```json
{
  "success":    true,
  "user_id":    "550e8400-e29b-41d4-a716-446655440000",
  "email":      "you@example.com",
  "full_name":  "Your Name",
  "created_at": "2026-02-22T09:00:00+00:00"
}
```

**Error responses**
| Status | Meaning |
|--------|---------|
| 400 | Missing required field (`email`, `password`, or `full_name`) |
| 409 | Email already registered |
| 500 | Server error |

---

### Login
`POST /api/auth/login`

Validate credentials and retrieve the `user_id` for subsequent requests.

**Request body**
```json
{
  "email":    "you@example.com",
  "password": "yourpassword"
}
```

**Success response** `200`
```json
{
  "success":    true,
  "user_id":    "550e8400-e29b-41d4-a716-446655440000",
  "email":      "you@example.com",
  "full_name":  "Your Name",
  "created_at": "2026-02-22T09:00:00+00:00"
}
```

**Error responses**
| Status | Meaning |
|--------|---------|
| 400 | Missing `email` or `password` |
| 404 | Email not found |
| 401 | Wrong password |

---

### Get Profile
`GET /api/auth/me?user_id=<uuid>`

Fetch basic profile info for a logged-in user.

**Success response** `200`
```json
{
  "success":    true,
  "user_id":    "550e8400-e29b-41d4-a716-446655440000",
  "email":      "you@example.com",
  "full_name":  "Your Name",
  "created_at": "2026-02-22T09:00:00+00:00"
}
```

---

### Get Message History
`GET /api/auth/history?user_id=<uuid>`

Returns the most recent conversation history for the user (last 20 messages, oldest first).

**Success response** `200`
```json
{
  "success": true,
  "user_id": "550e8400-e29b-41d4-a716-446655440000",
  "messages": [
    {
      "role":    "user",
      "content": "What's the current GBPUSD bias?",
      "ts":      "2026-02-22T09:01:00+00:00"
    },
    {
      "role":    "assistant",
      "content": "The SOD analysis shows a bearish bias on GBPUSD...",
      "ts":      "2026-02-22T09:01:02+00:00"
    }
  ]
}
```

---

### Clear Message History
`DELETE /api/auth/history`

Wipe the conversation history for a user (e.g. start a new session).

**Request body**
```json
{
  "user_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Success response** `200`
```json
{
  "success": true,
  "user_id": "550e8400-e29b-41d4-a716-446655440000",
  "message": "History cleared"
}
```

---

## Chat

All chat endpoints read live context from the database (market intelligence, SOD plan, last intraday run, open positions). If a `user_id` is provided, the user's recent message history is also included in the context window, and the new exchange is saved after each response.

### Chat (standard)
`POST /api/chat`

Send a message and receive the full response in one payload.

**Request body**
```json
{
  "message": "What's the current market regime?",
  "symbol":  "GBPUSD",
  "user_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

- `symbol` — optional, defaults to `GBPUSD`
- `user_id` — optional; enables per-user message history

**Success response** `200`
```json
{
  "success":  true,
  "symbol":   "GBPUSD",
  "message":  "What's the current market regime?",
  "response": "Based on today's SOD analysis, the market is in a risk-off regime..."
}
```

---

### Chat (streaming)
`POST /api/chat/stream`

Stream the response as newline-delimited JSON (NDJSON). Better for UI chat bubbles — renders the reply word by word as it arrives.

**Request body**
```json
{
  "message": "Walk me through today's trading plan",
  "symbol":  "GBPUSD",
  "user_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Response stream** (`Content-Type: application/x-ndjson`)

Each line is a JSON object:
```
{"type": "chunk",  "content": "Based on "}
{"type": "chunk",  "content": "today's SOD analysis"}
{"type": "chunk",  "content": ", the bias is..."}
{"type": "done",   "content": ""}
```

On error:
```
{"type": "error", "content": "error message here"}
```

**Frontend integration example (JavaScript)**
```javascript
const response = await fetch('/api/chat/stream', {
  method:  'POST',
  headers: { 'Content-Type': 'application/json' },
  body:    JSON.stringify({ message, symbol: 'GBPUSD', user_id })
});

const reader = response.body.getReader();
const decoder = new TextDecoder();

while (true) {
  const { done, value } = await reader.read();
  if (done) break;

  const lines = decoder.decode(value).split('\n').filter(Boolean);
  for (const line of lines) {
    const data = JSON.parse(line);
    if (data.type === 'chunk')  appendToChat(data.content);
    if (data.type === 'done')   finishChat();
    if (data.type === 'error')  showError(data.content);
  }
}
```

---

## Typical Chat User Flow

```
1. POST /api/auth/register   → get user_id (one time)
2. POST /api/auth/login      → get user_id on each session
3. POST /api/chat/stream     → chat with user_id, history is automatic
4. GET  /api/auth/history    → load past messages on page refresh
5. DELETE /api/auth/history  → clear chat to start fresh
```

---

## Notes

- Passwords are hashed with bcrypt — never stored in plain text.
- `user_id` is a UUID generated at registration. Store it on the client side (localStorage, cookie, etc.) after login.
- No JWT or session tokens are issued — the `user_id` acts as the session identifier. Add a proper auth layer (JWT, cookies) before exposing this publicly.
- Message history keeps the last **20 messages** (10 turns). Older messages are automatically dropped on each new exchange.
- The chat assistant has **read-only** access to trading data. It cannot execute trades or modify positions.
