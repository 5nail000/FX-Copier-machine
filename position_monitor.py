"""
Мониторинг позиций на донорском аккаунте
"""
from typing import Dict, List, Optional, Set
from dataclasses import dataclass
from datetime import datetime
import logging


@dataclass
class PositionInfo:
    """Информация о позиции"""
    ticket: int
    symbol: str
    type: int  # mt5.ORDER_TYPE_BUY или mt5.ORDER_TYPE_SELL
    volume: float
    price_open: float
    price_current: float
    profit: float
    time: datetime
    magic: Optional[int] = None  # Magic number позиции
    comment: Optional[str] = None  # Комментарий позиции
    sl: Optional[float] = None  # Stop Loss
    tp: Optional[float] = None  # Take Profit


class PositionMonitor:
    """Класс для мониторинга позиций"""
    
    def __init__(self, terminal_manager=None, donor_manager=None):
        self.tracked_positions: Set[int] = set()  # Отслеживаемые позиции
        self.copied_positions: Dict[int, int] = {}  # ticket донора -> ticket клиента
        self.terminal_manager = terminal_manager
        self.donor_manager = donor_manager
        # Хранение предыдущих состояний позиций для отслеживания изменений
        self.donor_position_states: Dict[int, PositionInfo] = {}  # ticket -> PositionInfo
        self.client_position_states: Dict[int, PositionInfo] = {}  # ticket -> PositionInfo
        self.logger = logging.getLogger(__name__)
    
    def get_donor_positions(self) -> List[PositionInfo]:
        """
        Получить все открытые позиции от всех доноров
        
        Returns:
            Список позиций
        """
        result = []
        
        # Используем donor_manager если доступен (новая система множественных доноров)
        if self.donor_manager and len(self.donor_manager.donors) > 0:
            from donors.donor_base import DonorPosition
            donor_positions = self.donor_manager.get_all_positions()
            
            # Конвертируем DonorPosition в PositionInfo
            for donor_pos in donor_positions:
                position_info = PositionInfo(
                    ticket=donor_pos.ticket,
                    symbol=donor_pos.symbol,
                    type=donor_pos.type,
                    volume=donor_pos.volume,
                    price_open=donor_pos.price_open,
                    price_current=donor_pos.price_current,
                    profit=donor_pos.profit,
                    time=donor_pos.time,
                    magic=donor_pos.magic,
                    comment=donor_pos.comment,
                    sl=getattr(donor_pos, 'sl', None),
                    tp=getattr(donor_pos, 'tp', None)
                )
                result.append(position_info)
        
        # Обратная совместимость: используем terminal_manager если donor_manager не доступен
        elif self.terminal_manager:
            positions_data = self.terminal_manager.get_donor_positions()
            if positions_data:
                for pos_data in positions_data:
                    position_info = PositionInfo(
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
                        sl=pos_data.get('sl'),
                        tp=pos_data.get('tp')
                    )
                    result.append(position_info)
        
        return result
    
    def get_new_positions(self) -> List[PositionInfo]:
        """
        Получить новые позиции, которые еще не отслеживаются
        
        Returns:
            Список новых позиций
        """
        all_positions = self.get_donor_positions()
        new_positions = [
            pos for pos in all_positions
            if pos.ticket not in self.tracked_positions
        ]
        
        # Добавляем новые позиции в отслеживаемые и в состояния
        for pos in new_positions:
            self.tracked_positions.add(pos.ticket)
            # Добавляем позицию в состояния для отслеживания изменений
            self.donor_position_states[pos.ticket] = pos
            self.logger.debug(f"Добавлена позиция {pos.ticket} в отслеживаемые. Всего отслеживаемых: {len(self.tracked_positions)}")
        
        return new_positions
    
    def get_closed_positions(self) -> List[int]:
        """
        Получить список закрытых позиций (которые были отслеживаемы, но больше не существуют)
        
        Returns:
            Список тикетов закрытых позиций
        """
        current_tickets = {pos.ticket for pos in self.get_donor_positions()}
        closed_tickets = [
            ticket for ticket in self.tracked_positions
            if ticket not in current_tickets
        ]
        
        if closed_tickets:
            self.logger.info(f"Обнаружены закрытые позиции донора: {closed_tickets}")
            self.logger.debug(f"Отслеживаемые позиции: {self.tracked_positions}")
            self.logger.debug(f"Текущие позиции донора: {current_tickets}")
        
        # Удаляем закрытые позиции из отслеживаемых
        for ticket in closed_tickets:
            self.tracked_positions.discard(ticket)
        
        return closed_tickets
    
    def get_position_by_ticket(self, ticket: int, is_client: bool = False) -> Optional[PositionInfo]:
        """
        Получить информацию о позиции по тикету
        
        Args:
            ticket: Тикет позиции
            is_client: True если ищем клиентскую позицию, False если донорскую
            
        Returns:
            Информация о позиции или None
        """
        positions = self.get_client_positions() if is_client else self.get_donor_positions()
        for pos in positions:
            if pos.ticket == ticket:
                return pos
        return None
    
    def mark_position_copied(self, donor_ticket: int, client_ticket: int):
        """
        Отметить позицию как скопированную
        
        Args:
            donor_ticket: Тикет позиции на донорском аккаунте
            client_ticket: Тикет позиции на клиентском аккаунте
        """
        self.copied_positions[donor_ticket] = client_ticket
    
    def get_client_ticket(self, donor_ticket: int) -> Optional[int]:
        """
        Получить тикет клиентской позиции по тикету донорской
        
        Args:
            donor_ticket: Тикет позиции на донорском аккаунте
            
        Returns:
            Тикет позиции на клиентском аккаунте или None
        """
        return self.copied_positions.get(donor_ticket)
    
    def get_client_positions(self) -> List[PositionInfo]:
        """
        Получить все открытые позиции на клиентском аккаунте
        
        Returns:
            Список позиций
        """
        if not self.terminal_manager:
            return []
        
        positions_data = self.terminal_manager.get_client_positions()
        if not positions_data:
            return []
        
        result = []
        for pos_data in positions_data:
            position_info = PositionInfo(
                ticket=pos_data['ticket'],
                symbol=pos_data['symbol'],
                type=pos_data['type'],
                volume=pos_data['volume'],
                price_open=pos_data['price_open'],
                price_current=pos_data['price_current'],
                profit=pos_data['profit'],
                time=datetime.fromtimestamp(pos_data['time']),
                magic=pos_data.get('magic'),  # Magic number позиции (если есть)
                comment=pos_data.get('comment'),  # Комментарий позиции (если есть)
                sl=pos_data.get('sl'),
                tp=pos_data.get('tp')
            )
            result.append(position_info)
        
        return result
    
    def get_position_changes_donor(self) -> List[Dict]:
        """
        Получить изменения в позициях донора
        
        Returns:
            Список словарей с информацией об изменениях
        """
        changes = []
        current_positions = self.get_donor_positions()
        
        # Создаем словарь текущих позиций для быстрого поиска
        current_dict = {pos.ticket: pos for pos in current_positions}
        
        # Проверяем изменения в существующих позициях
        for ticket, old_pos in self.donor_position_states.items():
            if ticket in current_dict:
                new_pos = current_dict[ticket]
                change_info = self._compare_positions(old_pos, new_pos, "ДОНОР")
                if change_info:
                    changes.append(change_info)
        
        # Обновляем состояния
        self.donor_position_states = current_dict.copy()
        
        return changes
    
    def get_position_changes_client(self) -> List[Dict]:
        """
        Получить изменения в позициях клиента
        
        Returns:
            Список словарей с информацией об изменениях
        """
        changes = []
        current_positions = self.get_client_positions()
        
        # Создаем словарь текущих позиций для быстрого поиска
        current_dict = {pos.ticket: pos for pos in current_positions}
        
        # Проверяем изменения в существующих позициях
        for ticket, old_pos in self.client_position_states.items():
            if ticket in current_dict:
                new_pos = current_dict[ticket]
                change_info = self._compare_positions(old_pos, new_pos, "КЛИЕНТ")
                if change_info:
                    changes.append(change_info)
        
        # Обновляем состояния
        self.client_position_states = current_dict.copy()
        
        return changes
    
    def _compare_positions(self, old_pos: PositionInfo, new_pos: PositionInfo, account_type: str) -> Optional[Dict]:
        """
        Сравнить две позиции и выявить изменения статуса (открыта/закрыта)
        
        Args:
            old_pos: Старое состояние позиции
            new_pos: Новое состояние позиции
            account_type: Тип аккаунта ("ДОНОР" или "КЛИЕНТ")
            
        Returns:
            Словарь с информацией об изменениях или None если изменений нет
        """
        # Мониторим только изменение объема (частичное закрытие/добавление)
        # Это указывает на изменение статуса позиции
        if abs(old_pos.volume - new_pos.volume) > 0.001:
            return {
                'account_type': account_type,
                'ticket': new_pos.ticket,
                'symbol': new_pos.symbol,
                'changes': [{
                    'field': 'volume',
                    'old': old_pos.volume,
                    'new': new_pos.volume,
                    'delta': new_pos.volume - old_pos.volume
                }]
            }
        
        return None
    
    def initialize_position_states(self):
        """Инициализировать состояния позиций при старте"""
        # Инициализируем состояния донорских позиций
        donor_positions = self.get_donor_positions()
        self.donor_position_states = {pos.ticket: pos for pos in donor_positions}
        
        # Инициализируем состояния клиентских позиций
        client_positions = self.get_client_positions()
        self.client_position_states = {pos.ticket: pos for pos in client_positions}
    
    def remove_donor_position_state(self, ticket: int):
        """Удалить состояние позиции донора"""
        if ticket in self.donor_position_states:
            del self.donor_position_states[ticket]
    
    def remove_client_position_state(self, ticket: int):
        """Удалить состояние позиции клиента"""
        if ticket in self.client_position_states:
            del self.client_position_states[ticket]

