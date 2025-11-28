//+------------------------------------------------------------------+
//|                                          DonorBroadcasterMT5.mq5 |
//|                        Трансляция данных о позициях и ордерах    |
//|                                    для копировщика сделок MT5     |
//+------------------------------------------------------------------+
#property copyright "FX Copier"
#property version   "1.00"
#property strict

#include <WinSock2.mqh>

// Параметры
input int SocketPort = 8888;  // Порт для сокета
input int UpdateInterval = 100;  // Интервал обновления (мс)

// Глобальные переменные
int serverSocket = INVALID_SOCKET;
int clientSocket = INVALID_SOCKET;
bool clientConnected = false;
datetime lastUpdateTime = 0;

// Переменные для отслеживания изменений состояния
string lastStateHash = "";  // Хеш предыдущего состояния
long lastAccountNumber = 0;
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
    serverAddr.sin_family = AF_INET;
    serverAddr.sin_addr.s_addr = INADDR_ANY;
    serverAddr.sin_port = htons(SocketPort);
    
    // Привязка сокета
    if(bind(serverSocket, serverAddr, sizeof(serverAddr)) == SOCKET_ERROR)
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
    
    return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                   |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
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
        int addrLen = sizeof(clientAddr);
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
//| Построение JSON с данными о позициях                              |
//+------------------------------------------------------------------+
string BuildPositionsJSON()
{
    string json = "{";
    json += "\"type\":\"positions\",";
    json += "\"timestamp\":" + IntegerToString((int)TimeCurrent()) + ",";
    json += "\"account_info\":{";
    json += "\"login\":" + IntegerToString((int)AccountInfoInteger(ACCOUNT_LOGIN)) + ",";
    json += "\"balance\":" + DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2) + ",";
    json += "\"equity\":" + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2) + ",";
    json += "\"server\":\"" + AccountInfoString(ACCOUNT_SERVER) + "\"";
    json += "},";
    json += "\"positions\":[";
    
    int totalPositions = PositionsTotal();
    bool first = true;
    
    for(int i = 0; i < totalPositions; i++)
    {
        ulong ticket = PositionGetTicket(i);
        if(ticket > 0)
        {
            if(!first) json += ",";
            first = false;
            
            json += "{";
            json += "\"ticket\":" + IntegerToString((int)ticket) + ",";
            json += "\"symbol\":\"" + PositionGetString(POSITION_SYMBOL) + "\",";
            json += "\"type\":" + IntegerToString((int)PositionGetInteger(POSITION_TYPE)) + ",";
            json += "\"volume\":" + DoubleToString(PositionGetDouble(POSITION_VOLUME), 2) + ",";
            json += "\"price_open\":" + DoubleToString(PositionGetDouble(POSITION_PRICE_OPEN), 5) + ",";
            json += "\"price_current\":" + DoubleToString(PositionGetDouble(POSITION_PRICE_CURRENT), 5) + ",";
            json += "\"sl\":" + DoubleToString(PositionGetDouble(POSITION_SL), 5) + ",";
            json += "\"tp\":" + DoubleToString(PositionGetDouble(POSITION_TP), 5) + ",";
            json += "\"profit\":" + DoubleToString(PositionGetDouble(POSITION_PROFIT), 2) + ",";
            json += "\"time\":" + IntegerToString((int)PositionGetInteger(POSITION_TIME)) + ",";
            json += "\"magic\":" + IntegerToString((int)PositionGetInteger(POSITION_MAGIC)) + ",";
            json += "\"comment\":\"" + PositionGetString(POSITION_COMMENT) + "\"";
            json += "}";
        }
    }
    
    json += "],";
    json += "\"orders\":[";
    
    // Добавить данные об ордерах
    int totalOrders = OrdersTotal();
    first = true;
    
    for(int i = 0; i < totalOrders; i++)
    {
        ulong ticket = OrderGetTicket(i);
        if(ticket > 0)
        {
            if(!first) json += ",";
            first = false;
            
            json += "{";
            json += "\"ticket\":" + IntegerToString((int)ticket) + ",";
            json += "\"symbol\":\"" + OrderGetString(ORDER_SYMBOL) + "\",";
            json += "\"type\":" + IntegerToString((int)OrderGetInteger(ORDER_TYPE)) + ",";
            json += "\"volume\":" + DoubleToString(OrderGetDouble(ORDER_VOLUME_INITIAL), 2) + ",";
            json += "\"price_open\":" + DoubleToString(OrderGetDouble(ORDER_PRICE_OPEN), 5) + ",";
            json += "\"sl\":" + DoubleToString(OrderGetDouble(ORDER_SL), 5) + ",";
            json += "\"tp\":" + DoubleToString(OrderGetDouble(ORDER_TP), 5) + ",";
            json += "\"time_setup\":" + IntegerToString((int)OrderGetInteger(ORDER_TIME_SETUP));
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
    long currentAccount = AccountInfoInteger(ACCOUNT_LOGIN);
    if(currentAccount != lastAccountNumber)
        return true;
    
    // Проверка изменения баланса (с небольшой погрешностью для плавающих значений)
    // Эквити не проверяем, т.к. оно меняется на каждом тике при наличии позиций
    double currentBalance = AccountInfoDouble(ACCOUNT_BALANCE);
    if(MathAbs(currentBalance - lastBalance) > 0.01)
        return true;
    
    // Проверка изменения количества позиций и ордеров
    int currentPositions = PositionsTotal();
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
    state += IntegerToString((int)AccountInfoInteger(ACCOUNT_LOGIN)) + "|";
    state += DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2) + "|";
    // Эквити не включаем в хеш, т.к. оно меняется на каждом тике при наличии позиций
    state += IntegerToString(PositionsTotal()) + "|";
    state += IntegerToString(OrdersTotal()) + "|";
    
    // Добавляем информацию о позициях (тикет, объем, цены, SL/TP)
    // Профит не включаем в хеш, т.к. он меняется на каждом тике
    for(int i = 0; i < PositionsTotal(); i++)
    {
        ulong ticket = PositionGetTicket(i);
        if(ticket > 0)
        {
            state += IntegerToString((int)ticket) + ":";
            state += PositionGetString(POSITION_SYMBOL) + ":";
            state += IntegerToString((int)PositionGetInteger(POSITION_TYPE)) + ":";
            state += DoubleToString(PositionGetDouble(POSITION_VOLUME), 2) + ":";
            state += DoubleToString(PositionGetDouble(POSITION_PRICE_OPEN), 5) + ":";
            state += DoubleToString(PositionGetDouble(POSITION_SL), 5) + ":";
            state += DoubleToString(PositionGetDouble(POSITION_TP), 5) + "|";
        }
    }
    
    // Добавляем информацию об ордерах (тикет, объем, цена, SL/TP)
    for(int i = 0; i < OrdersTotal(); i++)
    {
        ulong ticket = OrderGetTicket(i);
        if(ticket > 0)
        {
            state += IntegerToString((int)ticket) + ":";
            state += OrderGetString(ORDER_SYMBOL) + ":";
            state += IntegerToString((int)OrderGetInteger(ORDER_TYPE)) + ":";
            state += DoubleToString(OrderGetDouble(ORDER_VOLUME_INITIAL), 2) + ":";
            state += DoubleToString(OrderGetDouble(ORDER_PRICE_OPEN), 5) + ":";
            state += DoubleToString(OrderGetDouble(ORDER_SL), 5) + ":";
            state += DoubleToString(OrderGetDouble(ORDER_TP), 5) + "|";
        }
    }
    
    return state;
}

//+------------------------------------------------------------------+
//| Обновление последнего известного состояния                       |
//+------------------------------------------------------------------+
void UpdateLastState()
{
    lastAccountNumber = AccountInfoInteger(ACCOUNT_LOGIN);
    lastBalance = AccountInfoDouble(ACCOUNT_BALANCE);
    // lastEquity больше не обновляем, т.к. не отслеживаем изменения эквити
    lastPositionsCount = PositionsTotal();
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

