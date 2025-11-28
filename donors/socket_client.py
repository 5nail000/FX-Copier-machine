"""
Базовый клиент для подключения к MQL4/MQL5 EA через сокет
"""
import socket
import json
import threading
from typing import Callable, Optional, Dict
import struct
import logging


class SocketClient:
    """Клиент для подключения к EA через TCP сокет"""
    
    def __init__(self, host: str = 'localhost', port: int = 8888, donor_id: str = ""):
        self.host = host
        self.port = port
        self.donor_id = donor_id
        self.socket: Optional[socket.socket] = None
        self.connected = False
        self.running = False
        self.callback: Optional[Callable] = None
        self.thread: Optional[threading.Thread] = None
        self.last_data: Optional[Dict] = None
        self.lock = threading.Lock()
        self.logger = logging.getLogger(__name__)
    
    def connect(self, timeout: float = 5.0) -> bool:
        """Подключиться к EA"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(timeout)
            self.socket.connect((self.host, self.port))
            self.socket.settimeout(None)
            self.connected = True
            self.running = True
            self.logger.info(f"[SocketClient {self.donor_id}] Подключено к {self.host}:{self.port}")
            return True
        except Exception as e:
            self.logger.error(f"[SocketClient {self.donor_id}] Ошибка подключения: {e}")
            return False
    
    def set_callback(self, callback: Callable):
        """Установить callback для обработки данных"""
        self.callback = callback
    
    def start_listening(self):
        """Начать прослушивание данных"""
        if not self.connected:
            return
        
        self.thread = threading.Thread(target=self._listen_loop, daemon=True)
        self.thread.start()
    
    def _listen_loop(self):
        """Цикл прослушивания данных"""
        while self.running and self.connected:
            try:
                # Читаем длину сообщения (4 байта, big-endian)
                length_data = self._recv_exact(4)
                if not length_data:
                    break
                
                message_length = struct.unpack('>I', length_data)[0]
                
                # Читаем само сообщение
                data = self._recv_exact(message_length)
                if not data:
                    break
                
                # Парсим JSON
                json_data = json.loads(data.decode('utf-8'))
                
                # Сохраняем последние данные
                with self.lock:
                    self.last_data = json_data
                
                # Вызываем callback
                if self.callback:
                    self.callback(json_data)
                    
            except socket.timeout:
                continue
            except Exception as e:
                self.logger.error(f"[SocketClient {self.donor_id}] Ошибка чтения: {e}")
                break
        
        self.connected = False
        self.logger.info(f"[SocketClient {self.donor_id}] Отключено")
    
    def _recv_exact(self, n: int) -> Optional[bytes]:
        """Получить точно n байт"""
        if not self.socket:
            return None
        
        data = b""
        while len(data) < n:
            try:
                chunk = self.socket.recv(n - len(data))
                if not chunk:
                    return None
                data += chunk
            except socket.timeout:
                continue
            except Exception as e:
                self.logger.error(f"[SocketClient {self.donor_id}] Ошибка recv: {e}")
                return None
        return data
    
    def get_last_data(self) -> Optional[Dict]:
        """Получить последние полученные данные"""
        with self.lock:
            return self.last_data.copy() if self.last_data else None
    
    def disconnect(self):
        """Отключиться от EA"""
        self.running = False
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
        self.connected = False

