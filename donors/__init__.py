"""
Модуль для работы с множественными донорами
"""
from .donor_base import DonorBase, DonorPosition, DonorOrder
from .donor_manager import DonorManager
from .donor_config_loader import DonorConfigLoader

__all__ = [
    'DonorBase',
    'DonorPosition',
    'DonorOrder',
    'DonorManager',
    'DonorConfigLoader'
]

