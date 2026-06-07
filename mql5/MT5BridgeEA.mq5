//+------------------------------------------------------------------+
//| MT5BridgeEA.mq5                                                  |
//| Read-only JSONL bridge for TradingRading.                         |
//+------------------------------------------------------------------+
#property strict
#property version "0.1"

input string Host = "127.0.0.1";
input int Port = 9001;
input int TimerSeconds = 1;

int SocketHandle = INVALID_HANDLE;
string Buffer = "";

int OnInit()
{
   EventSetTimer(TimerSeconds);
   Connect();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   if(SocketHandle != INVALID_HANDLE)
   {
      SocketClose(SocketHandle);
      SocketHandle = INVALID_HANDLE;
   }
}

void OnTimer()
{
   if(SocketHandle == INVALID_HANDLE || !SocketIsConnected(SocketHandle))
      Connect();

   if(SocketHandle != INVALID_HANDLE && SocketIsConnected(SocketHandle))
      ReadRequests();
}

void Connect()
{
   if(SocketHandle != INVALID_HANDLE)
      SocketClose(SocketHandle);

   SocketHandle = SocketCreate();
   if(SocketHandle == INVALID_HANDLE)
      return;

   if(!SocketConnect(SocketHandle, Host, Port, 1000))
   {
      SocketClose(SocketHandle);
      SocketHandle = INVALID_HANDLE;
   }
}

void ReadRequests()
{
   uchar data[];
   ArrayResize(data, 8192);

   uint bytes = SocketRead(SocketHandle, data, 8192, 10);
   if(bytes <= 0)
      return;

   Buffer += CharArrayToString(data, 0, (int)bytes, CP_UTF8);

   while(true)
   {
      int pos = StringFind(Buffer, "\n");
      if(pos < 0)
         break;

      string line = StringSubstr(Buffer, 0, pos);
      Buffer = StringSubstr(Buffer, pos + 1);
      StringTrimLeft(line);
      StringTrimRight(line);
      if(StringLen(line) > 0)
         ProcessRequest(line);
   }
}

void ProcessRequest(const string line)
{
   string id = JsonString(line, "id");
   string type = JsonString(line, "type");
   string symbol = JsonString(line, "symbol");

   if(type == "account")
      SendLine(AccountResponse(id));
   else if(type == "snapshot")
      SendLine(SnapshotResponse(id, symbol));
   else if(type == "bars")
      SendLine(BarsResponse(id, symbol, JsonString(line, "timeframe"), JsonInt(line, "count", 200)));
   else if(type == "order_check")
      SendLine(OrderCheckResponse(id, symbol, JsonString(line, "side"), JsonDouble(line, "volume", 0.0), JsonDouble(line, "price", 0.0), JsonDouble(line, "sl", 0.0)));
   else
      SendLine(ErrorResponse(id, "unknown request type"));
}

string AccountResponse(const string id)
{
   return "{"
      + JsonPair("id", id) + ","
      + JsonPair("ok", true) + ","
      + JsonPair("type", "account") + ","
      + JsonPair("balance", AccountInfoDouble(ACCOUNT_BALANCE)) + ","
      + JsonPair("equity", AccountInfoDouble(ACCOUNT_EQUITY)) + ","
      + JsonPair("margin_free", AccountInfoDouble(ACCOUNT_MARGIN_FREE)) + ","
      + JsonPair("margin_level", AccountInfoDouble(ACCOUNT_MARGIN_LEVEL)) + ","
      + JsonPair("currency", AccountInfoString(ACCOUNT_CURRENCY))
      + "}";
}

string SnapshotResponse(const string id, const string symbol)
{
   if(!SymbolSelect(symbol, true))
      return ErrorResponse(id, "symbol_select failed");

   MqlTick tick;
   if(!SymbolInfoTick(symbol, tick))
      return ErrorResponse(id, "symbol_info_tick failed");

   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   double spread = tick.ask - tick.bid;

   return "{"
      + JsonPair("id", id) + ","
      + JsonPair("ok", true) + ","
      + JsonPair("type", "snapshot") + ","
      + JsonPair("symbol", symbol) + ","
      + JsonPair("bid", tick.bid) + ","
      + JsonPair("ask", tick.ask) + ","
      + JsonPair("spread", spread) + ","
      + JsonPair("tick_time", (long)tick.time) + ","
      + JsonPair("point", point) + ","
      + JsonPair("digits", (long)SymbolInfoInteger(symbol, SYMBOL_DIGITS)) + ","
      + JsonPair("volume_min", SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN)) + ","
      + JsonPair("volume_step", SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP)) + ","
      + JsonPair("volume_max", SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX)) + ","
      + JsonPair("tick_value", SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE)) + ","
      + JsonPair("tick_size", SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE)) + ","
      + JsonPair("contract_size", SymbolInfoDouble(symbol, SYMBOL_TRADE_CONTRACT_SIZE)) + ","
      + JsonPair("trade_stops_level", (long)SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL)) + ","
      + JsonPair("trade_freeze_level", (long)SymbolInfoInteger(symbol, SYMBOL_TRADE_FREEZE_LEVEL)) + ","
      + JsonPair("swap_long", SymbolInfoDouble(symbol, SYMBOL_SWAP_LONG)) + ","
      + JsonPair("swap_short", SymbolInfoDouble(symbol, SYMBOL_SWAP_SHORT)) + ","
      + JsonPair("trade_mode", (long)SymbolInfoInteger(symbol, SYMBOL_TRADE_MODE))
      + "}";
}

string BarsResponse(const string id, const string symbol, const string timeframeName, const int count)
{
   ENUM_TIMEFRAMES timeframe = ParseTimeframe(timeframeName);
   MqlRates rates[];
   int copied = CopyRates(symbol, timeframe, 0, count, rates);
   if(copied <= 0)
      return ErrorResponse(id, "copy_rates failed");

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
         + JsonPair("spread", (long)rates[i].spread)
         + "}";
   }
   bars += "]";

   return "{"
      + JsonPair("id", id) + ","
      + JsonPair("ok", true) + ","
      + JsonPair("type", "bars") + ","
      + JsonPair("symbol", symbol) + ","
      + JsonPair("timeframe", timeframeName) + ","
      + "\"bars\":" + bars
      + "}";
}

string OrderCheckResponse(const string id, const string symbol, const string side, const double volume, const double price, const double sl)
{
   MqlTradeRequest request;
   MqlTradeCheckResult result;
   ZeroMemory(request);
   ZeroMemory(result);

   request.action = TRADE_ACTION_DEAL;
   request.symbol = symbol;
   request.volume = volume;
   request.type = (side == "sell") ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
   request.price = price;
   request.sl = sl;
   request.deviation = 20;
   request.type_filling = ORDER_FILLING_FOK;

   bool checkOk = OrderCheck(request, result);
   return "{"
      + JsonPair("id", id) + ","
      + JsonPair("ok", true) + ","
      + JsonPair("type", "order_check") + ","
      + JsonPair("check_ok", checkOk) + ","
      + JsonPair("retcode", (long)result.retcode) + ","
      + JsonPair("margin", result.margin) + ","
      + JsonPair("comment", result.comment)
      + "}";
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

void SendLine(const string line)
{
   if(SocketHandle == INVALID_HANDLE || !SocketIsConnected(SocketHandle))
      return;

   string payload = line + "\n";
   uchar data[];
   StringToCharArray(payload, data, 0, WHOLE_ARRAY, CP_UTF8);
   SocketSend(SocketHandle, data, ArraySize(data) - 1);
}

string ErrorResponse(const string id, const string message)
{
   return "{"
      + JsonPair("id", id) + ","
      + JsonPair("ok", false) + ","
      + JsonPair("error", message)
      + "}";
}

string JsonString(const string json, const string key)
{
   string pattern = "\"" + key + "\":\"";
   int start = StringFind(json, pattern);
   if(start < 0)
      return "";
   start += StringLen(pattern);
   int end = StringFind(json, "\"", start);
   if(end < 0)
      return "";
   return StringSubstr(json, start, end - start);
}

double JsonDouble(const string json, const string key, const double fallback)
{
   string raw = JsonRawValue(json, key);
   if(raw == "")
      return fallback;
   return StringToDouble(raw);
}

int JsonInt(const string json, const string key, const int fallback)
{
   string raw = JsonRawValue(json, key);
   if(raw == "")
      return fallback;
   return (int)StringToInteger(raw);
}

string JsonRawValue(const string json, const string key)
{
   string pattern = "\"" + key + "\":";
   int start = StringFind(json, pattern);
   if(start < 0)
      return "";
   start += StringLen(pattern);
   int endComma = StringFind(json, ",", start);
   int endBrace = StringFind(json, "}", start);
   int end = endComma;
   if(end < 0 || (endBrace >= 0 && endBrace < end))
      end = endBrace;
   if(end < 0)
      return "";
   string value = StringSubstr(json, start, end - start);
   StringTrimLeft(value);
   StringTrimRight(value);
   return value;
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
   return value;
}

