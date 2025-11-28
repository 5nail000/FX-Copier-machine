"""
Загрузчик конфигурации доноров из JSON файла
"""
import json
from pathlib import Path
from typing import List, Dict
import logging


class DonorConfigLoader:
    """Загрузчик конфигурации доноров"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def load_from_file(self, config_path: str) -> List[Dict]:
        """
        Загрузить конфигурацию доноров из JSON файла
        
        Args:
            config_path: Путь к JSON файлу
            
        Returns:
            Список конфигураций доноров
        """
        try:
            path = Path(config_path)
            if not path.exists():
                self.logger.error(f"Файл конфигурации не найден: {config_path}")
                return []
            
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            donors = data.get('donors', [])
            
            if not donors:
                self.logger.warning("Файл конфигурации не содержит доноров")
                return []
            
            self.logger.info(f"Загружено {len(donors)} доноров из {config_path}")
            
            # Валидация конфигурации
            validated_donors = []
            for donor_config in donors:
                if self._validate_donor_config(donor_config):
                    validated_donors.append(donor_config)
                else:
                    self.logger.warning(f"Пропущен невалидный донор: {donor_config.get('id', 'unknown')}")
            
            return validated_donors
            
        except json.JSONDecodeError as e:
            self.logger.error(f"Ошибка парсинга JSON: {e}")
            return []
        except Exception as e:
            self.logger.error(f"Ошибка загрузки конфигурации: {e}")
            return []
    
    def _validate_donor_config(self, config: Dict) -> bool:
        """
        Валидировать конфигурацию одного донора
        
        Args:
            config: Словарь с конфигурацией донора
            
        Returns:
            True если конфигурация валидна
        """
        required_fields = ['id', 'type', 'account_number']
        
        # Проверяем обязательные поля
        for field in required_fields:
            if field not in config:
                self.logger.error(f"Отсутствует обязательное поле '{field}' в конфигурации донора")
                return False
        
        # Проверяем тип донора
        valid_types = ['python_api', 'socket_mt4', 'socket_mt5']
        if config['type'] not in valid_types:
            self.logger.error(f"Неизвестный тип донора: {config['type']}. Допустимые: {valid_types}")
            return False
        
        # Для socket типов проверяем наличие host и port
        if config['type'].startswith('socket_'):
            if 'host' not in config:
                config['host'] = 'localhost'  # Значение по умолчанию
            if 'port' not in config:
                self.logger.error(f"Для socket донора '{config['id']}' не указан порт")
                return False
        
        return True
    
    def create_default_config(self, output_path: str = "donors.json.example"):
        """
        Создать пример конфигурационного файла
        
        Args:
            output_path: Путь для сохранения примера
        """
        example_config = {
            "donors": [
                {
                    "id": "donor_mt5_api",
                    "type": "python_api",
                    "account_number": 12345678,
                    "description": "Основной донор через Python API MT5"
                },
                {
                    "id": "donor_mt5_socket_1",
                    "type": "socket_mt5",
                    "account_number": 87654321,
                    "host": "localhost",
                    "port": 8888,
                    "description": "Первый донор MT5 через сокет"
                },
                {
                    "id": "donor_mt5_socket_2",
                    "type": "socket_mt5",
                    "account_number": 11223344,
                    "host": "localhost",
                    "port": 8889,
                    "description": "Второй донор MT5 через сокет"
                },
                {
                    "id": "donor_mt5_socket_3",
                    "type": "socket_mt5",
                    "account_number": 55667788,
                    "host": "localhost",
                    "port": 8890,
                    "description": "Третий донор MT5 через сокет"
                },
                {
                    "id": "donor_mt4_socket",
                    "type": "socket_mt4",
                    "account_number": 99887766,
                    "host": "localhost",
                    "port": 8891,
                    "description": "Донор MT4 через сокет"
                }
            ]
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(example_config, f, indent=2, ensure_ascii=False)
        
        print(f"Создан пример конфигурационного файла: {output_path}")

