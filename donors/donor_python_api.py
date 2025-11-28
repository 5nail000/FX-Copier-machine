"""
Донор через Python API MT5 (текущий метод)
"""
import sys
import os
from typing import List, Optional, Dict
from datetime import datetime

# Добавляем родительскую директорию в путь для импорта
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from donors.donor_base import DonorBase, DonorPosition, DonorOrder
from terminal_manager import TerminalManager
import logging


class DonorPythonAPI(DonorBase):
    """Донор через Python API MT5"""
    
    def __init__(self, donor_id: str, account_number: int):
        super().__init__(donor_id, account_number)
        self.terminal_manager = TerminalManager()
        self.terminal_path: Optional[str] = None
        self.logger = logging.getLogger(__name__)
    
    def connect(self) -> bool:
        """Подключиться через Python API"""
        self.logger.info(f"[{self.donor_id}] Подключение через Python API MT5 (аккаунт {self.account_number})...")
        
        if self.terminal_manager.find_and_connect_donor(self.account_number):
            account_info = self.terminal_manager.get_donor_account_info()
            if account_info:
                self.balance = account_info.get('balance', 0.0)
                self.connected = True
                self.logger.info(f"[{self.donor_id}] Успешно подключен. Баланс: {self.balance}")
                return True
        
        self.logger.error(f"[{self.donor_id}] Не удалось подключиться")
        return False
    
    def disconnect(self):
        """Отключиться"""
        if self.terminal_manager:
            self.terminal_manager.shutdown_donor()
        self.connected = False
        self.logger.info(f"[{self.donor_id}] Отключен")
    
    def get_positions(self) -> List[DonorPosition]:
        """Получить позиции"""
        if not self.connected:
            return []
        
        try:
            positions_data = self.terminal_manager.get_donor_positions()
            result = []
            
            for pos_data in positions_data:
                result.append(DonorPosition(
                    ticket=pos_data['ticket'],
                    symbol=pos_data['symbol'],
                    type=pos_data['type'],
                    volume=pos_data['volume'],
                    price_open=pos_data['price_open'],
                    price_current=pos_data['price_current'],
                    profit=pos_data['profit'],
                    time=datetime.fromtimestamp(pos_data['time']),
                    magic=pos_data.get('magic'),
                    comment=pos_data.get('comment'),
                    donor_id=self.donor_id
                ))
            
            return result
        except Exception as e:
            self.logger.error(f"[{self.donor_id}] Ошибка получения позиций: {e}")
            return []
    
    def get_orders(self) -> List[DonorOrder]:
        """Получить ордера (для MT5 через API ордера не отслеживаются отдельно)"""
        # В текущей реализации ордера не отслеживаются через Python API
        return []
    
    def get_account_info(self) -> Optional[Dict]:
        """Получить информацию об аккаунте"""
        if not self.connected:
            return None
        return self.terminal_manager.get_donor_account_info()

