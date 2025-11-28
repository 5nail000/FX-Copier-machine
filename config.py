"""
Конфигурация для копировщика сделок MT5
"""
import json
from pathlib import Path
from dataclasses import dataclass
from typing import Optional


@dataclass
class AccountConfig:
    """Конфигурация аккаунта"""
    account_number: int


@dataclass
class LotConfig:
    """Конфигурация размера лота"""
    mode: str  # 'fixed', 'proportion', 'autolot'
    value: Optional[float] = None  # Для fixed - размер лота, для proportion - коэффициент
    min_lot: float = 0.01
    max_lot: float = 100.0


@dataclass
class OrderConfig:
    """Конфигурация ордеров"""
    max_retries: int = 10  # Максимальное количество попыток установки ордера
    magic: int = 234000  # Магическое число для идентификации ордеров копировщика
    optimize_to_market: bool = False  # Оптимизировать все ордера к рыночной цене (false = к оригинальной, по умолчанию)
    limit_offset_points: float = 2.0  # Отступ в пунктах от рыночной цены для лимитных ордеров (открывающих и закрывающих)
    copy_sl_tp: bool = False  # Копировать Stop Loss и Take Profit с донорских позиций
    copy_pending_orders: bool = False  # Копировать отложенные ордера с донорского аккаунта


@dataclass
class Config:
    """Основная конфигурация"""
    donor_account: AccountConfig
    client_account: AccountConfig
    lot_config: LotConfig
    order_config: OrderConfig
    check_interval: float = 1.0  # Интервал проверки позиций в секундах
    copy_style: str = "by_limits"  # Стиль копирования: "by_limits" (лимитные ордера) или "by_market" (рыночные ордера)
    
    def update_from_args(self, args):
        """Обновить конфигурацию из аргументов командной строки"""
        if args.optimize_to_market is not None:
            self.order_config.optimize_to_market = args.optimize_to_market
        if args.limit_offset_points is not None:
            self.order_config.limit_offset_points = args.limit_offset_points
        if args.copy_sl_tp is not None:
            self.order_config.copy_sl_tp = args.copy_sl_tp
        if args.copy_pending_orders is not None:
            self.order_config.copy_pending_orders = args.copy_pending_orders
        
        # Обновление клиентского аккаунта через аргументы
        if args.client_account is not None:
            self.client_account.account_number = args.client_account
        
        # Обновление настроек лота через аргументы
        if args.lot_mode is not None:
            self.lot_config.mode = args.lot_mode
        if args.lot_value is not None:
            self.lot_config.value = args.lot_value
        if args.min_lot is not None:
            self.lot_config.min_lot = args.min_lot
        if args.max_lot is not None:
            self.lot_config.max_lot = args.max_lot
        
        # Обновление интервала проверки через аргументы
        if args.check_interval is not None:
            self.check_interval = args.check_interval
        
        # Обновление стиля копирования через аргументы
        if args.copy_style is not None:
            self.copy_style = args.copy_style
    
    @classmethod
    def from_json(cls, config_path: str = "app_config.json"):
        """
        Загрузка конфигурации из JSON файла
        
        Args:
            config_path: Путь к JSON файлу конфигурации
            
        Returns:
            Экземпляр Config с загруженными настройками
        """
        path = Path(config_path)
        
        # Значения по умолчанию
        default_config = {
            'client_account': {'account_number': 0},
            'lot_config': {
                'mode': 'fixed',
                'value': 0.01,
                'min_lot': 0.01,
                'max_lot': 100.0
            },
            'order_config': {
                'max_retries': 50,
                'magic': 777777
            },
            'check_interval': 0.05,
            'copy_style': 'by_limits'
        }
        
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
                # Объединяем с дефолтными значениями
                config_data = {**default_config, **config_data}
            except (json.JSONDecodeError, IOError) as e:
                print(f"Ошибка чтения конфигурационного файла {config_path}: {e}")
                print("Используются значения по умолчанию")
                config_data = default_config
        else:
            config_data = default_config
        
        # Донорский аккаунт больше не нужен в конфиге (управляется через donors_config.json)
        # Но оставляем для обратной совместимости
        donor_account = AccountConfig(
            account_number=config_data.get('donor_account', {}).get('account_number', 0)
        )
        
        client_account = AccountConfig(
            account_number=config_data.get('client_account', {}).get('account_number', 0)
        )
        
        lot_data = config_data.get('lot_config', {})
        lot_config = LotConfig(
            mode=lot_data.get('mode', 'fixed'),
            value=lot_data.get('value', 0.01),
            min_lot=lot_data.get('min_lot', 0.01),
            max_lot=lot_data.get('max_lot', 100.0)
        )
        
        order_data = config_data.get('order_config', {})
        order_config = OrderConfig(
            max_retries=order_data.get('max_retries', 10),
            magic=order_data.get('magic', 234000),
            optimize_to_market=False,  # По умолчанию к оригинальной цене
            limit_offset_points=order_data.get('limit_offset_points', 2.0),  # По умолчанию 2 пункта
            copy_sl_tp=order_data.get('copy_sl_tp', False),  # По умолчанию не копировать SL/TP
            copy_pending_orders=order_data.get('copy_pending_orders', False)  # По умолчанию не копировать отложенные ордера
        )
        
        return cls(
            donor_account=donor_account,
            client_account=client_account,
            lot_config=lot_config,
            order_config=order_config,
            check_interval=config_data.get('check_interval', 1.0),
            copy_style=config_data.get('copy_style', 'by_limits')
        )

