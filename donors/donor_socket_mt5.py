"""
Донор через Socket MT5 (MQL5 EA)
"""
from donors.donor_socket_mt4 import DonorSocketMT4
import logging


class DonorSocketMT5(DonorSocketMT4):
    """Донор через Socket MT5"""
    
    def __init__(self, donor_id: str, account_number: int, host: str = 'localhost', port: int = 8888):
        super().__init__(donor_id, account_number, host, port)
        self.logger = logging.getLogger(__name__)
    
    def connect(self) -> bool:
        """Подключиться к MT5 EA через сокет"""
        self.logger.info(f"[{self.donor_id}] Подключение к MT5 EA через сокет {self.host}:{self.port}...")
        
        if self.socket_client.connect():
            self.socket_client.set_callback(self._on_data_received)
            self.socket_client.start_listening()
            self.connected = True
            self.logger.info(f"[{self.donor_id}] Успешно подключен к MT5 EA")
            return True
        
        self.logger.error(f"[{self.donor_id}] Не удалось подключиться к MT5 EA")
        return False

