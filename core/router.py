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

def reload():
    """
    Refreshes the routing map from the database.
    Can be called during 'Hot Reload' events.
    """
    global _all_extractors, ROUTES, FALLBACK_KERNEL
    
    logger.info("Initializing dynamic router...", ext="router")
    
    new_extractors = []
    new_routes = {'file': {}, 'folder': {}}
    
    rm = RegistryManager()
    rm.sync_disk_to_db() # Ensure DB is current with disk
    active = rm.get_active_kernels()
    
    for entry in active:
        mod_name = entry['module_name']
        kernel_type = entry.get('kernel_type', 'python')
        target_type = entry.get('target_type', 'file')
        
        try:
            # 1. Dynamic Load
            if kernel_type == 'subprocess':
                from core.extractors.base import SubprocessExtractorAdapter
                launch_config = json.loads(entry['launch_config']) if entry.get('launch_config') else []
                mod = SubprocessExtractorAdapter(mod_name, launch_config)
                mod.__name__ = mod_name
            else:
                file_path = os.path.join(EXTRACTORS_DIR, f"{mod_name}.py")
                spec = importlib.util.spec_from_file_location(mod_name, file_path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                mod.__name__ = mod_name
            
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
            
            logger.debug(f"Router activated kernel: {entry['kernel_id']} ({kernel_type}/{target_type})", ext="router")
            
        except Exception as e:
            logger.error(f"Failed to load activated kernel {mod_name}: {e}", ext="router")

    _all_extractors = new_extractors
    ROUTES = new_routes
    
    logger.info(f"Router active. {len(_all_extractors)} kernels loaded.", ext="router")

# --- Initialize on first import ---
reload()

def get_extractors(file_type: str, vault_id: str = None) -> list:
    """Return the ordered list of extractors for a given file extension."""
    file_type = file_type.lower()
    stack = ROUTES['file'].get(file_type, [])
    if not stack:
        return [FALLBACK_KERNEL] if FALLBACK_KERNEL else []
    return stack

def get_folder_extractors(extension_hint: str) -> list:
    """Returns extractors registered for 'folder' targets with a specific extension content."""
    return ROUTES['folder'].get(extension_hint.lower(), [])

def get_priority(file_type: str, vault_id: str = None) -> int:
    """Return the priority for a given file extension."""
    # Logic remains similar to before, can be moved to kernel MANIFEST in next phase
    slow_types = {'mp3', 'wav', 'm4a', 'flac', 'ogg', 'mp4', 'mov', 'mkv', 'avi', 'webm'}
    fast_types = {'txt', 'md', 'csv', 'json', 'py', 'js', 'ts', 'html', 'xml', 'yaml', 'toml', 'log'}
    
    if file_type.lower() in fast_types: return 20
    if file_type.lower() in slow_types: return 5
    return DEFAULT_PRIORITY
