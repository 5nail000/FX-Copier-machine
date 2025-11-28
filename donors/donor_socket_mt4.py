"""
Донор через Socket MT4 (MQL4 EA)
"""
from typing import List, Optional, Dict
from datetime import datetime
from donors.donor_base import DonorBase, DonorPosition, DonorOrder
from donors.socket_client import SocketClient
import logging


class DonorSocketMT4(DonorBase):
    """Донор через Socket MT4"""
    
    def __init__(self, donor_id: str, account_number: int, host: str = 'localhost', port: int = 8888):
        super().__init__(donor_id, account_number)
        self.host = host
        self.port = port
        self.socket_client = SocketClient(host, port, donor_id)
        self.last_positions: List[DonorPosition] = []
        self.last_orders: List[DonorOrder] = []
        self.logger = logging.getLogger(__name__)
    
    def connect(self) -> bool:
        """Подключиться к MT4 EA через сокет"""
        self.logger.info(f"[{self.donor_id}] Подключение к MT4 EA через сокет {self.host}:{self.port}...")
        
        if self.socket_client.connect():
            self.socket_client.set_callback(self._on_data_received)
            self.socket_client.start_listening()
            self.connected = True
            self.logger.info(f"[{self.donor_id}] Успешно подключен к MT4 EA")
            return True
        
        self.logger.error(f"[{self.donor_id}] Не удалось подключиться к MT4 EA")
        return False
    
    def disconnect(self):
        """Отключиться"""
        self.socket_client.disconnect()
        self.connected = False
        self.logger.info(f"[{self.donor_id}] Отключен")
    
    def _on_data_received(self, data: Dict):
        """Обработка полученных данных"""
        try:
            # Парсим позиции
            positions = []
            for pos_data in data.get('positions', []):
                positions.append(DonorPosition(
                    ticket=pos_data['ticket'],
                    symbol=pos_data['symbol'],
                    type=pos_data['type'],
                    volume=pos_data['volume'],
                    price_open=pos_data['price_open'],
                    price_current=pos_data['price_current'],
                    profit=pos_data['profit'],
                    time=datetime.fromtimestamp(pos_data['time']),
                    magic=pos_data.get('magic'),
                    comment=pos_data.get('comment', ''),
                    donor_id=self.donor_id
                ))
            
            # Парсим ордера
            orders = []
            for order_data in data.get('orders', []):
                orders.append(DonorOrder(
                    ticket=order_data['ticket'],
                    symbol=order_data['symbol'],
                    type=order_data['type'],
                    volume=order_data['volume'],
                    price_open=order_data['price_open'],
                    time_setup=datetime.fromtimestamp(order_data['time_setup']),
                    donor_id=self.donor_id,
                    sl=order_data.get('sl'),
                    tp=order_data.get('tp')
                ))
            
            self.last_positions = positions
            self.last_orders = orders
            
            # Обновляем баланс
            if 'account_info' in data:
                self.balance = data['account_info'].get('balance', 0.0)
        except Exception as e:
            self.logger.error(f"[{self.donor_id}] Ошибка обработки данных: {e}")
    
    def get_positions(self) -> List[DonorPosition]:
        """Получить позиции"""
        return self.last_positions
    
    def get_orders(self) -> List[DonorOrder]:
        """Получить ордера"""
        return self.last_orders
    
    def get_account_info(self) -> Optional[Dict]:
        """Получить информацию об аккаунте"""
        return {
            'login': self.account_number,
            'balance': self.balance,
            'server': 'MT4 Socket'
        }

