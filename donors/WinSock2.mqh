//+------------------------------------------------------------------+
//|                                                      WinSock2.mqh |
//|                        Обертка для работы с WinSock2 в MT4/MT5    |
//+------------------------------------------------------------------+
#property copyright "FX Copier"
#ifdef __MQL5__
// В MQL5 нет #property strict
#else
#property strict
#endif

// Константы WinSock
#define INVALID_SOCKET          -1
#define SOCKET_ERROR            -1
#define AF_INET                 2
#define SOCK_STREAM             1
#define INADDR_ANY              0

// Структура WSADATA
struct WSADATA
{
    ushort wVersion;
    ushort wHighVersion;
    char szDescription[257];
    char szSystemStatus[129];
    ushort iMaxSockets;
    ushort iMaxUdpDg;
    // lpVendorInfo не используется в strict mode
};

// Вспомогательная структура для адреса
struct in_addr_struct
{
    uchar s_b1;
    uchar s_b2;
    uchar s_b3;
    uchar s_b4;
};

// Структура sockaddr_in
struct sockaddr_in
{
    short sin_family;
    ushort sin_port;
    in_addr_struct s_un_b;
    char sin_zero[8];
};

// Импорт функций из ws2_32.dll
#import "ws2_32.dll"
   int WSAStartup(ushort wVersionRequested, WSADATA& lpWSAData);
   int WSACleanup();
   int WSAGetLastError();
   int socket(int af, int type, int protocol);
   int bind(int s, sockaddr_in& name, int namelen);
   int listen(int s, int backlog);
   int accept(int s, sockaddr_in& addr, int& addrlen);
   int send(int s, uchar& buf[], int len, int flags);
   int recv(int s, uchar& buf[], int len, int flags);
   int closesocket(int s);
   ushort htons(ushort hostshort);
   ulong inet_addr(string cp);
#import

// Вспомогательная функция для установки адреса
void SetSockAddr(sockaddr_in& addr, ulong ip, int port)
{
    addr.sin_family = AF_INET;
    addr.s_un_b.s_b1 = (uchar)((ip >> 24) & 0xFF);
    addr.s_un_b.s_b2 = (uchar)((ip >> 16) & 0xFF);
    addr.s_un_b.s_b3 = (uchar)((ip >> 8) & 0xFF);
    addr.s_un_b.s_b4 = (uchar)(ip & 0xFF);
    addr.sin_port = htons((ushort)port);
    ArrayInitialize(addr.sin_zero, 0);
}

// Константа размера структуры sockaddr_in
#define SOCKADDR_IN_SIZE 16

