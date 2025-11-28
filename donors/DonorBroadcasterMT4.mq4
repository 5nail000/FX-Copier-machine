//+------------------------------------------------------------------+
//|                                          DonorBroadcasterMT4.mq4 |
//|                        Трансляция данных о позициях и ордерах    |
//|                                        для копировщика сделок    |
//+------------------------------------------------------------------+
#property copyright "FX Copier"
#property version   "1.00"
#property strict

#include "WinSock2.mqh"

// Параметры
input int SocketPort = 8888;  // Порт для сокета
input int UpdateInterval = 100;  // Интервал обновления (мс) - не используется, оставлен для совместимости
input int TimerInterval = 500;  // Интервал проверки состояния через таймер (мс)

// Глобальные переменные
int serverSocket = INVALID_SOCKET;
int clientSocket = INVALID_SOCKET;
bool clientConnected = false;
datetime lastUpdateTime = 0;

// Переменные для отслеживания изменений состояния
string lastStateHash = "";  // Хеш предыдущего состояния
int lastAccountNumber = 0;
double lastBalance = 0;
double lastEquity = 0;
int lastPositionsCount = 0;
int lastOrdersCount = 0;

//+------------------------------------------------------------------+
//| Expert initialization function                                     |
//+------------------------------------------------------------------+
int OnInit()
{
    // Инициализация WinSock
    WSADATA wsaData;
    if(WSAStartup(0x0202, wsaData) != 0)
    {
        Print("Ошибка инициализации WinSock");
        return(INIT_FAILED);
    }
    
    // Создание сокета
    serverSocket = socket(AF_INET, SOCK_STREAM, 0);
    if(serverSocket == INVALID_SOCKET)
    {
        Print("Ошибка создания сокета: ", WSAGetLastError());
        WSACleanup();
        return(INIT_FAILED);
    }
    
    // Настройка адреса
    sockaddr_in serverAddr;
    SetSockAddr(serverAddr, INADDR_ANY, SocketPort);
    
    // Привязка сокета
    if(bind(serverSocket, serverAddr, SOCKADDR_IN_SIZE) == SOCKET_ERROR)
    {
        Print("Ошибка привязки сокета: ", WSAGetLastError());
        closesocket(serverSocket);
        WSACleanup();
        return(INIT_FAILED);
    }
    
    // Прослушивание подключений
    if(listen(serverSocket, 1) == SOCKET_ERROR)
    {
        Print("Ошибка прослушивания: ", WSAGetLastError());
        closesocket(serverSocket);
        WSACleanup();
        return(INIT_FAILED);
    }
    
    Print("Сервер запущен на порту ", SocketPort);
    Print("Ожидание подключения клиента...");
    
    // Устанавливаем таймер для периодической проверки состояния
    // Это гарантирует отправку данных даже при отсутствии тиков
    EventSetMillisecondTimer(TimerInterval);
    Print("Таймер установлен на ", TimerInterval, " мс");
    
    return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                   |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
    // Останавливаем таймер
    EventKillTimer();
    
    if(clientSocket != INVALID_SOCKET)
    {
        closesocket(clientSocket);
        clientSocket = INVALID_SOCKET;
    }
    
    if(serverSocket != INVALID_SOCKET)
    {
        closesocket(serverSocket);
        serverSocket = INVALID_SOCKET;
    }
    
    WSACleanup();
    Print("Сервер остановлен");
}

//+------------------------------------------------------------------+
//| Expert tick function                                               |
//+------------------------------------------------------------------+
void OnTick()
{
    // Принимаем подключение, если клиент не подключен
    if(!clientConnected)
    {
        sockaddr_in clientAddr;
        int addrLen = SOCKADDR_IN_SIZE;
        clientSocket = accept(serverSocket, clientAddr, addrLen);
        
        if(clientSocket != INVALID_SOCKET)
        {
            clientConnected = true;
            Print("Клиент подключен!");
            // При первом подключении сбрасываем хеш для отправки полного состояния
            lastStateHash = "";
        }
    }
    
    // Проверяем изменения и отправляем данные, если клиент подключен
    if(clientConnected)
    {
        if(HasStateChanged())
        {
            string jsonData = BuildPositionsJSON();
            SendData(jsonData);
            UpdateLastState();
        }
    }
}

//+------------------------------------------------------------------+
//| Timer function - периодическая проверка состояния                 |
//+------------------------------------------------------------------+
void OnTimer()
{
    // Периодически проверяем изменения состояния независимо от тиков
    // Это гарантирует отправку данных о закрытии позиций даже при отсутствии движения цены
    if(clientConnected)
    {
        if(HasStateChanged())
        {
            string jsonData = BuildPositionsJSON();
            SendData(jsonData);
            UpdateLastState();
        }
    }
    else
    {
        // Пытаемся принять подключение, если клиент не подключен
        sockaddr_in clientAddr;
        int addrLen = SOCKADDR_IN_SIZE;
        clientSocket = accept(serverSocket, clientAddr, addrLen);
        
        if(clientSocket != INVALID_SOCKET)
        {
            clientConnected = true;
            Print("Клиент подключен через таймер!");
            // При первом подключении сбрасываем хеш для отправки полного состояния
            lastStateHash = "";
        }
    }
}

//+------------------------------------------------------------------+
//| Построение JSON с данными о позициях                              |
//+------------------------------------------------------------------+
string BuildPositionsJSON()
{
    string json = "{";
    json += "\"type\":\"positions\",";
    json += "\"timestamp\":" + IntegerToString(TimeCurrent()) + ",";
    json += "\"account_info\":{";
    json += "\"login\":" + IntegerToString(AccountNumber()) + ",";
    json += "\"balance\":" + DoubleToString(AccountBalance(), 2) + ",";
    json += "\"equity\":" + DoubleToString(AccountEquity(), 2) + ",";
    json += "\"server\":\"" + AccountServer() + "\"";
    json += "},";
    json += "\"positions\":[";
    
    bool first = true;
    
    // В MT4 позиции - это ордера типа OP_BUY/OP_SELL
    for(int i = 0; i < OrdersTotal(); i++)
    {
        if(OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
        {
            if(OrderType() == OP_BUY || OrderType() == OP_SELL)
            {
                if(!first) json += ",";
                first = false;
                
                string symbol = OrderSymbol();
                double currentPrice = (OrderType() == OP_BUY) ? MarketInfo(symbol, MODE_BID) : MarketInfo(symbol, MODE_ASK);
                
                json += "{";
                json += "\"ticket\":" + IntegerToString(OrderTicket()) + ",";
                json += "\"symbol\":\"" + symbol + "\",";
                json += "\"type\":" + IntegerToString(OrderType()) + ",";
                json += "\"volume\":" + DoubleToString(OrderLots(), 2) + ",";
                json += "\"price_open\":" + DoubleToString(OrderOpenPrice(), 5) + ",";
                json += "\"price_current\":" + DoubleToString(currentPrice, 5) + ",";
                json += "\"sl\":" + DoubleToString(OrderStopLoss(), 5) + ",";
                json += "\"tp\":" + DoubleToString(OrderTakeProfit(), 5) + ",";
                json += "\"profit\":" + DoubleToString(OrderProfit() + OrderSwap(), 2) + ",";
                json += "\"time\":" + IntegerToString(OrderOpenTime()) + ",";
                json += "\"magic\":" + IntegerToString(OrderMagicNumber()) + ",";
                json += "\"comment\":\"" + OrderComment() + "\"";
                json += "}";
            }
        }
    }
    
    json += "],";
    json += "\"orders\":[";
    
    // Добавить данные об ордерах
    int totalOrders = OrdersTotal();
    first = true;
    
    for(int i = 0; i < totalOrders; i++)
    {
        if(OrderSelect(i, SELECT_BY_POS))
        {
            if(!first) json += ",";
            first = false;
            
            json += "{";
            json += "\"ticket\":" + IntegerToString(OrderTicket()) + ",";
            json += "\"symbol\":\"" + OrderSymbol() + "\",";
            json += "\"type\":" + IntegerToString(OrderType()) + ",";
            json += "\"volume\":" + DoubleToString(OrderLots(), 2) + ",";
            json += "\"price_open\":" + DoubleToString(OrderOpenPrice(), 5) + ",";
            json += "\"sl\":" + DoubleToString(OrderStopLoss(), 5) + ",";
            json += "\"tp\":" + DoubleToString(OrderTakeProfit(), 5) + ",";
            json += "\"time_setup\":" + IntegerToString(OrderOpenTime());
            json += "}";
        }
    }
    
    json += "]";
    json += "}";
    
    return json;
}

//+------------------------------------------------------------------+
//| Проверка изменений состояния                                      |
//+------------------------------------------------------------------+
bool HasStateChanged()
{
    // Проверка смены аккаунта
    int currentAccount = AccountNumber();
    if(currentAccount != lastAccountNumber)
        return true;
    
    // Проверка изменения баланса (с небольшой погрешностью для плавающих значений)
    // Эквити не проверяем, т.к. оно меняется на каждом тике при наличии позиций
    double currentBalance = AccountBalance();
    if(MathAbs(currentBalance - lastBalance) > 0.01)
        return true;
    
    // Проверка изменения количества позиций и ордеров
    // В MT4 позиции - это ордера типа OP_BUY/OP_SELL
    int currentPositions = 0;
    for(int i = 0; i < OrdersTotal(); i++)
    {
        if(OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
        {
            if(OrderType() == OP_BUY || OrderType() == OP_SELL)
                currentPositions++;
        }
    }
    int currentOrders = OrdersTotal();
    if(currentPositions != lastPositionsCount || currentOrders != lastOrdersCount)
        return true;
    
    // Генерируем хеш текущего состояния для точного сравнения
    string currentHash = GenerateStateHash();
    if(currentHash != lastStateHash)
        return true;
    
    return false;
}

//+------------------------------------------------------------------+
//| Генерация хеша состояния для сравнения                           |
//+------------------------------------------------------------------+
string GenerateStateHash()
{
    string state = "";
    state += IntegerToString(AccountNumber()) + "|";
    state += DoubleToString(AccountBalance(), 2) + "|";
    // Эквити не включаем в хеш, т.к. оно меняется на каждом тике при наличии позиций
    
    // Подсчитываем количество позиций (ордера типа OP_BUY/OP_SELL)
    int positionsCount = 0;
    for(int i = 0; i < OrdersTotal(); i++)
    {
        if(OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
        {
            if(OrderType() == OP_BUY || OrderType() == OP_SELL)
                positionsCount++;
        }
    }
    state += IntegerToString(positionsCount) + "|";
    state += IntegerToString(OrdersTotal()) + "|";
    
    // Добавляем информацию о позициях (тикет, объем, цены, SL/TP)
    // Профит не включаем в хеш, т.к. он меняется на каждом тике
    for(int i = 0; i < OrdersTotal(); i++)
    {
        if(OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
        {
            if(OrderType() == OP_BUY || OrderType() == OP_SELL)
            {
                state += IntegerToString(OrderTicket()) + ":";
                state += OrderSymbol() + ":";
                state += IntegerToString(OrderType()) + ":";
                state += DoubleToString(OrderLots(), 2) + ":";
                state += DoubleToString(OrderOpenPrice(), 5) + ":";
                state += DoubleToString(OrderStopLoss(), 5) + ":";
                state += DoubleToString(OrderTakeProfit(), 5) + "|";
            }
        }
    }
    
    // Добавляем информацию об ордерах (тикет, объем, цена, SL/TP)
    for(int i = 0; i < OrdersTotal(); i++)
    {
        if(OrderSelect(i, SELECT_BY_POS))
        {
            state += IntegerToString(OrderTicket()) + ":";
            state += OrderSymbol() + ":";
            state += IntegerToString(OrderType()) + ":";
            state += DoubleToString(OrderLots(), 2) + ":";
            state += DoubleToString(OrderOpenPrice(), 5) + ":";
            state += DoubleToString(OrderStopLoss(), 5) + ":";
            state += DoubleToString(OrderTakeProfit(), 5) + "|";
        }
    }
    
    return state;
}

//+------------------------------------------------------------------+
//| Обновление последнего известного состояния                       |
//+------------------------------------------------------------------+
void UpdateLastState()
{
    lastAccountNumber = AccountNumber();
    lastBalance = AccountBalance();
    // lastEquity больше не обновляем, т.к. не отслеживаем изменения эквити
    
    // Подсчитываем количество позиций (ордера типа OP_BUY/OP_SELL)
    lastPositionsCount = 0;
    for(int i = 0; i < OrdersTotal(); i++)
    {
        if(OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
        {
            if(OrderType() == OP_BUY || OrderType() == OP_SELL)
                lastPositionsCount++;
        }
    }
    lastOrdersCount = OrdersTotal();
    lastStateHash = GenerateStateHash();
}

//+------------------------------------------------------------------+
//| Отправка данных через сокет                                       |
//+------------------------------------------------------------------+
void SendData(string data)
{
    if(clientSocket == INVALID_SOCKET || !clientConnected)
        return;
    
    // Добавляем длину сообщения в начало (4 байта, big-endian)
    int dataLen = StringLen(data);
    uchar lengthBytes[4];
    lengthBytes[0] = (uchar)((dataLen >> 24) & 0xFF);
    lengthBytes[1] = (uchar)((dataLen >> 16) & 0xFF);
    lengthBytes[2] = (uchar)((dataLen >> 8) & 0xFF);
    lengthBytes[3] = (uchar)(dataLen & 0xFF);
    
    // Отправляем длину
    int sent = send(clientSocket, lengthBytes, 4, 0);
    if(sent == SOCKET_ERROR)
    {
        Print("Ошибка отправки длины сообщения: ", WSAGetLastError());
        closesocket(clientSocket);
        clientSocket = INVALID_SOCKET;
        clientConnected = false;
        return;
    }
    
    // Отправляем данные
    uchar dataBytes[];
    StringToCharArray(data, dataBytes, 0, StringLen(data));
    sent = send(clientSocket, dataBytes, dataLen, 0);
    if(sent == SOCKET_ERROR)
    {
        Print("Ошибка отправки данных: ", WSAGetLastError());
        closesocket(clientSocket);
        clientSocket = INVALID_SOCKET;
        clientConnected = false;
    }
}

