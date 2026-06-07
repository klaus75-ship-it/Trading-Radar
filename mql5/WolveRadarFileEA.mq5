//+------------------------------------------------------------------+
//| WolveRadarFileEA.mq5                                             |
//| Read-only FILE_COMMON bridge for TradingRading.                   |
//| No sockets, no signals, no trading.                               |
//+------------------------------------------------------------------+
#property strict
#property version "0.1"

input string InpSymbols = "XAUUSD,NDX100";
input string InpOutputFile = "wolve_radar_state.json";
input string InpTimeframe = "M15";
input int    InpBars = 200;
input int    InpTimerSeconds = 5;
input bool   InpVerbose = false;

int OnInit()
{
   EventSetTimer(MathMax(1, InpTimerSeconds));
   if(InpVerbose)
      Print("WolveRadarFileEA initialized output=", InpOutputFile, " symbols=", InpSymbols);
   WriteState();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

void OnTimer()
{
   WriteState();
}

void WriteState()
{
   string json = "{";
   json += JsonPair("schema", "wolve.radar.file.v1") + ",";
   json += JsonPair("ts", TimeToString(TimeLocal(), TIME_DATE|TIME_SECONDS)) + ",";
   json += JsonPair("server_time", (long)TimeCurrent()) + ",";
   json += "\"account\":{";
   json += JsonPair("login", (long)AccountInfoInteger(ACCOUNT_LOGIN)) + ",";
   json += JsonPair("trade_mode", (long)AccountInfoInteger(ACCOUNT_TRADE_MODE)) + ",";
   json += JsonPair("balance", AccountInfoDouble(ACCOUNT_BALANCE)) + ",";
   json += JsonPair("equity", AccountInfoDouble(ACCOUNT_EQUITY)) + ",";
   json += JsonPair("margin", AccountInfoDouble(ACCOUNT_MARGIN)) + ",";
   json += JsonPair("margin_free", AccountInfoDouble(ACCOUNT_MARGIN_FREE)) + ",";
   json += JsonPair("margin_level", AccountInfoDouble(ACCOUNT_MARGIN_LEVEL)) + ",";
   json += JsonPair("currency", AccountInfoString(ACCOUNT_CURRENCY));
   json += "},";
   json += "\"symbols\":[";

   string symbols[];
   int total = StringSplit(InpSymbols, ',', symbols);
   bool first = true;
   for(int i = 0; i < total; i++)
   {
      string symbol = symbols[i];
      StringTrimLeft(symbol);
      StringTrimRight(symbol);
      if(symbol == "")
         continue;

      if(!first)
         json += ",";
      json += SymbolState(symbol);
      first = false;
   }
   json += "]}";

   int h = FileOpen(InpOutputFile, FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);
   if(h == INVALID_HANDLE)
   {
      if(InpVerbose)
         Print("WolveRadarFileEA cannot open output file err=", _LastError);
      return;
   }
   FileWriteString(h, json);
   FileClose(h);
}

string SymbolState(const string symbol)
{
   if(!SymbolSelect(symbol, true))
      return "{"
         + JsonPair("symbol", symbol) + ","
         + JsonPair("ok", false) + ","
         + JsonPair("error", "SymbolSelect failed")
         + "}";

   MqlTick tick;
   bool hasTick = SymbolInfoTick(symbol, tick);
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   double spread = hasTick ? (tick.ask - tick.bid) : 0.0;
   double volumeMin = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double marginBuy = 0.0;
   if(hasTick && volumeMin > 0.0)
      OrderCalcMargin(ORDER_TYPE_BUY, symbol, volumeMin, tick.ask, marginBuy);

   string json = "{";
   json += JsonPair("symbol", symbol) + ",";
   json += JsonPair("ok", hasTick) + ",";
   json += JsonPair("bid", hasTick ? tick.bid : 0.0) + ",";
   json += JsonPair("ask", hasTick ? tick.ask : 0.0) + ",";
   json += JsonPair("last", hasTick ? tick.last : 0.0) + ",";
   json += JsonPair("spread", spread) + ",";
   json += JsonPair("tick_time", hasTick ? (long)tick.time : 0) + ",";
   json += JsonPair("point", point) + ",";
   json += JsonPair("digits", (long)SymbolInfoInteger(symbol, SYMBOL_DIGITS)) + ",";
   json += JsonPair("volume_min", volumeMin) + ",";
   json += JsonPair("volume_step", SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP)) + ",";
   json += JsonPair("volume_max", SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX)) + ",";
   json += JsonPair("tick_value", SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE)) + ",";
   json += JsonPair("tick_value_profit", SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE_PROFIT)) + ",";
   json += JsonPair("tick_value_loss", SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE_LOSS)) + ",";
   json += JsonPair("tick_size", SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE)) + ",";
   json += JsonPair("contract_size", SymbolInfoDouble(symbol, SYMBOL_TRADE_CONTRACT_SIZE)) + ",";
   json += JsonPair("trade_stops_level", (long)SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL)) + ",";
   json += JsonPair("trade_freeze_level", (long)SymbolInfoInteger(symbol, SYMBOL_TRADE_FREEZE_LEVEL)) + ",";
   json += JsonPair("swap_long", SymbolInfoDouble(symbol, SYMBOL_SWAP_LONG)) + ",";
   json += JsonPair("swap_short", SymbolInfoDouble(symbol, SYMBOL_SWAP_SHORT)) + ",";
   json += JsonPair("swap_rollover3days", (long)SymbolInfoInteger(symbol, SYMBOL_SWAP_ROLLOVER3DAYS)) + ",";
   json += JsonPair("trade_mode", (long)SymbolInfoInteger(symbol, SYMBOL_TRADE_MODE)) + ",";
   json += JsonPair("order_mode", (long)SymbolInfoInteger(symbol, SYMBOL_ORDER_MODE)) + ",";
   json += JsonPair("filling_mode", (long)SymbolInfoInteger(symbol, SYMBOL_FILLING_MODE)) + ",";
   json += JsonPair("margin_buy_min", marginBuy) + ",";
   json += "\"bars\":" + BarsJson(symbol);
   json += "}";
   return json;
}

string BarsJson(const string symbol)
{
   ENUM_TIMEFRAMES timeframe = ParseTimeframe(InpTimeframe);
   int count = MathMax(2, MathMin(InpBars, 1000));

   MqlRates rates[];
   int copied = CopyRates(symbol, timeframe, 0, count, rates);
   if(copied <= 0)
      return "[]";

   ArraySetAsSeries(rates, false);
   string bars = "[";
   for(int i = 0; i < copied; i++)
   {
      if(i > 0)
         bars += ",";
      bars += "{"
         + JsonPair("time", (long)rates[i].time) + ","
         + JsonPair("open", rates[i].open) + ","
         + JsonPair("high", rates[i].high) + ","
         + JsonPair("low", rates[i].low) + ","
         + JsonPair("close", rates[i].close) + ","
         + JsonPair("tick_volume", (long)rates[i].tick_volume) + ","
         + JsonPair("real_volume", (long)rates[i].real_volume) + ","
         + JsonPair("spread", (long)rates[i].spread)
         + "}";
   }
   bars += "]";
   return bars;
}

ENUM_TIMEFRAMES ParseTimeframe(const string name)
{
   if(name == "M1") return PERIOD_M1;
   if(name == "M5") return PERIOD_M5;
   if(name == "M15") return PERIOD_M15;
   if(name == "M30") return PERIOD_M30;
   if(name == "H1") return PERIOD_H1;
   if(name == "H4") return PERIOD_H4;
   if(name == "D1") return PERIOD_D1;
   return PERIOD_M15;
}

string JsonPair(const string key, const string value)
{
   return "\"" + key + "\":\"" + JsonEscape(value) + "\"";
}

string JsonPair(const string key, const bool value)
{
   return "\"" + key + "\":" + (value ? "true" : "false");
}

string JsonPair(const string key, const double value)
{
   if(!MathIsValidNumber(value))
      return "\"" + key + "\":null";
   return "\"" + key + "\":" + DoubleToString(value, 10);
}

string JsonPair(const string key, const long value)
{
   return "\"" + key + "\":" + IntegerToString(value);
}

string JsonEscape(string value)
{
   StringReplace(value, "\\", "\\\\");
   StringReplace(value, "\"", "\\\"");
   StringReplace(value, "\r", "\\r");
   StringReplace(value, "\n", "\\n");
   return value;
}

