"""
Главный файл копировщика сделок MT5
"""
import time
import argparse
import json
import os
import logging
from pathlib import Path
from datetime import datetime
import MetaTrader5 as mt5
from config import Config
from terminal_manager import TerminalManager
from position_monitor import PositionMonitor
from order_manager import OrderManager
from donors.donor_manager import DonorManager
from donors.donor_base import DonorPosition
from typing import Dict, Optional, List, Set

# Настройка логгера
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


class TradeCopier:
    """Основной класс копировщика сделок"""
    
    def __init__(self, config: Config, args, copy_existing_positions: bool = False, copy_donor_magic: bool = False, show_snapshot: bool = False):
        self.config = config
        self.copy_existing_positions = copy_existing_positions
        self.copy_donor_magic = copy_donor_magic
        self.show_snapshot = show_snapshot
        self.args = args
        self.logger = logging.getLogger(__name__)  # Инициализация logger перед использованием в _initialize_donors()
        self.terminal_manager = TerminalManager()
        self.donor_manager = DonorManager()
        
        # Инициализация доноров
        self._initialize_donors()
        
        self.position_monitor = PositionMonitor(terminal_manager=self.terminal_manager, donor_manager=self.donor_manager)
        self.order_manager = OrderManager(
            order_config=config.order_config,
            lot_config=config.lot_config,
            terminal_manager=self.terminal_manager
        )
        self.running = False
        self.client_positions: Dict[int, int] = {}  # donor_ticket -> client_ticket
        self.pending_close_orders: Dict[int, int] = {}  # donor_ticket -> close_order_ticket
        self.pending_close_orders_info: Dict[int, Dict] = {}  # close_order_ticket -> {donor_ticket, symbol, order_type, original_close_price, client_ticket}
        self.close_order_to_client_position: Dict[int, int] = {}  # close_order_ticket -> client_ticket (исходная позиция для закрытия)
        self.pending_orders: Dict[int, Dict] = {}  # order_ticket -> {donor_ticket, symbol, order_type, original_price}
        self.donor_pending_orders: Dict[int, int] = {}  # donor_order_ticket -> client_order_ticket (для отслеживания отложенных ордеров донора)
        self.donor_balance: float = 0.0
        self.client_balance: float = 0.0
        self.skipped_symbols: Set[str] = set()  # Символы, которые недоступны на клиенте
        self.state_file = Path("config/sync_state.json")  # Файл для сохранения состояния
    
    def _initialize_donors(self):
        """Инициализация доноров из аргументов командной строки"""
        # Проверяем, нужно ли игнорировать конфиг
        # Конфиг игнорируется если указан --ignore-donor-config или любая из опций --donor-*
        ignore_config = (self.args.ignore_donor_config or 
                        self.args.donor_api is not None or 
                        self.args.donor_socket_mt4 is not None or 
                        self.args.donor_socket_mt5 is not None)
        
        # По умолчанию пытаемся загрузить donors_config.json из папки config
        default_config_path = Path("config/donors_config.json")
        
        if not ignore_config:
            # Пытаемся загрузить конфиг из корня или из указанного пути
            config_path = self.args.donors_config if self.args.donors_config else default_config_path
            
            if config_path.exists():
                self.logger.info(f"Загрузка доноров из конфигурационного файла: {config_path}")
                connected = self.donor_manager.load_from_config(str(config_path))
                if connected == 0:
                    self.logger.warning("Не удалось подключить ни одного донора из конфигурационного файла")
                else:
                    self.logger.info(f"Подключено {connected} доноров из конфигурационного файла")
            else:
                if self.args.donors_config:
                    # Пользователь явно указал файл, но его нет
                    self.logger.error(f"Конфигурационный файл не найден: {config_path}")
                else:
                    # Файл по умолчанию не найден - это нормально, выводим информационное сообщение
                    self.logger.info(f"Конфигурационный файл {default_config_path} не найден. Используйте --donor-* для указания доноров или создайте конфигурационный файл.")
        
        # Если конфиг игнорируется или не загружен, используем аргументы командной строки
        if ignore_config or len(self.donor_manager.donors) == 0:
            # Быстрый способ: один донор через аргументы
            if self.args.donor_api:
                self.logger.info(f"Использование донора через Python API (аккаунт {self.args.donor_api})")
                self.donor_manager.add_python_api_donor(
                    donor_id="donor_api",
                    account_number=self.args.donor_api
                )
            
            if self.args.donor_socket_mt4:
                self.logger.info(f"Использование донора через Socket MT4 (аккаунт {self.args.donor_socket_mt4})")
                self.donor_manager.add_socket_mt4_donor(
                    donor_id="donor_mt4",
                    account_number=self.args.donor_socket_mt4,
                    host=self.args.socket_host,
                    port=self.args.socket_port
                )
            
            if self.args.donor_socket_mt5:
                self.logger.info(f"Использование донора через Socket MT5 (аккаунт {self.args.donor_socket_mt5})")
                self.donor_manager.add_socket_mt5_donor(
                    donor_id="donor_mt5",
                    account_number=self.args.donor_socket_mt5,
                    host=self.args.socket_host,
                    port=self.args.socket_port
                )
        
        # Проверяем, что хотя бы один донор подключен
        if len(self.donor_manager.donors) == 0:
            self.logger.error("Не указан ни один донор! Создайте файл config/donors_config.json или используйте --donor-*")
    
    def initialize(self) -> bool:
        """Инициализация системы"""
        self.logger.info("Инициализация копировщика сделок...")
        
        # Проверяем, что хотя бы один донор подключен
        if len(self.donor_manager.donors) == 0:
            self.logger.error("Ошибка: не подключен ни один донор")
            return False
        
        # Получаем баланс от первого донора (для обратной совместимости)
        # В будущем можно агрегировать балансы всех доноров
        first_donor = self.donor_manager.donors[0]
        account_info = first_donor.get_account_info()
        if account_info:
            self.donor_balance = account_info.get('balance', 0.0)
        else:
            self.donor_balance = 0.0
        
        self.logger.info(f"Подключено доноров: {self.donor_manager.get_connected_count()}")
        
        # Поиск и подключение к клиентскому терминалу
        # При --copy-donor-magic передаем None, чтобы получать все позиции клиента
        # (фильтрация по magic донорской позиции будет происходить при сопоставлении)
        client_magic = None if self.copy_donor_magic else self.config.order_config.magic
        if not self.terminal_manager.find_and_connect_client(
            self.config.client_account.account_number,
            client_magic
        ):
            self.logger.error("Ошибка: не удалось подключиться к клиентскому терминалу")
            return False
        
        # Получаем баланс клиентского счета
        client_account_info = self.terminal_manager.get_client_account_info()
        if client_account_info:
            self.client_balance = client_account_info.get('balance', 0.0)
        else:
            self.client_balance = 0.0
        
        # Инициализируем состояния позиций для мониторинга изменений
        self.position_monitor.initialize_position_states()
        
        # Добавляем существующие позиции донора в отслеживаемые
        existing_donor_positions = self.position_monitor.get_donor_positions()
        for pos in existing_donor_positions:
            self.position_monitor.tracked_positions.add(pos.ticket)
        
        # Восстанавливаем состояние синхронизации при перезапуске
        self.logger.info("Восстановление состояния синхронизации...")
        self.load_and_restore_sync_state()
        
        # Копируем существующие позиции, если опция включена
        if self.copy_existing_positions:
            self.logger.info("Копирование существующих позиций при запуске...")
            self.copy_existing_positions(existing_donor_positions)
        
        self.logger.info("Инициализация завершена успешно")
        return True
    
    def copy_existing_positions(self, existing_positions):
        """
        Копировать уже открытые позиции на клиентский аккаунт
        
        Args:
            existing_positions: Список существующих позиций донора
        """
        for position in existing_positions:
            # Пропускаем символы, которые недоступны на клиенте
            if position.symbol in self.skipped_symbols:
                self.logger.warning(f"Символ {position.symbol} недоступен на клиенте, пропускаем позицию {position.ticket}")
                continue
            
            self.logger.info(f"Копирование существующей позиции: ticket={position.ticket}, symbol={position.symbol}, "
                  f"type={position.type}, volume={position.volume}, price={position.price_open}")
            
            # Определяем тип лимитного ордера
            if position.type == mt5.POSITION_TYPE_BUY:
                order_type = mt5.ORDER_TYPE_BUY_LIMIT
            else:
                order_type = mt5.ORDER_TYPE_SELL_LIMIT
            
            # Определяем magic number: копируем с донора, если опция включена
            magic = position.magic if (self.copy_donor_magic and position.magic is not None) else None
            
            # Размещаем лимитный ордер для копирования позиции
            order_ticket = self.order_manager.place_limit_order(
                symbol=position.symbol,
                order_type=order_type,
                volume=position.volume,
                original_price=position.price_open,
                donor_balance=self.donor_balance,
                client_balance=self.client_balance,
                magic=magic
            )
            
            if order_ticket is None:
                # Символ недоступен или ошибка размещения
                if position.symbol not in self.skipped_symbols:
                    self.skipped_symbols.add(position.symbol)
                    self.logger.warning(f"Символ {position.symbol} недоступен на клиентском аккаунте. Позиция будет пропущена.")
                continue
            
            # Добавляем ордер в отслеживаемые для проверки исполнения
            self.pending_orders[order_ticket] = {
                'donor_ticket': position.ticket,
                'symbol': position.symbol,
                'order_type': mt5.ORDER_TYPE_BUY_LIMIT if position.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_SELL_LIMIT,
                'original_price': position.price_open
            }
            
            # Проверяем, исполнился ли ордер сразу
            if self.order_manager.wait_for_order_fill(order_ticket, timeout=1.0):
                # Ордер исполнился, ищем позицию
                client_position = self.terminal_manager.get_client_position_by_symbol(
                    position.symbol,
                    position.type
                )
                
                if client_position:
                    # Позиция найдена - ордер исполнен
                    self.client_positions[position.ticket] = client_position.ticket
                    self.position_monitor.mark_position_copied(
                        position.ticket,
                        client_position.ticket
                    )
                    # Инициализируем состояние новой позиции клиента
                    client_positions = self.position_monitor.get_client_positions()
                    for pos in client_positions:
                        if pos.ticket == client_position.ticket:
                            self.position_monitor.client_position_states[pos.ticket] = pos
                            break
                    self.logger.info(f"Существующая позиция скопирована: донор={position.ticket}, клиент={client_position.ticket}")
                    
                    # Удаляем из отслеживаемых ордеров
                    if order_ticket in self.pending_orders:
                        del self.pending_orders[order_ticket]
                else:
                    # Позиция не найдена - возможно ордер был отклонен
                    self.logger.warning(f"Ордер {order_ticket} удален, но позиция не найдена. Возможно, ордер был отклонен.")
                    if order_ticket in self.pending_orders:
                        del self.pending_orders[order_ticket]
            else:
                # Ордер еще не исполнен, проверим в следующей итерации через check_pending_order_fills
                self.logger.info(f"Ордер {order_ticket} размещен, ожидание исполнения...")
    
    def save_sync_state(self) -> bool:
        """
        Сохранить текущее состояние синхронизации в файл
        
        Returns:
            True если сохранение успешно
        """
        try:
            # Сохраняем расширенную информацию о позициях для лучшего сопоставления
            client_positions_extended = {}
            for donor_ticket, client_ticket in self.client_positions.items():
                # Получаем информацию о позициях для сохранения метаданных
                donor_pos = self.position_monitor.get_position_by_ticket(donor_ticket, is_client=False)
                client_pos = self.position_monitor.get_position_by_ticket(client_ticket, is_client=True)
                
                if donor_pos and client_pos:
                    client_positions_extended[str(donor_ticket)] = {
                        'client_ticket': client_ticket,
                        'symbol': donor_pos.symbol,
                        'type': donor_pos.type,
                        'donor_price_open': donor_pos.price_open,
                        'client_price_open': client_pos.price_open,
                        'donor_time': donor_pos.time.isoformat() if donor_pos.time else None,
                        'client_time': client_pos.time.isoformat() if client_pos.time else None,
                        'donor_magic': donor_pos.magic,
                        'client_magic': client_pos.magic,
                        'donor_comment': donor_pos.comment,
                        'client_comment': client_pos.comment
                    }
                else:
                    # Если не удалось получить полную информацию, сохраняем хотя бы связь
                    client_positions_extended[str(donor_ticket)] = {
                        'client_ticket': client_ticket
                    }
            
            # Сохраняем расширенную информацию об ордерах
            pending_orders_extended = {}
            for order_ticket, order_info in self.pending_orders.items():
                # Получаем информацию об ордере для сохранения времени размещения
                order_data = self.terminal_manager.get_client_order_by_ticket(order_ticket)
                pending_orders_extended[str(order_ticket)] = {
                    **order_info,
                    'time_setup': order_data.get('time_setup') if order_data else None
                }
            
            state = {
                'timestamp': datetime.now().isoformat(),
                'client_positions': client_positions_extended,
                'pending_orders': pending_orders_extended,
                'pending_close_orders': {str(k): v for k, v in self.pending_close_orders.items()},
                'pending_close_orders_info': {str(k): v for k, v in self.pending_close_orders_info.items()},
                'close_order_to_client_position': {str(k): v for k, v in self.close_order_to_client_position.items()},
                'donor_pending_orders': {str(k): v for k, v in self.donor_pending_orders.items()}
            }
            
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            
            return True
        except Exception as e:
            self.logger.error(f"Ошибка сохранения состояния: {e}")
            return False
    
    def load_sync_state(self) -> Optional[Dict]:
        """
        Загрузить сохраненное состояние синхронизации из файла
        
        Returns:
            Словарь с состоянием или None если файл не найден или поврежден
        """
        if not self.state_file.exists():
            return None
        
        try:
            with open(self.state_file, 'r', encoding='utf-8') as f:
                state = json.load(f)
            
            # Конвертируем строковые ключи обратно в int для совместимости со старым форматом
            # Новая структура уже содержит словари, старый формат - просто int значения
            client_positions_old_format = {}
            for k, v in state.get('client_positions', {}).items():
                if isinstance(v, dict):
                    # Новый формат с расширенной информацией
                    client_positions_old_format[int(k)] = v.get('client_ticket', v)
                else:
                    # Старый формат - просто ticket
                    client_positions_old_format[int(k)] = v
            state['client_positions'] = client_positions_old_format
            
            state['pending_orders'] = {int(k): v for k, v in state.get('pending_orders', {}).items()}
            state['pending_close_orders'] = {int(k): v for k, v in state.get('pending_close_orders', {}).items()}
            state['pending_close_orders_info'] = {int(k): v for k, v in state.get('pending_close_orders_info', {}).items()}
            state['close_order_to_client_position'] = {int(k): v for k, v in state.get('close_order_to_client_position', {}).items()}
            state['donor_pending_orders'] = {int(k): v for k, v in state.get('donor_pending_orders', {}).items()}
            
            return state
        except Exception as e:
            self.logger.error(f"Ошибка загрузки состояния: {e}")
            return None
    
    def validate_and_merge_state(self, saved_state: Dict) -> Dict:
        """
        Валидировать сохраненное состояние и слить с текущей ситуацией
        
        Args:
            saved_state: Сохраненное состояние из файла
            
        Returns:
            Валидированное и дополненное состояние
        """
        self.logger.info("Валидация сохраненного состояния...")
        
        # Получаем текущие данные из терминалов
        current_donor_positions = {pos.ticket: pos for pos in self.position_monitor.get_donor_positions()}
        current_client_positions = {pos.ticket: pos for pos in self.position_monitor.get_client_positions()}
        current_client_orders = {order['ticket']: order for order in self.terminal_manager.get_client_orders()}
        
        validated_state = {
            'client_positions': {},
            'pending_orders': {},
            'pending_close_orders': {},
            'pending_close_orders_info': {},
            'close_order_to_client_position': {},
            'donor_pending_orders': {}
        }
        
        # Валидация client_positions
        # Ищем для каждой клиентской позиции лучший матч среди донорских
        self.logger.debug("Проверка связей позиций...")
        valid_positions = 0
        invalid_positions = 0
        
        # Собираем сохраненные связи для проверки
        saved_links = {}
        for donor_ticket, pos_data in saved_state.get('client_positions', {}).items():
            if isinstance(pos_data, dict):
                client_ticket = pos_data.get('client_ticket')
            else:
                client_ticket = pos_data
            saved_links[client_ticket] = donor_ticket
        
        # Используем множество для отслеживания уже связанных донорских позиций
        used_donor_tickets = set()
        
        # Для каждой клиентской позиции ищем лучший матч среди донорских
        for client_ticket, client_pos in current_client_positions.items():
            best_match = None
            best_score = 0
            best_donor_ticket = None
            
            # Проверяем, была ли эта позиция связана в сохраненном состоянии
            saved_donor_ticket = saved_links.get(client_ticket)
            
            for donor_ticket, donor_pos in current_donor_positions.items():
                # Пропускаем уже связанные донорские позиции
                if donor_ticket in used_donor_tickets:
                    continue
                
                # Проверяем базовые условия
                if client_pos.symbol != donor_pos.symbol or client_pos.type != donor_pos.type:
                    continue
                
                score = 0
                
                # 1. Направление и символ совпадают (обязательно)
                score += 20
                
                # 2. Magic number - ПРИОРИТЕТ #1
                if self.copy_donor_magic:
                    if donor_pos.magic is not None:
                        if client_pos.magic != donor_pos.magic:
                            continue  # Magic не совпадает - пропускаем
                        score += 30
                else:
                    if donor_pos.magic is not None and client_pos.magic is not None:
                        if client_pos.magic == donor_pos.magic:
                            score += 15
                
                # 3. Время открытия - ПРИОРИТЕТ #2
                if client_pos.time and donor_pos.time:
                    time_diff = abs((client_pos.time - donor_pos.time).total_seconds())
                    if time_diff <= 60:
                        time_score = 20 * (1 - min(time_diff / 60, 1))
                        score += time_score
                    elif time_diff <= 300:
                        time_score = 15 * (1 - min((time_diff - 60) / 240, 1))
                        score += time_score
                    elif time_diff <= 3600:
                        time_score = 10 * (1 - min((time_diff - 300) / 3300, 1))
                        score += time_score
                    elif time_diff <= 86400:
                        time_score = 5 * (1 - min((time_diff - 3600) / 82800, 1))
                        score += time_score
                
                # 4. Цена открытия - ПРИОРИТЕТ #3
                price_diff = abs(client_pos.price_open - donor_pos.price_open)
                symbol_info = self.terminal_manager.check_and_select_client_symbol(donor_pos.symbol)
                if symbol_info:
                    point = symbol_info.get('point', 0.00001)
                    max_price_diff = max(point * 100, 0.01)
                else:
                    max_price_diff = 0.01
                
                if price_diff <= max_price_diff:
                    price_score = 10 * (1 - min(price_diff / max_price_diff, 1))
                    score += price_score
                else:
                    price_penalty = min(price_diff / max_price_diff, 2)
                    score -= 5 * price_penalty
                    if score < 0:
                        continue
                
                # Бонус за сохраненную связь (если она была)
                if saved_donor_ticket == donor_ticket:
                    score += 10
                
                # Сохраняем лучший матч
                if score > best_score:
                    best_score = score
                    best_match = donor_pos
                    best_donor_ticket = donor_ticket
            
            # Если найден хороший матч (score >= 20)
            if best_match and best_score >= 20:
                validated_state['client_positions'][best_donor_ticket] = client_ticket
                used_donor_tickets.add(best_donor_ticket)
                valid_positions += 1
                
                # Если это не сохраненная связь, логируем
                if saved_donor_ticket != best_donor_ticket:
                    self.logger.info(f"Пересопоставлена позиция: клиент {client_ticket} теперь связан с донором {best_donor_ticket} "
                          f"(было: {saved_donor_ticket if saved_donor_ticket else 'не было'}, score={best_score:.1f})")
            else:
                invalid_positions += 1
                if saved_donor_ticket:
                    self.logger.warning(f"Клиентская позиция {client_ticket} больше не имеет валидного матча "
                          f"(была связана с донором {saved_donor_ticket})")
        
        self.logger.info(f"Валидных связей позиций: {valid_positions}, невалидных: {invalid_positions}")
        
        # Валидация pending_orders
        self.logger.debug("Проверка открывающих ордеров...")
        valid_orders = 0
        invalid_orders = 0
        
        for order_ticket, order_info in saved_state.get('pending_orders', {}).items():
            if order_ticket in current_client_orders:
                validated_state['pending_orders'][order_ticket] = order_info
                valid_orders += 1
            else:
                invalid_orders += 1
                self.logger.debug(f"Ордер {order_ticket} больше не существует (возможно, исполнен)")
        
        self.logger.info(f"Валидных открывающих ордеров: {valid_orders}, невалидных: {invalid_orders}")
        
        # Валидация pending_close_orders
        self.logger.debug("Проверка закрывающих ордеров...")
        valid_close_orders = 0
        invalid_close_orders = 0
        
        for donor_ticket, close_order_ticket in saved_state.get('pending_close_orders', {}).items():
            # Проверяем, что позиция донора еще существует (если нет - позиция уже закрыта)
            if donor_ticket in current_donor_positions:
                if close_order_ticket in current_client_orders:
                    validated_state['pending_close_orders'][donor_ticket] = close_order_ticket
                    # Восстанавливаем связанную информацию
                    if close_order_ticket in saved_state.get('pending_close_orders_info', {}):
                        validated_state['pending_close_orders_info'][close_order_ticket] = saved_state['pending_close_orders_info'][close_order_ticket]
                    if close_order_ticket in saved_state.get('close_order_to_client_position', {}):
                        validated_state['close_order_to_client_position'][close_order_ticket] = saved_state['close_order_to_client_position'][close_order_ticket]
                    valid_close_orders += 1
                else:
                    invalid_close_orders += 1
                    self.logger.debug(f"Закрывающий ордер {close_order_ticket} больше не существует")
            else:
                invalid_close_orders += 1
                self.logger.debug(f"Позиция донора {donor_ticket} закрыта, закрывающий ордер больше не нужен")
        
        self.logger.info(f"Валидных закрывающих ордеров: {valid_close_orders}, невалидных: {invalid_close_orders}")
        
        # Валидация donor_pending_orders
        self.logger.debug("Проверка отложенных ордеров донора...")
        valid_donor_orders = 0
        invalid_donor_orders = 0
        
        for donor_order_ticket, client_order_ticket in saved_state.get('donor_pending_orders', {}).items():
            # Проверяем, что клиентский ордер еще существует
            if client_order_ticket in current_client_orders:
                validated_state['donor_pending_orders'][donor_order_ticket] = client_order_ticket
                valid_donor_orders += 1
            else:
                invalid_donor_orders += 1
                self.logger.debug(f"Клиентский отложенный ордер {client_order_ticket} больше не существует (возможно, исполнен или отменен)")
        
        self.logger.info(f"Валидных отложенных ордеров донора: {valid_donor_orders}, невалидных: {invalid_donor_orders}")
        
        self.logger.info(f"Валидация завершена. Восстановлено: {valid_positions} позиций, {valid_orders} открывающих ордеров, {valid_close_orders} закрывающих ордеров, {valid_donor_orders} отложенных ордеров донора")
        
        return validated_state
    
    def print_positions_snapshot(self):
        """
        Вывести снэпшот всех позиций на доноре и клиенте при первом запуске
        """
        self.logger.info("\n" + "="*80)
        self.logger.info("СНЭПШОТ ПОЗИЦИЙ")
        self.logger.info("="*80)
        
        # Получаем позиции
        donor_positions = self.position_monitor.get_donor_positions()
        client_positions = self.position_monitor.get_client_positions()
        
        snapshot_lines = []
        snapshot_lines.append(f"\nДОНОРСКИЕ ПОЗИЦИИ (всего: {len(donor_positions)}):")
        snapshot_lines.append("-"*80)
        if not donor_positions:
            snapshot_lines.append("  Нет открытых позиций")
        else:
            for pos in donor_positions:
                linked = "✓" if pos.ticket in self.client_positions else "✗"
                client_ticket = self.client_positions.get(pos.ticket, "не связана")
                pos_type = "BUY" if pos.type == mt5.POSITION_TYPE_BUY else "SELL"
                time_str = pos.time.strftime("%Y-%m-%d %H:%M:%S") if pos.time else "N/A"
                snapshot_lines.append(f"  [{linked}] Ticket: {pos.ticket:10} | Symbol: {pos.symbol:10} | Type: {pos_type:4} | "
                      f"Volume: {pos.volume:8.2f} | Price: {pos.price_open:10.5f} | "
                      f"Magic: {pos.magic or 'N/A':8} | Time: {time_str} | Comment: '{pos.comment or ''}'")
                if linked == "✓":
                    snapshot_lines.append(f"      → Связана с клиентской позицией: {client_ticket}")
        
        snapshot_lines.append(f"\nКЛИЕНТСКИЕ ПОЗИЦИИ (всего: {len(client_positions)}):")
        snapshot_lines.append("-"*80)
        if not client_positions:
            snapshot_lines.append("  Нет открытых позиций")
        else:
            # Находим обратные связи
            reverse_links = {v: k for k, v in self.client_positions.items()}
            for pos in client_positions:
                linked = "✓" if pos.ticket in reverse_links else "✗"
                donor_ticket = reverse_links.get(pos.ticket, "не связана")
                pos_type = "BUY" if pos.type == mt5.POSITION_TYPE_BUY else "SELL"
                time_str = pos.time.strftime("%Y-%m-%d %H:%M:%S") if pos.time else "N/A"
                snapshot_lines.append(f"  [{linked}] Ticket: {pos.ticket:10} | Symbol: {pos.symbol:10} | Type: {pos_type:4} | "
                      f"Volume: {pos.volume:8.2f} | Price: {pos.price_open:10.5f} | "
                      f"Magic: {pos.magic or 'N/A':8} | Time: {time_str} | Comment: '{pos.comment or ''}'")
                if linked == "✓":
                    snapshot_lines.append(f"      → Связана с донорской позицией: {donor_ticket}")
        
        snapshot_lines.append(f"\nСВЯЗИ (всего: {len(self.client_positions)}):")
        snapshot_lines.append("-"*80)
        if not self.client_positions:
            snapshot_lines.append("  Нет связей")
        else:
            for donor_ticket, client_ticket in self.client_positions.items():
                donor_pos = next((p for p in donor_positions if p.ticket == donor_ticket), None)
                client_pos = next((p for p in client_positions if p.ticket == client_ticket), None)
                
                if donor_pos and client_pos:
                    price_diff = abs(donor_pos.price_open - client_pos.price_open)
                    time_diff = abs((donor_pos.time - client_pos.time).total_seconds()) if (donor_pos.time and client_pos.time) else None
                    magic_match = "✓" if donor_pos.magic == client_pos.magic else "✗"
                    comment_match = "✓" if donor_pos.comment == client_pos.comment else "✗"
                    
                    donor_time_str = donor_pos.time.strftime("%Y-%m-%d %H:%M:%S") if donor_pos.time else "N/A"
                    client_time_str = client_pos.time.strftime("%Y-%m-%d %H:%M:%S") if client_pos.time else "N/A"
                    donor_type = "BUY" if donor_pos.type == mt5.POSITION_TYPE_BUY else "SELL"
                    client_type = "BUY" if client_pos.type == mt5.POSITION_TYPE_BUY else "SELL"
                    
                    snapshot_lines.append(f"\n  СВЯЗЬ: Донор {donor_ticket} ↔ Клиент {client_ticket}")
                    snapshot_lines.append(f"  {'─'*78}")
                    
                    # Полные данные донорской позиции
                    snapshot_lines.append(f"  ДОНОР:")
                    snapshot_lines.append(f"    Ticket: {donor_ticket:10} | Symbol: {donor_pos.symbol:10} | Type: {donor_type:4}")
                    snapshot_lines.append(f"    Volume: {donor_pos.volume:8.2f} | Price: {donor_pos.price_open:10.5f} | Magic: {donor_pos.magic or 'N/A':8}")
                    snapshot_lines.append(f"    Time: {donor_time_str} | Comment: '{donor_pos.comment or ''}'")
                    
                    # Полные данные клиентской позиции
                    snapshot_lines.append(f"  КЛИЕНТ:")
                    snapshot_lines.append(f"    Ticket: {client_ticket:10} | Symbol: {client_pos.symbol:10} | Type: {client_type:4}")
                    snapshot_lines.append(f"    Volume: {client_pos.volume:8.2f} | Price: {client_pos.price_open:10.5f} | Magic: {client_pos.magic or 'N/A':8}")
                    snapshot_lines.append(f"    Time: {client_time_str} | Comment: '{client_pos.comment or ''}'")
                    
                    # Разницы
                    snapshot_lines.append(f"  РАЗНИЦЫ:")
                    snapshot_lines.append(f"    Price diff: {price_diff:.5f} | Time diff: {time_diff:.0f}s" if time_diff is not None else f"    Price diff: {price_diff:.5f} | Time diff: N/A")
                    snapshot_lines.append(f"    Magic match: {magic_match} | Comment match: {comment_match}")
        
        snapshot_lines.append("\n" + "="*80 + "\n")
        self.logger.info("\n".join(snapshot_lines))
    
    def load_and_restore_sync_state(self):
        """
        Загрузить сохраненное состояние, валидировать и восстановить синхронизацию.
        Если состояние неактуально или частично актуально, дорабатывает через сопоставления.
        """
        # Пытаемся загрузить сохраненное состояние
        saved_state = self.load_sync_state()
        
        if saved_state:
            self.logger.info(f"Загружено сохраненное состояние от {saved_state.get('timestamp', 'неизвестно')}")
            
            # Валидируем и получаем актуальные части
            validated_state = self.validate_and_merge_state(saved_state)
            
            # Восстанавливаем валидные части состояния
            self.client_positions.update(validated_state['client_positions'])
            self.pending_orders.update(validated_state['pending_orders'])
            self.pending_close_orders.update(validated_state['pending_close_orders'])
            self.pending_close_orders_info.update(validated_state['pending_close_orders_info'])
            self.close_order_to_client_position.update(validated_state['close_order_to_client_position'])
            self.donor_pending_orders.update(validated_state.get('donor_pending_orders', {}))
            
            # Восстанавливаем copied_positions в PositionMonitor
            for donor_ticket, client_ticket in validated_state['client_positions'].items():
                self.position_monitor.mark_position_copied(donor_ticket, client_ticket)
            
            self.logger.info(f"Восстановлено из кэша: {len(validated_state['client_positions'])} позиций, "
                  f"{len(validated_state['pending_orders'])} открывающих ордеров, "
                  f"{len(validated_state['pending_close_orders'])} закрывающих ордеров, "
                  f"{len(validated_state.get('donor_pending_orders', {}))} отложенных ордеров донора")
            
            # Проверяем, нужно ли доработать через сопоставления
            need_restore = False
            
            # Если состояние частично неактуально (меньше восстановлено, чем было сохранено)
            if (len(validated_state['client_positions']) < len(saved_state.get('client_positions', {})) or
                len(validated_state['pending_orders']) < len(saved_state.get('pending_orders', {})) or
                len(validated_state['pending_close_orders']) < len(saved_state.get('pending_close_orders', {}))):
                self.logger.info("Состояние частично неактуально, дорабатываем через сопоставления...")
                need_restore = True
            # Если восстановлено 0 связей, но есть позиции на доноре и клиенте - пытаемся сопоставить
            elif len(validated_state['client_positions']) == 0:
                donor_positions = self.position_monitor.get_donor_positions()
                client_positions = self.position_monitor.get_client_positions()
                if len(donor_positions) > 0 and len(client_positions) > 0:
                    self.logger.info("Связи не найдены, но есть позиции на доноре и клиенте, пытаемся сопоставить...")
                    need_restore = True
            
            if need_restore:
                self.restore_sync_state()
        else:
            self.logger.info("Сохраненное состояние не найдено, используем полное сопоставление...")
            self.restore_sync_state()
        
        # Сохраняем состояние после завершения валидации и восстановления
        self.logger.info("Сохранение состояния после валидации...")
        if self.save_sync_state():
            self.logger.info("Состояние успешно сохранено")
        else:
            self.logger.warning("Не удалось сохранить состояние")
        
        # Выводим снэпшот после восстановления (один раз при старте), если опция включена
        if self.show_snapshot:
            self.print_positions_snapshot()
    
    def restore_sync_state(self):
        """
        Восстановить состояние синхронизации при перезапуске приложения.
        Сопоставляет позиции донора и клиента, восстанавливает отслеживание ордеров.
        """
        self.logger.info("Восстановление синхронизации позиций и ордеров...")
        
        # Получаем все позиции донора и клиента
        donor_positions = self.position_monitor.get_donor_positions()
        client_positions = self.position_monitor.get_client_positions()
        
        # Получаем все pending ордера клиента
        client_orders = self.terminal_manager.get_client_orders()
        
        # Восстанавливаем связи между позициями донора и клиента
        # Ищем для каждой клиентской позиции лучший матч среди донорских
        matched_count = 0
        used_donor_tickets = set(self.client_positions.keys())  # Уже связанные донорские позиции
        
        for client_pos in client_positions:
            # Пропускаем, если уже связана
            if client_pos.ticket in self.client_positions.values():
                continue
            
            # Ищем лучший матч среди донорских позиций
            best_match = None
            best_score = 0
            best_donor_ticket = None
            
            for donor_pos in donor_positions:
                # Пропускаем уже связанные позиции
                if donor_pos.ticket in used_donor_tickets:
                    continue
                
                # Проверяем базовые условия
                if client_pos.symbol != donor_pos.symbol or client_pos.type != donor_pos.type:
                    continue
                
                score = 0
                
                # 1. Направление и символ совпадают (обязательно)
                score += 20
                
                # 2. Magic number - ПРИОРИТЕТ #1
                if self.copy_donor_magic:
                    if donor_pos.magic is not None:
                        if client_pos.magic != donor_pos.magic:
                            continue  # Magic не совпадает - пропускаем
                        score += 30
                else:
                    if donor_pos.magic is not None and client_pos.magic is not None:
                        if client_pos.magic == donor_pos.magic:
                            score += 15
                
                # 3. Время открытия - ПРИОРИТЕТ #2
                if client_pos.time and donor_pos.time:
                    time_diff = abs((client_pos.time - donor_pos.time).total_seconds())
                    if time_diff <= 60:
                        time_score = 20 * (1 - min(time_diff / 60, 1))
                        score += time_score
                    elif time_diff <= 300:
                        time_score = 15 * (1 - min((time_diff - 60) / 240, 1))
                        score += time_score
                    elif time_diff <= 3600:
                        time_score = 10 * (1 - min((time_diff - 300) / 3300, 1))
                        score += time_score
                    elif time_diff <= 86400:
                        time_score = 5 * (1 - min((time_diff - 3600) / 82800, 1))
                        score += time_score
                
                # 4. Цена открытия - ПРИОРИТЕТ #3
                price_diff = abs(client_pos.price_open - donor_pos.price_open)
                symbol_info = self.terminal_manager.check_and_select_client_symbol(donor_pos.symbol)
                if symbol_info:
                    point = symbol_info.get('point', 0.00001)
                    max_price_diff = max(point * 100, 0.01)
                else:
                    max_price_diff = 0.01
                
                if price_diff <= max_price_diff:
                    price_score = 10 * (1 - min(price_diff / max_price_diff, 1))
                    score += price_score
                else:
                    price_penalty = min(price_diff / max_price_diff, 2)
                    score -= 5 * price_penalty
                    if score < 0:
                        continue
                
                # Сохраняем лучший матч
                if score > best_score:
                    best_score = score
                    best_match = donor_pos
                    best_donor_ticket = donor_pos.ticket
            
            # Если найден хороший матч (score >= 20)
            if best_match and best_score >= 20:
                # Восстанавливаем связь
                self.client_positions[best_donor_ticket] = client_pos.ticket
                used_donor_tickets.add(best_donor_ticket)
                self.position_monitor.mark_position_copied(
                    best_donor_ticket,
                    client_pos.ticket
                )
                matched_count += 1
                
                # Формируем информацию о комментариях
                comment_info = ""
                if best_match.comment != client_pos.comment:
                    comment_info = f", комментарии различаются (донор='{best_match.comment}', клиент='{client_pos.comment}')"
                
                self.logger.info(f"Восстановлена связь: донор={best_donor_ticket}, клиент={client_pos.ticket}, "
                      f"symbol={best_match.symbol}, score={best_score:.1f}, "
                      f"price_diff={abs(client_pos.price_open - best_match.price_open):.5f}, "
                      f"time_diff={(client_pos.time - best_match.time).total_seconds():.0f}s{comment_info}")
        
        if matched_count > 0:
            self.logger.info(f"Восстановлено связей позиций: {matched_count}")
        else:
            self.logger.info("Связи позиций не найдены (возможно, позиции еще не скопированы)")
        
        # Восстанавливаем отслеживание pending ордеров
        # Ордера могут быть открывающими (для новых позиций) или закрывающими (для закрытия позиций)
        restored_orders = 0
        for order in client_orders:
            order_ticket = order['ticket']
            order_type = order['type']
            symbol = order['symbol']
            
            # Определяем, является ли ордер открывающим или закрывающим
            # Открывающие: BUY_LIMIT, SELL_LIMIT для символов, где нет открытой позиции
            # Закрывающие: BUY_LIMIT, SELL_LIMIT для символов, где есть открытая позиция противоположного типа
            
            # Проверяем, есть ли открытая позиция по этому символу
            has_open_position = False
            client_position_ticket = None
            donor_ticket = None
            
            for client_pos in client_positions:
                if client_pos.symbol == symbol:
                    has_open_position = True
                    client_position_ticket = client_pos.ticket
                    # Находим соответствующий donor_ticket
                    for d_ticket, c_ticket in self.client_positions.items():
                        if c_ticket == client_pos.ticket:
                            donor_ticket = d_ticket
                            break
                    break
            
            if has_open_position and donor_ticket:
                # Это закрывающий ордер
                # Проверяем, что тип ордера противоположен типу позиции
                client_pos = next((p for p in client_positions if p.ticket == client_position_ticket), None)
                if client_pos:
                    is_opposite = (
                        (order_type == mt5.ORDER_TYPE_SELL_LIMIT and client_pos.type == mt5.POSITION_TYPE_BUY) or
                        (order_type == mt5.ORDER_TYPE_BUY_LIMIT and client_pos.type == mt5.POSITION_TYPE_SELL)
                    )
                    
                    if is_opposite:
                        # Восстанавливаем отслеживание закрывающего ордера
                        self.pending_close_orders[donor_ticket] = order_ticket
                        self.close_order_to_client_position[order_ticket] = client_position_ticket
                        
                        # Определяем оригинальную цену закрытия (используем цену ордера)
                        original_close_price = order['price_open']
                        
                        self.pending_close_orders_info[order_ticket] = {
                            'donor_ticket': donor_ticket,
                            'symbol': symbol,
                            'order_type': order_type,
                            'original_close_price': original_close_price,
                            'client_ticket': client_position_ticket
                        }
                        restored_orders += 1
                        self.logger.info(f"Восстановлен закрывающий ордер: ticket={order_ticket}, donor={donor_ticket}, symbol={symbol}")
            else:
                # Это открывающий ордер - пытаемся найти соответствующую позицию донора
                # Используем направление, время размещения и цену для сопоставления
                best_donor_match = None
                best_score = 0
                
                order_time_setup = order.get('time_setup', 0)
                order_price = order.get('price_open', 0)
                
                # Определяем тип позиции, которая должна открыться из этого ордера
                expected_position_type = None
                if order_type == mt5.ORDER_TYPE_BUY_LIMIT:
                    expected_position_type = mt5.POSITION_TYPE_BUY
                elif order_type == mt5.ORDER_TYPE_SELL_LIMIT:
                    expected_position_type = mt5.POSITION_TYPE_SELL
                
                for donor_pos in donor_positions:
                    # Пропускаем уже связанные позиции
                    if donor_pos.ticket in self.client_positions:
                        continue
                    
                    # Проверяем соответствие символа и типа
                    if donor_pos.symbol != symbol or donor_pos.type != expected_position_type:
                        continue
                    
                    score = 10  # Базовый score за совпадение символа и типа
                    
                    # Проверяем соответствие цены (цена ордера должна быть близкой к цене открытия позиции)
                    price_diff = abs(order_price - donor_pos.price_open)
                    symbol_info = self.terminal_manager.check_and_select_client_symbol(symbol)
                    if symbol_info:
                        point = symbol_info.get('point', 0.00001)
                        max_price_diff = max(point * 10, 0.001)
                    else:
                        max_price_diff = 0.001
                    
                    if price_diff <= max_price_diff:
                        price_score = 10 * (1 - min(price_diff / max_price_diff, 1))
                        score += price_score
                    else:
                        continue  # Цена слишком отличается
                    
                    # Проверяем соответствие времени (время размещения ордера должно быть близким к времени открытия позиции)
                    # Время открытия позиции может быть немного позже времени размещения ордера
                    if order_time_setup > 0 and donor_pos.time:
                        # time_setup может быть timestamp или datetime, нормализуем
                        if isinstance(order_time_setup, (int, float)):
                            order_time_ts = order_time_setup
                        else:
                            # Если это datetime объект
                            order_time_ts = order_time_setup.timestamp() if hasattr(order_time_setup, 'timestamp') else 0
                        
                        donor_time_ts = donor_pos.time.timestamp() if hasattr(donor_pos.time, 'timestamp') else 0
                        
                        if order_time_ts > 0 and donor_time_ts > 0:
                            time_diff = abs(donor_time_ts - order_time_ts)
                            
                            # Время открытия позиции должно быть близко к времени размещения ордера
                            # (позиция может открыться сразу после размещения ордера или немного позже)
                            if time_diff <= 300:  # До 5 минут разницы
                                time_score = 10 * (1 - min(time_diff / 300, 1))
                                score += time_score
                            elif time_diff <= 3600:  # До 1 часа - снижаем score, но не исключаем
                                time_score = 5 * (1 - min((time_diff - 300) / 3300, 1))
                                score += time_score
                            else:
                                # Слишком большой разрыв во времени - пропускаем
                                continue
                    
                    # Сохраняем лучший матч
                    if score > best_score:
                        best_score = score
                        best_donor_match = donor_pos
                
                if best_donor_match and best_score >= 15:  # Минимальный порог
                    # Восстанавливаем отслеживание открывающего ордера
                    self.pending_orders[order_ticket] = {
                        'donor_ticket': best_donor_match.ticket,
                        'symbol': symbol,
                        'order_type': order_type,
                        'original_price': order_price
                    }
                    restored_orders += 1
                    self.logger.info(f"Восстановлен открывающий ордер: ticket={order_ticket}, donor={best_donor_match.ticket}, "
                          f"symbol={symbol}, score={best_score:.1f}")
        
        if restored_orders > 0:
            self.logger.info(f"Восстановлено ордеров: {restored_orders}")
        else:
            self.logger.info("Pending ордера не найдены")
        
        self.logger.info("Восстановление состояния завершено")
    
    def process_new_positions(self):
        """Обработка новых позиций на донорском аккаунте"""
        # Получаем новые позиции (оба процесса работают параллельно, переключение не нужно)
        new_positions = self.position_monitor.get_new_positions()
        
        for position in new_positions:
            # Пропускаем символы, которые недоступны на клиенте
            if position.symbol in self.skipped_symbols:
                continue
            
            # Проверяем, не появилась ли эта позиция из-за исполнения отложенного ордера, который мы уже скопировали
            # В MT4/MT5 ticket позиции может совпадать с ticket ордера
            if position.ticket in self.donor_pending_orders:
                # Это позиция, которая появилась из отложенного ордера
                client_order_ticket = self.donor_pending_orders[position.ticket]
                self.logger.info(f"Позиция донора {position.ticket} появилась из отложенного ордера. Проверяем клиентский ордер {client_order_ticket}...")
                
                # Проверяем, исполнился ли клиентский ордер
                client_order_data = self.terminal_manager.get_client_order_by_ticket(client_order_ticket)
                
                if client_order_data is None:
                    # Ордер исчез - возможно, исполнился
                    time.sleep(0.3)  # Небольшая задержка для обработки MT5
                    
                    # Ищем клиентскую позицию
                    client_positions = self.position_monitor.get_client_positions()
                    found_client_position = None
                    for pos in client_positions:
                        if (pos.symbol == position.symbol and 
                            pos.type == position.type and
                            pos.ticket not in self.client_positions.values()):
                            # Проверяем время открытия
                            time_diff = abs((pos.time - position.time).total_seconds())
                            if time_diff < 60:  # Открыта примерно в то же время
                                found_client_position = pos
                                break
                    
                    if found_client_position:
                        # Связываем позиции
                        self.client_positions[position.ticket] = found_client_position.ticket
                        self.position_monitor.mark_position_copied(
                            position.ticket,
                            found_client_position.ticket
                        )
                        # Инициализируем состояние новой позиции клиента
                        client_positions_list = self.position_monitor.get_client_positions()
                        for pos in client_positions_list:
                            if pos.ticket == found_client_position.ticket:
                                self.position_monitor.client_position_states[pos.ticket] = pos
                                break
                        del self.donor_pending_orders[position.ticket]
                        self.logger.info(f"Позиция связана с клиентской позицией из ордера: донор={position.ticket}, клиент={found_client_position.ticket}")
                        self.save_sync_state()
                        continue
                    else:
                        self.logger.warning(f"Клиентский ордер {client_order_ticket} исчез, но позиция не найдена. Продолжаем обычное копирование...")
                        # Удаляем из отслеживания, чтобы не мешать обычному копированию
                        del self.donor_pending_orders[position.ticket]
            
            self.logger.info(f"Обнаружена новая позиция: ticket={position.ticket}, symbol={position.symbol}, "
                  f"type={position.type}, volume={position.volume}, price={position.price_open}")
            
            # Определяем magic number: копируем с донора, если опция включена
            magic = position.magic if (self.copy_donor_magic and position.magic is not None) else None
            
            # Выбираем стиль копирования
            if self.config.copy_style == "by_market":
                # Стиль By_Market: мгновенное открытие по маркету
                if position.type == mt5.POSITION_TYPE_BUY:
                    order_type = mt5.ORDER_TYPE_BUY
                else:
                    order_type = mt5.ORDER_TYPE_SELL
                
                deal_ticket = self.order_manager.place_market_order(
                    symbol=position.symbol,
                    order_type=order_type,
                    volume=position.volume,
                    donor_balance=self.donor_balance,
                    client_balance=self.client_balance,
                    magic=magic,
                    sl=position.sl if self.config.order_config.copy_sl_tp else None,
                    tp=position.tp if self.config.order_config.copy_sl_tp else None
                )
                
                if deal_ticket:
                    # Даем небольшую задержку для обработки MT5
                    time.sleep(0.3)
                    
                    # Получаем открытую позицию
                    client_position = self.order_manager.get_position_by_symbol(position.symbol)
                    if client_position:
                        self.client_positions[position.ticket] = client_position.ticket
                        self.position_monitor.mark_position_copied(
                            position.ticket,
                            client_position.ticket
                        )
                        # Инициализируем состояние новой позиции клиента
                        client_positions = self.position_monitor.get_client_positions()
                        for pos in client_positions:
                            if pos.ticket == client_position.ticket:
                                self.position_monitor.client_position_states[pos.ticket] = pos
                                break
                        self.logger.info(f"Позиция скопирована по маркету: донор={position.ticket}, клиент={client_position.ticket}")
                        # Сохраняем состояние при изменении статуса (новая позиция скопирована)
                        self.save_sync_state()
                    else:
                        self.logger.warning(f"Рыночный ордер исполнен (deal={deal_ticket}), но позиция не найдена. Проверка в следующей итерации...")
                else:
                    # Ошибка размещения рыночного ордера
                    symbol_data = self.terminal_manager.check_and_select_client_symbol(position.symbol)
                    if symbol_data is None:
                        if position.symbol not in self.skipped_symbols:
                            self.skipped_symbols.add(position.symbol)
                            self.logger.warning(f"Символ {position.symbol} недоступен на клиентском аккаунте. Позиция будет пропущена.")
            else:
                # Стиль By_Limits: лимитные ордера с оптимизацией (текущий метод)
                if position.type == mt5.POSITION_TYPE_BUY:
                    order_type = mt5.ORDER_TYPE_BUY_LIMIT
                else:
                    order_type = mt5.ORDER_TYPE_SELL_LIMIT
                
                order_ticket = self.order_manager.place_limit_order(
                    symbol=position.symbol,
                    order_type=order_type,
                    volume=position.volume,
                    original_price=position.price_open,
                    donor_balance=self.donor_balance,
                    client_balance=self.client_balance,
                    magic=magic,
                    sl=position.sl if self.config.order_config.copy_sl_tp else None,
                    tp=position.tp if self.config.order_config.copy_sl_tp else None
                )
                
                # Если символ недоступен, добавляем в список пропущенных
                if order_ticket is None:
                    # Проверяем, была ли ошибка из-за недоступного символа
                    symbol_data = self.terminal_manager.check_and_select_client_symbol(position.symbol)
                    if symbol_data is None:
                        if position.symbol not in self.skipped_symbols:
                            self.skipped_symbols.add(position.symbol)
                            self.logger.warning(f"Символ {position.symbol} недоступен на клиентском аккаунте. Позиция будет пропущена.")
                            self.logger.warning(f"Добавьте символ {position.symbol} в Market Watch клиентского терминала или проверьте доступность символа у брокера.")
                
                if order_ticket:
                    # Сохраняем информацию об ордере для оптимизации
                    # position_id будет получен при следующей проверке ордера
                    self.pending_orders[order_ticket] = {
                        'donor_ticket': position.ticket,
                        'symbol': position.symbol,
                        'order_type': order_type,
                        'original_price': position.price_open,
                        'position_id': None  # Будет обновлен при проверке ордера
                    }
                    
                    self.logger.info(f"Ожидание исполнения ордера {order_ticket}...")
                    
                    # Делаем быструю проверку, но не блокируем основной цикл
                    # Если ордер не исполнен сразу, проверим в следующей итерации
                    if self.order_manager.wait_for_order_fill(order_ticket, timeout=1.0):
                        # Удаляем из отслеживаемых ордеров
                        if order_ticket in self.pending_orders:
                            del self.pending_orders[order_ticket]
                        
                        # Даем небольшую задержку для обработки MT5
                        time.sleep(0.3)
                        
                        # Получаем открытую позицию
                        client_position = self.order_manager.get_position_by_symbol(position.symbol)
                        if client_position:
                            self.client_positions[position.ticket] = client_position.ticket
                            self.position_monitor.mark_position_copied(
                                position.ticket,
                                client_position.ticket
                            )
                            # Инициализируем состояние новой позиции клиента
                            client_positions = self.position_monitor.get_client_positions()
                            for pos in client_positions:
                                if pos.ticket == client_position.ticket:
                                    self.position_monitor.client_position_states[pos.ticket] = pos
                                    break
                            self.logger.info(f"Позиция скопирована: донор={position.ticket}, клиент={client_position.ticket}")
                            # Сохраняем состояние при изменении статуса (новая позиция скопирована)
                            self.save_sync_state()
                        else:
                            self.logger.warning(f"Ордер {order_ticket} исполнен, но позиция не найдена. Проверка в следующей итерации...")
                    else:
                        # Ордер еще не исполнен, проверим в следующей итерации через check_pending_order_fills
                        pass
    
    def _convert_order_type_to_mt5(self, order_type: int) -> Optional[int]:
        """
        Конвертация типа ордера из MT4/MT5 донора в MT5 клиента
        
        Args:
            order_type: Тип ордера донора (MT4 или MT5)
            
        Returns:
            Тип ордера MT5 или None, если тип не поддерживается
        """
        # MT4 типы: OP_BUYLIMIT=2, OP_SELLLIMIT=3, OP_BUYSTOP=4, OP_SELLSTOP=5
        # MT5 типы: ORDER_TYPE_BUY_LIMIT=2, ORDER_TYPE_SELL_LIMIT=3, ORDER_TYPE_BUY_STOP=4, ORDER_TYPE_SELL_STOP=5
        # Типы совпадают, но нужно проверить, что это отложенный ордер
        if order_type == 2:  # OP_BUYLIMIT / ORDER_TYPE_BUY_LIMIT
            return mt5.ORDER_TYPE_BUY_LIMIT
        elif order_type == 3:  # OP_SELLLIMIT / ORDER_TYPE_SELL_LIMIT
            return mt5.ORDER_TYPE_SELL_LIMIT
        elif order_type == 4:  # OP_BUYSTOP / ORDER_TYPE_BUY_STOP
            return mt5.ORDER_TYPE_BUY_STOP
        elif order_type == 5:  # OP_SELLSTOP / ORDER_TYPE_SELL_STOP
            return mt5.ORDER_TYPE_SELL_STOP
        elif order_type == 6:  # ORDER_TYPE_BUY_STOP_LIMIT (только MT5)
            return mt5.ORDER_TYPE_BUY_STOP_LIMIT
        elif order_type == 7:  # ORDER_TYPE_SELL_STOP_LIMIT (только MT5)
            return mt5.ORDER_TYPE_SELL_STOP_LIMIT
        else:
            return None
    
    def process_new_orders(self):
        """Обработка новых отложенных ордеров на донорском аккаунте"""
        # Получаем все ордера от всех доноров
        all_donor_orders = self.donor_manager.get_all_orders()
        
        # Отслеживаем уже скопированные ордера
        known_donor_orders = set(self.donor_pending_orders.keys())
        current_donor_orders = {order.ticket for order in all_donor_orders}
        
        # Находим новые ордера
        new_orders = [order for order in all_donor_orders if order.ticket not in known_donor_orders]
        
        for order in new_orders:
            # Пропускаем символы, которые недоступны на клиенте
            if order.symbol in self.skipped_symbols:
                continue
            
            # Конвертируем тип ордера в MT5
            mt5_order_type = self._convert_order_type_to_mt5(order.type)
            if mt5_order_type is None:
                self.logger.warning(f"Неподдерживаемый тип ордера донора: {order.type} (ticket={order.ticket})")
                continue
            
            self.logger.info(f"Обнаружен новый отложенный ордер донора: ticket={order.ticket}, symbol={order.symbol}, "
                  f"type={mt5_order_type}, volume={order.volume}, price={order.price_open}")
            
            # Размещаем отложенный ордер на клиентском аккаунте
            client_order_ticket = self.order_manager.place_pending_order(
                symbol=order.symbol,
                order_type=mt5_order_type,
                volume=order.volume,
                price=order.price_open,
                donor_balance=self.donor_balance,
                client_balance=self.client_balance,
                sl=order.sl if self.config.order_config.copy_sl_tp else None,
                tp=order.tp if self.config.order_config.copy_sl_tp else None
            )
            
            if client_order_ticket:
                # Сохраняем связь между донорским и клиентским ордером
                self.donor_pending_orders[order.ticket] = client_order_ticket
                self.logger.info(f"Отложенный ордер скопирован: донор={order.ticket}, клиент={client_order_ticket}")
                self.save_sync_state()
            else:
                # Ошибка размещения ордера
                symbol_data = self.terminal_manager.check_and_select_client_symbol(order.symbol)
                if symbol_data is None:
                    if order.symbol not in self.skipped_symbols:
                        self.skipped_symbols.add(order.symbol)
                        self.logger.warning(f"Символ {order.symbol} недоступен на клиентском аккаунте. Ордер будет пропущен.")
    
    def process_closed_orders(self):
        """Обработка закрытых/удаленных отложенных ордеров на донорском аккаунте"""
        # Получаем все ордера от всех доноров
        all_donor_orders = self.donor_manager.get_all_orders()
        current_donor_order_tickets = {order.ticket for order in all_donor_orders}
        
        # Находим ордера, которые были удалены
        closed_order_tickets = set(self.donor_pending_orders.keys()) - current_donor_order_tickets
        
        for donor_order_ticket in closed_order_tickets:
            client_order_ticket = self.donor_pending_orders.get(donor_order_ticket)
            if client_order_ticket:
                # Проверяем, не исполнился ли клиентский ордер (превратился в позицию)
                client_order_data = self.terminal_manager.get_client_order_by_ticket(client_order_ticket)
                
                if client_order_data is None:
                    # Ордер исчез - проверяем, не превратился ли он в позицию
                    # Получаем информацию о донорском ордере для поиска
                    donor_order_info = None
                    # Ищем в последних данных донора
                    for order in all_donor_orders:
                        if order.ticket == donor_order_ticket:
                            donor_order_info = order
                            break
                    
                    # Если не нашли в текущих ордерах, проверяем новые позиции донора
                    # Возможно, ордер исполнился и превратился в позицию
                    donor_positions = self.position_monitor.get_donor_positions()
                    matching_position = None
                    for pos in donor_positions:
                        # В MT4/MT5 ticket позиции может совпадать с ticket ордера
                        if pos.ticket == donor_order_ticket:
                            matching_position = pos
                            break
                    
                    if matching_position:
                        # Ордер донора исполнился и превратился в позицию
                        self.logger.info(f"Отложенный ордер донора {donor_order_ticket} исполнился и превратился в позицию {matching_position.ticket}")
                        
                        # Ищем клиентскую позицию, которая могла открыться из клиентского ордера
                        time.sleep(0.3)  # Небольшая задержка для обработки MT5
                        
                        # Определяем ожидаемый тип позиции
                        if donor_order_info:
                            mt5_order_type = self._convert_order_type_to_mt5(donor_order_info.type)
                            if mt5_order_type in [mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_BUY_STOP, mt5.ORDER_TYPE_BUY_STOP_LIMIT]:
                                expected_position_type = mt5.POSITION_TYPE_BUY
                            else:
                                expected_position_type = mt5.POSITION_TYPE_SELL
                        else:
                            # Если не знаем тип ордера, используем тип позиции донора
                            expected_position_type = matching_position.type
                        
                        # Ищем позицию по символу и типу
                        client_positions = self.position_monitor.get_client_positions()
                        found_client_position = None
                        for pos in client_positions:
                            if (pos.symbol == matching_position.symbol and 
                                pos.type == expected_position_type and
                                pos.ticket not in self.client_positions.values()):
                                # Проверяем время открытия (должно быть недавно)
                                time_diff = abs((pos.time - matching_position.time).total_seconds())
                                if time_diff < 60:  # Открыта примерно в то же время
                                    found_client_position = pos
                                    break
                        
                        if found_client_position:
                            # Связываем позиции
                            self.client_positions[matching_position.ticket] = found_client_position.ticket
                            self.position_monitor.mark_position_copied(
                                matching_position.ticket,
                                found_client_position.ticket
                            )
                            del self.donor_pending_orders[donor_order_ticket]
                            self.logger.info(f"Позиция связана: донор={matching_position.ticket}, клиент={found_client_position.ticket} (из ордера {donor_order_ticket})")
                            self.save_sync_state()
                            continue
                        else:
                            self.logger.warning(f"Ордер донора {donor_order_ticket} исполнился, но клиентская позиция не найдена. Удаляем из отслеживания.")
                            del self.donor_pending_orders[donor_order_ticket]
                            self.save_sync_state()
                            continue
                
                # Если ордер еще существует, отменяем его
                self.logger.info(f"Отложенный ордер донора {donor_order_ticket} удален. Отменяем клиентский ордер {client_order_ticket}")
                if self.order_manager.cancel_order(client_order_ticket):
                    del self.donor_pending_orders[donor_order_ticket]
                    self.logger.info(f"Клиентский отложенный ордер {client_order_ticket} отменен")
                    self.save_sync_state()
                else:
                    self.logger.warning(f"Не удалось отменить клиентский отложенный ордер {client_order_ticket}")
    
    def process_closed_positions(self):
        """Обработка закрытых позиций на донорском аккаунте"""
        # Получаем закрытые позиции (донорский процесс работает параллельно)
        closed_tickets = self.position_monitor.get_closed_positions()
        
        if closed_tickets:
            self.logger.info(f"Обработка {len(closed_tickets)} закрытых позиций донора: {closed_tickets}")
        
        if closed_tickets:
            for donor_ticket in closed_tickets:
                # Проверяем, есть ли лимитный ордер открытия для этой позиции донора
                pending_order_ticket = None
                for order_ticket, order_info in self.pending_orders.items():
                    if order_info.get('donor_ticket') == donor_ticket:
                        pending_order_ticket = order_ticket
                        break
                
                # Если есть лимитный ордер открытия, но позиция еще не открыта - отменяем ордер
                if pending_order_ticket and donor_ticket not in self.client_positions:
                    self.logger.info(f"Позиция донора {donor_ticket} закрыта до открытия клиентской позиции. Отменяем лимитный ордер {pending_order_ticket}")
                    self.logger.info(f"Лимитный ордер {pending_order_ticket} найден в pending_orders для донорской позиции {donor_ticket}")
                    if self.order_manager.cancel_order(pending_order_ticket):
                        # Удаляем ордер из отслеживаемых
                        if pending_order_ticket in self.pending_orders:
                            del self.pending_orders[pending_order_ticket]
                        # Удаляем позицию из отслеживаемых
                        self.position_monitor.remove_donor_position_state(donor_ticket)
                        self.logger.info(f"Лимитный ордер {pending_order_ticket} отменен")
                        # Сохраняем состояние при изменении статуса (ордер отменен)
                        self.save_sync_state()
                    else:
                        self.logger.warning(f"Не удалось отменить лимитный ордер {pending_order_ticket}")
                    continue
                
                # Если связь не установлена, но ордер уже сработал (его нет в pending_orders)
                # - пытаемся найти клиентскую позицию по символу и типу позиции донора
                if donor_ticket not in self.client_positions:
                    self.logger.info(f"Позиция донора {donor_ticket} закрыта, но связь с клиентской позицией не установлена. Проверяем состояние...")
                    # Получаем информацию о позиции донора из сохраненного состояния
                    donor_position_info = self.position_monitor.donor_position_states.get(donor_ticket)
                    if donor_position_info:
                        self.logger.info(f"Найдена информация о позиции донора {donor_ticket}: symbol={donor_position_info.symbol}, type={donor_position_info.type}")
                        # Ищем клиентскую позицию по символу и типу
                        # Получаем все позиции по символу и ищем ту, которая:
                        # 1. Не связана с другим донорским тикетом
                        # 2. Имеет правильный тип
                        # 3. Была открыта недавно (в течение последних 60 секунд)
                        client_positions = self.position_monitor.get_client_positions()
                        current_time = time.time()
                        
                        found_position = None
                        for pos in client_positions:
                            if pos.symbol == donor_position_info.symbol and pos.type == donor_position_info.type:
                                # Проверяем, не связана ли позиция с другим донором
                                is_linked = False
                                for linked_donor_ticket, linked_client_ticket in self.client_positions.items():
                                    if linked_client_ticket == pos.ticket and linked_donor_ticket != donor_ticket:
                                        is_linked = True
                                        break
                                
                                if not is_linked:
                                    # Проверяем, что позиция была открыта недавно (в течение последних 60 секунд)
                                    pos_open_time = pos.time.timestamp()
                                    if current_time - pos_open_time < 60:
                                        found_position = pos
                                        break
                        
                        if found_position:
                            # Устанавливаем связь
                            self.client_positions[donor_ticket] = found_position.ticket
                            self.position_monitor.mark_position_copied(donor_ticket, found_position.ticket)
                            self.logger.info(f"Найдена клиентская позиция {found_position.ticket} для закрытой позиции донора {donor_ticket}. Связь установлена.")
                        else:
                            # Позиция не найдена - возможно уже закрыта или не была открыта
                            self.logger.debug(f"Позиция донора {donor_ticket} закрыта, но клиентская позиция не найдена. Возможно, она уже закрыта или не была открыта.")
                            # Удаляем позицию из отслеживаемых
                            self.position_monitor.remove_donor_position_state(donor_ticket)
                            continue
                    else:
                        # Информация о позиции донора не найдена - пропускаем
                        self.logger.debug(f"Позиция донора {donor_ticket} закрыта, но информация о ней не найдена. Пропускаем.")
                        continue
                
                if donor_ticket not in self.client_positions:
                    continue
                
                client_ticket = self.client_positions[donor_ticket]
                self.logger.info(f"Позиция закрыта на донорском аккаунте: ticket={donor_ticket}, клиентская позиция: {client_ticket}")
                
                # Получаем информацию о клиентской позиции (клиентский процесс работает параллельно)
                client_position_data = self.terminal_manager.get_client_position_by_ticket(client_ticket)
                if not client_position_data:
                    # Позиция уже закрыта
                    del self.client_positions[donor_ticket]
                    if donor_ticket in self.pending_close_orders:
                        del self.pending_close_orders[donor_ticket]
                    # Удаляем состояния позиций
                    self.position_monitor.remove_donor_position_state(donor_ticket)
                    self.position_monitor.remove_client_position_state(client_ticket)
                    continue
                
                # Выбираем стиль закрытия
                if self.config.copy_style == "by_market":
                    # Стиль By_Market: мгновенное закрытие по маркету
                    success = self.order_manager.close_position_by_market(
                        position_ticket=client_ticket,
                        symbol=client_position_data['symbol'],
                        position_type=client_position_data['type'],
                        volume=client_position_data['volume']
                    )
                    
                    if success:
                        # Позиция закрыта
                        del self.client_positions[donor_ticket]
                        self.position_monitor.remove_donor_position_state(donor_ticket)
                        self.position_monitor.remove_client_position_state(client_ticket)
                        self.logger.info(f"Позиция {client_ticket} закрыта по маркету")
                        # Сохраняем состояние при изменении статуса (позиция закрыта)
                        self.save_sync_state()
                    else:
                        self.logger.error(f"Не удалось закрыть позицию {client_ticket} по маркету")
                else:
                    # Стиль By_Limits: закрытие через лимитный ордер (текущий метод)
                    # Получаем цену закрытия
                    tick_data = self.terminal_manager.get_client_symbol_tick(client_position_data['symbol'])
                    if tick_data is None:
                        self.logger.warning(f"Не удалось получить тик для {client_position_data['symbol']}")
                        continue
                    
                    # Определяем цену закрытия в зависимости от типа позиции
                    if client_position_data['type'] == mt5.POSITION_TYPE_BUY:
                        close_price = tick_data['bid']  # Цена продажи для длинной позиции
                    else:
                        close_price = tick_data['ask']  # Цена покупки для короткой позиции
                    
                    # Размещаем лимитный ордер для закрытия
                    close_order_ticket = self.order_manager.close_position_by_opposite_order(
                        symbol=client_position_data['symbol'],
                        position_volume=client_position_data['volume'],
                        position_type=client_position_data['type'],
                        original_close_price=close_price,
                        client_ticket=client_ticket  # Передаем тикет исходной позиции
                    )
                    
                    if close_order_ticket:
                        self.pending_close_orders[donor_ticket] = close_order_ticket
                        # Сохраняем информацию об ордере закрытия для оптимизации
                        # Определяем тип ордера закрытия
                        if client_position_data['type'] == mt5.POSITION_TYPE_BUY:
                            close_order_type = mt5.ORDER_TYPE_SELL_LIMIT
                        else:
                            close_order_type = mt5.ORDER_TYPE_BUY_LIMIT
                        
                        self.pending_close_orders_info[close_order_ticket] = {
                            'donor_ticket': donor_ticket,
                            'symbol': client_position_data['symbol'],
                            'order_type': close_order_type,
                            'original_close_price': close_price,
                            'client_ticket': client_ticket  # Сохраняем тикет исходной позиции
                        }
                        # Сохраняем связь между ордером закрытия и исходной позицией
                        self.close_order_to_client_position[close_order_ticket] = client_ticket
                        self.logger.info(f"Размещен ордер закрытия: ticket={close_order_ticket} для позиции {client_ticket}")
                        # Сохраняем состояние при изменении статуса (размещен закрывающий ордер)
                        self.save_sync_state()
                    else:
                        self.logger.error(f"Не удалось разместить ордер закрытия для позиции {client_ticket}")
    
    def check_pending_close_orders(self):
        """
        Проверка исполнения ордеров закрытия через TRADE_ACTION_CLOSE_BY.
        
        Логика:
        1. Размещается лимитный ордер в противоположную сторону
        2. Когда ордер срабатывает, он открывает встречную позицию с нашим magic number
        3. Находим встречную позицию с нашим magic number
        4. Используем TRADE_ACTION_CLOSE_BY для закрытия исходной позиции встречной
        """
        closed_orders = []
        
        for donor_ticket, close_order_ticket in list(self.pending_close_orders.items()):
            # Получаем тикет исходной позиции
            client_ticket = self.close_order_to_client_position.get(close_order_ticket)
            if not client_ticket:
                # Пробуем получить из client_positions
                client_ticket = self.client_positions.get(donor_ticket)
            
            if not client_ticket:
                # Позиция уже удалена из словаря, считаем закрытой
                closed_orders.append(donor_ticket)
                continue
            
            # Проверяем, исполнен ли лимитный ордер
            order_data = self.terminal_manager.get_client_order_by_ticket(close_order_ticket)
            
            if order_data is None:
                # Ордер больше не в списке - значит он сработал и открыл встречную позицию
                # Даем небольшую задержку для обработки MT5
                time.sleep(0.3)
                
                # Получаем информацию об исходной позиции
                original_position = self.terminal_manager.get_client_position_by_ticket(client_ticket)
                
                if not original_position:
                    # Исходная позиция уже закрыта (возможно, автоматическим netting)
                    self.logger.info(f"Позиция {client_ticket} уже закрыта")
                    self.position_monitor.remove_client_position_state(client_ticket)
                    closed_orders.append(donor_ticket)
                    continue
                
                # Ищем встречную позицию с нашим magic number
                symbol = original_position['symbol']
                position_type = original_position['type']
                opposite_type = mt5.POSITION_TYPE_SELL if position_type == mt5.POSITION_TYPE_BUY else mt5.POSITION_TYPE_BUY
                
                # Получаем все позиции по символу (они уже отфильтрованы по magic number в terminal_worker)
                all_positions = self.terminal_manager.get_client_positions()
                symbol_positions = [
                    p for p in all_positions 
                    if p.get('symbol') == symbol and p.get('type') == opposite_type and p.get('ticket') != client_ticket
                ]
                
                if symbol_positions:
                    # Нашли встречную позицию с нашим magic number
                    opposite_position = symbol_positions[0]  # Берем первую встречную позицию
                    opposite_ticket = opposite_position['ticket']
                    
                    # Используем TRADE_ACTION_CLOSE_BY для закрытия исходной позиции встречной
                    success = self.order_manager.close_position_by_opposite_position(
                        original_position_ticket=client_ticket,
                        opposite_position_ticket=opposite_ticket
                    )
                    
                    if success:
                        # Закрытие успешно
                        self.position_monitor.remove_client_position_state(client_ticket)
                        closed_orders.append(donor_ticket)
                    else:
                        # Ошибка закрытия, попробуем в следующей итерации
                        self.logger.warning(f"Не удалось закрыть позицию {client_ticket} через CLOSE_BY, попробуем позже")
                else:
                    # Встречная позиция еще не появилась или уже закрыта
                    # Возможно, ордер только что сработал и позиция еще не появилась
                    # Проверяем еще раз исходную позицию
                    if not self.terminal_manager.get_client_position_by_ticket(client_ticket):
                        # Исходная позиция закрыта (возможно, автоматическим netting)
                        self.logger.info(f"Позиция {client_ticket} закрыта (автоматический netting)")
                        self.position_monitor.remove_client_position_state(client_ticket)
                        closed_orders.append(donor_ticket)
                        # Сохраняем состояние при изменении статуса (позиция закрыта)
                        self.save_sync_state()
        
        # Очищаем закрытые ордера
        if closed_orders:
            for donor_ticket in closed_orders:
                if donor_ticket in self.pending_close_orders:
                    close_order_ticket = self.pending_close_orders[donor_ticket]
                    # Удаляем информацию об ордере закрытия
                    if close_order_ticket in self.pending_close_orders_info:
                        del self.pending_close_orders_info[close_order_ticket]
                    # Удаляем связь между ордером закрытия и позицией
                    if close_order_ticket in self.close_order_to_client_position:
                        del self.close_order_to_client_position[close_order_ticket]
                    del self.pending_close_orders[donor_ticket]
                if donor_ticket in self.client_positions:
                    del self.client_positions[donor_ticket]
            # Сохраняем состояние после очистки закрытых ордеров
            self.save_sync_state()
            # Удаляем состояние позиции донора
            self.position_monitor.remove_donor_position_state(donor_ticket)
    
    def monitor_position_changes(self):
        """Мониторинг изменений статусов позиций"""
        # Мониторинг изменений позиций донора
        donor_changes = self.position_monitor.get_position_changes_donor()
        for change in donor_changes:
            self._print_position_change(change)
        
        # Мониторинг изменений позиций клиента
        client_changes = self.position_monitor.get_position_changes_client()
        for change in client_changes:
            self._print_position_change(change)
    
    def check_pending_order_fills(self):
        """Проверка исполнения размещенных ордеров"""
        if not self.pending_orders:
            return
        
        for order_ticket, order_info in list(self.pending_orders.items()):
            # Проверяем, исполнен ли ордер
            order_data = self.terminal_manager.get_client_order_by_ticket(order_ticket)
            
            # Обновляем position_id, если он изменился
            if order_data:
                new_position_id = order_data.get('position_id')
                if new_position_id and new_position_id != order_info.get('position_id'):
                    order_info['position_id'] = new_position_id
                    self.logger.debug(f"Ордер {order_ticket}: position_id обновлен = {new_position_id}")
            
            if order_data is None:
                # Ордер больше не в списке - возможно исполнен
                self.logger.info(f"Ордер {order_ticket} исчез из списка ордеров - проверяем, превратился ли он в позицию...")
                time.sleep(0.3)  # Небольшая задержка для обработки MT5
                
                # Получаем открытую позицию
                donor_ticket = order_info['donor_ticket']
                position_id = order_info.get('position_id')
                
                self.logger.debug(f"Ордер {order_ticket}: donor_ticket={donor_ticket}, position_id={position_id}")
                
                # В MT5 ticket позиции совпадает с ticket ордера (position_id ордера = ticket позиции)
                # Поэтому ищем позицию напрямую по ticket ордера
                found_position = None
                
                # Сначала пробуем по position_id, если он установлен
                search_ticket = position_id if position_id else order_ticket
                self.logger.info(f"Ордер {order_ticket} исполнен. Ищем позицию по ticket={search_ticket} (position_id={position_id}, ticket ордера={order_ticket})")
                
                position_data = self.terminal_manager.get_client_position_by_ticket(search_ticket)
                if position_data:
                    pos_ticket = position_data.get('ticket')
                    self.logger.info(f"Найдена позиция по ticket={search_ticket}: ticket позиции={pos_ticket}")
                    client_positions = self.position_monitor.get_client_positions()
                    for pos in client_positions:
                        if pos.ticket == pos_ticket:
                            found_position = pos
                            self.logger.info(f"✓ Ордер {order_ticket} превратился в позицию: ticket={pos.ticket}, symbol={pos.symbol}, type={pos.type}")
                            break
                else:
                    self.logger.debug(f"Позиция с ticket={search_ticket} не найдена, пробуем по ticket ордера={order_ticket}")
                    # Если не нашли по position_id, пробуем по ticket ордера
                    if search_ticket != order_ticket:
                        position_data = self.terminal_manager.get_client_position_by_ticket(order_ticket)
                        if position_data:
                            pos_ticket = position_data.get('ticket')
                            self.logger.info(f"Найдена позиция по ticket ордера={order_ticket}: ticket позиции={pos_ticket}")
                            client_positions = self.position_monitor.get_client_positions()
                            for pos in client_positions:
                                if pos.ticket == pos_ticket:
                                    found_position = pos
                                    self.logger.info(f"✓ Ордер {order_ticket} превратился в позицию: ticket={pos.ticket}, symbol={pos.symbol}, type={pos.type}")
                                    break
                
                # Если не нашли по position_id, ищем по символу и типу (fallback)
                if not found_position:
                    symbol = order_info['symbol']
                    order_type = order_info['order_type']
                    expected_position_type = mt5.POSITION_TYPE_BUY if order_type == mt5.ORDER_TYPE_BUY_LIMIT else mt5.POSITION_TYPE_SELL
                    
                    client_positions = self.position_monitor.get_client_positions()
                    current_time = time.time()
                    
                    # Находим позицию, которая не связана с другим донором и имеет правильный тип
                    for pos in client_positions:
                        if pos.symbol == symbol and pos.type == expected_position_type:
                            # Проверяем, не связана ли позиция с другим донором
                            is_linked = False
                            for linked_donor_ticket, linked_client_ticket in self.client_positions.items():
                                if linked_client_ticket == pos.ticket and linked_donor_ticket != donor_ticket:
                                    is_linked = True
                                    break
                            
                            if not is_linked:
                                # Проверяем, что позиция была открыта недавно (в течение последних 60 секунд)
                                # И что ticket позиции не совпадает с ticket ордера (это была бы ошибка)
                                pos_open_time = pos.time.timestamp()
                                if current_time - pos_open_time < 60 and pos.ticket != order_ticket:
                                    found_position = pos
                                    self.logger.info(f"✓ Позиция найдена по символу и типу (fallback): ticket={pos.ticket}, symbol={pos.symbol}, type={pos.type}, время открытия={pos.time}")
                                    break
                
                if found_position:
                    # Позиция найдена - ордер исполнен
                    self.client_positions[donor_ticket] = found_position.ticket
                    self.position_monitor.mark_position_copied(
                        donor_ticket,
                        found_position.ticket
                    )
                    # Инициализируем состояние новой позиции клиента
                    for pos in client_positions:
                        if pos.ticket == found_position.ticket:
                            self.position_monitor.client_position_states[pos.ticket] = pos
                            break
                    self.logger.info(f"Позиция скопирована: донор={donor_ticket}, клиент={found_position.ticket}")
                    
                    # Удаляем из отслеживаемых ордеров
                    del self.pending_orders[order_ticket]
                    
                    # Сохраняем состояние при изменении статуса (ордер исполнен, позиция открыта)
                    self.save_sync_state()
                else:
                    # Позиция не найдена - возможно ордер был отклонен
                    self.logger.warning(f"Ордер {order_ticket} удален, но позиция не найдена. Возможно, ордер был отклонен.")
                    # Удаляем из отслеживаемых, чтобы не проверять бесконечно
                    del self.pending_orders[order_ticket]
    
    def optimize_pending_orders(self):
        """Оптимизация цены размещенных ордеров"""
        # Оптимизация ордеров открытия (приближение к оригинальной цене открытия)
        if self.pending_orders:
            for order_ticket, order_info in list(self.pending_orders.items()):
                # Проверяем, существует ли ордер
                order_data = self.terminal_manager.get_client_order_by_ticket(order_ticket)
                if not order_data:
                    # Ордер исполнен или удален
                    del self.pending_orders[order_ticket]
                    continue
                
                # Пытаемся оптимизировать цену (приблизить к оригинальной цене открытия)
                optimized = self.order_manager.optimize_order_price(
                    order_ticket=order_ticket,
                    symbol=order_info['symbol'],
                    order_type=order_info['order_type'],
                    original_price=order_info['original_price']
                )
                
                # Если ордер был оптимизирован, обновляем информацию
                if optimized:
                    # Информация об ордере уже обновлена в optimize_order_price
                    pass
        
        # Оптимизация ордеров закрытия (приближение к текущей рыночной цене)
        if self.pending_close_orders_info:
            for close_order_ticket, close_order_info in list(self.pending_close_orders_info.items()):
                # Проверяем, существует ли ордер
                order_data = self.terminal_manager.get_client_order_by_ticket(close_order_ticket)
                if not order_data:
                    # Ордер исполнен или удален
                    del self.pending_close_orders_info[close_order_ticket]
                    continue
                
                # Пытаемся оптимизировать цену закрытия (приблизить к текущей рыночной цене)
                optimized = self.order_manager.optimize_close_order_price(
                    order_ticket=close_order_ticket,
                    symbol=close_order_info['symbol'],
                    order_type=close_order_info['order_type'],
                    original_close_price=close_order_info['original_close_price']
                )
                
                # Если ордер был оптимизирован, обновляем информацию
                if optimized:
                    # Информация об ордере уже обновлена в optimize_close_order_price
                    pass
    
    def _print_position_change(self, change_info: Dict):
        """
        Вывести информацию об изменении позиции
        
        Args:
            change_info: Словарь с информацией об изменении
        """
        account_type = change_info['account_type']
        ticket = change_info['ticket']
        symbol = change_info['symbol']
        changes = change_info['changes']
        
        self.logger.info(f"[{account_type}] Изменение позиции {ticket} ({symbol}):")
        for change in changes:
            field = change['field']
            old_val = change['old']
            new_val = change['new']
            delta = change['delta']
            
            if field == 'volume':
                if delta < 0:
                    self.logger.warning(f"Частичное закрытие: объем уменьшился с {old_val:.2f} до {new_val:.2f} (Δ {delta:+.2f})")
                else:
                    self.logger.info(f"Увеличение объема: {old_val:.2f} → {new_val:.2f} (Δ {delta:+.2f})")
    
    def run(self):
        """Запуск основного цикла"""
        if not self.initialize():
            self.logger.error("Ошибка инициализации. Завершение работы.")
            return
        
        self.running = True
        self.logger.info("Копировщик сделок запущен. Нажмите Ctrl+C для остановки.")
        
        try:
            while self.running:
                # Обработка новых позиций
                self.process_new_positions()
                
                # Обработка новых отложенных ордеров донора (если включено)
                if self.config.order_config.copy_pending_orders:
                    self.process_new_orders()
                    self.process_closed_orders()
                
                # Оптимизация цены размещенных ордеров (перед проверкой исполнения)
                self.optimize_pending_orders()
                
                # Проверка исполнения размещенных ордеров (если не были исполнены сразу)
                self.check_pending_order_fills()
                
                # Обработка закрытых позиций
                self.process_closed_positions()
                
                # Проверка ордеров закрытия
                self.check_pending_close_orders()
                
                # Мониторинг изменений статусов позиций (только открытие/закрытие)
                self.monitor_position_changes()
                
                # Пауза перед следующей проверкой
                time.sleep(self.config.check_interval)
        
        except KeyboardInterrupt:
            self.logger.info("\nПолучен сигнал остановки...")
        except Exception as e:
            self.logger.error(f"Ошибка в основном цикле: {e}", exc_info=True)
        finally:
            self.shutdown()
    
    def shutdown(self):
        """Завершение работы"""
        self.logger.info("Завершение работы копировщика...")
        # Сохраняем состояние перед завершением
        if self.save_sync_state():
            self.logger.info("Состояние синхронизации сохранено")
        self.running = False
        
        # Отключаем всех доноров
        if self.donor_manager:
            self.donor_manager.disconnect_all()
        
        # Закрываем подключения к терминалам
        if self.terminal_manager:
            self.terminal_manager.shutdown()
        
        self.logger.info("Работа завершена.")


def parse_args():
    """Парсинг аргументов командной строки"""
    parser = argparse.ArgumentParser(
        description='Копировщик сделок MT5 с поддержкой множественных доноров',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:
    # По умолчанию загружается donors_config.json из папки config
  python main.py
  
  # Указать другой конфигурационный файл
  python main.py --donors-config custom_donors.json
  
  # Игнорировать конфиг и использовать аргументы командной строки
  python main.py --ignore-donor-config --donor-api 12345678
  
  # Один донор через Python API (автоматически отменяет загрузку конфига)
  python main.py --donor-api 12345678
  
  # Один донор через Socket MT5
  python main.py --donor-socket-mt5 12345678 --socket-port 8888
  
  # Оптимизировать все ордера к рыночной цене
  python main.py --optimize-to-market
        """
    )
    
    # Группа доноров
    donor_group = parser.add_argument_group('Доноры', 'Настройка донорских аккаунтов')
    
    donor_group.add_argument(
        '--donors-config',
        type=str,
        metavar='FILE',
        help='Путь к JSON файлу с конфигурацией доноров (по умолчанию: config/donors_config.json)'
    )
    
    donor_group.add_argument(
        '--ignore-donor-config',
        action='store_true',
        default=False,
        help='Игнорировать конфигурационный файл config/donors_config.json и использовать только аргументы командной строки'
    )
    
    # Простые опции для быстрого запуска одного донора
    donor_group.add_argument(
        '--donor-api',
        type=int,
        metavar='ACCOUNT',
        help='Один донор через Python API MT5 (номер аккаунта, система найдет терминал автоматически). Отменяет загрузку конфига.'
    )
    
    donor_group.add_argument(
        '--donor-socket-mt4',
        type=int,
        metavar='ACCOUNT',
        help='Быстрый способ: один донор через Socket MT4 (номер аккаунта, подключение к MQL4 EA)'
    )
    
    donor_group.add_argument(
        '--donor-socket-mt5',
        type=int,
        metavar='ACCOUNT',
        help='Быстрый способ: один донор через Socket MT5 (номер аккаунта, подключение к MQL5 EA)'
    )
    
    donor_group.add_argument(
        '--socket-host',
        type=str,
        default='localhost',
        help='Хост для сокет-подключений (по умолчанию: localhost)'
    )
    
    donor_group.add_argument(
        '--socket-port',
        type=int,
        default=8888,
        help='Порт для сокет-подключений (по умолчанию: 8888, используется только с --donor-socket-*)'
    )
    
    # Группа настроек лота
    lot_group = parser.add_argument_group('Настройки лота', 'Параметры расчета размера лота')
    lot_group.add_argument(
        '--lot-mode',
        type=str,
        choices=['fixed', 'proportion', 'autolot'],
        default=None,
        help='Режим расчета лота: fixed (фиксированный), proportion (пропорциональный), autolot (автолот по балансу). По умолчанию: fixed'
    )
    lot_group.add_argument(
        '--lot-value',
        type=float,
        default=None,
        help='Значение для расчета лота (для fixed - размер лота, для proportion - коэффициент). По умолчанию: 0.01'
    )
    lot_group.add_argument(
        '--min-lot',
        type=float,
        default=None,
        help='Минимальный размер лота. По умолчанию: 0.01'
    )
    lot_group.add_argument(
        '--max-lot',
        type=float,
        default=None,
        help='Максимальный размер лота. По умолчанию: 100.0'
    )
    
    # Группа общих настроек
    general_group = parser.add_argument_group('Общие настройки', 'Общие параметры приложения')
    general_group.add_argument(
        '--client-account',
        type=int,
        metavar='ACCOUNT',
        default=None,
        help='Номер клиентского аккаунта (переопределяет значение из config/app_config.json)'
    )
    general_group.add_argument(
        '--check-interval',
        type=float,
        default=None,
        help='Интервал проверки позиций в секундах. По умолчанию: 0.05'
    )
    general_group.add_argument(
        '--copy-style',
        type=str,
        choices=['by_limits', 'by_market'],
        default=None,
        help='Стиль копирования: by_limits (лимитные ордера с оптимизацией) или by_market (мгновенное открытие/закрытие по маркету). По умолчанию: by_limits'
    )
    
    # Существующие опции
    parser.add_argument(
        '--optimize-to-market',
        action='store_true',
        default=False,
        help='Оптимизировать все ордера (открывающие и закрывающие) к текущей рыночной цене. По умолчанию - к оригинальной цене'
    )
    
    parser.add_argument(
        '--limit-offset-points',
        type=float,
        default=None,
        metavar='POINTS',
        help='Отступ в пунктах от рыночной цены для лимитных ордеров (открывающих и закрывающих). По умолчанию: 2.0'
    )
    
    parser.add_argument(
        '--copy-sl-tp',
        action='store_true',
        default=False,
        help='Копировать Stop Loss и Take Profit с донорских позиций на клиентские'
    )
    
    parser.add_argument(
        '--copy-pending-orders',
        action='store_true',
        default=False,
        help='Копировать отложенные ордера с донорского аккаунта на клиентский'
    )
    
    parser.add_argument(
        '--copy-existing-positions',
        action='store_true',
        default=False,
        help='Копировать уже открытые позиции при запуске программы'
    )
    
    parser.add_argument(
        '--copy-donor-magic',
        action='store_true',
        default=False,
        help='Копировать magic number с донорских позиций вместо использования значения из конфига'
    )
    
    parser.add_argument(
        '--show-snapshot',
        action='store_true',
        default=False,
        help='Показать снэпшот всех позиций при первой загрузке'
    )
    
    return parser.parse_args()


def main():
    """Главная функция"""
    # Парсим аргументы командной строки
    args = parse_args()
    
    # Загрузка конфигурации из JSON файла
    config_path = Path("config/app_config.json")
    if not config_path.exists():
        logger.warning(f"Конфигурационный файл {config_path} не найден. Используются значения по умолчанию.")
        logger.info(f"Создайте файл {config_path} на основе примера config/app_config.json.example")
    
    config = Config.from_json(str(config_path))
    
    # Обновляем конфигурацию из аргументов командной строки
    config.update_from_args(args)
    
    # Проверка конфигурации
    if config.client_account.account_number == 0:
        logger.error("Ошибка: не указан номер клиентского аккаунта в конфигурации")
        logger.error(f"Укажите client_account.account_number в файле {config_path} или используйте --client-account ACCOUNT")
        logger.error("")
        logger.error("ВАЖНО: Клиентский аккаунт должен быть авторизован в запущенном терминале MT5!")
        return
    
    # Создание и запуск копировщика
    copier = TradeCopier(
        config,
        args,
        copy_existing_positions=args.copy_existing_positions,
        copy_donor_magic=args.copy_donor_magic,
        show_snapshot=args.show_snapshot
    )
    
    try:
        if not copier.initialize():
            logger.error("Ошибка инициализации. Завершение работы.")
            return
        copier.run()
    except KeyboardInterrupt:
        logger.info("\nПолучен сигнал остановки...")
    finally:
        copier.shutdown()
        logger.info("Работа завершена.")


if __name__ == "__main__":
    main()

