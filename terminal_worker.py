"""
Рабочие процессы для управления терминалами MT5
Каждый процесс работает со своим терминалом независимо
"""
import MetaTrader5 as mt5
import time
from multiprocessing import Queue
from typing import Dict, Any, Optional


def donor_worker(terminal_path: str, command_queue: Queue, result_queue: Queue):
    """
    Рабочий процесс для донорского терминала
    
    Args:
        terminal_path: Путь к терминалу
        command_queue: Очередь команд для выполнения
        result_queue: Очередь для отправки результатов
    """
    # Инициализация MT5 в этом процессе
    if not mt5.initialize(path=terminal_path):
        result_queue.put({
            'status': 'error',
            'message': f"Ошибка инициализации донорского терминала: {mt5.last_error()}"
        })
        return
    
    # Получаем информацию об аккаунте для подтверждения
    account_info = mt5.account_info()
    if account_info is None:
        result_queue.put({
            'status': 'error',
            'message': "Не удалось получить информацию об аккаунте"
        })
        mt5.shutdown()
        return
    
    result_queue.put({
        'status': 'connected',
        'account': account_info.login,
        'balance': account_info.balance,
        'server': account_info.server
    })
    
    try:
        while True:
            try:
                # Получаем команду из очереди (блокирующий вызов)
                command = command_queue.get()
                
                if command['action'] == 'get_positions':
                    positions = mt5.positions_get()
                    if positions is None:
                        result_queue.put({
                            'status': 'ok',
                            'action': 'positions',
                            'data': []
                        })
                    else:
                        # Сериализуем позиции
                        positions_data = []
                        for pos in positions:
                            positions_data.append({
                                'ticket': pos.ticket,
                                'symbol': pos.symbol,
                                'type': pos.type,
                                'volume': pos.volume,
                                'price_open': pos.price_open,
                                'price_current': pos.price_current,
                                'profit': pos.profit,
                                'time': pos.time,
                                'magic': pos.magic,  # Magic number позиции
                                'comment': pos.comment if hasattr(pos, 'comment') else None  # Комментарий позиции
                            })
                        result_queue.put({
                            'status': 'ok',
                            'action': 'positions',
                            'data': positions_data
                        })
                
                elif command['action'] == 'get_account_info':
                    account_info = mt5.account_info()
                    if account_info:
                        result_queue.put({
                            'status': 'ok',
                            'action': 'account_info',
                            'data': {
                                'login': account_info.login,
                                'balance': account_info.balance,
                                'equity': account_info.equity,
                                'margin_free': account_info.margin_free,
                                'currency': account_info.currency,
                                'server': account_info.server
                            }
                        })
                    else:
                        result_queue.put({
                            'status': 'error',
                            'action': 'account_info',
                            'message': 'Не удалось получить информацию об аккаунте'
                        })
                
                elif command['action'] == 'shutdown':
                    break
                
            except Exception as e:
                result_queue.put({
                    'status': 'error',
                    'message': f"Ошибка в донорском процессе: {str(e)}"
                })
    
    finally:
        mt5.shutdown()
        result_queue.put({'status': 'disconnected'})


def client_worker(terminal_path: str, command_queue: Queue, result_queue: Queue, magic_number: Optional[int] = 234000):
    """
    Рабочий процесс для клиентского терминала
    
    Args:
        terminal_path: Путь к терминалу
        command_queue: Очередь команд для выполнения
        result_queue: Очередь для отправки результатов
        magic_number: Магическое число для фильтрации позиций (None = получать все позиции)
    """
    # Инициализация MT5 в этом процессе
    if not mt5.initialize(path=terminal_path):
        result_queue.put({
            'status': 'error',
            'message': f"Ошибка инициализации клиентского терминала: {mt5.last_error()}"
        })
        return
    
    # Получаем информацию об аккаунте для подтверждения
    account_info = mt5.account_info()
    if account_info is None:
        result_queue.put({
            'status': 'error',
            'message': "Не удалось получить информацию об аккаунте"
        })
        mt5.shutdown()
        return
    
    # Проверяем разрешения на торговлю
    trade_allowed = account_info.trade_allowed
    trade_expert = account_info.trade_expert
    
    if not trade_allowed or not trade_expert:
        result_queue.put({
            'status': 'error',
            'message': f"Торговля запрещена: trade_allowed={trade_allowed}, trade_expert={trade_expert}"
        })
        mt5.shutdown()
        return
    
    result_queue.put({
        'status': 'connected',
        'account': account_info.login,
        'balance': account_info.balance,
        'server': account_info.server,
        'trade_allowed': trade_allowed,
        'trade_expert': trade_expert
    })
    
    try:
        while True:
            try:
                # Получаем команду из очереди
                command = command_queue.get()
                
                if command['action'] == 'place_order':
                    request = command['request']
                    result = mt5.order_send(request)
                    
                    if result is None:
                        result_queue.put({
                            'status': 'error',
                            'action': 'order_result',
                            'message': f"Ошибка отправки ордера: {mt5.last_error()}"
                        })
                    else:
                        result_queue.put({
                            'status': 'ok',
                            'action': 'order_result',
                            'data': {
                                'retcode': result.retcode,
                                'deal': result.deal,
                                'order': result.order,
                                'volume': result.volume,
                                'price': result.price,
                                'comment': result.comment,
                                'request_id': result.request_id
                            }
                        })
                
                elif command['action'] == 'get_positions':
                    positions = mt5.positions_get()
                    if positions is None:
                        result_queue.put({
                            'status': 'ok',
                            'action': 'positions',
                            'data': []
                        })
                    else:
                        positions_data = []
                        for pos in positions:
                            # Фильтруем только позиции с нашим magic number
                            # При --copy-donor-magic фильтрация происходит при сопоставлении по magic донорской позиции
                            if magic_number is None or pos.magic == magic_number:
                                positions_data.append({
                                    'ticket': pos.ticket,
                                    'symbol': pos.symbol,
                                    'type': pos.type,
                                    'volume': pos.volume,
                                    'price_open': pos.price_open,
                                    'price_current': pos.price_current,
                                    'profit': pos.profit,
                                    'time': pos.time,
                                    'magic': pos.magic,
                                    'comment': pos.comment if hasattr(pos, 'comment') else None
                                })
                        result_queue.put({
                            'status': 'ok',
                            'action': 'positions',
                            'data': positions_data
                        })
                
                elif command['action'] == 'get_position_by_symbol':
                    symbol = command['symbol']
                    positions = mt5.positions_get(symbol=symbol)
                    if positions and len(positions) > 0:
                        # Ищем позицию (если magic_number == None, берем первую)
                        for pos in positions:
                            if magic_number is None or pos.magic == magic_number:
                                result_queue.put({
                                    'status': 'ok',
                                    'action': 'position',
                                    'data': {
                                        'ticket': pos.ticket,
                                        'symbol': pos.symbol,
                                        'type': pos.type,
                                        'volume': pos.volume,
                                        'price_open': pos.price_open,
                                        'price_current': pos.price_current,
                                        'profit': pos.profit,
                                        'time': pos.time,
                                        'magic': pos.magic,
                                        'comment': pos.comment if hasattr(pos, 'comment') else None
                                    }
                                })
                                break
                        else:
                            # Позиция с нужным magic не найдена
                            result_queue.put({
                                'status': 'ok',
                                'action': 'position',
                                'data': None
                            })
                    else:
                        result_queue.put({
                            'status': 'ok',
                            'action': 'position',
                            'data': None
                        })
                
                elif command['action'] == 'get_position_by_ticket':
                    ticket = command['ticket']
                    positions = mt5.positions_get(ticket=ticket)
                    if positions and len(positions) > 0:
                        pos = positions[0]
                        # Проверяем magic number (если magic_number == None, пропускаем проверку)
                        if magic_number is None or pos.magic == magic_number:
                            result_queue.put({
                                'status': 'ok',
                                'action': 'position',
                                'data': {
                                    'ticket': pos.ticket,
                                    'symbol': pos.symbol,
                                    'type': pos.type,
                                    'volume': pos.volume,
                                    'price_open': pos.price_open,
                                    'price_current': pos.price_current,
                                    'profit': pos.profit,
                                    'time': pos.time
                                }
                            })
                        else:
                            # Позиция найдена, но не с нашим magic number
                            result_queue.put({
                                'status': 'ok',
                                'action': 'position',
                                'data': None
                            })
                    else:
                        result_queue.put({
                            'status': 'ok',
                            'action': 'position',
                            'data': None
                        })
                
                elif command['action'] == 'get_orders':
                    orders = mt5.orders_get()
                    if orders is None:
                        result_queue.put({
                            'status': 'ok',
                            'action': 'orders',
                            'data': []
                        })
                    else:
                        orders_data = []
                        for order in orders:
                            orders_data.append({
                                'ticket': order.ticket,
                                'symbol': order.symbol,
                                'type': order.type,
                                'volume_initial': order.volume_initial,
                                'volume_current': order.volume_current,
                                'price_open': order.price_open,
                                'time_setup': order.time_setup
                            })
                        result_queue.put({
                            'status': 'ok',
                            'action': 'orders',
                            'data': orders_data
                        })
                
                elif command['action'] == 'get_order_by_ticket':
                    ticket = command['ticket']
                    orders = mt5.orders_get(ticket=ticket)
                    if orders and len(orders) > 0:
                        order = orders[0]
                        # В MT5 у ордера есть поле position, которое указывает на ticket позиции
                        # Для лимитных ордеров это поле может быть не установлено до исполнения
                        position_id = None
                        if hasattr(order, 'position') and order.position > 0:
                            position_id = order.position
                        elif hasattr(order, 'position_id') and order.position_id > 0:
                            position_id = order.position_id
                        
                        result_queue.put({
                            'status': 'ok',
                            'action': 'order',
                            'data': {
                                'ticket': order.ticket,
                                'symbol': order.symbol,
                                'type': order.type,
                                'volume_initial': order.volume_initial,
                                'volume_current': order.volume_current,
                                'price_open': order.price_open,
                                'time_setup': order.time_setup,
                                'position_id': position_id  # position_id ордера (ticket позиции)
                            }
                        })
                    else:
                        result_queue.put({
                            'status': 'ok',
                            'action': 'order',
                            'data': None
                        })
                
                elif command['action'] == 'get_deal_by_order':
                    # Получить deal по ticket ордера для поиска позиции
                    order_ticket = command['order_ticket']
                    from_time = command.get('from_time', 0)
                    to_time = command.get('to_time', int(time.time()))
                    
                    # Получаем историю сделок за последние 60 секунд
                    deals = mt5.history_deals_get(from_time, to_time)
                    if deals:
                        # Ищем deal, который был создан этим ордером
                        for deal in deals:
                            if deal.order == order_ticket:
                                # Нашли deal - получаем position из deal
                                position_ticket = deal.position if hasattr(deal, 'position') else None
                                result_queue.put({
                                    'status': 'ok',
                                    'action': 'deal',
                                    'data': {
                                        'deal_ticket': deal.ticket,
                                        'order_ticket': deal.order,
                                        'position_ticket': position_ticket,
                                        'symbol': deal.symbol,
                                        'type': deal.type,
                                        'volume': deal.volume,
                                        'price': deal.price,
                                        'time': deal.time
                                    }
                                })
                                break
                        else:
                            # Deal не найден
                            result_queue.put({
                                'status': 'ok',
                                'action': 'deal',
                                'data': None
                            })
                    else:
                        result_queue.put({
                            'status': 'ok',
                            'action': 'deal',
                            'data': None
                        })
                
                elif command['action'] == 'check_and_select_symbol':
                    symbol = command['symbol']
                    
                    # Сначала пробуем выбрать символ (даже если он уже в Market Watch)
                    # Это помогает "активировать" символ для API
                    mt5.symbol_select(symbol, True)
                    
                    # Получаем информацию о символе
                    symbol_info = mt5.symbol_info(symbol)
                    if symbol_info is None:
                        result_queue.put({
                            'status': 'error',
                            'action': 'symbol_check',
                            'message': f"Символ {symbol} не найден на клиентском аккаунте. Убедитесь, что символ доступен у вашего брокера."
                        })
                        continue
                    
                    # Проверяем, можно ли получить тик (это подтверждает, что символ доступен для торговли)
                    tick = mt5.symbol_info_tick(symbol)
                    if tick is None:
                        result_queue.put({
                            'status': 'error',
                            'action': 'symbol_check',
                            'message': f"Символ {symbol} найден, но недоступен для получения котировок. Возможно, рынок закрыт или символ не торгуется."
                        })
                        continue
                    
                    result_queue.put({
                        'status': 'ok',
                        'action': 'symbol_check',
                        'data': {
                            'symbol': symbol,
                            'selected': True,
                            'digits': symbol_info.digits,
                            'point': symbol_info.point,
                            'trade_mode': symbol_info.trade_mode,
                            'volume_min': symbol_info.volume_min,
                            'volume_max': symbol_info.volume_max,
                            'volume_step': symbol_info.volume_step
                        }
                    })
                
                elif command['action'] == 'get_symbol_info_tick':
                    symbol = command['symbol']
                    tick = mt5.symbol_info_tick(symbol)
                    if tick:
                        result_queue.put({
                            'status': 'ok',
                            'action': 'tick',
                            'data': {
                                'bid': tick.bid,
                                'ask': tick.ask,
                                'last': tick.last,
                                'volume': tick.volume,
                                'time': tick.time
                            }
                        })
                    else:
                        result_queue.put({
                            'status': 'error',
                            'action': 'tick',
                            'message': f"Не удалось получить тик для {symbol}"
                        })
                
                elif command['action'] == 'get_account_info':
                    account_info = mt5.account_info()
                    if account_info:
                        result_queue.put({
                            'status': 'ok',
                            'action': 'account_info',
                            'data': {
                                'login': account_info.login,
                                'balance': account_info.balance,
                                'equity': account_info.equity,
                                'margin_free': account_info.margin_free,
                                'currency': account_info.currency,
                                'server': account_info.server
                            }
                        })
                    else:
                        result_queue.put({
                            'status': 'error',
                            'action': 'account_info',
                            'message': 'Не удалось получить информацию об аккаунте'
                        })
                
                elif command['action'] == 'shutdown':
                    break
                
            except Exception as e:
                result_queue.put({
                    'status': 'error',
                    'message': f"Ошибка в клиентском процессе: {str(e)}"
                })
    
    finally:
        mt5.shutdown()
        result_queue.put({'status': 'disconnected'})

