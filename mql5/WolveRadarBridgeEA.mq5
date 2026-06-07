//+------------------------------------------------------------------+
//| WolveRadarBridgeEA.mq5                                           |
//| Read-only localhost JSONL bridge for TradingRading.               |
//|                                                                  |
//| Python runs a TCP server on macOS. This EA connects to it from    |
//| MT5/Wine and answers request/response JSON lines.                 |
//|                                                                  |
//| Supported requests: account, snapshot, bars, order_check, ping.   |
//| This EA never places real orders.                                 |
//+------------------------------------------------------------------+
#property strict
#property version "0.2"

input string InpHost = "127.0.0.1";
input int    InpPort = 9001;
input int    InpTimerSeconds = 1;
input int    InpSocketTimeoutMs = 50;
input int    InpDefaultDeviationPoints = 20;
input string InpStatusFile = "wolve_radar_bridge_status.json";
input bool   InpVerbose = false;

int g_socket = INVALID_HANDLE;
string g_buffer = "";
ulong g_lastConnectAttemptMs = 0;
int g_lastConnectError = 0;
string g_lastStatus = "init";
long g_readableBytes = 0;
long g_lastReadBytes = 0;
long g_lastSendBytes = 0;
string g_lastRequestType = "";

int OnInit()
{
   EventSetTimer(MathMax(1, InpTimerSeconds));
   if(InpVerbose)
      Print("WolveRadarBridgeEA initialized. host=", InpHost, " port=", InpPort);
   ConnectBridge();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   CloseBridge();
}

void OnTimer()
{
   g_lastStatus = "timer";
   if(g_socket == INVALID_HANDLE || !SocketIsConnected(g_socket))
      ConnectBridge();

   if(g_socket != INVALID_HANDLE && SocketIsConnected(g_socket))
      ReadRequests();

   WriteStatus();
}

void CloseBridge()
{
   if(g_socket != INVALID_HANDLE)
   {
      SocketClose(g_socket);
      g_socket = INVALID_HANDLE;
   }
}

void ConnectBridge()
{
   ulong nowMs = GetTickCount64();
   if(g_lastConnectAttemptMs > 0 && nowMs - g_lastConnectAttemptMs < 1000)
      return;
   g_lastConnectAttemptMs = nowMs;

   CloseBridge();
   g_socket = SocketCreate();
   if(g_socket == INVALID_HANDLE)
   {
      g_lastConnectError = _LastError;
      g_lastStatus = "socket_create_failed";
      if(InpVerbose)
         Print("WolveRadarBridgeEA SocketCreate failed err=", g_lastConnectError);
      WriteStatus();
      return;
   }

   if(!SocketConnect(g_socket, InpHost, InpPort, 1000))
   {
      int err = _LastError;
      g_lastStatus = "socket_connect_failed";
      if(err != g_lastConnectError)
      {
         Print("WolveRadarBridgeEA SocketConnect failed host=", InpHost, " port=", InpPort, " err=", err,
               ". Add the address to Tools > Options > Expert Advisors > Allow WebRequest for listed URL.");
         g_lastConnectError = err;
      }
      WriteStatus();
      SocketClose(g_socket);
      g_socket = INVALID_HANDLE;
      return;
   }

   g_buffer = "";
   g_lastConnectError = 0;
   g_lastStatus = "connected";
   if(InpVerbose)
      Print("WolveRadarBridgeEA connected to ", InpHost, ":", InpPort);
   WriteStatus();
}

void ReadRequests()
{
   uint readable = SocketIsReadable(g_socket);
   g_readableBytes = (long)readable;
   if(readable <= 0)
      return;

   uchar data[];
   ArrayResize(data, readable);

   int bytes = SocketRead(g_socket, data, readable, InpSocketTimeoutMs);
   g_lastReadBytes = (long)bytes;
   if(bytes <= 0)
   {
      g_lastStatus = "socket_read_failed";
      g_lastConnectError = _LastError;
      WriteStatus();
      return;
   }

   g_buffer += CharArrayToString(data, 0, (int)bytes, CP_UTF8);
   g_lastStatus = "request_received";

   while(true)
   {
      int pos = StringFind(g_buffer, "\n");
      if(pos < 0)
         break;

      string line = StringSubstr(g_buffer, 0, pos);
      g_buffer = StringSubstr(g_buffer, pos + 1);
      StringTrimLeft(line);
      StringTrimRight(line);

      if(StringLen(line) > 0)
         ProcessRequest(line);
   }

   WriteStatus();
}

void ProcessRequest(const string line)
{
   string id = JsonString(line, "id");
   string type = JsonString(line, "type");
   g_lastRequestType = type;
   g_lastStatus = "processing_" + type;
   WriteStatus();

   if(type == "ping")
   {
      SendLine("{"
               + JsonPair("id", id) + ","
               + JsonPair("ok", true) + ","
               + JsonPair("type", "pong") + ","
               + JsonPair("server_time", (long)TimeCurrent())
               + "}");
      return;
   }

   if(type == "account")
   {
      SendLine(AccountResponse(id));
      return;
   }

   string symbol = JsonString(line, "symbol");
   if(symbol == "")
      symbol = _Symbol;

   if(type == "snapshot")
      SendLine(SnapshotResponse(id, symbol));
   else if(type == "bars")
      SendLine(BarsResponse(id, symbol, JsonString(line, "timeframe"), JsonInt(line, "count", 200)));
   else if(type == "order_check")
      SendLine(OrderCheckResponse(id, symbol, JsonString(line, "side"), JsonDouble(line, "volume", 0.0), JsonDouble(line, "price", 0.0), JsonDouble(line, "sl", 0.0), JsonDouble(line, "tp", 0.0)));
   else
      SendLine(ErrorResponse(id, "unknown request type: " + type));
}

string AccountResponse(const string id)
{
   return "{"
      + JsonPair("id", id) + ","
      + JsonPair("ok", true) + ","
      + JsonPair("type", "account") + ","
      + JsonPair("login", (long)AccountInfoInteger(ACCOUNT_LOGIN)) + ","
      + JsonPair("trade_mode", (long)AccountInfoInteger(ACCOUNT_TRADE_MODE)) + ","
      + JsonPair("balance", AccountInfoDouble(ACCOUNT_BALANCE)) + ","
      + JsonPair("equity", AccountInfoDouble(ACCOUNT_EQUITY)) + ","
      + JsonPair("margin", AccountInfoDouble(ACCOUNT_MARGIN)) + ","
      + JsonPair("margin_free", AccountInfoDouble(ACCOUNT_MARGIN_FREE)) + ","
      + JsonPair("margin_level", AccountInfoDouble(ACCOUNT_MARGIN_LEVEL)) + ","
      + JsonPair("currency", AccountInfoString(ACCOUNT_CURRENCY)) + ","
      + JsonPair("server_time", (long)TimeCurrent())
      + "}";
}

string SnapshotResponse(const string id, const string symbol)
{
   if(!SymbolSelect(symbol, true))
      return ErrorResponse(id, "SymbolSelect failed: " + symbol);

   MqlTick tick;
   if(!SymbolInfoTick(symbol, tick))
      return ErrorResponse(id, "SymbolInfoTick failed: " + symbol);

   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   double spread = tick.ask - tick.bid;

   return "{"
      + JsonPair("id", id) + ","
      + JsonPair("ok", true) + ","
      + JsonPair("type", "snapshot") + ","
      + JsonPair("symbol", symbol) + ","
      + JsonPair("bid", tick.bid) + ","
      + JsonPair("ask", tick.ask) + ","
      + JsonPair("last", tick.last) + ","
      + JsonPair("spread", spread) + ","
      + JsonPair("tick_time", (long)tick.time) + ","
      + JsonPair("server_time", (long)TimeCurrent()) + ","
      + JsonPair("point", point) + ","
      + JsonPair("digits", (long)SymbolInfoInteger(symbol, SYMBOL_DIGITS)) + ","
      + JsonPair("volume_min", SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN)) + ","
      + JsonPair("volume_step", SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP)) + ","
      + JsonPair("volume_max", SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX)) + ","
      + JsonPair("tick_value", SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE)) + ","
      + JsonPair("tick_value_profit", SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE_PROFIT)) + ","
      + JsonPair("tick_value_loss", SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE_LOSS)) + ","
      + JsonPair("tick_size", SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE)) + ","
      + JsonPair("contract_size", SymbolInfoDouble(symbol, SYMBOL_TRADE_CONTRACT_SIZE)) + ","
      + JsonPair("trade_stops_level", (long)SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL)) + ","
      + JsonPair("trade_freeze_level", (long)SymbolInfoInteger(symbol, SYMBOL_TRADE_FREEZE_LEVEL)) + ","
      + JsonPair("swap_long", SymbolInfoDouble(symbol, SYMBOL_SWAP_LONG)) + ","
      + JsonPair("swap_short", SymbolInfoDouble(symbol, SYMBOL_SWAP_SHORT)) + ","
      + JsonPair("swap_rollover3days", (long)SymbolInfoInteger(symbol, SYMBOL_SWAP_ROLLOVER3DAYS)) + ","
      + JsonPair("trade_mode", (long)SymbolInfoInteger(symbol, SYMBOL_TRADE_MODE)) + ","
      + JsonPair("order_mode", (long)SymbolInfoInteger(symbol, SYMBOL_ORDER_MODE)) + ","
      + JsonPair("filling_mode", (long)SymbolInfoInteger(symbol, SYMBOL_FILLING_MODE))
      + "}";
}

string BarsResponse(const string id, const string symbol, const string timeframeName, const int requestedCount)
{
   if(!SymbolSelect(symbol, true))
      return ErrorResponse(id, "SymbolSelect failed: " + symbol);

   ENUM_TIMEFRAMES timeframe = ParseTimeframe(timeframeName);
   int count = MathMax(2, MathMin(requestedCount, 2000));

   MqlRates rates[];
   int copied = CopyRates(symbol, timeframe, 0, count, rates);
   if(copied <= 0)
      return ErrorResponse(id, "CopyRates failed: " + symbol);

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

   return "{"
      + JsonPair("id", id) + ","
      + JsonPair("ok", true) + ","
      + JsonPair("type", "bars") + ","
      + JsonPair("symbol", symbol) + ","
      + JsonPair("timeframe", timeframeName) + ","
      + "\"bars\":" + bars
      + "}";
}

string OrderCheckResponse(const string id, const string symbol, const string side, const double volume, const double price, const double sl, const double tp)
{
   if(!SymbolSelect(symbol, true))
      return ErrorResponse(id, "SymbolSelect failed: " + symbol);

   if(volume <= 0.0)
      return ErrorResponse(id, "volume must be positive");

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
   request.tp = tp;
   request.deviation = InpDefaultDeviationPoints;
   request.type_filling = BestFillingMode(symbol);
   request.type_time = ORDER_TIME_GTC;

   bool checkOk = OrderCheck(request, result);
   return "{"
      + JsonPair("id", id) + ","
      + JsonPair("ok", true) + ","
      + JsonPair("type", "order_check") + ","
      + JsonPair("check_ok", checkOk) + ","
      + JsonPair("retcode", (long)result.retcode) + ","
      + JsonPair("balance", result.balance) + ","
      + JsonPair("equity", result.equity) + ","
      + JsonPair("profit", result.profit) + ","
      + JsonPair("margin", result.margin) + ","
      + JsonPair("margin_free", result.margin_free) + ","
      + JsonPair("margin_level", result.margin_level) + ","
      + JsonPair("comment", result.comment)
      + "}";
}

ENUM_ORDER_TYPE_FILLING BestFillingMode(const string symbol)
{
   long mode = SymbolInfoInteger(symbol, SYMBOL_FILLING_MODE);
   if((mode & SYMBOL_FILLING_FOK) == SYMBOL_FILLING_FOK)
      return ORDER_FILLING_FOK;
   if((mode & SYMBOL_FILLING_IOC) == SYMBOL_FILLING_IOC)
      return ORDER_FILLING_IOC;
   return ORDER_FILLING_RETURN;
}

ENUM_TIMEFRAMES ParseTimeframe(const string name)
{
   if(name == "M1") return PERIOD_M1;
   if(name == "M2") return PERIOD_M2;
   if(name == "M3") return PERIOD_M3;
   if(name == "M4") return PERIOD_M4;
   if(name == "M5") return PERIOD_M5;
   if(name == "M10") return PERIOD_M10;
   if(name == "M15") return PERIOD_M15;
   if(name == "M30") return PERIOD_M30;
   if(name == "H1") return PERIOD_H1;
   if(name == "H4") return PERIOD_H4;
   if(name == "D1") return PERIOD_D1;
   return PERIOD_M15;
}

void SendLine(const string line)
{
   if(g_socket == INVALID_HANDLE || !SocketIsConnected(g_socket))
      return;

   if(!SocketIsWritable(g_socket))
   {
      g_lastStatus = "socket_not_writable";
      g_lastConnectError = _LastError;
      WriteStatus();
      return;
   }

   string payload = line + "\n";
   uchar data[];
   StringToCharArray(payload, data, 0, WHOLE_ARRAY, CP_UTF8);
   int sent = SocketSend(g_socket, data, ArraySize(data) - 1);
   g_lastSendBytes = (long)sent;
   g_lastStatus = sent > 0 ? "response_sent" : "socket_send_failed";
   if(sent <= 0)
      g_lastConnectError = _LastError;
   WriteStatus();
}

void WriteStatus()
{
   int h = FileOpen(InpStatusFile, FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);
   if(h == INVALID_HANDLE)
      return;

   string j = "{";
   j += JsonPair("schema", "wolve.radar.bridge.status.v1") + ",";
   j += JsonPair("ts", TimeToString(TimeLocal(), TIME_DATE|TIME_SECONDS)) + ",";
   j += JsonPair("symbol", _Symbol) + ",";
   j += JsonPair("host", InpHost) + ",";
   j += JsonPair("port", (long)InpPort) + ",";
   j += JsonPair("status", g_lastStatus) + ",";
   j += JsonPair("socket_handle", (long)g_socket) + ",";
   j += JsonPair("socket_connected", (g_socket != INVALID_HANDLE && SocketIsConnected(g_socket))) + ",";
   j += JsonPair("readable_bytes", g_readableBytes) + ",";
   j += JsonPair("last_read_bytes", g_lastReadBytes) + ",";
   j += JsonPair("last_send_bytes", g_lastSendBytes) + ",";
   j += JsonPair("last_request_type", g_lastRequestType) + ",";
   j += JsonPair("last_error", (long)g_lastConnectError);
   j += "}";

   FileWriteString(h, j);
   FileClose(h);
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
   int end = start;
   bool escaped = false;
   while(end < (int)StringLen(json))
   {
      ushort ch = StringGetCharacter(json, end);
      if(ch == '\\' && !escaped)
      {
         escaped = true;
         end++;
         continue;
      }
      if(ch == '"' && !escaped)
         break;
      escaped = false;
      end++;
   }

   if(end >= (int)StringLen(json))
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
   while(start < (int)StringLen(json))
   {
      ushort ch = StringGetCharacter(json, start);
      if(ch != ' ' && ch != '\t')
         break;
      start++;
   }

   int end = start;
   while(end < (int)StringLen(json))
   {
      ushort ch = StringGetCharacter(json, end);
      if(ch == ',' || ch == '}')
         break;
      end++;
   }

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
