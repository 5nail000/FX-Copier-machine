"""
Менеджер для управления ордерами
"""
import MetaTrader5 as mt5
import time
import logging
from typing import Optional
from config import OrderConfig, LotConfig
from utils import (
    calculate_lot_size,
    is_price_better_or_equal,
    calculate_limit_price,
    get_point_size
)
from position_monitor import PositionInfo
from datetime import datetime


class OrderManager:
    """Класс для управления ордерами на клиентском аккаунте"""
    
    def __init__(self, order_config: OrderConfig, lot_config: LotConfig, terminal_manager=None):
        self.order_config = order_config
        self.lot_config = lot_config
        self.terminal_manager = terminal_manager
        self.logger = logging.getLogger(__name__)
    
    def get_account_balance(self) -> float:
        """Получить баланс текущего аккаунта"""
        if not self.terminal_manager:
            return 0.0
        
        account_info = self.terminal_manager.get_client_account_info()
        if account_info:
            return account_info.get('balance', 0.0)
        return 0.0
    
    def place_limit_order(
        self,
        symbol: str,
        order_type: int,
        volume: float,
        original_price: float,
        donor_balance: Optional[float] = None,
        client_balance: Optional[float] = None,
        magic: Optional[int] = None,  # Magic number (если None, используется из конфига)
        sl: Optional[float] = None,  # Stop Loss
        tp: Optional[float] = None  # Take Profit
    ) -> Optional[int]:
        """
        Разместить лимитный ордер с адаптивным отступом
        
        Args:
            symbol: Символ
            order_type: Тип ордера (mt5.ORDER_TYPE_BUY_LIMIT или mt5.ORDER_TYPE_SELL_LIMIT)
            volume: Объем донорской позиции
            original_price: Оригинальная цена входа
            donor_balance: Баланс донорского счета (опционально)
            client_balance: Баланс клиентского счета (опционально)
            
        Returns:
            Тикет ордера или None при ошибке
        """
        if not self.terminal_manager:
            self.logger.error("Terminal manager не установлен")
            return None
        
        # Проверяем и выбираем символ на клиентском аккаунте
        symbol_data = self.terminal_manager.check_and_select_client_symbol(symbol)
        if symbol_data is None:
            self.logger.error(f"❌ Символ {symbol} не найден или недоступен на клиентском аккаунте")
            self.logger.error(f"   Убедитесь, что символ {symbol} доступен на клиентском брокере")
            return None
        
        # Получаем тик для получения актуальной цены
        tick_data = self.terminal_manager.get_client_symbol_tick(symbol)
        if tick_data is None:
            self.logger.warning(f"Не удалось получить тик для {symbol}")
            return None
        
        # Определяем рыночную цену в зависимости от типа ордера
        # Для BUY_LIMIT используем bid (цена покупки), чтобы получить лучшую цену
        # Для SELL_LIMIT используем ask (цена продажи)
        bid_price = tick_data['bid']
        ask_price = tick_data['ask']
        
        if order_type == mt5.ORDER_TYPE_BUY_LIMIT:
            market_price = ask_price  # Для BUY_LIMIT используем ask
        else:
            market_price = bid_price  # Для SELL_LIMIT используем bid
        
        # Рассчитываем размер лота
        if client_balance is None:
            client_balance = self.get_account_balance()
        if donor_balance is None:
            # Если баланс донора не передан, используем баланс клиента (для режима fixed это не критично)
            donor_balance = client_balance
        
        lot = calculate_lot_size(
            donor_lot=volume,
            mode=self.lot_config.mode,
            value=self.lot_config.value,
            min_lot=self.lot_config.min_lot,
            max_lot=self.lot_config.max_lot,
            donor_balance=donor_balance,
            client_balance=client_balance
        )
        
        # Получаем информацию о символе для определения point
        digits = symbol_data.get('digits', 5)
        point = symbol_data.get('point', 0.00001)
        
        # Начинаем с отступа от рыночной цены (из конфигурации)
        current_offset = self.order_config.limit_offset_points * point
        
        # Отладочная информация
        if order_type == mt5.ORDER_TYPE_BUY_LIMIT:
            self.logger.debug(f"BUY_LIMIT: bid={bid_price:.5f}, ask={ask_price:.5f}, original={original_price:.5f}, market={market_price:.5f}, point={point}")
        else:
            self.logger.debug(f"SELL_LIMIT: bid={bid_price:.5f}, ask={ask_price:.5f}, original={original_price:.5f}, market={market_price:.5f}, point={point}")
        
        for attempt in range(self.order_config.max_retries):
            # Рассчитываем цену лимитного ордера
            # market_price: ask для BUY_LIMIT, bid для SELL_LIMIT
            limit_price = calculate_limit_price(
                order_type=order_type,
                market_price=market_price,  # ask для BUY_LIMIT, bid для SELL_LIMIT
                original_price=original_price,
                offset=current_offset,
                symbol=symbol,
                digits=digits,
                point=point,
                bid_price=bid_price,  # Для проверки правил MT5 (BUY_LIMIT должен быть < bid)
                ask_price=ask_price  # Для проверки правил MT5 (SELL_LIMIT должен быть > ask)
            )
            
            self.logger.debug(f"Попытка {attempt + 1}: limit_price={limit_price:.5f}, offset={current_offset:.{digits}f}, market={market_price:.5f}")
            
            # Проверяем, что цена не хуже оригинальной
            if not is_price_better_or_equal(order_type, limit_price, original_price, point):
                self.logger.debug(f"Попытка {attempt + 1}: Цена {limit_price} хуже оригинальной {original_price}, увеличиваем отступ на один пункт ({point})")
                current_offset += point  # Увеличиваем на один пункт (минимальная единица символа)
                continue
            
            # Подготавливаем запрос на размещение ордера
            request = {
                "action": mt5.TRADE_ACTION_PENDING,
                "symbol": symbol,
                "volume": lot,
                "type": order_type,
                "price": limit_price,
                "deviation": 20,
                "magic": magic if magic is not None else self.order_config.magic,
                "comment": "Copied order",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            
            # Добавляем SL/TP, если они указаны и включено копирование
            if self.order_config.copy_sl_tp:
                if sl is not None and sl > 0:
                    request["sl"] = sl
                if tp is not None and tp > 0:
                    request["tp"] = tp
            
            # Отправляем ордер через terminal_manager
            result_data = self.terminal_manager.place_client_order(request)
            
            if result_data is None:
                self.logger.debug("Ошибка отправки ордера, увеличиваем отступ на один пункт")
                current_offset += point  # Увеличиваем на один пункт
                continue
            
            retcode = result_data.get('retcode')
            
            if retcode == mt5.TRADE_RETCODE_DONE:
                order_ticket = result_data.get('order')
                # Для лимитных ордеров position_id может быть не установлен сразу
                # Но мы получим его при следующей проверке ордера
                self.logger.info(f"Лимитный ордер размещен: ticket={order_ticket}, price={limit_price}, lot={lot}")
                return order_ticket
            elif retcode == mt5.TRADE_RETCODE_INVALID_PRICE:
                self.logger.debug(f"Попытка {attempt + 1}: Неверная цена {limit_price:.{digits}f}, увеличиваем отступ на один пункт ({point})")
                current_offset += point  # Увеличиваем на один пункт
                continue  # Продолжаем с новым offset
            else:
                comment = result_data.get('comment', 'Неизвестная ошибка')
                self.logger.debug(f"Ошибка размещения ордера: {retcode}, {comment}, увеличиваем отступ на один пункт ({point})")
                current_offset += point  # Увеличиваем на один пункт
                continue  # Продолжаем с новым offset
        
        self.logger.error(f"Не удалось разместить лимитный ордер после {self.order_config.max_retries} попыток")
        return None
    
    def wait_for_order_fill(self, order_ticket: int, timeout: float = 60.0) -> bool:
        """
        Ожидать исполнения ордера
        
        Args:
            order_ticket: Тикет ордера
            timeout: Таймаут ожидания в секундах
            
        Returns:
            True если ордер исполнен
        """
        if not self.terminal_manager:
            return False
        
        start_time = time.time()
        max_wait_time = min(timeout, 10.0)  # Максимум 10 секунд ожидания
        
        while time.time() - start_time < max_wait_time:
            # Проверяем, есть ли ордер в списке
            order_data = self.terminal_manager.get_client_order_by_ticket(order_ticket)
            if order_data is None:
                # Ордер больше не в списке - возможно исполнен
                # Даем небольшую задержку для обработки MT5
                time.sleep(0.2)
                # Проверяем позиции
                positions = self.terminal_manager.get_client_positions()
                if positions:
                    # Есть позиции - ордер скорее всего исполнен
                    return True
                # Если позиций нет, возможно ордер был отклонен
                return False
            
            time.sleep(0.2)
        
        # Если таймаут истек, проверяем еще раз
        order_data = self.terminal_manager.get_client_order_by_ticket(order_ticket)
        if order_data is None:
            time.sleep(0.2)
            positions = self.terminal_manager.get_client_positions()
            return len(positions) > 0
        
        return False
    
    def close_position_by_opposite_order(
        self,
        symbol: str,
        position_volume: float,
        position_type: int,
        original_close_price: float,
        client_ticket: int
    ) -> Optional[int]:
        """
        Закрыть позицию встречным ордером через лимитный ордер и TRADE_ACTION_CLOSE_BY
        
        Args:
            symbol: Символ
            position_volume: Объем позиции
            position_type: Тип позиции (mt5.POSITION_TYPE_BUY или mt5.POSITION_TYPE_SELL)
            original_close_price: Оригинальная цена закрытия
            client_ticket: Тикет исходной позиции для закрытия
            
        Returns:
            Тикет ордера закрытия или None
        """
        # Определяем тип встречного ордера
        if position_type == mt5.POSITION_TYPE_BUY:
            # Для длинной позиции нужен лимит на продажу
            order_type = mt5.ORDER_TYPE_SELL_LIMIT
        else:
            # Для короткой позиции нужен лимит на покупку
            order_type = mt5.ORDER_TYPE_BUY_LIMIT
        
        # Размещаем лимитный ордер для закрытия
        opposite_order_ticket = self.place_limit_order(
            symbol=symbol,
            order_type=order_type,
            volume=position_volume,
            original_price=original_close_price
        )
        
        return opposite_order_ticket
    
    def place_pending_order(
        self,
        symbol: str,
        order_type: int,
        volume: float,
        price: float,
        donor_balance: Optional[float] = None,
        client_balance: Optional[float] = None,
        magic: Optional[int] = None,
        sl: Optional[float] = None,
        tp: Optional[float] = None
    ) -> Optional[int]:
        """
        Разместить отложенный ордер (лимитный, стоп или стоп-лимитный)
        
        Args:
            symbol: Символ
            order_type: Тип ордера (ORDER_TYPE_BUY_LIMIT, ORDER_TYPE_SELL_LIMIT, ORDER_TYPE_BUY_STOP, ORDER_TYPE_SELL_STOP, etc.)
            volume: Объем
            price: Цена ордера
            donor_balance: Баланс донорского счета (опционально)
            client_balance: Баланс клиентского счета (опционально)
            magic: Magic number (если None, используется из конфига)
            sl: Stop Loss (опционально)
            tp: Take Profit (опционально)
            
        Returns:
            Тикет ордера или None при ошибке
        """
        if not self.terminal_manager:
            self.logger.error("Terminal manager не установлен")
            return None
        
        # Проверяем и выбираем символ на клиентском аккаунте
        symbol_data = self.terminal_manager.check_and_select_client_symbol(symbol)
        if symbol_data is None:
            self.logger.error(f"❌ Символ {symbol} не найден или недоступен на клиентском аккаунте")
            return None
        
        # Рассчитываем размер лота
        if client_balance is None:
            client_balance = self.get_account_balance()
        if donor_balance is None:
            donor_balance = client_balance
        
        lot = calculate_lot_size(
            donor_lot=volume,
            mode=self.lot_config.mode,
            value=self.lot_config.value,
            min_lot=self.lot_config.min_lot,
            max_lot=self.lot_config.max_lot,
            donor_balance=donor_balance,
            client_balance=client_balance
        )
        
        # Подготавливаем запрос на размещение ордера
        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": symbol,
            "volume": lot,
            "type": order_type,
            "price": price,
            "deviation": 20,
            "magic": magic if magic is not None else self.order_config.magic,
            "comment": "Copied pending order",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        # Добавляем SL/TP, если они указаны и включено копирование
        if self.order_config.copy_sl_tp:
            if sl is not None and sl > 0:
                request["sl"] = sl
            if tp is not None and tp > 0:
                request["tp"] = tp
        
        # Отправляем ордер через terminal_manager
        result_data = self.terminal_manager.place_client_order(request)
        
        if result_data is None:
            self.logger.error(f"Ошибка отправки отложенного ордера для {symbol}")
            return None
        
        retcode = result_data.get('retcode')
        
        if retcode == mt5.TRADE_RETCODE_DONE:
            order_ticket = result_data.get('order')
            self.logger.info(f"Отложенный ордер размещен: ticket={order_ticket}, type={order_type}, price={price}, lot={lot}")
            return order_ticket
        else:
            self.logger.error(f"Ошибка размещения отложенного ордера: retcode={retcode}, {result_data.get('comment', '')}")
            return None
    
    def place_market_order(
        self,
        symbol: str,
        order_type: int,  # mt5.ORDER_TYPE_BUY или mt5.ORDER_TYPE_SELL
        volume: float,
        donor_balance: Optional[float] = None,
        client_balance: Optional[float] = None,
        magic: Optional[int] = None,
        sl: Optional[float] = None,  # Stop Loss
        tp: Optional[float] = None  # Take Profit
    ) -> Optional[int]:
        """
        Разместить рыночный ордер (мгновенное открытие позиции)
        
        Args:
            symbol: Символ
            order_type: Тип ордера (mt5.ORDER_TYPE_BUY или mt5.ORDER_TYPE_SELL)
            volume: Объем позиции
            donor_balance: Баланс донорского счета (опционально)
            client_balance: Баланс клиентского счета (опционально)
            magic: Magic number (если None, используется из конфига)
            
        Returns:
            Тикет сделки или None при ошибке
        """
        if not self.terminal_manager:
            self.logger.error("Terminal manager не установлен")
            return None
        
        # Проверяем и выбираем символ на клиентском аккаунте
        symbol_data = self.terminal_manager.check_and_select_client_symbol(symbol)
        if symbol_data is None:
            self.logger.error(f"❌ Символ {symbol} не найден или недоступен на клиентском аккаунте")
            return None
        
        # Получаем тик для получения актуальной рыночной цены
        tick_data = self.terminal_manager.get_client_symbol_tick(symbol)
        if tick_data is None:
            self.logger.warning(f"Не удалось получить тик для {symbol}")
            return None
        
        # Рассчитываем размер лота
        if client_balance is None:
            client_balance = self.get_account_balance()
        if donor_balance is None:
            donor_balance = client_balance
        
        lot = calculate_lot_size(
            donor_lot=volume,
            mode=self.lot_config.mode,
            value=self.lot_config.value,
            min_lot=self.lot_config.min_lot,
            max_lot=self.lot_config.max_lot,
            donor_balance=donor_balance,
            client_balance=client_balance
        )
        
        # Определяем цену в зависимости от типа ордера
        if order_type == mt5.ORDER_TYPE_BUY:
            price = tick_data['ask']  # Для покупки используем ask
        else:
            price = tick_data['bid']  # Для продажи используем bid
        
        # Подготавливаем запрос на размещение рыночного ордера
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": order_type,
            "price": price,
            "deviation": 20,
            "magic": magic if magic is not None else self.order_config.magic,
            "comment": "Copied order (market)",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        # Добавляем SL/TP, если они указаны и включено копирование
        if self.order_config.copy_sl_tp:
            if sl is not None and sl > 0:
                request["sl"] = sl
            if tp is not None and tp > 0:
                request["tp"] = tp
        
        # Отправляем ордер через terminal_manager
        result_data = self.terminal_manager.place_client_order(request)
        
        if result_data is None:
            self.logger.error("Ошибка отправки рыночного ордера")
            return None
        
        retcode = result_data.get('retcode')
        
        if retcode == mt5.TRADE_RETCODE_DONE:
            deal_ticket = result_data.get('deal')
            self.logger.info(f"✓ Рыночный ордер исполнен: deal={deal_ticket}, price={price:.5f}, lot={lot}")
            return deal_ticket
        else:
            comment = result_data.get('comment', 'Неизвестная ошибка')
            self.logger.error(f"❌ Ошибка размещения рыночного ордера: {retcode}, {comment}")
            return None
    
    def close_position_by_market(
        self,
        position_ticket: int,
        symbol: str,
        position_type: int,
        volume: float
    ) -> bool:
        """
        Закрыть позицию мгновенно по рыночной цене
        
        Args:
            position_ticket: Тикет позиции для закрытия
            symbol: Символ
            position_type: Тип позиции (mt5.POSITION_TYPE_BUY или mt5.POSITION_TYPE_SELL)
            volume: Объем позиции
            
        Returns:
            True если закрытие успешно
        """
        if not self.terminal_manager:
            return False
        
        # Получаем текущую рыночную цену
        tick_data = self.terminal_manager.get_client_symbol_tick(symbol)
        if tick_data is None:
            self.logger.warning(f"Не удалось получить тик для {symbol}")
            return False
        
        # Определяем тип ордера для закрытия (противоположный типу позиции)
        if position_type == mt5.POSITION_TYPE_BUY:
            # Закрываем длинную позицию - продаем по bid
            order_type = mt5.ORDER_TYPE_SELL
            price = tick_data['bid']
        else:
            # Закрываем короткую позицию - покупаем по ask
            order_type = mt5.ORDER_TYPE_BUY
            price = tick_data['ask']
        
        # Подготавливаем запрос на закрытие позиции
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": position_ticket,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "deviation": 20,
            "magic": self.order_config.magic,
            "comment": "Close position (market)",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        result_data = self.terminal_manager.place_client_order(request)
        if result_data and result_data.get('retcode') == mt5.TRADE_RETCODE_DONE:
            deal_ticket = result_data.get('deal')
            self.logger.info(f"✓ Позиция {position_ticket} закрыта по маркету: deal={deal_ticket}, price={price:.5f}")
            return True
        else:
            retcode = result_data.get('retcode') if result_data else 'None'
            self.logger.error(f"❌ Ошибка закрытия позиции по маркету: retcode={retcode}")
            return False
    
    def close_position_by_opposite_position(
        self,
        original_position_ticket: int,
        opposite_position_ticket: int
    ) -> bool:
        """
        Закрыть позицию встречной позицией через TRADE_ACTION_CLOSE_BY
        
        Args:
            original_position_ticket: Тикет исходной позиции (которую закрываем)
            opposite_position_ticket: Тикет встречной позиции (которой закрываем)
            
        Returns:
            True если закрытие успешно
        """
        if not self.terminal_manager:
            return False
        
        request = {
            "action": mt5.TRADE_ACTION_CLOSE_BY,
            "position": original_position_ticket,  # Позиция, которую закрываем
            "position_by": opposite_position_ticket,  # Встречная позиция
            "deviation": 20,
            "type_time": mt5.ORDER_TIME_GTC,
        }
        
        result_data = self.terminal_manager.place_client_order(request)
        if result_data and result_data.get('retcode') == mt5.TRADE_RETCODE_DONE:
            self.logger.info(f"✓ Позиция {original_position_ticket} закрыта встречной позицией {opposite_position_ticket} через CLOSE_BY")
            return True
        else:
            retcode = result_data.get('retcode') if result_data else 'None'
            self.logger.error(f"❌ Ошибка закрытия через CLOSE_BY: retcode={retcode}")
            return False
    
    def optimize_order_price(
        self,
        order_ticket: int,
        symbol: str,
        order_type: int,
        original_price: float
    ) -> bool:
        """
        Оптимизировать цену уже размещенного ордера, если появилась возможность улучшить
        
        Args:
            order_ticket: Тикет ордера
            symbol: Символ
            order_type: Тип ордера
            original_price: Оригинальная цена
            
        Returns:
            True если ордер был оптимизирован
        """
        if not self.terminal_manager:
            return False
        
        # Получаем информацию об ордере
        order_data = self.terminal_manager.get_client_order_by_ticket(order_ticket)
        if not order_data:
            return False  # Ордер уже исполнен или удален
        
        current_price = order_data.get('price_open')
        if not current_price:
            return False
        
        # Получаем текущие рыночные цены (актуальные на момент оптимизации)
        tick_data = self.terminal_manager.get_client_symbol_tick(symbol)
        if not tick_data:
            return False
        
        # Определяем рыночные цены
        bid_price = tick_data['bid']
        ask_price = tick_data['ask']
        
        if order_type == mt5.ORDER_TYPE_BUY_LIMIT:
            market_price = ask_price  # Для BUY_LIMIT используем ask
        else:
            market_price = bid_price  # Для SELL_LIMIT используем bid
        
        # Получаем информацию о символе
        symbol_data = self.terminal_manager.check_and_select_client_symbol(symbol)
        if not symbol_data:
            return False
        
        digits = symbol_data.get('digits', 5)
        point = symbol_data.get('point', 0.00001)
        
        # Приоритет: на каждом тике сдвигаем ордер на один пункт ближе к рыночной цене (если ордер не активизировался)
        # Определяем целевую цену (к рыночной или к оригинальной)
        if self.order_config.optimize_to_market:
            # К рыночной цене
            if order_type == mt5.ORDER_TYPE_BUY_LIMIT:
                target_price = ask_price  # Для BUY_LIMIT цель - ask
            else:
                target_price = bid_price  # Для SELL_LIMIT цель - bid
        else:
            # К оригинальной цене
            target_price = original_price
        
        price_improved = False
        
        # Пытаемся сдвинуть на один пункт ближе к целевой цене
        if order_type == mt5.ORDER_TYPE_BUY_LIMIT:
            # Для BUY_LIMIT: двигаем вверх (к ask или оригинальной) на один пункт
            new_price = current_price + point
            if new_price > current_price:  # Базовое условие - цена увеличилась
                # Проверяем ограничения по оригинальной цене
                if self.order_config.optimize_to_market:
                    # К рыночной: пытаемся двигать к ask, пусть терминал решит, разрешить или нет
                    # Проверяем, что движемся к ask (целевой цене)
                    if abs(new_price - ask_price) < abs(current_price - ask_price):
                        optimal_price = new_price
                        price_improved = True
                else:
                    # К оригинальной: не выше оригинальной и строго ниже bid (правило MT5)
                    if new_price <= original_price and new_price < bid_price:
                        # Проверяем, что движемся к оригинальной цене
                        if abs(new_price - original_price) < abs(current_price - original_price):
                            optimal_price = new_price
                            price_improved = True
        else:
            # Для SELL_LIMIT: двигаем вниз (к bid или оригинальной) на один пункт
            new_price = current_price - point
            if new_price < current_price:  # Базовое условие - цена уменьшилась
                # Проверяем ограничения по оригинальной цене
                if self.order_config.optimize_to_market:
                    # К рыночной: пытаемся двигать к bid, пусть терминал решит, разрешить или нет
                    # Проверяем, что движемся к bid (целевой цене)
                    if abs(new_price - bid_price) < abs(current_price - bid_price):
                        optimal_price = new_price
                        price_improved = True
                else:
                    # К оригинальной: не ниже оригинальной и строго выше ask (правило MT5)
                    if new_price >= original_price and new_price > ask_price:
                        # Проверяем, что движемся к оригинальной цене
                        if abs(new_price - original_price) < abs(current_price - original_price):
                            optimal_price = new_price
                            price_improved = True
        
        # Если сдвиг на один пункт не возможен, пытаемся установить оптимальную цену (старая логика как fallback)
        if not price_improved:
            if not self.order_config.optimize_to_market:
                # К оригинальной цене (fallback)
                optimal_price = calculate_limit_price(
                    order_type=order_type,
                    market_price=market_price,
                    original_price=original_price,
                    offset=0.0,
                    symbol=symbol,
                    digits=digits,
                    point=point,
                    bid_price=bid_price,
                    ask_price=ask_price
                )
                
                if order_type == mt5.ORDER_TYPE_BUY_LIMIT:
                    if (optimal_price > current_price and 
                        optimal_price <= original_price and 
                        optimal_price < bid_price):
                        price_improved = True
                else:
                    if (optimal_price < current_price and 
                        optimal_price >= original_price and 
                        optimal_price > ask_price):
                        price_improved = True
            else:
                # К рыночной цене (fallback)
                if order_type == mt5.ORDER_TYPE_BUY_LIMIT:
                    optimal_price = ask_price - point
                    if optimal_price > original_price:
                        optimal_price = original_price
                    if optimal_price >= bid_price:
                        optimal_price = bid_price - point
                    
                    if (optimal_price > current_price and 
                        optimal_price <= original_price and 
                        optimal_price < bid_price):
                        price_improved = True
                else:
                    optimal_price = bid_price + point
                    if optimal_price < original_price:
                        optimal_price = original_price
                    if optimal_price <= ask_price:
                        optimal_price = ask_price + point
                    
                    if (optimal_price < current_price and 
                        optimal_price >= original_price and 
                        optimal_price > ask_price):
                        price_improved = True
        
        if not price_improved:
            # Логируем, почему оптимизация не произошла (для отладки)
            if order_type == mt5.ORDER_TYPE_BUY_LIMIT:
                self.logger.debug(f"BUY_LIMIT {order_ticket} не оптимизирован: current={current_price:.{digits}f}, target={target_price:.{digits}f}, ask={ask_price:.{digits}f}, bid={bid_price:.{digits}f}, original={original_price:.{digits}f}")
            else:
                self.logger.debug(f"SELL_LIMIT {order_ticket} не оптимизирован: current={current_price:.{digits}f}, target={target_price:.{digits}f}, bid={bid_price:.{digits}f}, ask={ask_price:.{digits}f}, original={original_price:.{digits}f}")
            return False
        
        # Модифицируем ордер
        request = {
            "action": mt5.TRADE_ACTION_MODIFY,
            "order": order_ticket,
            "symbol": symbol,
            "price": optimal_price,
            "deviation": 20,
            "type_time": mt5.ORDER_TIME_GTC,
        }
        
        result_data = self.terminal_manager.place_client_order(request)
        if result_data and result_data.get('retcode') == mt5.TRADE_RETCODE_DONE:
            self.logger.info(f"✓ Ордер {order_ticket} оптимизирован: {current_price:.{digits}f} → {optimal_price:.{digits}f}")
            return True
        
        return False
    
    def optimize_close_order_price(
        self,
        order_ticket: int,
        symbol: str,
        order_type: int,
        original_close_price: float
    ) -> bool:
        """
        Оптимизировать цену закрывающего ордера, приближая к текущей рыночной цене
        
        Args:
            order_ticket: Тикет ордера
            symbol: Символ
            order_type: Тип ордера (ORDER_TYPE_BUY_LIMIT или ORDER_TYPE_SELL_LIMIT)
            original_close_price: Оригинальная цена закрытия (не должна быть хуже)
            
        Returns:
            True если ордер был оптимизирован
        """
        if not self.terminal_manager:
            return False
        
        # Получаем информацию об ордере
        order_data = self.terminal_manager.get_client_order_by_ticket(order_ticket)
        if not order_data:
            return False  # Ордер уже исполнен или удален
        
        current_price = order_data.get('price_open')
        if not current_price:
            return False
        
        # Получаем текущие рыночные цены (актуальные на момент оптимизации)
        tick_data = self.terminal_manager.get_client_symbol_tick(symbol)
        if not tick_data:
            return False
        
        # Определяем рыночные цены
        bid_price = tick_data['bid']
        ask_price = tick_data['ask']
        
        # Получаем информацию о символе
        symbol_data = self.terminal_manager.check_and_select_client_symbol(symbol)
        if not symbol_data:
            return False
        
        digits = symbol_data.get('digits', 5)
        point = symbol_data.get('point', 0.00001)
        
        # Приоритет: на каждом тике сдвигаем ордер на один пункт ближе к целевой цене
        # Определяем целевую цену (к рыночной или к оригинальной)
        if self.order_config.optimize_to_market:
            # К рыночной цене
            if order_type == mt5.ORDER_TYPE_BUY_LIMIT:
                target_price = ask_price  # Для BUY_LIMIT цель - ask
            else:
                target_price = bid_price  # Для SELL_LIMIT цель - bid
        else:
            # К оригинальной цене закрытия
            target_price = original_close_price
        
        price_improved = False
        
        # Пытаемся сдвинуть на один пункт ближе к целевой цене
        if order_type == mt5.ORDER_TYPE_BUY_LIMIT:
            # Для BUY_LIMIT: двигаем вверх (к ask или оригинальной) на один пункт
            new_price = current_price + point
            if new_price > current_price:  # Базовое условие - цена увеличилась
                # Проверяем ограничения по оригинальной цене
                if self.order_config.optimize_to_market:
                    # К рыночной: пытаемся двигать к ask, пусть терминал решит, разрешить или нет
                    # Проверяем, что движемся к ask (целевой цене)
                    if abs(new_price - ask_price) < abs(current_price - ask_price):
                        optimal_price = new_price
                        price_improved = True
                else:
                    # К оригинальной: не выше оригинальной и строго ниже bid (правило MT5)
                    if new_price <= original_close_price and new_price < bid_price:
                        # Проверяем, что движемся к оригинальной цене
                        if abs(new_price - original_close_price) < abs(current_price - original_close_price):
                            optimal_price = new_price
                            price_improved = True
        else:
            # Для SELL_LIMIT: двигаем вниз (к bid или оригинальной) на один пункт
            new_price = current_price - point
            if new_price < current_price:  # Базовое условие - цена уменьшилась
                # Проверяем ограничения по оригинальной цене
                if self.order_config.optimize_to_market:
                    # К рыночной: пытаемся двигать к bid, пусть терминал решит, разрешить или нет
                    # Проверяем, что движемся к bid (целевой цене)
                    if abs(new_price - bid_price) < abs(current_price - bid_price):
                        optimal_price = new_price
                        price_improved = True
                else:
                    # К оригинальной: не ниже оригинальной и строго выше ask (правило MT5)
                    if new_price >= original_close_price and new_price > ask_price:
                        # Проверяем, что движемся к оригинальной цене
                        if abs(new_price - original_close_price) < abs(current_price - original_close_price):
                            optimal_price = new_price
                            price_improved = True
        
        # Если сдвиг на один пункт не возможен, пытаемся установить оптимальную цену (старая логика как fallback)
        if not price_improved:
            if not self.order_config.optimize_to_market:
                # К оригинальной цене закрытия (fallback)
                optimal_price = calculate_limit_price(
                    order_type=order_type,
                    market_price=ask_price if order_type == mt5.ORDER_TYPE_BUY_LIMIT else bid_price,
                    original_price=original_close_price,
                    offset=0.0,
                    symbol=symbol,
                    digits=digits,
                    point=point,
                    bid_price=bid_price,
                    ask_price=ask_price
                )
                
                if order_type == mt5.ORDER_TYPE_BUY_LIMIT:
                    if (optimal_price > current_price and 
                        optimal_price <= original_close_price and 
                        optimal_price < bid_price):
                        price_improved = True
                else:
                    if (optimal_price < current_price and 
                        optimal_price >= original_close_price and 
                        optimal_price > ask_price):
                        price_improved = True
            else:
                # К рыночной цене (fallback)
                if order_type == mt5.ORDER_TYPE_BUY_LIMIT:
                    optimal_price = ask_price - point
                if optimal_price > original_close_price:
                    optimal_price = original_close_price
                if optimal_price >= bid_price:
                    optimal_price = bid_price - point
                
                if (optimal_price > current_price and 
                    optimal_price <= original_close_price and 
                    optimal_price < bid_price):
                    price_improved = True
        else:
            # К рыночной цене (fallback) для SELL_LIMIT
            optimal_price = bid_price + point
            if optimal_price < original_close_price:
                optimal_price = original_close_price
            if optimal_price <= ask_price:
                optimal_price = ask_price + point
            
            if (optimal_price < current_price and 
                optimal_price >= original_close_price and 
                optimal_price > ask_price):
                price_improved = True
        
        if not price_improved:
            # Логируем, почему оптимизация не произошла (для отладки)
            if order_type == mt5.ORDER_TYPE_BUY_LIMIT:
                self.logger.debug(f"BUY_LIMIT закрытие {order_ticket} не оптимизирован: current={current_price:.{digits}f}, target={target_price:.{digits}f}, ask={ask_price:.{digits}f}, bid={bid_price:.{digits}f}, original_close={original_close_price:.{digits}f}")
            else:
                self.logger.debug(f"SELL_LIMIT закрытие {order_ticket} не оптимизирован: current={current_price:.{digits}f}, target={target_price:.{digits}f}, bid={bid_price:.{digits}f}, ask={ask_price:.{digits}f}, original_close={original_close_price:.{digits}f}")
            return False
        
        # Округляем цену
        optimal_price = round(optimal_price, digits)
        
        # Модифицируем ордер
        request = {
            "action": mt5.TRADE_ACTION_MODIFY,
            "order": order_ticket,
            "symbol": symbol,
            "price": optimal_price,
            "deviation": 20,
            "type_time": mt5.ORDER_TIME_GTC,
        }
        
        result_data = self.terminal_manager.place_client_order(request)
        if result_data and result_data.get('retcode') == mt5.TRADE_RETCODE_DONE:
            self.logger.info(f"✓ Ордер закрытия {order_ticket} оптимизирован: {current_price:.5f} → {optimal_price:.5f} (к текущей рыночной цене)")
            return True
        
        return False
    
    def get_position_by_symbol(self, symbol: str) -> Optional[PositionInfo]:
        """
        Получить позицию по символу на клиентском аккаунте
        
        Args:
            symbol: Символ
            
        Returns:
            Информация о позиции или None
        """
        if not self.terminal_manager:
            return None
        
            position_data = self.terminal_manager.get_client_position_by_symbol(symbol)
            if position_data:
                return PositionInfo(
                    ticket=position_data['ticket'],
                    symbol=position_data['symbol'],
                    type=position_data['type'],
                    volume=position_data['volume'],
                    price_open=position_data['price_open'],
                    price_current=position_data['price_current'],
                    profit=position_data['profit'],
                    time=datetime.fromtimestamp(position_data['time'])
                )
        return None
    
    def cancel_order(self, order_ticket: int) -> bool:
        """
        Отменить лимитный ордер
        
        Args:
            order_ticket: Тикет ордера для отмены
            
        Returns:
            True если ордер успешно отменен, False в противном случае
        """
        if not self.terminal_manager:
            return False
        
        # Подготавливаем запрос на удаление ордера
        request = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": order_ticket,
        }
        
        result_data = self.terminal_manager.place_client_order(request)
        if result_data and result_data.get('retcode') == mt5.TRADE_RETCODE_DONE:
            self.logger.info(f"✓ Ордер {order_ticket} отменен")
            return True
        else:
            retcode = result_data.get('retcode') if result_data else 'None'
            self.logger.error(f"❌ Ошибка отмены ордера {order_ticket}: retcode={retcode}")
            return False
