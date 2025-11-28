"""
Базовый класс для всех типов доноров
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Optional
from dataclasses import dataclass
from datetime import datetime


@dataclass
class DonorPosition:
    """Унифицированная структура позиции донора"""
    ticket: int
    symbol: str
    type: int  # BUY/SELL
    volume: float
    price_open: float
    price_current: float
    profit: float
    time: datetime
    donor_id: str  # Идентификатор донора (для различения)
    magic: Optional[int] = None
    comment: Optional[str] = None


@dataclass
class DonorOrder:
    """Унифицированная структура ордера донора"""
    ticket: int
    symbol: str
    type: int
    volume: float
    price_open: float
    time_setup: datetime
    donor_id: str
    sl: Optional[float] = None
    tp: Optional[float] = None


class DonorBase(ABC):
    """Базовый класс для всех типов доноров"""
    
    def __init__(self, donor_id: str, account_number: int):
        self.donor_id = donor_id
        self.account_number = account_number
        self.connected = False
        self.balance: float = 0.0
    
    @abstractmethod
    def connect(self) -> bool:
        """Подключиться к донору"""
        pass
    
    @abstractmethod
    def disconnect(self):
        """Отключиться от донора"""
        pass
    
    @abstractmethod
    def get_positions(self) -> List[DonorPosition]:
        """Получить все позиции донора"""
        pass
    
    @abstractmethod
    def get_orders(self) -> List[DonorOrder]:
        """Получить все ордера донора"""
        pass
    
    @abstractmethod
    def get_account_info(self) -> Optional[Dict]:
        """Получить информацию об аккаунте"""
        pass
    
    def is_connected(self) -> bool:
        """Проверить подключение"""
        return self.connected

