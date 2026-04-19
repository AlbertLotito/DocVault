"""
DocVault Dynamic Router.
Builds extension-to-kernel mapping at runtime from the ext_registry.
Only serves certified and enabled kernels.
"""
import os
import importlib.util
import json
from typing import Any
from core.registry import RegistryManager, EXTRACTORS_DIR
from core import logger

_all_extractors = []
ROUTES = {'file': {}, 'folder': {}}
FALLBACK_KERNEL = None
DEFAULT_PRIORITY = 10

class LazyPythonKernel:
    """
    A proxy object that only imports and executes the actual Python kernel
    module when a method or attribute (like .extract) is accessed.
    """
    def __init__(self, module_name: str, description: str = ""):
        self.__name__ = module_name
        self.__description__ = description
        self._mod = None

    def _ensure_loaded(self):
        if self._mod is None:
            try:
                logger.info(f"Lazy-loading kernel: {self.__name__}...", ext="router")
                file_path = os.path.join(EXTRACTORS_DIR, f"{self.__name__}.py")
                spec = importlib.util.spec_from_file_location(self.__name__, file_path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                self._mod = mod
            except Exception as e:
                logger.error(f"Failed to lazy-load kernel {self.__name__}: {e}", ext="router")
                raise

    def __getattr__(self, name):
        if name == "__name__": return self.__name__
        if name == "__description__": return self.__description__
        self._ensure_loaded()
        return getattr(self._mod, name)


def reload(sync_disk: bool = True):
    """
    Refreshes the routing map from the database.
    Can be called during 'Hot Reload' events.
    """
    global _all_extractors, ROUTES, FALLBACK_KERNEL, _initialized
    
    logger.info("Initializing dynamic router...", ext="router")
    
    new_extractors = []
    new_routes = {'file': {}, 'folder': {}}
    
    rm = RegistryManager()
    if sync_disk:
        rm.sync_disk_to_db() # Ensure DB is current with disk
    
    active = rm.get_active_kernels()
    
    for entry in active:
        mod_name = entry['module_name']
        kernel_type = entry.get('kernel_type', 'python')
        target_type = entry.get('target_type', 'file')
        
        try:
            # 1. Prepare Kernel Proxy
            if kernel_type == 'subprocess':
                from core.extractors.base import SubprocessExtractorAdapter
                launch_config = json.loads(entry['launch_config']) if entry.get('launch_config') else []
                mod = SubprocessExtractorAdapter(mod_name, launch_config, description=entry.get('description', ''))
                mod.__name__ = mod_name
            else:
                # Use Lazy Loader for Python kernels to avoid heavy re-imports
                mod = LazyPythonKernel(mod_name, description=entry.get('description', ''))
            
            new_extractors.append(mod)
            
            # 2. Build Routes
            exts = json.loads(entry['extensions'])
            for ext in exts:
                ext = ext.lower()
                if ext == '*':
                    FALLBACK_KERNEL = mod
                    continue
                    
                target_map = new_routes.get(target_type, new_routes['file'])
                if ext not in target_map:
                    target_map[ext] = []
                target_map[ext].append(mod)
            
            logger.debug(f"Router mapped kernel: {entry['kernel_id']} ({kernel_type}/{target_type})", ext="router")
            
        except Exception as e:
            logger.error(f"Failed to map activated kernel {mod_name}: {e}", ext="router")

    _all_extractors = new_extractors
    ROUTES = new_routes
    _initialized = True
    
    logger.info(f"Router updated. {len(_all_extractors)} kernels mapped.", ext="router")

# --- Initialize on first use ---
_initialized = False

def _ensure_initialized(sync_disk: bool = True):
    global _initialized
    if not _initialized:
        try:
            reload(sync_disk=sync_disk)
        except Exception as e:
            # If DB isn't ready yet, we'll try again on the next call
            pass

def get_extractors(file_type: str, vault_id: str = None) -> list:
    """Return the ordered list of extractors for a given file extension."""
    _ensure_initialized()
    file_type = file_type.lower()
    stack = ROUTES['file'].get(file_type, [])
    if not stack:
        return [FALLBACK_KERNEL] if FALLBACK_KERNEL else []
    return stack

def get_folder_extractors(extension_hint: str) -> list:
    """Returns extractors registered for 'folder' targets with a specific extension content."""
    _ensure_initialized()
    return ROUTES['folder'].get(extension_hint.lower(), [])

def get_priority(file_type: str, vault_id: str = None) -> int:
    """Return the priority for a given file extension."""
    # Logic remains similar to before, can be moved to kernel MANIFEST in next phase
    slow_types = {'mp3', 'wav', 'm4a', 'flac', 'ogg', 'au', 'mp4', 'mov', 'mkv', 'avi', 'webm'}
    fast_types = {'txt', 'md', 'csv', 'json', 'py', 'js', 'ts', 'html', 'xml', 'yaml', 'toml', 'log'}
    
    if file_type.lower() in fast_types: return 20
    if file_type.lower() in slow_types: return 5
    return DEFAULT_PRIORITY
