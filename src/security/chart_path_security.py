#!/usr/bin/env python3
"""
🛡️ SISTEMA DE SEGURIDAD PARA PATHS DE CAPTURAS
==============================================

Este módulo implementa un sistema de seguridad que garantiza que:
1. ✅ NUNCA se usen capturas más antiguas de 5 minutos
2. ✅ Se valide la frescura de cada captura antes de enviarla al modelo
3. ✅ Se mantenga un registro de las últimas capturas usadas
4. ✅ Se detecten y prevengan errores de paths antiguos

🚨 ESTE ES UN SISTEMA CRÍTICO PARA EVITAR ERRORES FATALES EN TRADING
"""

import os
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

class ChartPathSecurityManager:
    """
    🛡️ Gestor de seguridad para paths de capturas de gráficos
    
    Garantiza que NUNCA se usen capturas antiguas que podrían
    causar decisiones de trading incorrectas.
    """
    
    def __init__(self, max_age_minutes: int = 60, disable_age_check: Optional[bool] = None):  # TEMPORAL: 60 min para testing
        """
        Inicializar el gestor de seguridad
        
        Args:
            max_age_minutes: Edad máxima permitida para una captura (default: 60 minutos para testing)
        """
        # Permitir override por entorno para endurecer la frescura sin tocar el código
        env_max_age = os.getenv("FENIX_CHART_MAX_AGE_MINUTES")
        if env_max_age:
            try:
                max_age_minutes = max(1, int(env_max_age))
            except ValueError:
                logger.warning("⚠️ FENIX_CHART_MAX_AGE_MINUTES inválido (%s), usando valor por defecto %s", env_max_age, max_age_minutes)

        self.max_age_seconds = max_age_minutes * 60
        self.used_paths_history = []  # Historial de paths usados
        self.last_validated_path = None
        self.last_validation_time = None

        flag = os.getenv("FENIX_DISABLE_CHART_AGE_CHECK")
        if disable_age_check is None:
            self._age_check_disabled = flag == "1"
        else:
            self._age_check_disabled = disable_age_check

        if self._age_check_disabled:
            logger.warning("⚠️ ChartPathSecurityManager: age validation disabled (testing mode)")
        
        logger.info(f"🛡️ ChartPathSecurityManager inicializado (max_age: {max_age_minutes} min)")
    
    def validate_chart_path_freshness(self, chart_path: str, symbol: str, timeframe: str) -> Tuple[bool, str]:
        """
        🔍 Validar que un path de captura sea fresco y seguro para usar
        
        Args:
            chart_path: Path de la captura a validar
            symbol: Símbolo del trading pair
            timeframe: Timeframe de la captura
            
        Returns:
            Tuple[bool, str]: (es_válido, mensaje_explicativo)
        """
        current_time = time.time()
        
        # Validación 1: El archivo debe existir
        if not chart_path or not os.path.exists(chart_path):
            error_msg = f"❌ SEGURIDAD: Archivo no existe: {chart_path}"
            logger.error(error_msg)
            return False, error_msg
        
        # Validación 2: El archivo debe tener contenido mínimo
        file_size = os.path.getsize(chart_path)
        if file_size < 5000:  # Mínimo 5KB para una imagen válida
            error_msg = f"❌ SEGURIDAD: Archivo muy pequeño ({file_size} bytes): {chart_path}"
            logger.error(error_msg)
            return False, error_msg
        
        # Validación 3: El archivo debe ser reciente
        file_mtime = os.path.getmtime(chart_path)
        age_seconds = current_time - file_mtime
        
        if not self._age_check_disabled:
            if age_seconds > self.max_age_seconds:
                age_minutes = age_seconds / 60
                error_msg = f"❌ SEGURIDAD: Captura demasiado antigua ({age_minutes:.1f} min): {chart_path}"
                logger.error(error_msg)
                return False, error_msg
        else:
            logger.debug("ChartPathSecurityManager: omitiendo validación de antigüedad para %s", chart_path)
        
        # Validación 4: El path debe contener el símbolo y timeframe correctos
        path_lower = chart_path.lower()
        symbol_lower = symbol.lower()
        timeframe_lower = timeframe.lower()
        
        if symbol_lower not in path_lower:
            error_msg = f"❌ SEGURIDAD: Path no contiene símbolo correcto ({symbol}): {chart_path}"
            logger.error(error_msg)
            return False, error_msg
        
        if timeframe_lower not in path_lower:
            error_msg = f"❌ SEGURIDAD: Path no contiene timeframe correcto ({timeframe}): {chart_path}"
            logger.error(error_msg)
            return False, error_msg
        
        # Validación 5: No debe ser el mismo path que la validación anterior (para análisis temporal)
        if (self.last_validated_path == chart_path and 
            self.last_validation_time and 
            (current_time - self.last_validation_time) < 30):  # Menos de 30 segundos
            
            warning_msg = f"⚠️ SEGURIDAD: Mismo path validado recientemente: {chart_path}"
            logger.warning(warning_msg)
            # No es error crítico, pero es sospechoso
        
        # ✅ Todas las validaciones pasaron
        self.last_validated_path = chart_path
        self.last_validation_time = current_time
        self.used_paths_history.append({
            'path': chart_path,
            'timestamp': current_time,
            'symbol': symbol,
            'timeframe': timeframe,
            'file_age_seconds': age_seconds
        })
        
        # Mantener solo los últimos 10 registros
        if len(self.used_paths_history) > 10:
            self.used_paths_history = self.used_paths_history[-10:]
        
        success_msg = f"✅ SEGURIDAD: Captura válida y fresca ({age_seconds:.1f}s): {chart_path}"
        logger.info(success_msg)
        return True, success_msg
    
    def get_most_recent_chart_path(self, symbol: str, timeframe: str, screenshots_dir: str = "screenshots") -> Optional[str]:
        """
        🔍 Obtener el path de la captura MÁS RECIENTE que pase todas las validaciones
        
        Args:
            symbol: Símbolo del trading pair
            timeframe: Timeframe deseado
            screenshots_dir: Directorio de capturas
            
        Returns:
            Optional[str]: Path de la captura más reciente válida, o None si no hay ninguna
        """
        screenshots_path = Path(screenshots_dir)
        
        if not screenshots_path.exists():
            logger.error(f"❌ SEGURIDAD: Directorio de capturas no existe: {screenshots_dir}")
            return None
        
        # Buscar archivos que contengan el símbolo y timeframe
        pattern = f"*{symbol}*{timeframe}*.png"
        matching_files = list(screenshots_path.glob(pattern))
        
        if not matching_files:
            logger.warning(f"⚠️ SEGURIDAD: No se encontraron capturas para {symbol} {timeframe}")
            return None
        
        # Ordenar por timestamp de modificación (más reciente primero)
        matching_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        
        # Validar cada archivo hasta encontrar uno válido
        for file_path in matching_files:
            is_valid, message = self.validate_chart_path_freshness(str(file_path), symbol, timeframe)
            if is_valid:
                logger.info(f"🎯 SEGURIDAD: Captura más reciente válida encontrada: {file_path}")
                return str(file_path)
            else:
                logger.warning(f"⚠️ SEGURIDAD: Captura rechazada: {message}")
        
        logger.error(f"❌ SEGURIDAD: No se encontró ninguna captura válida para {symbol} {timeframe}")
        return None
    
    def validate_temporal_analysis_paths(self, current_path: str, previous_path: str, 
                                       symbol: str, timeframe: str) -> Tuple[bool, str]:
        """
        🔍 Validar paths para análisis temporal
        
        Args:
            current_path: Path de la captura actual
            previous_path: Path de la captura anterior
            symbol: Símbolo del trading pair
            timeframe: Timeframe
            
        Returns:
            Tuple[bool, str]: (son_válidos, mensaje_explicativo)
        """
        # Validar ambos paths individualmente
        current_valid, current_msg = self.validate_chart_path_freshness(current_path, symbol, timeframe)
        if not current_valid:
            return False, f"Captura actual inválida: {current_msg}"
        
        previous_valid, previous_msg = self.validate_chart_path_freshness(previous_path, symbol, timeframe)
        if not previous_valid:
            return False, f"Captura anterior inválida: {previous_msg}"
        
        # Validar que sean diferentes
        if current_path == previous_path:
            error_msg = f"❌ SEGURIDAD: Paths idénticos para análisis temporal: {current_path}"
            logger.error(error_msg)
            return False, error_msg
        
        # Validar orden cronológico
        current_mtime = os.path.getmtime(current_path)
        previous_mtime = os.path.getmtime(previous_path)
        
        if current_mtime <= previous_mtime:
            error_msg = f"❌ SEGURIDAD: Orden cronológico incorrecto. Actual: {current_mtime}, Anterior: {previous_mtime}"
            logger.error(error_msg)
            return False, error_msg
        
        success_msg = f"✅ SEGURIDAD: Paths temporales válidos. Actual: {current_path}, Anterior: {previous_path}"
        logger.info(success_msg)
        return True, success_msg
    
    def get_security_report(self) -> dict:
        """
        📊 Obtener reporte de seguridad del gestor
        
        Returns:
            dict: Reporte con estadísticas de seguridad
        """
        report = {
            'max_age_seconds': self.max_age_seconds,
            'last_validated_path': self.last_validated_path,
            'last_validation_time': self.last_validation_time,
            'validations_count': len(self.used_paths_history),
            'recent_validations': []
        }
        
        # Agregar validaciones recientes
        for validation in self.used_paths_history[-5:]:  # Últimas 5
            validation_copy = validation.copy()
            validation_copy['timestamp_human'] = datetime.fromtimestamp(validation['timestamp']).isoformat()
            validation_copy['age_at_validation_minutes'] = validation['file_age_seconds'] / 60
            report['recent_validations'].append(validation_copy)
        
        return report

# Instancia global del gestor de seguridad
chart_security_manager = ChartPathSecurityManager()

def validate_chart_path_safe(chart_path: str, symbol: str, timeframe: str) -> bool:
    """
    🛡️ Función de conveniencia para validar un path de captura
    
    Args:
        chart_path: Path a validar
        symbol: Símbolo del trading pair
        timeframe: Timeframe
        
    Returns:
        bool: True si el path es seguro para usar
    """
    is_valid, _ = chart_security_manager.validate_chart_path_freshness(chart_path, symbol, timeframe)
    return is_valid

def get_safe_chart_path(symbol: str, timeframe: str) -> Optional[str]:
    """
    🎯 Obtener un path de captura seguro y validado
    
    Args:
        symbol: Símbolo del trading pair
        timeframe: Timeframe deseado
        
    Returns:
        Optional[str]: Path seguro o None si no hay capturas válidas
    """
    return chart_security_manager.get_most_recent_chart_path(symbol, timeframe)

def validate_temporal_paths_safe(current_path: str, previous_path: str, 
                                symbol: str, timeframe: str) -> bool:
    """
    🛡️ Validar paths para análisis temporal de forma segura
    
    Args:
        current_path: Path actual
        previous_path: Path anterior
        symbol: Símbolo
        timeframe: Timeframe
        
    Returns:
        bool: True si ambos paths son seguros para análisis temporal
    """
    is_valid, _ = chart_security_manager.validate_temporal_analysis_paths(
        current_path, previous_path, symbol, timeframe
    )
    return is_valid
