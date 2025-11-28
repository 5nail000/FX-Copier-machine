"""
Вспомогательные функции
"""
import MetaTrader5 as mt5
from typing import Optional, Tuple


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
    min_lot: float,
    max_lot: float,
    donor_balance: float,
    client_balance: float
) -> float:
    """
    Рассчитать размер лота для клиентского ордера
    
    Args:
        donor_lot: Размер лота донорского ордера
        mode: Режим расчета ('fixed', 'proportion', 'autolot')
        value: Значение для режима (для fixed - размер, для proportion - коэффициент)
        min_lot: Минимальный размер лота
        max_lot: Максимальный размер лота
        donor_balance: Баланс донорского счета
        client_balance: Баланс клиентского счета
        
    Returns:
        Рассчитанный размер лота
    """
    if mode == 'fixed':
        lot = value if value is not None else donor_lot
    elif mode == 'proportion':
        coefficient = value if value is not None else 1.0
        lot = donor_lot * coefficient
    elif mode == 'autolot':
        # Автолот на основе соотношения балансов
        if donor_balance > 0:
            lot = donor_lot * (client_balance / donor_balance)
        else:
            lot = donor_lot
    else:
        lot = donor_lot
    
    # Ограничение размера лота
    lot = max(min_lot, min(max_lot, lot))
    
    # Округление до допустимого шага лота (обычно 0.01)
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

