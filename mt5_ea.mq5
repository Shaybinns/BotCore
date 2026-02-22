//+------------------------------------------------------------------+
//|                                                    BotCore_EA.mq5 |
//|                                  AI-Led Discretionary Trading EA |
//|                                                                  |
//+------------------------------------------------------------------+
#property copyright "BotCore"
#property version   "2.00"
#property strict

//--- Input parameters
input string   ServerURL = "http://localhost:5000";  // BotCore API URL
input string   TradingSymbol = "GBPUSD";              // Symbol to trade
input int      SODHour = 7;                           // Start of Day hour (24h format)

//--- EA State Enum
enum EA_STATE
{
   STATE_INIT,          // Initial state
   STATE_WAIT,          // AI says WAIT
   STATE_WATCH,         // AI says WATCH (monitoring level)
   STATE_HOTZONE,       // AI says HOTZONE (price at level)
   STATE_IN_TRADE,      // Position opened
   STATE_MANAGING,      // Managing position
   STATE_EXITING        // Closing position
};

//--- Global variables
EA_STATE CurrentState = STATE_INIT;
datetime LastSODTime = 0;
datetime NextReviewTime = 0;
string CurrentAction = "";
string MonitoringTimeframes = "";  // Comma-separated string
int CurrentPositionTicket = -1;
string LastAIResponse = "";

//+------------------------------------------------------------------+
//| Expert initialization function                                     |
//+------------------------------------------------------------------+
int OnInit()
{
   Print("========================================");
   Print("BotCore EA v2.0 Initialized");
   Print("Server URL: ", ServerURL);
   Print("Trading Symbol: ", TradingSymbol);
   Print("SOD Hour: ", SODHour, ":00");
   Print("========================================");
   
   CurrentState = STATE_INIT;
   LastSODTime = 0;
   NextReviewTime = 0;
   
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
   // Phase 2: Check if it's 7am for SOD
   CheckSODTime();
   
   // Phase 4: Check if it's time for next review (intraday)
   CheckReviewTime();
}

//+------------------------------------------------------------------+
//| Phase 2: Check and trigger SOD at 7am                            |
//+------------------------------------------------------------------+
void CheckSODTime()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   
   // Check if it's 7am and we haven't run SOD today
   if(dt.hour == SODHour && dt.min == 0)
   {
      datetime currentDay = StringToTime(IntegerToString(dt.year) + "." + 
                                          IntegerToString(dt.mon) + "." + 
                                          IntegerToString(dt.day));
      
      if(LastSODTime < currentDay)
      {
         Print("========================================");
         Print("SOD Time Reached (", SODHour, ":00) - Running SOD Analysis");
         Print("========================================");
         
         RunSOD();
         LastSODTime = currentDay;
      }
   }
}

//+------------------------------------------------------------------+
//| Phase 4: Check if it's time for next scheduled review            |
//+------------------------------------------------------------------+
void CheckReviewTime()
{
   if(NextReviewTime == 0) return;  // No review scheduled
   
   datetime currentTime = TimeCurrent();
   
   if(currentTime >= NextReviewTime)
   {
      Print("========================================");
      Print("Review Time Reached - Running Intraday Analysis");
      Print("Scheduled: ", TimeToString(NextReviewTime));
      Print("Current: ", TimeToString(currentTime));
      Print("========================================");
      
      RunIntraday();
      NextReviewTime = 0;  // Clear until next schedule
   }
}

//+------------------------------------------------------------------+
//| Phase 2: Run Start of Day Analysis                               |
//+------------------------------------------------------------------+
void RunSOD()
{
   Print("Preparing SOD OHLC Data...");
   
   // Prepare SOD payload with positions
   string payload = PrepareSODOHLCData();
   
   if(StringLen(payload) > 0)
   {
      // Send to SOD endpoint
      string response = SendToAPI("/api/trading/sod", payload);
      
      if(StringLen(response) > 0)
      {
         // Phase 3: Parse the response
         ParseAIResponse(response);
         
         // Always store positions after SOD (to sync state for the day)
         StoreCurrentPositions();
      }
   }
   else
   {
      Print("ERROR: Failed to prepare SOD data");
   }
}

//+------------------------------------------------------------------+
//| Phase 7: Run Intraday Analysis                                   |
//+------------------------------------------------------------------+
void RunIntraday()
{
   Print("Preparing Intraday OHLC Data...");
   
   // Phase 5: Build dynamic OHLC based on monitoring timeframes
   string payload = PrepareIntradayOHLCData();
   
   if(StringLen(payload) > 0)
   {
      // Send to Intraday endpoint
      string response = SendToAPI("/api/trading/intraday", payload);
      
      if(StringLen(response) > 0)
      {
         // Phase 3: Parse the response (will call StoreCurrentPositions() if trade executed)
         ParseAIResponse(response);
      }
      }
      else
      {
      Print("ERROR: Failed to prepare Intraday data");
   }
}

//+------------------------------------------------------------------+
//| Phase 3: Parse AI Response and Execute Actions                   |
//+------------------------------------------------------------------+
void ParseAIResponse(string response)
{
   Print("========================================");
   Print("Parsing AI Response");
   Print("========================================");
   
   LastAIResponse = response;
   
   // Extract action
   CurrentAction = ExtractJSONString(response, "action");
   Print("Action: ", CurrentAction);
   
   // Extract next_review_time
   string nextReviewStr = ExtractJSONString(response, "next_review_time");
   if(StringLen(nextReviewStr) > 0)
   {
      NextReviewTime = StringToTime(nextReviewStr);
      Print("Next Review: ", TimeToString(NextReviewTime));
   }
   
   // Extract monitoring_timeframes (as comma-separated string)
   MonitoringTimeframes = ExtractJSONArray(response, "monitoring_timeframes");
   Print("Monitoring Timeframes: ", MonitoringTimeframes);
   
   // Phase 8: Execute based on action
   if(CurrentAction == "ENTER")
   {
      ExecuteEnter(response);
   }
   else if(CurrentAction == "MANAGE")
   {
      ExecuteManage(response);
   }
   else if(CurrentAction == "EXIT")
   {
      ExecuteExit(response);
   }
   else if(CurrentAction == "WAIT")
   {
      UpdateState(STATE_WAIT);
   }
   else if(CurrentAction == "WATCH")
   {
      UpdateState(STATE_WATCH);
   }
   else if(CurrentAction == "HOTZONE")
   {
      UpdateState(STATE_HOTZONE);
   }
   
   Print("========================================");
   Print("State: ", EnumToString(CurrentState));
   Print("Next Review: ", TimeToString(NextReviewTime));
   Print("========================================");
}

//+------------------------------------------------------------------+
//| Phase 8: Execute ENTER action (Placeholder)                      |
//+------------------------------------------------------------------+
void ExecuteEnter(string response)
{
   Print("========================================");
   Print("EXECUTING ENTER ACTION");
   Print("========================================");
   
   // Extract asset_traded
   string assetTraded = ExtractJSONString(response, "asset_traded");
   if(StringLen(assetTraded) == 0)
   {
      assetTraded = TradingSymbol;  // Fallback to default
   }
   
   // Extract enter_order details
   string orderType = ExtractNestedJSONString(response, "enter_order", "order_type");
   double entryPrice = ExtractNestedJSONDouble(response, "enter_order", "entry_price");
   double stopLoss = ExtractNestedJSONDouble(response, "enter_order", "stop_loss");
   double takeProfit = ExtractNestedJSONDouble(response, "enter_order", "take_profit");
   double riskPercentage = ExtractNestedJSONDouble(response, "enter_order", "risk_percentage");
   
   Print("Asset: ", assetTraded);
   Print("Order Type: ", orderType);
   Print("Entry Price: ", entryPrice);
   Print("Stop Loss: ", stopLoss);
   Print("Take Profit: ", takeProfit);
   Print("Risk %: ", riskPercentage * 100, "%");
   
   // Validate order details
   if(!ValidateEnterOrder(assetTraded, orderType, entryPrice, stopLoss, takeProfit, riskPercentage))
   {
      Print("❌ Order validation failed - aborting");
      return;
   }
   
   // Calculate lot size based on risk
   double lotSize = CalculateLotSize(assetTraded, entryPrice, stopLoss, riskPercentage);
   
   if(lotSize <= 0)
   {
      Print("❌ Invalid lot size calculated: ", lotSize);
      return;
   }
   
   Print("Calculated Lot Size: ", lotSize);
   
   // Place the order
   int ticket = PlaceOrder(assetTraded, orderType, entryPrice, stopLoss, takeProfit, lotSize);
   
   if(ticket > 0)
   {
      CurrentPositionTicket = ticket;
      UpdateState(STATE_IN_TRADE);
      Print("✅ Order placed successfully - Ticket: ", ticket);
      
      // Update position database after successful entry
      StoreCurrentPositions();
   }
   else
   {
      Print("❌ Order placement failed");
      UpdateState(STATE_WATCH);  // Fallback to WATCH
   }
}

//+------------------------------------------------------------------+
//| Phase 8: Execute MANAGE action                                   |
//+------------------------------------------------------------------+
void ExecuteManage(string response)
{
   Print("========================================");
   Print("EXECUTING MANAGE ACTION");
   Print("========================================");
   
   // Extract ticket number
   int ticket = (int)ExtractNestedJSONDouble(response, "manage_order", "ticket");
   
   if(ticket <= 0)
   {
      Print("❌ Invalid ticket number: ", ticket);
      return;
   }
   
   Print("Managing Position - Ticket: ", ticket);
   
   // Extract position details for validation
   string aiAsset = ExtractNestedJSONString(response, "manage_order.position", "asset");
   string aiDirection = ExtractNestedJSONString(response, "manage_order.position", "direction");
   double aiEntryPrice = ExtractNestedJSONDouble(response, "manage_order.position", "entry_price");
   
   Print("AI Position Details:");
   Print("  Asset: ", aiAsset);
   Print("  Direction: ", aiDirection);
   Print("  Entry Price: ", aiEntryPrice);
   
   // Extract manage_order details
   double updateSL = ExtractNestedJSONDouble(response, "manage_order", "update_stop_loss");
   double updateTP = ExtractNestedJSONDouble(response, "manage_order", "update_take_profit");
   double partialClosePercent = ExtractNestedJSONDouble(response, "manage_order", "partial_close_percentage");
   
   Print("Manage Actions:");
   if(updateSL > 0) Print("  Update SL: ", updateSL);
   if(updateTP > 0) Print("  Update TP: ", updateTP);
   if(partialClosePercent > 0) Print("  Partial Close: ", partialClosePercent, "%");
   
   // Validate manage order (ticket + position details)
   if(!ValidateManageOrder(ticket, aiAsset, aiDirection, aiEntryPrice, updateSL, updateTP, partialClosePercent))
   {
      Print("❌ Manage validation failed");
      return;
   }
   
   // Execute partial close first (if requested)
   if(partialClosePercent > 0)
   {
      ExecutePartialClose(ticket, partialClosePercent);
   }
   
   // Modify SL/TP (if requested)
   if(updateSL > 0 || updateTP > 0)
   {
      ModifyPosition(ticket, updateSL, updateTP);
   }
   
   UpdateState(STATE_MANAGING);
   
   // Update position database after successful management
   StoreCurrentPositions();
}

//+------------------------------------------------------------------+
//| Phase 8: Execute EXIT action                                     |
//+------------------------------------------------------------------+
void ExecuteExit(string response)
{
   Print("========================================");
   Print("EXECUTING EXIT ACTION");
   Print("========================================");
   
   // Extract ticket number
   int ticket = (int)ExtractNestedJSONDouble(response, "exit_order", "ticket");
   string reason = ExtractNestedJSONString(response, "exit_order", "reason");
   
   if(ticket <= 0)
   {
      Print("❌ Invalid ticket number: ", ticket);
      return;
   }
   
   Print("Closing Position - Ticket: ", ticket);
   Print("Reason: ", reason);
   
   // Extract position details for validation
   string aiAsset = ExtractNestedJSONString(response, "exit_order.position", "asset");
   string aiDirection = ExtractNestedJSONString(response, "exit_order.position", "direction");
   double aiEntryPrice = ExtractNestedJSONDouble(response, "exit_order.position", "entry_price");
   
   Print("AI Position Details:");
   Print("  Asset: ", aiAsset);
   Print("  Direction: ", aiDirection);
   Print("  Entry Price: ", aiEntryPrice);
   
   // Validate exit order (ticket + position details)
   if(!ValidateExitOrder(ticket, aiAsset, aiDirection, aiEntryPrice))
   {
      Print("❌ Exit validation failed");
      return;
   }
   
   // Close the position (100%)
   if(ClosePosition(ticket))
   {
      Print("✅ Position closed successfully");
      CurrentPositionTicket = -1;
      UpdateState(STATE_WAIT);
      
      // Update position database after successful exit
      StoreCurrentPositions();
   }
   else
   {
      Print("❌ Failed to close position");
   }
}

//+------------------------------------------------------------------+
//| Phase 8: Update EA State                                         |
//+------------------------------------------------------------------+
void UpdateState(EA_STATE newState)
{
   CurrentState = newState;
   Print("State Updated: ", EnumToString(CurrentState));
}

//+------------------------------------------------------------------+
//| Validate ENTER order details                                     |
//+------------------------------------------------------------------+
bool ValidateEnterOrder(string symbol, string orderType, double entryPrice, double stopLoss, double takeProfit, double riskPercentage)
{
   Print("Validating ENTER order...");
   
   // Check if fields are populated
   if(StringLen(orderType) == 0)
   {
      Print("❌ Order type is empty");
      return false;
   }
   
   if(entryPrice <= 0)
   {
      Print("❌ Invalid entry price: ", entryPrice);
      return false;
   }
   
   if(stopLoss <= 0)
   {
      Print("❌ Invalid stop loss: ", stopLoss);
      return false;
   }
   
   if(takeProfit <= 0)
   {
      Print("❌ Invalid take profit: ", takeProfit);
      return false;
   }
   
   if(riskPercentage <= 0 || riskPercentage > 10)  // Max 10% risk
   {
      Print("❌ Invalid risk percentage: ", riskPercentage, "% (must be 0.1% - 10%)");
      return false;
   }
   
   // Get current market price
   double currentPrice;
   if(orderType == "BUY")
   {
      currentPrice = SymbolInfoDouble(symbol, SYMBOL_ASK);
   }
   else
   {
      currentPrice = SymbolInfoDouble(symbol, SYMBOL_BID);
   }
   
   // Check if entry price is reasonable (within 1000 pips of current price)
   double priceDifference = MathAbs(entryPrice - currentPrice) / SymbolInfoDouble(symbol, SYMBOL_POINT);
   if(priceDifference > 10000)  // 1000 pips
   {
      Print("❌ Entry price too far from current price: ", priceDifference, " points");
      return false;
   }
   
   // Validate SL/TP relationship
   if(orderType == "BUY")
   {
      if(stopLoss >= entryPrice)
      {
         Print("❌ BUY order: Stop loss must be below entry price");
         return false;
      }
      if(takeProfit <= entryPrice)
      {
         Print("❌ BUY order: Take profit must be above entry price");
         return false;
      }
   }
   else if(orderType == "SELL")
   {
      if(stopLoss <= entryPrice)
      {
         Print("❌ SELL order: Stop loss must be above entry price");
         return false;
      }
      if(takeProfit >= entryPrice)
      {
         Print("❌ SELL order: Take profit must be below entry price");
         return false;
      }
   }
   
   // Check minimum stop level
   int stopLevel = (int)SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double minDistance = stopLevel * SymbolInfoDouble(symbol, SYMBOL_POINT);
   
   if(MathAbs(entryPrice - stopLoss) < minDistance)
   {
      Print("❌ Stop loss too close to entry. Minimum distance: ", stopLevel, " points");
      return false;
   }
   
   Print("✅ Order validation passed");
   return true;
}

//+------------------------------------------------------------------+
//| Calculate lot size based on risk percentage                      |
//| Risk% is sent as whole number (2 = 2%, not 0.02)                |
//| Using: BaseLot = 1.0 for 100 points = 1% risk on 10k account    |
//+------------------------------------------------------------------+
double CalculateLotSize(string symbol, double entryPrice, double stopLoss, double riskPercentage)
{
   Print("Calculating lot size...");
   
   // Get account balance
   double accountBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   Print("Account Balance: ", accountBalance);
   
   // Convert risk percentage from whole number to decimal
   // AI sends: 2 (meaning 2%), we need: 0.02
   double riskDecimal = riskPercentage / 100.0;
   Print("Risk Percentage: ", riskPercentage, "% (", riskDecimal, " decimal)");
   
   // Calculate risk amount in account currency
   double riskAmount = accountBalance * riskDecimal;
   Print("Risk Amount: ", riskAmount);
   
   // Calculate SL distance in points
   double slDistance = MathAbs(entryPrice - stopLoss) / SymbolInfoDouble(symbol, SYMBOL_POINT);
   Print("SL Distance: ", slDistance, " points");
   
   if(slDistance <= 0)
   {
      Print("❌ Invalid SL distance");
      return 0;
   }
   
   // Get symbol properties for point value calculation
   // SYMBOL_TRADE_TICK_VALUE = profit/loss for 1 tick movement on 1 lot
   // SYMBOL_TRADE_TICK_SIZE = minimum price movement in quote currency
   // SYMBOL_POINT = point size (0.00001 for GBPUSD)
   
   double tickValue = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   
   Print("Tick Value: ", tickValue);
   Print("Tick Size: ", tickSize);
   Print("Point: ", point);
   
   // Calculate point value (value per point for 1 lot)
   // For GBPUSD: tickValue might be $10 per tick (0.0001)
   // point is 0.00001 (1 point = 1 pip on 5-digit broker)
   // pointValue = tickValue * (point / tickSize)
   // Example: If tickSize = 0.00001 and point = 0.00001, pointValue = tickValue
   //          If tickSize = 0.0001 and point = 0.00001, pointValue = tickValue * 0.1
   
   double pointValue = tickValue * (point / tickSize);
   Print("Point Value (per lot): $", pointValue);
   
   // Calculate lot size
   // Formula: lot = riskAmount / (slDistance * pointValue)
   // Example: Risk $100, SL 50 points, pointValue $1
   //          lot = 100 / (50 * 1) = 2.0 lots
   
   double lotSize = riskAmount / (slDistance * pointValue);
   
   Print("Raw Lot Size: ", lotSize);
   
   // Normalize lot size to broker requirements
   double minLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   
   // Round down to nearest lot step
   lotSize = MathFloor(lotSize / lotStep) * lotStep;
   
   // Clamp to min/max
   lotSize = MathMax(minLot, MathMin(maxLot, lotSize));
   
   Print("Final Lot Size: ", lotSize);
   Print("(Min: ", minLot, " | Max: ", maxLot, " | Step: ", lotStep, ")");
   
   return lotSize;
}

//+------------------------------------------------------------------+
//| Place order (Buy Stop, Buy Limit, Sell Stop, Sell Limit)        |
//+------------------------------------------------------------------+
int PlaceOrder(string symbol, string orderType, double entryPrice, double stopLoss, double takeProfit, double lotSize)
{
   Print("Placing order...");
   
   MqlTradeRequest request;
   MqlTradeResult result;
   ZeroMemory(request);
   ZeroMemory(result);
   
   // Get current price
   double currentPrice;
   ENUM_ORDER_TYPE mqlOrderType;
   
   if(orderType == "BUY")
   {
      currentPrice = SymbolInfoDouble(symbol, SYMBOL_ASK);
      
      // Determine Buy Stop or Buy Limit
      if(entryPrice > currentPrice)
      {
         mqlOrderType = ORDER_TYPE_BUY_STOP;
         Print("Order Type: BUY STOP (entry above current price)");
      }
      else
      {
         mqlOrderType = ORDER_TYPE_BUY_LIMIT;
         Print("Order Type: BUY LIMIT (entry below current price)");
      }
   }
   else if(orderType == "SELL")
   {
      currentPrice = SymbolInfoDouble(symbol, SYMBOL_BID);
      
      // Determine Sell Stop or Sell Limit
      if(entryPrice < currentPrice)
      {
         mqlOrderType = ORDER_TYPE_SELL_STOP;
         Print("Order Type: SELL STOP (entry below current price)");
      }
      else
      {
         mqlOrderType = ORDER_TYPE_SELL_LIMIT;
         Print("Order Type: SELL LIMIT (entry above current price)");
      }
   }
   else
   {
      Print("❌ Invalid order type: ", orderType);
      return -1;
   }
   
   // Fill request
   request.action = TRADE_ACTION_PENDING;
   request.symbol = symbol;
   request.volume = lotSize;
   request.type = mqlOrderType;
   request.price = entryPrice;
   request.sl = stopLoss;
   request.tp = takeProfit;
   request.deviation = 10;
   request.magic = 123456;
   request.comment = "BotCore AI";
   
   // Send order
   bool sent = OrderSend(request, result);
   
   if(sent && result.retcode == TRADE_RETCODE_DONE)
   {
      Print("✅ Order placed successfully!");
      Print("Ticket: ", result.order);
      Print("Entry: ", entryPrice);
      Print("SL: ", stopLoss);
      Print("TP: ", takeProfit);
      Print("Lot Size: ", lotSize);
      return (int)result.order;
   }
   else
   {
      Print("❌ Order failed: ", result.retcode);
      Print("Error: ", GetLastError());
      return -1;
   }
}

//+------------------------------------------------------------------+
//| Validate MANAGE order                                            |
//+------------------------------------------------------------------+
bool ValidateManageOrder(int ticket, string aiAsset, string aiDirection, double aiEntryPrice, double updateSL, double updateTP, double partialClosePercent)
{
   Print("Validating MANAGE order...");
   
   // Select the position by ticket
   if(!PositionSelectByTicket(ticket))
   {
      Print("❌ Position not found - Ticket: ", ticket);
      return false;
   }
   
   // Get actual position details
   string actualSymbol = PositionGetString(POSITION_SYMBOL);
   long posType = PositionGetInteger(POSITION_TYPE);
   string actualDirection = (posType == POSITION_TYPE_BUY) ? "BUY" : "SELL";
   double actualEntryPrice = PositionGetDouble(POSITION_PRICE_OPEN);
   
   Print("Actual Position Details:");
   Print("  Symbol: ", actualSymbol);
   Print("  Direction: ", actualDirection);
   Print("  Entry Price: ", actualEntryPrice);
   
   // Validate position details match (with 10-point tolerance for entry price)
   if(aiAsset != actualSymbol)
   {
      Print("❌ Symbol mismatch - AI: ", aiAsset, " | Actual: ", actualSymbol);
      return false;
   }
   
   if(aiDirection != actualDirection)
   {
      Print("❌ Direction mismatch - AI: ", aiDirection, " | Actual: ", actualDirection);
      return false;
   }
   
   // Entry price validation with 10-point tolerance
   double point = SymbolInfoDouble(actualSymbol, SYMBOL_POINT);
   double entryDifference = MathAbs(aiEntryPrice - actualEntryPrice) / point;
   
   if(entryDifference > 10.0)
   {
      Print("❌ Entry price mismatch - AI: ", aiEntryPrice, " | Actual: ", actualEntryPrice);
      Print("   Difference: ", entryDifference, " points (max 10 allowed)");
      return false;
   }
   
   Print("✅ Position validation passed (Entry difference: ", entryDifference, " points)");
   
   // Validate updateSL if provided
   double currentPrice = (posType == POSITION_TYPE_BUY) ? 
                         SymbolInfoDouble(actualSymbol, SYMBOL_BID) : 
                         SymbolInfoDouble(actualSymbol, SYMBOL_ASK);
   
   if(updateSL > 0)
   {
      if(posType == POSITION_TYPE_BUY && updateSL >= currentPrice)
      {
         Print("❌ BUY position: New SL must be below current price");
         return false;
      }
      if(posType == POSITION_TYPE_SELL && updateSL <= currentPrice)
      {
         Print("❌ SELL position: New SL must be above current price");
         return false;
      }
   }
   
   // Validate updateTP if provided
   if(updateTP > 0)
   {
      if(posType == POSITION_TYPE_BUY && updateTP <= currentPrice)
      {
         Print("❌ BUY position: New TP must be above current price");
         return false;
      }
      if(posType == POSITION_TYPE_SELL && updateTP >= currentPrice)
      {
         Print("❌ SELL position: New TP must be below current price");
         return false;
      }
   }
   
   // Validate partial close percentage
   if(partialClosePercent > 0 && (partialClosePercent < 1 || partialClosePercent > 99))
   {
      Print("❌ Partial close percentage must be 1-99%");
      return false;
   }
   
   Print("✅ Manage order validation passed");
   return true;
}

//+------------------------------------------------------------------+
//| Validate EXIT order                                              |
//+------------------------------------------------------------------+
bool ValidateExitOrder(int ticket, string aiAsset, string aiDirection, double aiEntryPrice)
{
   Print("Validating EXIT order...");
   
   // Check if position exists
   if(!PositionSelectByTicket(ticket))
   {
      Print("❌ Position does not exist - Ticket: ", ticket);
      return false;
   }
   
   // Get actual position details
   string actualSymbol = PositionGetString(POSITION_SYMBOL);
   long posType = PositionGetInteger(POSITION_TYPE);
   string actualDirection = (posType == POSITION_TYPE_BUY) ? "BUY" : "SELL";
   double actualEntryPrice = PositionGetDouble(POSITION_PRICE_OPEN);
   
   Print("Actual Position Details:");
   Print("  Symbol: ", actualSymbol);
   Print("  Direction: ", actualDirection);
   Print("  Entry Price: ", actualEntryPrice);
   
   // Validate position details match (with 10-point tolerance for entry price)
   if(aiAsset != actualSymbol)
   {
      Print("❌ Symbol mismatch - AI: ", aiAsset, " | Actual: ", actualSymbol);
      return false;
   }
   
   if(aiDirection != actualDirection)
   {
      Print("❌ Direction mismatch - AI: ", aiDirection, " | Actual: ", actualDirection);
      return false;
   }
   
   // Entry price validation with 10-point tolerance
   double point = SymbolInfoDouble(actualSymbol, SYMBOL_POINT);
   double entryDifference = MathAbs(aiEntryPrice - actualEntryPrice) / point;
   
   if(entryDifference > 10.0)
   {
      Print("❌ Entry price mismatch - AI: ", aiEntryPrice, " | Actual: ", actualEntryPrice);
      Print("   Difference: ", entryDifference, " points (max 10 allowed)");
      return false;
   }
   
   Print("✅ Position validation passed (Entry difference: ", entryDifference, " points)");
   Print("✅ Exit order validation passed");
   return true;
}

//+------------------------------------------------------------------+
//| Execute partial close of position                                |
//+------------------------------------------------------------------+
void ExecutePartialClose(int ticket, double percentage)
{
   Print("Executing partial close...");
   
   if(!PositionSelectByTicket(ticket))
   {
      Print("❌ Cannot select position");
      return;
   }
   
   double currentVolume = PositionGetDouble(POSITION_VOLUME);
   double closeVolume = currentVolume * (percentage / 100.0);
   
   // Normalize volume
   string symbol = PositionGetString(POSITION_SYMBOL);
   double minLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double lotStep = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   
   closeVolume = MathFloor(closeVolume / lotStep) * lotStep;
   closeVolume = MathMax(minLot, closeVolume);
   
   // Don't close more than available
   closeVolume = MathMin(closeVolume, currentVolume);
   
   Print("Current Volume: ", currentVolume);
   Print("Close Volume: ", closeVolume, " (", percentage, "%)");
   
   MqlTradeRequest request;
   MqlTradeResult result;
   ZeroMemory(request);
   ZeroMemory(result);
   
   request.action = TRADE_ACTION_DEAL;
   request.symbol = symbol;
   request.volume = closeVolume;
   request.type = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? 
                   ORDER_TYPE_SELL : ORDER_TYPE_BUY;
   request.position = ticket;
   request.price = (request.type == ORDER_TYPE_SELL) ? 
                    SymbolInfoDouble(symbol, SYMBOL_BID) : 
                    SymbolInfoDouble(symbol, SYMBOL_ASK);
   request.deviation = 10;
   request.magic = 123456;
   request.comment = "Partial close " + DoubleToString(percentage, 0) + "%";
   
   if(OrderSend(request, result) && result.retcode == TRADE_RETCODE_DONE)
   {
      Print("✅ Partial close executed: ", closeVolume, " lots");
   }
   else
   {
      Print("❌ Partial close failed: ", result.retcode);
   }
}

//+------------------------------------------------------------------+
//| Modify position SL/TP                                            |
//+------------------------------------------------------------------+
void ModifyPosition(int ticket, double newSL, double newTP)
{
   Print("Modifying position...");
   
   if(!PositionSelectByTicket(ticket))
   {
      Print("❌ Cannot select position");
      return;
   }
   
   string symbol = PositionGetString(POSITION_SYMBOL);
   double currentSL = PositionGetDouble(POSITION_SL);
   double currentTP = PositionGetDouble(POSITION_TP);
   
   // Use current values if not updating
   double finalSL = (newSL > 0) ? newSL : currentSL;
   double finalTP = (newTP > 0) ? newTP : currentTP;
   
   Print("Current SL: ", currentSL, " → New SL: ", finalSL);
   Print("Current TP: ", currentTP, " → New TP: ", finalTP);
   
   MqlTradeRequest request;
   MqlTradeResult result;
   ZeroMemory(request);
   ZeroMemory(result);
   
   request.action = TRADE_ACTION_SLTP;
   request.symbol = symbol;
   request.position = ticket;
   request.sl = finalSL;
   request.tp = finalTP;
   
   if(OrderSend(request, result) && result.retcode == TRADE_RETCODE_DONE)
   {
      Print("✅ Position modified successfully");
   }
   else
   {
      Print("❌ Position modification failed: ", result.retcode);
   }
}

//+------------------------------------------------------------------+
//| Close position completely                                        |
//+------------------------------------------------------------------+
bool ClosePosition(int ticket)
{
   Print("Closing position...");
   
   if(!PositionSelectByTicket(ticket))
   {
      Print("❌ Cannot select position");
      return false;
   }
   
   string symbol = PositionGetString(POSITION_SYMBOL);
   double volume = PositionGetDouble(POSITION_VOLUME);
   long posType = PositionGetInteger(POSITION_TYPE);
   
   MqlTradeRequest request;
   MqlTradeResult result;
   ZeroMemory(request);
   ZeroMemory(result);
   
   request.action = TRADE_ACTION_DEAL;
   request.symbol = symbol;
   request.volume = volume;
   request.type = (posType == POSITION_TYPE_BUY) ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
   request.position = ticket;
   request.price = (request.type == ORDER_TYPE_SELL) ? 
                    SymbolInfoDouble(symbol, SYMBOL_BID) : 
                    SymbolInfoDouble(symbol, SYMBOL_ASK);
   request.deviation = 10;
   request.magic = 123456;
   request.comment = "BotCore AI Exit";
   
   if(OrderSend(request, result) && result.retcode == TRADE_RETCODE_DONE)
   {
      Print("✅ Position closed successfully");
      return true;
   }
   else
   {
      Print("❌ Position close failed: ", result.retcode);
      return false;
   }
}

//+------------------------------------------------------------------+
//| Update EA State                                                  |
//+------------------------------------------------------------------+
void UpdateState(EA_STATE newState)
{
   CurrentState = newState;
   Print("State Updated: ", EnumToString(CurrentState));
}

//+------------------------------------------------------------------+
//| Prepare Start of Day OHLC data with positions                    |
//+------------------------------------------------------------------+
string PrepareSODOHLCData()
{
   string json = "{";
   
   // Add symbol
   json += "\"symbol\":\"" + TradingSymbol + "\",";
   
   // Get and add 1h DATA
   json += "\"1h_DATA\":";
   json += GetOHLCArray(TradingSymbol, PERIOD_H1, 200);
   json += ",";
   
   // Get and add 4h DATA
   json += "\"4h_DATA\":";
   json += GetOHLCArray(TradingSymbol, PERIOD_H4, 150);
   json += ",";
   
   // Get and add 1D DATA
   json += "\"1D_DATA\":";
   json += GetOHLCArray(TradingSymbol, PERIOD_D1, 100);
   json += ",";
   
   // Get and add 1W DATA
   json += "\"1W_DATA\":";
   json += GetOHLCArray(TradingSymbol, PERIOD_W1, 52);
   json += ",";
   
   // Phase 6: Add current positions
   json += "\"positions\":";
   json += GetCurrentPositions();
   
   json += "}";
   
   return json;
}

//+------------------------------------------------------------------+
//| Phase 5: Prepare Intraday OHLC data with dynamic timeframes      |
//+------------------------------------------------------------------+
string PrepareIntradayOHLCData()
{
   string json = "{";
   
   // Add symbol
   json += "\"symbol\":\"" + TradingSymbol + "\",";
   
   // Parse monitoring timeframes and add data
   if(StringLen(MonitoringTimeframes) > 0)
   {
      string timeframes[];
      int count = StringSplit(MonitoringTimeframes, ',', timeframes);
      
      for(int i = 0; i < count; i++)
      {
         string tf = timeframes[i];
         StringTrimLeft(tf);
         StringTrimRight(tf);
         
         // Remove quotes if present
         StringReplace(tf, "\"", "");
         
         ENUM_TIMEFRAMES period = StringToTimeframe(tf);
         int candles = GetCandleCount(tf);
         
         if(period != PERIOD_CURRENT)
         {
            json += "\"" + tf + "_DATA\":";
            json += GetOHLCArray(TradingSymbol, period, candles);
            
            if(i < count - 1) json += ",";
         }
      }
      
      json += ",";
   }
   else
   {
      // Default to H1 if no timeframes specified
      json += "\"H1_DATA\":";
      json += GetOHLCArray(TradingSymbol, PERIOD_H1, 100);
      json += ",";
   }
   
   // Phase 6: Add current positions
   json += "\"positions\":";
   json += GetCurrentPositions();
   
   json += "}";
   
   return json;
}

//+------------------------------------------------------------------+
//| Phase 6: Get current positions as JSON                           |
//+------------------------------------------------------------------+
string GetCurrentPositions()
{
   string json = "[";
   
   int total = PositionsTotal();
   int count = 0;
   
   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      
      if(ticket > 0)
      {
         string symbol = PositionGetString(POSITION_SYMBOL);
         
         // Only include positions for our symbol
         if(symbol == TradingSymbol)
         {
            if(count > 0) json += ",";
            
            json += "{";
            json += "\"ticket\":" + IntegerToString(ticket) + ",";
            json += "\"type\":\"" + (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? "BUY" : "SELL") + "\",";
            json += "\"entry_price\":" + DoubleToString(PositionGetDouble(POSITION_PRICE_OPEN), 5) + ",";
            json += "\"current_price\":" + DoubleToString(PositionGetDouble(POSITION_PRICE_CURRENT), 5) + ",";
            json += "\"stop_loss\":" + DoubleToString(PositionGetDouble(POSITION_SL), 5) + ",";
            json += "\"take_profit\":" + DoubleToString(PositionGetDouble(POSITION_TP), 5) + ",";
            json += "\"entry_time\":" + IntegerToString((int)PositionGetInteger(POSITION_TIME)) + ",";
            json += "\"lot_size\":" + DoubleToString(PositionGetDouble(POSITION_VOLUME), 2);
            json += "}";
            
            count++;
            
            // Store ticket for tracking
            CurrentPositionTicket = (int)ticket;
         }
      }
   }
   
   json += "]";
   
   return json;
}

//+------------------------------------------------------------------+
//| Get OHLC array for a specific symbol and timeframe              |
//+------------------------------------------------------------------+
string GetOHLCArray(string symbol, ENUM_TIMEFRAMES timeframe, int count)
{
   string json = "[";
   
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   
   int copied = CopyRates(symbol, timeframe, 0, count, rates);
   
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
   else
   {
      Print("ERROR: Failed to copy rates for ", symbol, " ", EnumToString(timeframe));
   }
   
   json += "]";
   return json;
}

//+------------------------------------------------------------------+
//| Send data to BotCore API                                         |
//+------------------------------------------------------------------+
string SendToAPI(string endpoint, string payload)
{
   Print("========================================");
   Print("Sending to BotCore API");
   Print("Endpoint: ", endpoint);
   Print("========================================");
   
   char post[];
   char result[];
   string headers = "Content-Type: application/json\r\n";
   
   ArrayResize(post, StringToCharArray(payload, post, 0, WHOLE_ARRAY, CP_UTF8) - 1);
   
   string url = ServerURL + endpoint;
   
   int res = WebRequest("POST", url, headers, NULL, 30000, post, 0, result, headers);
   
   if(res == -1)
   {
      int error = GetLastError();
      Print("ERROR: WebRequest failed - Error code: ", error);
      
      if(error == 4060)
      {
         Print("ERROR: URL not allowed. Add '", url, "' to allowed URLs in MT5 Tools->Options->Expert Advisors");
      }
      return "";
   }
   
   string responseStr = CharArrayToString(result);
   Print("API Response received (", StringLen(responseStr), " bytes)");
   
   return responseStr;
}

//+------------------------------------------------------------------+
//| Helper: Map timeframe string to ENUM_TIMEFRAMES                  |
//+------------------------------------------------------------------+
ENUM_TIMEFRAMES StringToTimeframe(string tf)
{
   if(tf == "W1") return PERIOD_W1;
   if(tf == "D1") return PERIOD_D1;
   if(tf == "H4") return PERIOD_H4;
   if(tf == "H1") return PERIOD_H1;
   if(tf == "M15") return PERIOD_M15;
   if(tf == "M5") return PERIOD_M5;
   if(tf == "M1") return PERIOD_M1;
   
   return PERIOD_CURRENT;
}

//+------------------------------------------------------------------+
//| Helper: Get candle count for timeframe                           |
//+------------------------------------------------------------------+
int GetCandleCount(string tf)
{
   if(tf == "W1") return 12;    // 3 months
   if(tf == "D1") return 30;    // 1 month
   if(tf == "H4") return 60;    // 10 days
   if(tf == "H1") return 100;   // 4 days
   if(tf == "M15") return 96;   // 1 day
   if(tf == "M5") return 144;   // 12 hours
   if(tf == "M1") return 180;   // 3 hours
   
   return 100;  // Default
}

//+------------------------------------------------------------------+
//| Helper: Extract string value from JSON                           |
//+------------------------------------------------------------------+
string ExtractJSONString(string json, string key)
{
   string searchKey = "\"" + key + "\":\"";
   int start = StringFind(json, searchKey);
   
   if(start == -1) return "";
   
   start += StringLen(searchKey);
   int end = StringFind(json, "\"", start);
   
   if(end == -1) return "";
   
   return StringSubstr(json, start, end - start);
}

//+------------------------------------------------------------------+
//| Helper: Extract nested string value from JSON                    |
//+------------------------------------------------------------------+
string ExtractNestedJSONString(string json, string parentKey, string childKey)
{
   string searchParent = "\"" + parentKey + "\":{";
   int parentStart = StringFind(json, searchParent);
   
   if(parentStart == -1) return "";
   
   int parentEnd = StringFind(json, "}", parentStart);
   if(parentEnd == -1) return "";
   
   string parentSection = StringSubstr(json, parentStart, parentEnd - parentStart);
   
   return ExtractJSONString(parentSection, childKey);
}

//+------------------------------------------------------------------+
//| Helper: Extract nested double value from JSON                    |
//+------------------------------------------------------------------+
double ExtractNestedJSONDouble(string json, string parentKey, string childKey)
{
   string searchParent = "\"" + parentKey + "\":{";
   int parentStart = StringFind(json, searchParent);
   
   if(parentStart == -1) return 0;
   
   int parentEnd = StringFind(json, "}", parentStart);
   if(parentEnd == -1) return 0;
   
   string parentSection = StringSubstr(json, parentStart, parentEnd - parentStart);
   
   string searchKey = "\"" + childKey + "\":";
   int start = StringFind(parentSection, searchKey);
   
   if(start == -1) return 0;
   
   start += StringLen(searchKey);
   
   // Skip whitespace
   while(StringGetCharacter(parentSection, start) == ' ' || 
         StringGetCharacter(parentSection, start) == '\n' ||
         StringGetCharacter(parentSection, start) == '\r')
   {
      start++;
   }
   
   // Check for null
   if(StringSubstr(parentSection, start, 4) == "null") return 0;
   
   int end = start;
   while(end < StringLen(parentSection))
   {
      ushort ch = StringGetCharacter(parentSection, end);
      if(ch != '0' && ch != '1' && ch != '2' && ch != '3' && ch != '4' && 
         ch != '5' && ch != '6' && ch != '7' && ch != '8' && ch != '9' && 
         ch != '.' && ch != '-')
         break;
      end++;
   }
   
   string value = StringSubstr(parentSection, start, end - start);
   return StringToDouble(value);
}

//+------------------------------------------------------------------+
//| Helper: Extract array from JSON as comma-separated string        |
//+------------------------------------------------------------------+
string ExtractJSONArray(string json, string key)
{
   string searchKey = "\"" + key + "\":[";
   int start = StringFind(json, searchKey);
   
   if(start == -1) return "";
   
   start += StringLen(searchKey);
   int end = StringFind(json, "]", start);
   
   if(end == -1) return "";
   
   string arrayContent = StringSubstr(json, start, end - start);
   
   // Remove extra spaces
   StringTrimLeft(arrayContent);
   StringTrimRight(arrayContent);
   
   return arrayContent;
}

//+------------------------------------------------------------------+
//| Send current positions to database via API                       |
//+------------------------------------------------------------------+
bool StoreCurrentPositions()
{
   Print("========================================");
   Print("STORING POSITIONS TO DATABASE");
   Print("========================================");
   
   // Get current positions
   string positionsJSON = GetCurrentPositions();
   
   // Build payload
   string payload = "{";
   payload += "\"symbol\":\"" + TradingSymbol + "\",";
   payload += "\"positions\":" + positionsJSON;
   payload += "}";
   
   // Send to API
   string url = ServerURL + "/api/trading/store_positions";
   string headers = "Content-Type: application/json\r\n";
   char postData[];
   char result[];
   string resultHeaders;
   
   ArrayResize(postData, StringToCharArray(payload, postData, 0, WHOLE_ARRAY, CP_UTF8) - 1);
   
   Print("📡 Sending positions to: ", url);
   Print("Payload: ", payload);
   
   int httpCode = WebRequest(
      "POST",
      url,
      headers,
      5000,
      postData,
      result,
      resultHeaders
   );
   
   if(httpCode == 200)
   {
      string response = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
      Print("✅ Positions stored successfully");
      Print("Response: ", response);
      return true;
   }
   else
   {
      Print("❌ Failed to store positions - HTTP Code: ", httpCode);
      if(ArraySize(result) > 0)
      {
         string response = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
         Print("Error response: ", response);
      }
      return false;
   }
}

//+------------------------------------------------------------------+
//| Helper: Extract nested-nested string value from JSON             |
//| For: response → manage_order → position → asset                  |
//+------------------------------------------------------------------+
string ExtractNestedNestedJSONString(string json, string parent1Key, string parent2Key, string childKey)
{
   // Find parent1
   string searchParent1 = "\"" + parent1Key + "\":{";
   int parent1Start = StringFind(json, searchParent1);
   if(parent1Start == -1) return "";
   
   int parent1End = FindMatchingBrace(json, parent1Start + StringLen(searchParent1) - 1);
   if(parent1End == -1) return "";
   
   string parent1Section = StringSubstr(json, parent1Start, parent1End - parent1Start + 1);
   
   // Find parent2 within parent1
   string searchParent2 = "\"" + parent2Key + "\":{";
   int parent2Start = StringFind(parent1Section, searchParent2);
   if(parent2Start == -1) return "";
   
   int parent2End = FindMatchingBrace(parent1Section, parent2Start + StringLen(searchParent2) - 1);
   if(parent2End == -1) return "";
   
   string parent2Section = StringSubstr(parent1Section, parent2Start, parent2End - parent2Start + 1);
   
   // Extract child string from parent2
   return ExtractJSONString(parent2Section, childKey);
}

//+------------------------------------------------------------------+
//| Helper: Extract nested-nested double value from JSON             |
//+------------------------------------------------------------------+
double ExtractNestedNestedJSONDouble(string json, string parent1Key, string parent2Key, string childKey)
{
   // Find parent1
   string searchParent1 = "\"" + parent1Key + "\":{";
   int parent1Start = StringFind(json, searchParent1);
   if(parent1Start == -1) return 0;
   
   int parent1End = FindMatchingBrace(json, parent1Start + StringLen(searchParent1) - 1);
   if(parent1End == -1) return 0;
   
   string parent1Section = StringSubstr(json, parent1Start, parent1End - parent1Start + 1);
   
   // Find parent2 within parent1
   string searchParent2 = "\"" + parent2Key + "\":{";
   int parent2Start = StringFind(parent1Section, searchParent2);
   if(parent2Start == -1) return 0;
   
   int parent2End = FindMatchingBrace(parent1Section, parent2Start + StringLen(searchParent2) - 1);
   if(parent2End == -1) return 0;
   
   string parent2Section = StringSubstr(parent1Section, parent2Start, parent2End - parent2Start + 1);
   
   // Extract child value from parent2
   string searchKey = "\"" + childKey + "\":";
   int start = StringFind(parent2Section, searchKey);
   if(start == -1) return 0;
   
   start += StringLen(searchKey);
   
   // Skip whitespace
   while(StringGetCharacter(parent2Section, start) == ' ')
      start++;
   
   // Check for null
   if(StringSubstr(parent2Section, start, 4) == "null") return 0;
   
   int end = start;
   while(end < StringLen(parent2Section))
   {
      ushort ch = StringGetCharacter(parent2Section, end);
      if(ch != '0' && ch != '1' && ch != '2' && ch != '3' && ch != '4' && 
         ch != '5' && ch != '6' && ch != '7' && ch != '8' && ch != '9' && 
         ch != '.' && ch != '-')
         break;
      end++;
   }
   
   string value = StringSubstr(parent2Section, start, end - start);
   return StringToDouble(value);
}

//+------------------------------------------------------------------+
//| Helper: Extract nested boolean value from JSON                   |
//+------------------------------------------------------------------+
bool ExtractNestedJSONBool(string json, string parentKey, string childKey)
{
   string searchParent = "\"" + parentKey + "\":{";
   int parentStart = StringFind(json, searchParent);
   if(parentStart == -1) return false;
   
   int parentEnd = FindMatchingBrace(json, parentStart + StringLen(searchParent) - 1);
   if(parentEnd == -1) return false;
   
   string parentSection = StringSubstr(json, parentStart, parentEnd - parentStart + 1);
   
   string searchKey = "\"" + childKey + "\":";
   int start = StringFind(parentSection, searchKey);
   if(start == -1) return false;
   
   start += StringLen(searchKey);
   
   // Skip whitespace
   while(StringGetCharacter(parentSection, start) == ' ')
      start++;
   
   // Check for true/false
   if(StringSubstr(parentSection, start, 4) == "true") return true;
   if(StringSubstr(parentSection, start, 5) == "false") return false;
   if(StringSubstr(parentSection, start, 4) == "null") return false;
   
   return false;
}

//+------------------------------------------------------------------+
//| Helper: Find matching closing brace                              |
//+------------------------------------------------------------------+
int FindMatchingBrace(string json, int startPos)
{
   int depth = 1;
   int pos = startPos + 1;
   
   while(pos < StringLen(json) && depth > 0)
   {
      ushort ch = StringGetCharacter(json, pos);
      if(ch == '{') depth++;
      if(ch == '}') depth--;
      if(depth == 0) return pos;
      pos++;
   }
   
   return -1;
}

//+------------------------------------------------------------------+