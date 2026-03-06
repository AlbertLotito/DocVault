"""
DocVault Unified Logger.
Provides a central log() function that respects the 'system:debug_mode' setting.
"""
import sys
from datetime import datetime
from core.settings import settings

# ANSI colors for console output
_COLORS = {
    'DEBUG':    '\033[94m', # Blue
    'INFO':     '\033[92m', # Green
    'WARNING':  '\033[93m', # Yellow
    'ERROR':    '\033[91m', # Red
    'CRITICAL': '\033[95m', # Magenta
    'RESET':    '\033[0m'
}

def is_debug_enabled() -> bool:
    v = settings.get('system:debug_mode')
    return str(v).lower() in ('1', 'true', 'yes', 'on')

def log(level: str, message: str, extractor: str = None):
    """
    Unified console logging.
    Default: Only WARNING, ERROR, CRITICAL.
    Debug: All levels.
    """
    level = level.upper()
    debug = is_debug_enabled()
    
    # Filter based on level
    if not debug and level in ('DEBUG', 'INFO'):
        return

    timestamp = datetime.now().strftime("%H:%M:%S")
    color = _COLORS.get(level, '')
    reset = _COLORS['RESET']
    
    prefix = f"[{timestamp}] [{color}{level:8}{reset}]"
    if extractor:
        prefix += f" [{extractor}]"
        
    print(f"{prefix} {message}")
    sys.stdout.flush()

# Helper wrappers
def debug(msg, ext=None): log('DEBUG', msg, ext)
def info(msg, ext=None):  log('INFO', msg, ext)
def warn(msg, ext=None):  log('WARNING', msg, ext)
def error(msg, ext=None): log('ERROR', msg, ext)
def critical(msg, ext=None): log('CRITICAL', msg, ext)
