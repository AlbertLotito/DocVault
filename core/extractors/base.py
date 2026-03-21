"""
core/extractors/base.py — DocVault extractor contract system.

All extractors implement BaseExtractor. Workers only ever call .run().
Legacy extractors (returning (result, err) tuples) are wrapped via LegacyExtractorAdapter.

Extractor type hierarchy:
    BaseExtractor
        UtilityExtractor     — deterministic tools (Tesseract, Poppler, ffmpeg)
        IntelligentExtractor — single-prompt AI (Ollama vision, Whisper, Claude vision)
            ChatExtractor    — multi-turn AI with conversation history
        PipelineExtractor    — orchestrates other extractors in sequence
        RemoteExtractor      — external API with auth + retry
        ArchiveExtractor     — container formats yielding child tasks
        SynthesisExtractor   — post-extraction enrichment (summary, tags)
"""

from __future__ import annotations
import threading
import time
import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Data classes — the universal result envelope
# ---------------------------------------------------------------------------

@dataclass
class ExtractedImage:
    file_path: str
    page_num: int | None = None
    image_index: int | None = None
    width: int | None = None
    height: int | None = None


@dataclass
class ChildTask:
    """Produced by ArchiveExtractor — a sub-document to queue for extraction."""
    file_path:     str
    file_type:     str
    vault_id:      str
    file_hash:     str  = ''
    priority:      int  = 10
    parent_hash:   str | None = None
    metadata_json: dict = field(default_factory=dict)


@dataclass
class Enrichment:
    """Produced by SynthesisExtractor — derived metadata."""
    kind: str        # 'summary' | 'tags' | 'entities' | ...
    value: Any


@dataclass
class ExtractError:
    extractor_name: str
    error_type: str
    message: str
    tb: str = ''


@dataclass
class IngestResult:
    """Universal envelope returned by every extractor to the worker."""
    text: str | None
    metadata: dict = field(default_factory=dict)
    images: list[ExtractedImage] = field(default_factory=list)
    child_tasks: list[ChildTask] = field(default_factory=list)
    enrichments: list[Enrichment] = field(default_factory=list)
    errors: list[ExtractError] = field(default_factory=list)
    status: str = 'success'          # success | partial | failed | cancelled
    extractor_name: str = ''
    elapsed_secs: float = 0.0


# ---------------------------------------------------------------------------
# ExtractorLogger — structured logging to logs.db
# ---------------------------------------------------------------------------

class ExtractorLogger:
    """
    Structured logger that writes to logs.db worker_errors and worker_log tables.
    Never raises — logging failures are silently swallowed to protect extractors.
    """

    def __init__(self, extractor_name: str, vault_id: str, file_hash: str,
                 debug_enabled: bool = False):
        self.extractor_name = extractor_name
        self.vault_id = vault_id
        self.file_hash = file_hash
        self.debug_enabled = debug_enabled

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _write_log(self, level: str, message: str):
        try:
            from core.manager import get_logs_db_path, _connect
            with _connect(get_logs_db_path()) as conn:
                conn.execute(
                    """INSERT INTO worker_log
                       (file_hash, vault_id, extractor, level, message, occurred_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (self.file_hash, self.vault_id, self.extractor_name,
                     level, message, self._now())
                )
                conn.commit()
        except Exception:
            pass  # never let logging kill an extractor

    def _write_error(self, level: str, message: str, error_type: str = '', tb: str = ''):
        try:
            from core.manager import get_logs_db_path, _connect
            with _connect(get_logs_db_path()) as conn:
                conn.execute(
                    """INSERT INTO worker_errors
                       (file_hash, vault_id, extractor, error_type, error_message, traceback, occurred_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (self.file_hash, self.vault_id, self.extractor_name,
                     error_type or level, message, tb, self._now())
                )
                conn.commit()
        except Exception:
            pass

    def debug(self, msg: str):
        if self.debug_enabled:
            self._write_log('DEBUG', msg)

    def info(self, msg: str):
        self._write_log('INFO', msg)

    def warning(self, msg: str):
        self._write_log('WARNING', msg)
        self._write_error('WARNING', msg)

    def error(self, msg: str, exc: Exception | None = None):
        tb = traceback.format_exc() if exc else ''
        self._write_error('ERROR', msg, type(exc).__name__ if exc else '', tb)

    def critical(self, msg: str, exc: Exception | None = None):
        tb = traceback.format_exc() if exc else ''
        self._write_error('CRITICAL', msg, type(exc).__name__ if exc else '', tb)


# ---------------------------------------------------------------------------
# ExtractorContext — per-invocation execution environment
# ---------------------------------------------------------------------------

@dataclass
class ExtractorContext:
    vault_id: str
    file_hash: str
    cancel_token: threading.Event
    logger: ExtractorLogger
    settings: Any                    # SettingsResolver — injected by worker
    timeout_secs: int | None = None

    def report_progress(self, text: str, pct: float):
        """Update task progress in the database."""
        try:
            from core.manager import update_task_progress, get_db_path
            update_task_progress(get_db_path(), self.file_hash, text, pct)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# BaseExtractor — the contract all extractors satisfy
# ---------------------------------------------------------------------------

class BaseExtractor(ABC):
    """
    Workers call .run(). Never call .extract() or .normalize() directly.

    Subclasses implement:
        extract(file_path, ctx)  -> any type-specific result
        normalize(result, ctx)   -> IngestResult
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique extractor identifier used in logs and timing records."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Technical description of the kernel displayed in the Lab."""
        ...


    @abstractmethod
    def extract(self, file_path: Path, ctx: ExtractorContext):
        """Perform extraction. Check ctx.cancel_token at safe checkpoints."""
        ...

    @abstractmethod
    def normalize(self, result, ctx: ExtractorContext) -> IngestResult:
        """Convert type-specific result to IngestResult."""
        ...

    def run(self, file_path: Path | str, ctx: ExtractorContext) -> IngestResult:
        """Entry point for workers. Checks cancel token and times execution."""
        file_path = Path(file_path)
        start = time.monotonic()

        if ctx.cancel_token.is_set():
            ctx.logger.warning("Cancelled before start")
            return IngestResult(text=None, status='cancelled',
                                extractor_name=self.name, elapsed_secs=0.0)
        try:
            raw = self.extract(file_path, ctx)
            result = self.normalize(raw, ctx)
        except Exception as exc:
            ctx.logger.error(f"Unhandled exception in {self.name}", exc)
            result = IngestResult(
                text=None, status='failed', extractor_name=self.name,
                errors=[ExtractError(self.name, type(exc).__name__, str(exc),
                                     traceback.format_exc())]
            )

        result.extractor_name = self.name
        result.elapsed_secs = time.monotonic() - start
        return result


# ---------------------------------------------------------------------------
# LegacyExtractorAdapter — wraps old (result, err) extractors
# ---------------------------------------------------------------------------

class LegacyExtractorAdapter(BaseExtractor):
    """
    Wraps a legacy extractor module using the old interface:
        extractor.extract(file_path) -> (result, err)

    result may be: str | list[dict] | dict | None
    err may be:    str | None

    Allows existing extractors to work unchanged while the new contract
    is established for future extractors.
    """

    def __init__(self, legacy_extractor):
        self._ext = legacy_extractor

    @property
    def name(self) -> str:
        return getattr(self._ext, '__name__', str(self._ext))

    @property
    def description(self) -> str:
        # 1. Check for explicit __description__ variable in module
        # 2. Fall back to module docstring
        # 3. Fall back to generic message
        desc = getattr(self._ext, '__description__', None)
        if desc: return desc
        doc = getattr(self._ext, '__doc__', None)
        if doc: return doc.strip()
        return f"Legacy adapter for {self.name} kernel."

    def extract(self, file_path: Path, ctx: ExtractorContext):
        import inspect
        # 1. Determine if the underlying extract() wants 'ctx' or not
        func = getattr(self._ext, 'extract', None)
        if not func:
            return None, f"Extractor {self.name} has no extract() method"

        sig = inspect.signature(func)
        params = list(sig.parameters.values())
        
        # 2. Call based on signature
        if len(params) >= 2:
            # Modern: extract(path, ctx)
            return func(str(file_path), ctx)
        else:
            # Legacy: extract(path)
            return func(str(file_path))

    def normalize(self, result, ctx: ExtractorContext) -> IngestResult:
        # result is (value, err) or (value, err, meta) from legacy extractor
        if isinstance(result, tuple):
            if len(result) == 3:
                value, err, meta = result
            elif len(result) == 2:
                value, err = result
                meta = {}
            else:
                value, err, meta = result[0], None, {}
        else:
            value, err, meta = result, None, {}

        errors = []
        if err:
            errors.append(ExtractError(self.name, 'ExtractorError', str(err)))

        # str -> main text
        if isinstance(value, str) and value:
            status = 'failed' if (err and not value) else 'success'
            return IngestResult(text=value, errors=errors, status=status, metadata=meta)

        # list[dict] -> images (image_extractor pattern)
        if isinstance(value, list):
            images = []
            text_parts = []
            for img_meta in value:
                img_meta = dict(img_meta)  # don't mutate original
                desc = img_meta.pop('description', None)
                images.append(ExtractedImage(
                    file_path=img_meta.get('file_path', ''),
                    page_num=img_meta.get('page_num'),
                    image_index=img_meta.get('image_index'),
                    width=img_meta.get('width'),
                    height=img_meta.get('height'),
                ))
                if desc:
                    label = f"[Image — page {img_meta.get('page_num', '?')}]"
                    text_parts.append(f"{label}\n{desc}")
            return IngestResult(
                text="\n\n".join(text_parts) if text_parts else None,
                images=images, errors=errors, metadata=meta,
                status='failed' if (err and not images) else 'success'
            )

        # dict -> metadata
        if isinstance(value, dict):
            status = 'failed' if (err and not value) else 'success'
            return IngestResult(text=None, metadata=value, errors=errors, status=status)

        # None with error
        if err:
            return IngestResult(text=None, errors=errors, status='failed', metadata=meta)

        return IngestResult(text=None, status='success', metadata=meta)


# ---------------------------------------------------------------------------
# SubprocessExtractorAdapter — executes external binaries
# ---------------------------------------------------------------------------

class SubprocessExtractorAdapter(BaseExtractor):
    """
    Adapter for external binaries (Go, Rust, Node, etc.).
    Executes the binary and captures JSON from stdout.
    """
    def __init__(self, name: str, launch_config: list, description: str = ""):
        self._name = name
        self._launch_config = launch_config
        self._description = description or f"External subprocess kernel: {name}"

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    def extract(self, file_path: Path, ctx: ExtractorContext):
        import subprocess
        import json
        import time
        
        # 1. Build Command
        cmd = [arg.replace('{file_path}', str(file_path)) for arg in self._launch_config]

        # 2. Execute
        ctx.logger.info(f"Invoking binary: {' '.join(cmd)}")
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            # Drain stdout/stderr in a background thread to prevent pipe-buffer deadlock.
            # A kernel that writes >64KB to either pipe would otherwise block on write(),
            # while this thread is sleeping in poll() — classic OS deadlock.
            _result: dict = {}

            def _communicate():
                out, err = process.communicate()
                _result['stdout'] = out
                _result['stderr'] = err

            comm_thread = threading.Thread(target=_communicate, daemon=True)
            comm_thread.start()

            timeout = ctx.timeout_secs or 300
            start_time = time.time()

            while comm_thread.is_alive():
                if ctx.cancel_token.is_set():
                    process.kill()
                    comm_thread.join(timeout=5)
                    ctx.logger.warning(f"Killed subprocess {self.name} due to cancellation.")
                    return None, "Extraction cancelled by user", {}
                if time.time() - start_time > timeout:
                    process.kill()
                    comm_thread.join(timeout=5)
                    ctx.logger.error(f"Killed subprocess {self.name} due to timeout.")
                    return None, "Binary execution timed out", {}
                time.sleep(0.5)

            stdout = _result.get('stdout', '')
            stderr = _result.get('stderr', '')

            # 3. Capture Stderr (Logs/Progress)
            if stderr:
                ctx.logger.debug(f"Binary STDERR: {stderr.strip()}")

            if process.returncode != 0:
                return None, f"Binary exited with code {process.returncode}: {stderr}", {}

            # 4. Parse Stdout (JSON Contract)
            if not stdout.strip():
                return None, "Binary returned no stdout", {}

            try:
                data = json.loads(stdout)
            except json.JSONDecodeError:
                return None, f"Binary output violated JSON contract: {stdout[:100]}...", {}

            text = data.get("text")
            error = data.get("error")
            meta = data.get("metadata", {})

            return text, error, meta

        except Exception as e:
            return None, f"Subprocess failed: {e}", {}

    def normalize(self, result, ctx: ExtractorContext) -> IngestResult:
        # We can reuse the LegacyExtractorAdapter logic since we return the exact same 3-tuple format
        # For simplicity, we just instantiate a temporary legacy adapter to do the normalization
        dummy_adapter = LegacyExtractorAdapter(None)
        dummy_adapter._ext = type('Dummy', (), {'__name__': self.name})()
        return dummy_adapter.normalize(result, ctx)


# ---------------------------------------------------------------------------
# Type-specific base classes (structural stubs for the hierarchy)
# ---------------------------------------------------------------------------

class UtilityExtractor(BaseExtractor):
    """Deterministic tool-based extractor (Tesseract, Poppler, ffmpeg, etc.)."""
    tool_path: str = ''
    supported_formats: list = []


class IntelligentExtractor(BaseExtractor):
    """Single-prompt AI extractor (vision model, Whisper, Claude, etc.)."""
    model: str = ''
    provider: str = 'ollama'
    temperature: float = 0.1
    system_prompt: str = ''


class ChatExtractor(IntelligentExtractor):
    """Multi-turn AI extractor with conversation history."""
    history: list = []


class PipelineExtractor(BaseExtractor):
    """Runs a sequence of extractors, stops when stop_condition is satisfied."""
    stages: list = []

    def stop_condition(self, result: IngestResult) -> bool:
        return bool(result.text and len(result.text) > 50)


class RemoteExtractor(BaseExtractor):
    """External API extractor with auth lifecycle and retry."""
    endpoint: str = ''
    rate_limit: float = 1.0


class ArchiveExtractor(BaseExtractor):
    """Container format extractor — yields child tasks rather than text."""
    max_depth: int = 3


class SynthesisExtractor(BaseExtractor):
    """Post-extraction enrichment — operates on already-extracted text."""
    prompt_template: str = ''
