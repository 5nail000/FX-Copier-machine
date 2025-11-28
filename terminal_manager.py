"""
Менеджер для работы с терминалами MT5 через multiprocessing
Каждый терминал работает в отдельном процессе
"""
import psutil
import os
import threading
from multiprocessing import Process, Queue
from typing import Optional, Dict, List, Tuple
import terminal_worker


class TerminalManager:
    """Класс для управления подключениями к терминалам MT5 через процессы"""
    
    def __init__(self):
        self.donor_terminal_path: Optional[str] = None
        self.client_terminal_path: Optional[str] = None
        self.donor_connected: bool = False
        self.client_connected: bool = False
        
        # Очереди для донорского процесса
        self.donor_commands: Optional[Queue] = None
        self.donor_results: Optional[Queue] = None
        self.donor_process: Optional[Process] = None
        
        # Очереди для клиентского процесса
        self.client_commands: Optional[Queue] = None
        self.client_results: Optional[Queue] = None
        self.client_process: Optional[Process] = None
    
    def is_mt5_terminal(self, terminal_path: str) -> bool:
        """
        Проверить, является ли терминал MT5 (не MT4)
        
        Args:
            terminal_path: Путь к терминалу
            
        Returns:
            True если это MT5 терминал
        """
        if not terminal_path:
            return False
        
        path_lower = terminal_path.lower()
        
        # Исключаем MT4 терминалы
        if 'metatrader 4' in path_lower or 'mt4' in path_lower:
            return False
        
        # Проверяем, что это MT5
        if 'metatrader 5' in path_lower or 'mt5' in path_lower:
            return True
        
        # Проверяем стандартные пути MT5
        if 'terminal64.exe' in path_lower or 'terminal.exe' in path_lower:
            # Проверяем родительскую директорию
            parent_dir = os.path.dirname(terminal_path).lower()
            if 'metatrader 4' in parent_dir or 'mt4' in parent_dir:
                return False
            # Если путь содержит "MetaTrader 5" или просто "terminal64.exe" в стандартных местах
            if 'metatrader 5' in parent_dir or 'terminal' in parent_dir:
                return True
        
        return False
    
    def find_running_terminals(self) -> List[str]:
        """
        Найти все запущенные терминалы MT5 (исключая MT4)
        
        Returns:
            Список путей к исполняемым файлам терминалов MT5
        """
        terminals = []
        seen_paths = set()
        
        # Ищем запущенные процессы terminal64.exe или terminal.exe
        for proc in psutil.process_iter(['pid', 'name', 'exe']):
            try:
                name = proc.info.get('name', '').lower()
                exe = proc.info.get('exe', '')
                if name and ('terminal64.exe' in name or 'terminal.exe' in name):
                    if exe and exe not in seen_paths:
                        # Проверяем, что это MT5, а не MT4
                        if self.is_mt5_terminal(exe):
                            terminals.append(exe)
                            seen_paths.add(exe)
                        else:
                            print(f"[DEBUG] Пропущен MT4 терминал: {exe}")
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        
        # Также проверим стандартные пути установки MT5
        standard_paths = [
            r"C:\Program Files\MetaTrader 5\terminal64.exe",
            r"C:\Program Files (x86)\MetaTrader 5\terminal64.exe",
        ]
        
        # Проверяем пути в AppData (только MT5)
        appdata_path = os.path.expanduser(r"~\AppData\Roaming\MetaQuotes\Terminal")
        if os.path.exists(appdata_path):
            for item in os.listdir(appdata_path):
                # Пропускаем MT4 директории
                if 'mt4' in item.lower() or 'metatrader 4' in item.lower():
                    continue
                terminal_path = os.path.join(appdata_path, item, "terminal64.exe")
                if os.path.exists(terminal_path):
                    standard_paths.append(terminal_path)
        
        for path in standard_paths:
            if os.path.exists(path) and path not in seen_paths:
                if self.is_mt5_terminal(path):
                    terminals.append(path)
                    seen_paths.add(path)
        
        return terminals
    
    def check_terminal_account(self, terminal_path: str, timeout: float = 5.0) -> Optional[int]:
        """
        Проверить, какой аккаунт авторизован в терминале
        Использует временное подключение для проверки с таймаутом
        
        Args:
            terminal_path: Путь к терминалу
            timeout: Таймаут в секундах для операции
            
        Returns:
            Номер аккаунта или None если не удалось подключиться
        """
        # Проверяем, что это MT5 терминал
        if not self.is_mt5_terminal(terminal_path):
            print(f"[DEBUG] Пропущен не-MT5 терминал: {terminal_path}")
            return None
        
        # Временный импорт для проверки
        import MetaTrader5 as mt5
        
        result = [None]  # Используем список для изменения из вложенной функции
        exception_occurred = [False]
        
        def check_account():
            try:
                # Инициализация MT5 без авторизации (используем уже авторизованный аккаунт)
                if not mt5.initialize(path=terminal_path):
                    return
                
                # Получаем информацию об аккаунте (без авторизации, если уже авторизован)
                account_info = mt5.account_info()
                
                if account_info is None:
                    mt5.shutdown()
                    return
                
                result[0] = account_info.login
                mt5.shutdown()
            except Exception as e:
                exception_occurred[0] = True
                print(f"[DEBUG] Ошибка при проверке терминала {terminal_path}: {e}")
                try:
                    mt5.shutdown()
                except:
                    pass
        
        # Запускаем проверку в отдельном потоке с таймаутом
        thread = threading.Thread(target=check_account)
        thread.daemon = True
        thread.start()
        thread.join(timeout=timeout)
        
        if thread.is_alive():
            # Таймаут - возможно, это MT4 терминал или завис
            print(f"[DEBUG] Таймаут при проверке терминала: {terminal_path} (возможно, это MT4)")
            try:
                import MetaTrader5 as mt5
                mt5.shutdown()
            except:
                pass
            return None
        
        if exception_occurred[0]:
            return None
        
        return result[0]
    
    def print_connection_status(self, account_info: Dict):
        """
        Вывести статус подключения к аккаунту
        
        Args:
            account_info: Словарь с информацией об аккаунте
        """
        print(f"\n{'='*60}")
        print(f"Аккаунт: {account_info.get('account', 'N/A')}")
        print(f"Сервер: {account_info.get('server', 'N/A')}")
        print(f"Баланс: {account_info.get('balance', 0)}")
        
        if 'trade_allowed' in account_info:
            trade_allowed = account_info.get('trade_allowed', False)
            trade_expert = account_info.get('trade_expert', False)
            
            print(f"\nРазрешения на торговлю:")
            print(f"  Торговля разрешена: {'ДА' if trade_allowed else 'НЕТ'}")
            print(f"  Торговля через экспертов: {'ДА' if trade_expert else 'НЕТ'}")
            
            if not trade_allowed:
                print(f"  ⚠️  ВНИМАНИЕ: Торговля запрещена на этом аккаунте!")
            if not trade_expert:
                print(f"  ⚠️  ВНИМАНИЕ: Торговля через экспертов запрещена!")
        
        print(f"{'='*60}\n")
    
    def find_and_connect_donor(self, donor_account_number: int) -> bool:
        """
        Найти и запустить процесс для донорского терминала
        
        Args:
            donor_account_number: Номер донорского аккаунта
            
        Returns:
            True если подключение успешно
        """
        terminals = self.find_running_terminals()
        
        if not terminals:
            print("Не найдено запущенных терминалов MT5")
            return False
        
        print(f"Поиск донорского аккаунта {donor_account_number} среди {len(terminals)} терминалов MT5...")
        
        for terminal_path in terminals:
            print(f"  Проверка терминала: {terminal_path}")
            account_number = self.check_terminal_account(terminal_path)
            if account_number == donor_account_number:
                # Найден нужный терминал, запускаем процесс
                print(f"  ✓ Найден донорский аккаунт в терминале: {terminal_path}")
                return self.start_donor_process(terminal_path)
            elif account_number is not None:
                print(f"  Аккаунт {account_number} найден, но это не донорский ({donor_account_number})")
        
        print(f"Ошибка: Донорский аккаунт {donor_account_number} не найден ни в одном запущенном терминале.")
        print("Убедитесь, что:")
        print(f"  1. Терминал MT5 запущен")
        print(f"  2. Аккаунт {donor_account_number} авторизован в терминале")
        return False
    
    def find_and_connect_client(self, client_account_number: int, magic_number: Optional[int] = 234000) -> bool:
        """
        Найти и запустить процесс для клиентского терминала
        
        Args:
            client_account_number: Номер клиентского аккаунта
            magic_number: Магическое число для фильтрации позиций
            
        Returns:
            True если подключение успешно
        """
        terminals = self.find_running_terminals()
        
        if not terminals:
            print("Не найдено запущенных терминалов MT5")
            return False
        
        print(f"Поиск клиентского аккаунта {client_account_number} среди {len(terminals)} терминалов MT5...")
        
        for terminal_path in terminals:
            # Пропускаем донорский терминал, если он уже найден
            if terminal_path == self.donor_terminal_path:
                continue
            
            print(f"  Проверка терминала: {terminal_path}")
            account_number = self.check_terminal_account(terminal_path)
            if account_number == client_account_number:
                # Найден нужный терминал, запускаем процесс
                print(f"  ✓ Найден клиентский аккаунт в терминале: {terminal_path}")
                return self.start_client_process(terminal_path, magic_number)
            elif account_number is not None:
                print(f"  Аккаунт {account_number} найден, но это не клиентский ({client_account_number})")
        
        print(f"Ошибка: Клиентский аккаунт {client_account_number} не найден ни в одном запущенном терминале.")
        print("Убедитесь, что:")
        print(f"  1. Терминал MT5 запущен")
        print(f"  2. Аккаунт {client_account_number} авторизован в терминале")
        print(f"  3. Аккаунт находится в другом терминале, чем донорский ({self.donor_terminal_path})")
        return False
    
    def start_donor_process(self, terminal_path: str) -> bool:
        """
        Запустить процесс для донорского терминала
        
        Args:
            terminal_path: Путь к терминалу
            
        Returns:
            True если процесс запущен успешно
        """
        if self.donor_process and self.donor_process.is_alive():
            print("Донорский процесс уже запущен")
            return True
        
        # Создаем очереди
        self.donor_commands = Queue()
        self.donor_results = Queue()
        
        # Запускаем процесс
        self.donor_terminal_path = terminal_path
        self.donor_process = Process(
            target=terminal_worker.donor_worker,
            args=(terminal_path, self.donor_commands, self.donor_results),
            daemon=False
        )
        self.donor_process.start()
        
        # Ждем подтверждения подключения
        try:
            result = self.donor_results.get(timeout=10.0)
            if result.get('status') == 'connected':
                self.donor_connected = True
                print(f"✓ Донорский терминал найден: {terminal_path}")
                self.print_connection_status(result)
                return True
            else:
                print(f"Ошибка подключения к донорскому терминалу: {result.get('message', 'Неизвестная ошибка')}")
                return False
        except Exception as e:
            print(f"Ошибка при запуске донорского процесса: {e}")
            return False
    
    def start_client_process(self, terminal_path: str, magic_number: int = 234000) -> bool:
        """
        Запустить процесс для клиентского терминала
        
        Args:
            terminal_path: Путь к терминалу
            magic_number: Магическое число для фильтрации позиций
            
        Returns:
            True если процесс запущен успешно
        """
        if self.client_process and self.client_process.is_alive():
            print("Клиентский процесс уже запущен")
            return True
        
        # Создаем очереди
        self.client_commands = Queue()
        self.client_results = Queue()
        
        # Запускаем процесс
        self.client_terminal_path = terminal_path
        self.client_process = Process(
            target=terminal_worker.client_worker,
            args=(terminal_path, self.client_commands, self.client_results, magic_number),
            daemon=False
        )
        self.client_process.start()
        
        # Ждем подтверждения подключения
        try:
            result = self.client_results.get(timeout=10.0)
            if result.get('status') == 'connected':
                self.client_connected = True
                print(f"✓ Клиентский терминал найден: {terminal_path}")
                print(f"✓ Разрешения на торговлю: ОК")
                self.print_connection_status(result)
                return True
            else:
                print(f"Ошибка подключения к клиентскому терминалу: {result.get('message', 'Неизвестная ошибка')}")
                return False
        except Exception as e:
            print(f"Ошибка при запуске клиентского процесса: {e}")
            return False
    
    def get_donor_positions(self) -> List[Dict]:
        """
        Получить позиции с донорского аккаунта
        
        Returns:
            Список позиций
        """
        if not self.donor_connected or not self.donor_commands:
            return []
        
        self.donor_commands.put({'action': 'get_positions'})
        
        try:
            result = self.donor_results.get(timeout=5.0)
            if result.get('status') == 'ok' and result.get('action') == 'positions':
                return result.get('data', [])
            return []
        except Exception as e:
            print(f"Ошибка получения позиций донора: {e}")
            return []
    
    def get_client_positions(self) -> List[Dict]:
        """
        Получить позиции с клиентского аккаунта
        
        Returns:
            Список позиций
        """
        if not self.client_connected or not self.client_commands:
            return []
        
        self.client_commands.put({'action': 'get_positions'})
        
        try:
            result = self.client_results.get(timeout=5.0)
            if result.get('status') == 'ok' and result.get('action') == 'positions':
                return result.get('data', [])
            return []
        except Exception as e:
            print(f"Ошибка получения позиций клиента: {e}")
            return []
    
    def get_client_position_by_symbol(self, symbol: str) -> Optional[Dict]:
        """
        Получить позицию клиента по символу
        
        Args:
            symbol: Символ
            
        Returns:
            Информация о позиции или None
        """
        if not self.client_connected or not self.client_commands:
            return None
        
        self.client_commands.put({
            'action': 'get_position_by_symbol',
            'symbol': symbol
        })
        
        try:
            result = self.client_results.get(timeout=5.0)
            if result.get('status') == 'ok' and result.get('action') == 'position':
                return result.get('data')
            return None
        except Exception as e:
            print(f"Ошибка получения позиции клиента: {e}")
            return None
    
    def get_client_position_by_ticket(self, ticket: int) -> Optional[Dict]:
        """
        Получить позицию клиента по тикету
        
        Args:
            ticket: Тикет позиции
            
        Returns:
            Информация о позиции или None
        """
        if not self.client_connected or not self.client_commands:
            return None
        
        self.client_commands.put({
            'action': 'get_position_by_ticket',
            'ticket': ticket
        })
        
        try:
            result = self.client_results.get(timeout=5.0)
            if result.get('status') == 'ok' and result.get('action') == 'position':
                return result.get('data')
            return None
        except Exception as e:
            print(f"Ошибка получения позиции клиента: {e}")
            return None
    
    def place_client_order(self, request: Dict) -> Optional[Dict]:
        """
        Разместить ордер на клиентском аккаунте
        
        Args:
            request: Словарь с параметрами ордера
            
        Returns:
            Результат размещения ордера или None
        """
        if not self.client_connected or not self.client_commands:
            return None
        
        self.client_commands.put({
            'action': 'place_order',
            'request': request
        })
        
        try:
            result = self.client_results.get(timeout=10.0)
            if result.get('status') == 'ok' and result.get('action') == 'order_result':
                return result.get('data')
            elif result.get('status') == 'error':
                print(f"Ошибка размещения ордера: {result.get('message', 'Неизвестная ошибка')}")
            return None
        except Exception as e:
            print(f"Ошибка размещения ордера: {e}")
            return None
    
    def get_client_order_by_ticket(self, ticket: int) -> Optional[Dict]:
        """
        Получить ордер клиента по тикету
        
        Args:
            ticket: Тикет ордера
            
        Returns:
            Информация об ордере или None
        """
        if not self.client_connected or not self.client_commands:
            return None
        
        self.client_commands.put({
            'action': 'get_order_by_ticket',
            'ticket': ticket
        })
        
        try:
            result = self.client_results.get(timeout=5.0)
            if result.get('status') == 'ok' and result.get('action') == 'order':
                return result.get('data')
            return None
        except Exception as e:
            print(f"Ошибка получения ордера: {e}")
            return None
    
    def get_deal_by_order(self, order_ticket: int, from_time: int = 0, to_time: int = None) -> Optional[Dict]:
        """
        Получить deal по ticket ордера для поиска позиции
        
        Args:
            order_ticket: Тикет ордера
            from_time: Время начала поиска (Unix timestamp)
            to_time: Время окончания поиска (Unix timestamp, по умолчанию текущее время)
            
        Returns:
            Информация о deal или None
        """
        if not self.client_connected or not self.client_commands:
            return None
        
        if to_time is None:
            import time
            to_time = int(time.time())
        
        self.client_commands.put({
            'action': 'get_deal_by_order',
            'order_ticket': order_ticket,
            'from_time': from_time,
            'to_time': to_time
        })
        
        try:
            result = self.client_results.get(timeout=5.0)
            if result.get('status') == 'ok' and result.get('action') == 'deal':
                return result.get('data')
            return None
        except Exception as e:
            print(f"Ошибка получения deal: {e}")
            return None
    
    def get_client_orders(self) -> List[Dict]:
        """
        Получить все ордера клиента
        
        Returns:
            Список ордеров
        """
        if not self.client_connected or not self.client_commands:
            return []
        
        self.client_commands.put({'action': 'get_orders'})
        
        try:
            result = self.client_results.get(timeout=5.0)
            if result.get('status') == 'ok' and result.get('action') == 'orders':
                return result.get('data', [])
            return []
        except Exception as e:
            print(f"Ошибка получения ордеров: {e}")
            return []
    
    def check_and_select_client_symbol(self, symbol: str) -> Optional[Dict]:
        """
        Проверить и выбрать символ на клиентском аккаунте
        
        Args:
            symbol: Символ
            
        Returns:
            Информация о символе или None если символ недоступен
        """
        if not self.client_connected or not self.client_commands:
            return None
        
        self.client_commands.put({
            'action': 'check_and_select_symbol',
            'symbol': symbol
        })
        
        try:
            result = self.client_results.get(timeout=5.0)
            if result.get('status') == 'ok' and result.get('action') == 'symbol_check':
                return result.get('data')
            elif result.get('status') == 'error':
                print(f"⚠️  {result.get('message')}")
            return None
        except Exception as e:
            print(f"Ошибка проверки символа: {e}")
            return None
    
    def get_client_symbol_tick(self, symbol: str) -> Optional[Dict]:
        """
        Получить тик для символа на клиентском аккаунте
        
        Args:
            symbol: Символ
            
        Returns:
            Информация о тике или None
        """
        if not self.client_connected or not self.client_commands:
            return None
        
        self.client_commands.put({
            'action': 'get_symbol_info_tick',
            'symbol': symbol
        })
        
        try:
            result = self.client_results.get(timeout=5.0)
            if result.get('status') == 'ok' and result.get('action') == 'tick':
                return result.get('data')
            return None
        except Exception as e:
            print(f"Ошибка получения тика: {e}")
            return None
    
    def get_donor_account_info(self) -> Optional[Dict]:
        """
        Получить информацию о донорском аккаунте
        
        Returns:
            Информация об аккаунте или None
        """
        if not self.donor_connected or not self.donor_commands:
            return None
        
        self.donor_commands.put({'action': 'get_account_info'})
        
        try:
            result = self.donor_results.get(timeout=5.0)
            if result.get('status') == 'ok' and result.get('action') == 'account_info':
                return result.get('data')
            return None
        except Exception as e:
            print(f"Ошибка получения информации о донорском аккаунте: {e}")
            return None
    
    def get_client_account_info(self) -> Optional[Dict]:
        """
        Получить информацию о клиентском аккаунте
        
        Returns:
            Информация об аккаунте или None
        """
        if not self.client_connected or not self.client_commands:
            return None
        
        self.client_commands.put({'action': 'get_account_info'})
        
        try:
            result = self.client_results.get(timeout=5.0)
            if result.get('status') == 'ok' and result.get('action') == 'account_info':
                return result.get('data')
            return None
        except Exception as e:
            print(f"Ошибка получения информации о клиентском аккаунте: {e}")
            return None
    
    def shutdown(self):
        """Закрыть все подключения и остановить процессы"""
        # Останавливаем донорский процесс
        if self.donor_process and self.donor_process.is_alive():
            if self.donor_commands:
                self.donor_commands.put({'action': 'shutdown'})
            self.donor_process.join(timeout=3.0)
            if self.donor_process.is_alive():
                self.donor_process.terminate()
                self.donor_process.join(timeout=1.0)
        
        # Останавливаем клиентский процесс
        if self.client_process and self.client_process.is_alive():
            if self.client_commands:
                self.client_commands.put({'action': 'shutdown'})
            self.client_process.join(timeout=3.0)
            if self.client_process.is_alive():
                self.client_process.terminate()
                self.client_process.join(timeout=1.0)
        
        self.donor_connected = False
        self.client_connected = False
