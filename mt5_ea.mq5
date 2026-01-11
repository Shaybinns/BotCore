//+------------------------------------------------------------------+
//|                                                    BotCore_EA.mq5 |
//|                                  AI-Led Discretionary Trading EA |
//|                                                                  |
//+------------------------------------------------------------------+
#property copyright "BotCore"
#property version   "1.00"
#property strict

//--- Input parameters
input string   ServerURL = "http://localhost:5000";  // BotCore API URL
input string   TradingSymbol = "GBPUSD";              // Symbol to trade (default GBPUSD)
input int      MaxTradesPerDay = 10;                  // Maximum trades per day
input double   MaxRiskPerTrade = 0.02;                // Max risk per trade (2%)
input double   DailyDrawdownLimit = 0.05;             // Daily drawdown limit (5%)
input int      MaxSpreadPips = 30;                    // Maximum spread in pips
input int      RequestTimeout = 30;                   // API request timeout (seconds)

//--- Global variables
datetime lastCheckTime = 0;
datetime nextRunTime = 0;
string currentSetupId = "";
string currentSymbol = "";                             // Track which symbol we're trading
datetime lastDayChecked = 0;                          // Track last day we checked
string requestedTimeframes[];                       // Timeframes requested by AI
bool isStartOfDay = false;                             // Flag for start of day request

//+------------------------------------------------------------------+
//| Expert initialization function                                     |
//+------------------------------------------------------------------+
int OnInit()
{
   Print("BotCore EA initialized");
   Print("Server URL: ", ServerURL);
   
   // Set the symbol we're trading
   currentSymbol = TradingSymbol;
   Print("Trading Symbol: ", currentSymbol);
   
   // Initialize timeframe array
   ArrayResize(requestedTimeframes, 0);
   
   // Check if this is start of day
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   lastDayChecked = dt.day;
   isStartOfDay = true;  // First run is always start of day
   
   // Initial check
   nextRunTime = TimeCurrent();
   
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                   |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   Print("BotCore EA deinitialized");
}

//+------------------------------------------------------------------+
//| Expert tick function                                               |
//+------------------------------------------------------------------+
void OnTick()
{
   // Check if it's a new day (start of day detection)
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   if(dt.day != lastDayChecked)
   {
      isStartOfDay = true;
      lastDayChecked = dt.day;
      ArrayResize(requestedTimeframes, 0);  // Reset timeframes for new day
      Print("New day detected - will send start of day timeframes");
   }
   
   // Check if it's time to request analysis
   if(TimeCurrent() >= nextRunTime)
   {
      RequestTradingAnalysis();
   }
}

//+------------------------------------------------------------------+
//| Request trading analysis from BotCore API                         |
//+------------------------------------------------------------------+
void RequestTradingAnalysis()
{
   // Prepare OHLC data (with timeframes based on start of day or AI request)
   string ohlcJson = PrepareOHLCData();
   
   // Prepare account state
   string accountJson = PrepareAccountState();
   
   // Build request payload
   string payload = "{";
   payload += "\"symbol\":\"" + currentSymbol + "\",";
   payload += "\"ohlc_data\":" + ohlcJson + ",";
   payload += "\"account_state\":" + accountJson;
   
   // Include requested timeframes if we have them
   if(ArraySize(requestedTimeframes) > 0 || isStartOfDay)
   {
      payload += ",\"requested_timeframes\":[";
      if(isStartOfDay)
      {
         // Start of day timeframes
         payload += "\"H1\",\"H4\",\"D1\",\"W1\"";
      }
      else
      {
         // AI-requested timeframes
         for(int i = 0; i < ArraySize(requestedTimeframes); i++)
         {
            if(i > 0) payload += ",";
            payload += "\"" + requestedTimeframes[i] + "\"";
         }
      }
      payload += "]";
   }
   
   payload += "}";
   
   Print("========================================");
   Print("Sending request to BotCore API");
   Print("Symbol: ", currentSymbol);
   if(isStartOfDay)
   {
      Print("Type: START OF DAY");
      Print("Timeframes: H1, H4, D1, W1");
   }
   else
   {
      Print("Type: REGULAR REQUEST");
      Print("AI-requested timeframes: ", ArraySize(requestedTimeframes));
   }
   Print("========================================");
   
   // Make HTTP request
   char post[];
   char result[];
   string headers;
   
   ArrayResize(post, StringToCharArray(payload, post, 0, WHOLE_ARRAY, CP_UTF8) - 1);
   
   string url = ServerURL + "/api/trading/snapshot";
   
   int res = WebRequest("POST", url, "", NULL, RequestTimeout * 1000, post, 0, result, headers);
   
   if(res == -1)
   {
      int error = GetLastError();
      Print("WebRequest error: ", error);
      // Retry in 1 minute
      nextRunTime = TimeCurrent() + 60;
      return;
   }
   
   // Parse response
   string responseStr = CharArrayToString(result);
   ProcessTradingDecision(responseStr);
   
   // Reset start of day flag after first request
   isStartOfDay = false;
}

//+------------------------------------------------------------------+
//| Prepare OHLC data for API                                         |
//+------------------------------------------------------------------+
string PrepareOHLCData()
{
   string json = "{";
   
   // Get current price for the trading symbol
   double bid = SymbolInfoDouble(currentSymbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(currentSymbol, SYMBOL_ASK);
   double currentPrice = (bid + ask) / 2.0;
   
   json += "\"current_price\":" + DoubleToString(currentPrice, 5) + ",";
   json += "\"primary_timeframe\":\"H1\",";
   
   // Determine which timeframes to send
   string timeframesToSend[];
   int timeframeCount = 0;
   
   if(isStartOfDay)
   {
      // Start of day: always send H1, H4, D1, W1
      ArrayResize(timeframesToSend, 4);
      timeframesToSend[0] = "H1";
      timeframesToSend[1] = "H4";
      timeframesToSend[2] = "D1";
      timeframesToSend[3] = "W1";
      timeframeCount = 4;
   }
   else if(ArraySize(requestedTimeframes) > 0)
   {
      // Use AI-requested timeframes
      ArrayResize(timeframesToSend, ArraySize(requestedTimeframes));
      for(int i = 0; i < ArraySize(requestedTimeframes); i++)
      {
         timeframesToSend[i] = requestedTimeframes[i];
      }
      timeframeCount = ArraySize(requestedTimeframes);
   }
   else
   {
      // Default fallback: send H1, M15, M5, M1
      ArrayResize(timeframesToSend, 4);
      timeframesToSend[0] = "H1";
      timeframesToSend[1] = "M15";
      timeframesToSend[2] = "M5";
      timeframesToSend[3] = "M1";
      timeframeCount = 4;
   }
   
   // Get OHLC for each requested timeframe
   for(int i = 0; i < timeframeCount; i++)
   {
      if(i > 0) json += ",";
      
      string tf = timeframesToSend[i];
      ENUM_TIMEFRAMES mt5Timeframe = GetMT5Timeframe(tf);
      int candleCount = GetCandleCountForTimeframe(tf);
      
      json += "\"" + tf + "\":";
      json += GetOHLCArray(mt5Timeframe, candleCount);
   }
   
   json += "}";
   
   return json;
}

//+------------------------------------------------------------------+
//| Convert timeframe string to MT5 enum                              |
//+------------------------------------------------------------------+
ENUM_TIMEFRAMES GetMT5Timeframe(string tf)
{
   if(tf == "M1") return PERIOD_M1;
   if(tf == "M5") return PERIOD_M5;
   if(tf == "M15") return PERIOD_M15;
   if(tf == "M30") return PERIOD_M30;
   if(tf == "H1") return PERIOD_H1;
   if(tf == "H4") return PERIOD_H4;
   if(tf == "D1") return PERIOD_D1;
   if(tf == "W1") return PERIOD_W1;
   if(tf == "MN1") return PERIOD_MN1;
   
   return PERIOD_H1;  // Default
}

//+------------------------------------------------------------------+
//| Get appropriate candle count for timeframe                        |
//+------------------------------------------------------------------+
int GetCandleCountForTimeframe(string tf)
{
   if(tf == "M1") return 500;
   if(tf == "M5") return 300;
   if(tf == "M15") return 200;
   if(tf == "M30") return 200;
   if(tf == "H1") return 200;
   if(tf == "H4") return 150;
   if(tf == "D1") return 100;
   if(tf == "W1") return 52;
   if(tf == "MN1") return 24;
   
   return 100;  // Default
}

//+------------------------------------------------------------------+
//| Get OHLC array for a timeframe                                    |
//+------------------------------------------------------------------+
string GetOHLCArray(ENUM_TIMEFRAMES timeframe, int count)
{
   string json = "[";
   
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   
   // Use currentSymbol instead of _Symbol
   int copied = CopyRates(currentSymbol, timeframe, 0, count, rates);
   
   if(copied > 0)
   {
      for(int i = 0; i < copied; i++)
      {
         if(i > 0) json += ",";
         json += "{";
         json += "\"time\":" + IntegerToString((int)rates[i].time) + ",";
         json += "\"open\":" + DoubleToString(rates[i].open, 5) + ",";
         json += "\"high\":" + DoubleToString(rates[i].high, 5) + ",";
         json += "\"low\":" + DoubleToString(rates[i].low, 5) + ",";
         json += "\"close\":" + DoubleToString(rates[i].close, 5) + ",";
         json += "\"volume\":" + IntegerToString((int)rates[i].tick_volume);
         json += "}";
      }
   }
   
   json += "]";
   return json;
}

//+------------------------------------------------------------------+
//| Prepare account state for API                                     |
//+------------------------------------------------------------------+
string PrepareAccountState()
{
   string json = "{";
   
   json += "\"balance\":" + DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2) + ",";
   json += "\"equity\":" + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2) + ",";
   
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double drawdown = (balance - equity) / balance;
   json += "\"drawdown\":" + DoubleToString(drawdown, 4) + ",";
   
   json += "\"open_positions\":[";
   // Get open positions (simplified)
   int totalPositions = PositionsTotal();
   for(int i = 0; i < totalPositions; i++)
   {
      if(i > 0) json += ",";
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0)
      {
         json += "{";
         json += "\"ticket\":" + IntegerToString((int)ticket) + ",";
         json += "\"symbol\":\"" + PositionGetString(POSITION_SYMBOL) + "\",";
         json += "\"type\":\"" + (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? "BUY" : "SELL") + "\",";
         json += "\"volume\":" + DoubleToString(PositionGetDouble(POSITION_VOLUME), 2) + ",";
         json += "\"price\":" + DoubleToString(PositionGetDouble(POSITION_PRICE_OPEN), 5);
         json += "}";
      }
   }
   json += "],";
   
   json += "\"pending_orders\":[],";  // Simplified
   json += "\"max_trades_per_day\":" + IntegerToString(MaxTradesPerDay) + ",";
   json += "\"max_risk_per_trade\":" + DoubleToString(MaxRiskPerTrade, 4) + ",";
   json += "\"daily_drawdown_limit\":" + DoubleToString(DailyDrawdownLimit, 4);
   
   json += "}";
   return json;
}

//+------------------------------------------------------------------+
//| Process trading decision from API                                 |
//+------------------------------------------------------------------+
void ProcessTradingDecision(string responseJson)
{
   Print("Received decision: ", responseJson);
   
   // Parse next_requested_timeframes from response
   ParseRequestedTimeframes(responseJson);
   
   // Parse next_run_at_utc and set nextRunTime
   ParseNextRunTime(responseJson);
   
   // Parse action and setup_id
   ParseAction(responseJson);
   
   // TODO: Execute trades if action is "ENTER" and passes safety checks
}

//+------------------------------------------------------------------+
//| Parse next_requested_timeframes from JSON response               |
//+------------------------------------------------------------------+
void ParseRequestedTimeframes(string json)
{
   // Look for "next_requested_timeframes" in JSON
   int startPos = StringFind(json, "\"next_requested_timeframes\"");
   if(startPos == -1)
   {
      Print("No next_requested_timeframes found in response");
      return;
   }
   
   // Find the array start
   int arrayStart = StringFind(json, "[", startPos);
   if(arrayStart == -1) return;
   
   // Find the array end
   int arrayEnd = StringFind(json, "]", arrayStart);
   if(arrayEnd == -1) return;
   
   // Extract the array content
   string arrayContent = StringSubstr(json, arrayStart + 1, arrayEnd - arrayStart - 1);
   
   // Parse timeframes (simple comma-separated)
   ArrayResize(requestedTimeframes, 0);
   string timeframes[];
   int count = StringSplit(arrayContent, ',', timeframes);
   
   for(int i = 0; i < count; i++)
   {
      // Clean up the timeframe string (remove quotes, spaces)
      string tf = timeframes[i];
      StringReplace(tf, "\"", "");
      StringReplace(tf, " ", "");
      StringTrimLeft(tf);
      StringTrimRight(tf);
      
      if(StringLen(tf) > 0)
      {
         int size = ArraySize(requestedTimeframes);
         ArrayResize(requestedTimeframes, size + 1);
         requestedTimeframes[size] = tf;
         Print("AI requested timeframe: ", tf);
      }
   }
   
   if(ArraySize(requestedTimeframes) > 0)
      Print("Stored ", ArraySize(requestedTimeframes), " requested timeframes for next call");
}

//+------------------------------------------------------------------+
//| Parse next_run_at_utc and update nextRunTime                     |
//+------------------------------------------------------------------+
void ParseNextRunTime(string json)
{
   // Look for "next_run_at_utc" in JSON
   int startPos = StringFind(json, "\"next_run_at_utc\"");
   if(startPos == -1)
   {
      Print("No next_run_at_utc found, using default 15 minutes");
      nextRunTime = TimeCurrent() + 900;  // 15 minutes default
      return;
   }
   
   // Find the value (after colon)
   int colonPos = StringFind(json, ":", startPos);
   if(colonPos == -1) return;
   
   // Find the quoted value
   int quoteStart = StringFind(json, "\"", colonPos);
   if(quoteStart == -1) return;
   
   int quoteEnd = StringFind(json, "\"", quoteStart + 1);
   if(quoteEnd == -1) return;
   
   string timeStr = StringSubstr(json, quoteStart + 1, quoteEnd - quoteStart - 1);
   
   // Parse ISO 8601 timestamp (simplified - just extract the time part)
   // For now, use a simple approach: if we can't parse, use default
   // TODO: Implement proper ISO 8601 parsing
   
   Print("AI requested next run at: ", timeStr);
   
   // For now, default to 15 minutes
   // In production, you'd parse the ISO timestamp properly
   nextRunTime = TimeCurrent() + 900;  // 15 minutes
}

//+------------------------------------------------------------------+
//| Parse action and setup_id from response                          |
//+------------------------------------------------------------------+
void ParseAction(string json)
{
   // Extract action
   int actionStart = StringFind(json, "\"action\"");
   if(actionStart != -1)
   {
      int colonPos = StringFind(json, ":", actionStart);
      int quoteStart = StringFind(json, "\"", colonPos);
      int quoteEnd = StringFind(json, "\"", quoteStart + 1);
      if(quoteEnd > quoteStart)
      {
         string action = StringSubstr(json, quoteStart + 1, quoteEnd - quoteStart - 1);
         Print("AI Action: ", action);
      }
   }
   
   // Extract setup_id
   int setupStart = StringFind(json, "\"setup_id\"");
   if(setupStart != -1)
   {
      int colonPos = StringFind(json, ":", setupStart);
      int quoteStart = StringFind(json, "\"", colonPos);
      int quoteEnd = StringFind(json, "\"", quoteStart + 1);
      if(quoteEnd > quoteStart)
      {
         currentSetupId = StringSubstr(json, quoteStart + 1, quoteEnd - quoteStart - 1);
         Print("Setup ID: ", currentSetupId);
      }
   }
}

//+------------------------------------------------------------------+

