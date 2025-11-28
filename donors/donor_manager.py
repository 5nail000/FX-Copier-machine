"""
Менеджер для управления множественными донорами
"""
from typing import List, Optional
from donors.donor_base import DonorBase, DonorPosition, DonorOrder
from donors.donor_python_api import DonorPythonAPI
from donors.donor_socket_mt4 import DonorSocketMT4
from donors.donor_socket_mt5 import DonorSocketMT5
from donors.donor_config_loader import DonorConfigLoader
import logging


class DonorManager:
    """Менеджер всех доноров"""
    
    def __init__(self):
        self.donors: List[DonorBase] = []
        self.logger = logging.getLogger(__name__)
    
    def load_from_config(self, config_path: str) -> int:
        """
        Загрузить доноров из конфигурационного файла
        
        Args:
            config_path: Путь к JSON файлу с конфигурацией
            
        Returns:
            Количество успешно подключенных доноров
        """
        loader = DonorConfigLoader()
        donor_configs = loader.load_from_file(config_path)
        
        connected_count = 0
        
        for donor_config in donor_configs:
            donor_id = donor_config['id']
            account_number = donor_config['account_number']
            donor_type = donor_config['type']
            
            success = False
            
            if donor_type == 'python_api':
                success = self.add_python_api_donor(donor_id, account_number)
            
            elif donor_type == 'socket_mt4':
                host = donor_config.get('host', 'localhost')
                port = donor_config.get('port', 8888)
                success = self.add_socket_mt4_donor(donor_id, account_number, host, port)
            
            elif donor_type == 'socket_mt5':
                host = donor_config.get('host', 'localhost')
                port = donor_config.get('port', 8888)
                success = self.add_socket_mt5_donor(donor_id, account_number, host, port)
            
            if success:
                connected_count += 1
                description = donor_config.get('description', '')
                if description:
                    self.logger.info(f"  Описание: {description}")
        
        return connected_count
    
    def add_donor(self, donor: DonorBase) -> bool:
        """Добавить донора"""
        if donor.connect():
            self.donors.append(donor)
            self.logger.info(f"Донор {donor.donor_id} (аккаунт {donor.account_number}) подключен")
            return True
        else:
            self.logger.error(f"Не удалось подключить донора {donor.donor_id}")
            return False
    
    def add_python_api_donor(self, donor_id: str, account_number: int) -> bool:
        """Добавить донора через Python API"""
        donor = DonorPythonAPI(donor_id, account_number)
        return self.add_donor(donor)
    
    def add_socket_mt4_donor(self, donor_id: str, account_number: int, host: str = 'localhost', port: int = 8888) -> bool:
        """Добавить донора через Socket MT4"""
        donor = DonorSocketMT4(donor_id, account_number, host, port)
        return self.add_donor(donor)
    
    def add_socket_mt5_donor(self, donor_id: str, account_number: int, host: str = 'localhost', port: int = 8889) -> bool:
        """Добавить донора через Socket MT5"""
        donor = DonorSocketMT5(donor_id, account_number, host, port)
        return self.add_donor(donor)
    
    def get_all_positions(self) -> List[DonorPosition]:
        """Получить все позиции от всех доноров"""
        all_positions = []
        for donor in self.donors:
            if donor.is_connected():
                try:
                    positions = donor.get_positions()
                    all_positions.extend(positions)
                except Exception as e:
                    self.logger.error(f"Ошибка получения позиций от донора {donor.donor_id}: {e}")
        return all_positions
    
    def get_all_orders(self) -> List[DonorOrder]:
        """Получить все ордера от всех доноров"""
        all_orders = []
        for donor in self.donors:
            if donor.is_connected():
                try:
                    orders = donor.get_orders()
                    all_orders.extend(orders)
                except Exception as e:
                    self.logger.error(f"Ошибка получения ордеров от донора {donor.donor_id}: {e}")
        return all_orders
    
    def disconnect_all(self):
        """Отключить всех доноров"""
        for donor in self.donors:
            try:
                donor.disconnect()
            except Exception as e:
                self.logger.error(f"Ошибка отключения донора {donor.donor_id}: {e}")
        self.donors.clear()
    
    def get_donor_by_id(self, donor_id: str) -> Optional[DonorBase]:
        """Найти донора по ID"""
        for donor in self.donors:
            if donor.donor_id == donor_id:
                return donor
        return None
    
    def get_connected_count(self) -> int:
        """Получить количество подключенных доноров"""
        return sum(1 for donor in self.donors if donor.is_connected())

