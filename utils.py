"""
Вспомогательные функции
"""
import MetaTrader5 as mt5
from typing import Optional, Tuple
import math

# Константы по умолчанию для размера лота (используются только если брокер не предоставил данные)
DEFAULT_MIN_LOT = 0.01
DEFAULT_MAX_LOT = 100.0
DEFAULT_VOLUME_STEP = 0.01


def get_point_size(symbol: str) -> float:
    """
    Получить размер пункта для символа
    
    Args:
        symbol: Название символа
        
    Returns:
        Размер пункта
    """
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        return 0.0001  # Значение по умолчанию для большинства валютных пар
    
    if symbol_info.digits == 3 or symbol_info.digits == 5:
        return 0.0001
    elif symbol_info.digits == 2 or symbol_info.digits == 4:
        return 0.01
    else:
        return 10 ** (-symbol_info.digits)


def calculate_lot_size(
    donor_lot: float,
    mode: str,
    value: Optional[float],
    donor_balance: float,
    client_balance: float,
    symbol_min_lot: Optional[float] = None,  # Минимальный лот для символа (приоритет)
    symbol_max_lot: Optional[float] = None,   # Максимальный лот для символа (приоритет)
    symbol_volume_step: Optional[float] = None  # Шаг изменения лота для символа (приоритет)
) -> float:
    """
    Рассчитать размер лота для клиентского ордера
    
    Args:
        donor_lot: Размер лота донорского ордера
        mode: Режим расчета ('fixed', 'proportion', 'autolot')
        value: Значение для режима (для fixed - размер, для proportion - коэффициент, для autolot - множитель)
        donor_balance: Баланс донорского счета
        client_balance: Баланс клиентского счета
        symbol_min_lot: Минимальный размер лота для символа (приоритет над константами)
        symbol_max_lot: Максимальный размер лота для символа (приоритет над константами)
        symbol_volume_step: Шаг изменения лота для символа (приоритет над константами)
        
    Returns:
        Рассчитанный размер лота
        
    Формулы:
        - fixed: lot = value
        - proportion: lot = donor_lot * value
        - autolot: lot = (client_balance / 1000) * value
    """
    if mode == 'fixed':
        lot = value if value is not None else donor_lot
    elif mode == 'proportion':
        coefficient = value if value is not None else 1.0
        lot = donor_lot * coefficient
    elif mode == 'autolot':
        # Автолот: lot = (balance/1000) * autoLotValue
        auto_lot_value = value if value is not None else 1.0
        lot = (client_balance / 1000) * auto_lot_value
    else:
        lot = donor_lot
    
    # Используем значения для символа, если они доступны, иначе константы по умолчанию
    effective_min_lot = symbol_min_lot if symbol_min_lot is not None else DEFAULT_MIN_LOT
    effective_max_lot = symbol_max_lot if symbol_max_lot is not None else DEFAULT_MAX_LOT
    effective_volume_step = symbol_volume_step if symbol_volume_step is not None else DEFAULT_VOLUME_STEP
    
    # Ограничение размера лота
    lot = max(effective_min_lot, min(effective_max_lot, lot))
    
    # Округление до допустимого шага лота символа
    if effective_volume_step > 0:
        # Округляем до ближайшего кратного шагу значения
        lot = round(lot / effective_volume_step) * effective_volume_step
        # Убеждаемся, что не вышли за границы после округления
        lot = max(effective_min_lot, min(effective_max_lot, lot))
    else:
        # Если шаг не указан, округляем до 2 знаков
        lot = round(lot, 2)
    
    return lot


def is_price_better_or_equal(
    order_type: int,
    our_price: float,
    original_price: float,
    point: float
) -> bool:
    """
    Проверить, не хуже ли наша цена оригинальной
    
    Args:
        order_type: Тип ордера (mt5.ORDER_TYPE_BUY_LIMIT или mt5.ORDER_TYPE_SELL_LIMIT)
        our_price: Наша цена
        original_price: Оригинальная цена
        point: Размер пункта символа
        
    Returns:
        True если цена не хуже оригинальной
    """
    if order_type == mt5.ORDER_TYPE_BUY_LIMIT:
        # Для покупки лимитом: наша цена должна быть <= оригинальной
        return our_price <= original_price + point * 0.1  # Небольшая погрешность
    elif order_type == mt5.ORDER_TYPE_SELL_LIMIT:
        # Для продажи лимитом: наша цена должна быть >= оригинальной
        return our_price >= original_price - point * 0.1  # Небольшая погрешность
    
    return False


def calculate_limit_price(
    order_type: int,
    market_price: float,  # ask для BUY_LIMIT, bid для SELL_LIMIT
    original_price: float,
    offset: float,
    symbol: str,
    digits: int = 5,
    point: float = 0.00001,
    bid_price: float = None,  # Для проверки правил MT5
    ask_price: float = None   # Для проверки правил MT5
) -> float:
    """
    Рассчитать цену лимитного ордера, максимально близко к оригинальной цене
    
    Args:
        order_type: Тип ордера
        market_price: Текущая рыночная цена (ask для BUY_LIMIT, bid для SELL_LIMIT)
        original_price: Оригинальная цена входа/выхода
        offset: Отступ от рыночной цены
        symbol: Символ (для совместимости)
        digits: Количество знаков после запятой
        point: Размер пункта символа
        bid_price: Цена bid (для проверки правил MT5 для BUY_LIMIT)
        ask_price: Цена ask (для проверки правил MT5 для SELL_LIMIT)
        
    Returns:
        Цена лимитного ордера
    """
    if order_type == mt5.ORDER_TYPE_BUY_LIMIT:
        # Лимит на покупку - цена ДОЛЖНА быть строго ниже bid
        # market_price для BUY_LIMIT = ask (используем ask как reference)
        # Рассчитываем цену от рыночной цены (ask) с отступом
        # Для BUY_LIMIT отодвигаем вниз от ask (уменьшаем цену)
        limit_price_from_market = market_price - offset
        
        # Используем лучшую цену: либо от рыночной с отступом, либо оригинальную (если она лучше/ближе к рынку)
        # Для BUY_LIMIT лучшая цена - это более высокая (ближе к ask)
        if original_price > limit_price_from_market:
            limit_price = original_price
        else:
            limit_price = limit_price_from_market
            
    elif order_type == mt5.ORDER_TYPE_SELL_LIMIT:
        # Лимит на продажу - цена ДОЛЖНА быть строго выше ask
        # market_price для SELL_LIMIT = bid (используем bid как reference)
        # Рассчитываем цену от рыночной цены (bid) с отступом
        # Для SELL_LIMIT отодвигаем вверх от bid (увеличиваем цену)
        limit_price_from_market = market_price + offset
        
        # Используем лучшую цену: либо от рыночной с отступом, либо оригинальную (если она лучше/ближе к рынку)
        # Для SELL_LIMIT лучшая цена - это более низкая (ближе к bid)
        if original_price < limit_price_from_market:
            limit_price = original_price
        else:
            limit_price = limit_price_from_market
    else:
        limit_price = market_price
    
    # Округление до правильного количества знаков
    limit_price = round(limit_price, digits)
    
    return limit_price

