"""
DocVault Kernel Certification Suite.
Performs contract auditing and behavioral sandbox testing for extractors.
"""
import inspect
import hashlib
import os
import time
import json
import threading
import dataclasses
from typing import get_type_hints, Any
from pathlib import Path
from core.extractors.base import ExtractorContext, ExtractorLogger, IngestResult, LegacyExtractorAdapter
from core.settings import SettingsResolver
from core import logger

class CertificationError(Exception):
    def __init__(self, check_id: str, message: str):
        self.check_id = check_id
        self.message = message
        super().__init__(message)

@dataclasses.dataclass
class CertificationResult:
    check_id: str
    passed: bool
    message: str
    details: Any = None

class ContractAuditor:
    """Static and dynamic analysis of kernel compliance."""

    @staticmethod
    def verify_manifest(module: Any) -> CertificationResult:
        manifest = getattr(module, 'MANIFEST', None)
        if not manifest:
            return CertificationResult("manifest", False, "Missing mandatory MANIFEST dictionary.")
        
        required = ["id", "version", "name", "extensions"]
        missing = [key for key in required if key not in manifest]
        if missing:
            return CertificationResult("manifest", False, f"Manifest missing keys: {', '.join(missing)}")
        
        return CertificationResult("manifest", True, f"Valid manifest found: {manifest['id']} (v{manifest['version']})")

    @staticmethod
    def verify_signatures(module: Any) -> CertificationResult:
        """Enforces Strict Typing contract on extract() and normalize()."""
        try:
            # 1. Check extract()
            if not hasattr(module, 'extract'):
                return CertificationResult("signatures", False, "Missing extract() function.")
            
            sig = inspect.signature(module.extract)
            hints = get_type_hints(module.extract)
            
            # Check arguments
            params = list(sig.parameters.values())
            if len(params) < 2:
                return CertificationResult("signatures", False, "extract() must accept (file_path: str, ctx: ExtractorContext)")
            
            # Verify types
            path_param = params[0]
            ctx_param = params[1]
            
            if hints.get(path_param.name) != str:
                return CertificationResult("signatures", False, f"Argument '{path_param.name}' must be typed as 'str'")
            
            if hints.get(ctx_param.name) != ExtractorContext:
                return CertificationResult("signatures", False, f"Argument '{ctx_param.name}' must be typed as 'ExtractorContext'")
            
            # Check return type
            if hints.get('return') != tuple:
                return CertificationResult("signatures", False, "extract() must have return type hint '-> tuple'")

            return CertificationResult("signatures", True, "Strict typing and signatures verified.")
        except Exception as e:
            return CertificationResult("signatures", False, f"Signature analysis failed: {e}")

class BehavioralSandbox:
    """Live execution testing in a restricted environment."""

    def __init__(self, module: Any, test_file_path: str):
        self.module = module
        self.path = test_file_path
        self.adapter = LegacyExtractorAdapter(module)
        self.dummy_hash = f"CERT_TEST_{int(time.time())}"
        self.io_log = []

    def _trap_log(self, level, msg, ext=None):
        self.io_log.append({"time": time.time(), "level": level, "msg": msg, "ext": ext})

    def run_full_test(self) -> dict:
        """Automated certification run."""
        results = []
        
        # 1. Context Injection
        ctx_logger = ExtractorLogger(self.adapter.name, "CERT_VAULT", self.dummy_hash)
        cancel_token = threading.Event()
        
        ctx = ExtractorContext(
            vault_id="CERT_VAULT",
            file_hash=self.dummy_hash,
            cancel_token=cancel_token,
            logger=ctx_logger,
            settings=SettingsResolver()
        )

        # 2. Execution with timing
        start_time = time.time()
        try:
            raw_result = self.module.extract(self.path, ctx)
            elapsed = time.time() - start_time
            
            # 3. Normalization test
            ingest_result = self.adapter.normalize(raw_result, ctx)
            
            return {
                "status": "success",
                "elapsed": elapsed,
                "result": dataclasses.asdict(ingest_result),
                "io_log": self.io_log
            }
        except Exception as e:
            return {
                "status": "failed",
                "error": str(e),
                "io_log": self.io_log
            }

def calculate_file_hash(file_path: str) -> str:
    sha = hashlib.sha256()
    with open(file_path, 'rb') as f:
        while chunk := f.read(8192):
            sha.update(chunk)
    return sha.hexdigest()
