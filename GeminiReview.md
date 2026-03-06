# DocVault: System Review & Architectural Analysis

**Date:** 2026-03-04
**Reviewer:** Gemini CLI Agent
**Status:** Comprehensive Analysis

---

## 1. System Overview: The Document Operating System Metaphor

DocVault is not merely a document indexer; it is a **Document Processing Runtime** structured exactly like a modern multitasking operating system.

### 1.1 The Kernel (Core)
The "Kernel" of DocVault resides in the `core/` package. It manages the fundamental resources of the system:
*   **The File System & Registry (`manager.py`)**: SQLite acts as the backing store for the system's "Registry" (settings) and "File Allocation Table" (tasks). It tracks every document (process) and its state.
*   **The Mount Manager (`vault_manager.py`)**: Manages "Vaults" as mounted namespaces. Each vault is a distinct volume with its own permissions (priority), configuration (settings), and lifecycle (state machine).
*   **The Configuration Manager (`settings.py`)**: A 4-tier resolution engine that functions like a hierarchical registry (Vault -> Global -> Config File -> Hardcoded Defaults).
*   **The Resource Governor (`monitor.py`)**: The system's power and thermal management daemon. It samples sensors (CPU/GPU/RAM) and throttles the "CPU" (Workers) to prevent hardware exhaustion.

### 1.2 The Scheduler & Dispatcher (Workers)
The "Scheduler" (`workers/`) manages the execution of tasks:
*   **The Process Scheduler (`extraction_worker.py`)**: Uses a **Composite Priority with Aging** algorithm (similar to the Linux CFS). It calculates `effective_priority` based on vault importance, task type, and "starvation prevention" (aging).
*   **The Dispatcher (`router.py`)**: Acts as the "Interrupt Vector Table," mapping file types (interrupts) to the appropriate extractors (interrupt handlers).

### 1.3 The Drivers (Extractors)
The `extractors/` are the "Hardware Drivers" of the system.
*   **Abstract Driver API (`BaseExtractor`)**: Defines the standard interface for all content extraction "hardware."
*   **Legacy Adapter (`LegacyExtractorAdapter`)**: A "shim" driver that allows older, simpler extractors to run on the new high-performance kernel.
*   **Specific Drivers**: `text_extractor` (native PDF), `ocr_extractor` (vision/Tesseract), `transcriber` (audio/Whisper).

### 1.4 Utilities & Subsystems
*   **The MMU / Indexing (`search/`, `embeddings/`)**: Manages the mapping of raw data to searchable address space (Vector space and FTS5).
*   **The Intelligence Engine (`llm/`)**: A high-level coprocessor for RAG and natural language reasoning.

### 1.5 The System Call Interface (API)
FastAPI (`api/`) serves as the "System Call" interface, allowing user-space applications (the Frontend) to interact with the kernel.

### 1.6 The Shell (Frontend)
The HTML/JS interface is the "Command Shell," providing a visual environment for the user to monitor "Processes" (Vault Status) and query "Storage" (Search).

---

## 2. In-Depth Analysis of System Contracts

The reliability of DocVault stems from its rigorous adherence to explicit and implicit contracts between its components.

### 2.1 The Extractor Contract (`BaseExtractor`)
The fundamental contract for any "Driver" in the system is:
`run(file_path, ctx) -> IngestResult`

*   **Explicit Contract**: Every extractor must implement `extract()` (low-level logic) and `normalize()` (convert to the universal `IngestResult` envelope).
*   **The Envelope (`IngestResult`)**: A structured data packet containing `text`, `metadata`, `images`, `child_tasks`, and `errors`. This ensures the Kernel doesn't need to know *how* a file was processed, only *what* was found.
*   **The Environment (`ExtractorContext`)**: Passed to every driver, providing "syscall" access to `logger`, `settings`, and a `cancel_token` for cooperative multitasking.

### 2.2 The Scheduling Contract (Composite Priority)
The Scheduler claims tasks using a deterministic formula:
`((10 - vault_priority) * extractor_priority) + age_bonus`

*   **Implicit Contract**: Starvation is impossible. Even the lowest-priority task in a low-priority vault will eventually reach a high enough `age_bonus` to be executed.
*   **Explicit Contract**: Workers must claim tasks using `BEGIN IMMEDIATE` transactions to prevent race conditions in a multi-threaded environment.

### 2.3 The Resource Contract (The Governor)
The `HardwareMonitor` enforces a "Social Contract" between DocVault and the Host Machine:
*   **Throttle State Machine**: Transitions between `normal`, `throttled`, and `cooldown`.
*   **Cooperative Backoff**: Workers are not forcibly killed (usually), but they check `should_pause_or_throttle()` before every task. This "politeness" prevents DocVault from making the host machine unusable during heavy tasks (like gaming).

### 2.4 The Storage Contract (Three-DB Topology)
DocVault maintains a strict separation of concerns across three datastores:
1.  **`docvault.db` (The Data)**: Ephemeral/Wipeable. Contains the results of extraction. Wiping this triggers a "re-install" of the data.
2.  **`settings.db` (The Identity)**: Persistent. Contains vault definitions and user preferences. Must survive data resets.
3.  **`logs.db` (The Telemetry)**: Observable. Contains timings and errors. Can be cleared without affecting system state.

---

## 3. Full Security Review

### 3.1 Trust Boundaries
*   **Local-First Architecture**: By far the strongest security feature. Data never leaves the machine unless explicitly configured (e.g., Claude API).
*   **No Authentication**: The system currently has **ZERO** authentication. If the API is exposed to a local network (0.0.0.0), any user on that network has full control over the vaults, can read all documents, and can execute files via the `open_path` utility.

### 3.2 File System Risks
*   **`open_path` (System Call)**: The `/api/utils/open_path` endpoint allows the frontend to trigger `os.startfile`. While convenient, this is a powerful primitive. A malicious user with network access could theoretically trigger the opening of dangerous files if they know the path.
*   **Path Traversal**: The system relies on `scan_directory` but doesn't strictly jail the workers. However, since the user *configures* these directories, the risk is primarily to the user themselves.

### 3.3 Data Exfiltration (LLM Providers)
*   **Ollama (Local)**: Safe. Data stays on-device.
*   **Claude/Gemini (Cloud)**: The "Ask" feature sends document chunks to third-party APIs. Users must be aware that sensitive chunks are being transmitted over the wire to Anthropic/Google when using these providers.

### 3.4 Resource Exhaustion (DoS)
*   **Thermal Safety**: The Resource Governor is a critical security feature for the hardware. Without it, the intensive GPU/CPU tasks (Whisper/Vision) could lead to thermal throttling or hardware damage on poorly cooled systems.
*   **Disk Pressure**: Maximum extraction (especially video frame extraction) can consume significant disk space in `.cache/`. There is currently no "Disk Quota" enforcement.

### 3.5 Recommendations
1.  **Network Isolation**: Ensure DocVault is bound to `127.0.0.1` by default.
2.  **Auth Layer**: Implement a simple API key or Basic Auth if multi-user or network access is ever intended.
3.  **Path Jailing**: Restrict `open_path` and ingestion strictly to the defined vault roots.

---

## 4. Conclusion
DocVault is a remarkably sophisticated piece of software engineering. Its transition to an "OS-like" architecture has given it the robustness needed for heavy lifting. The extraction pipeline is "Maximum Effort," and the infrastructure is "Minimum Maintenance" due to its self-governing and self-healing nature.


 Summary of the Review:


   * Operating System Metaphors:
       * Kernel: Managed by core/manager.py (Registry/FAT) and core/monitor.py (Resource/Thermal Governor).
       * Scheduler: Implemented in workers/extraction_worker.py using a Composite Priority with Aging algorithm (Linux CFS-style).
       * Drivers: The extractors/ suite, abstracted via the BaseExtractor contract and LegacyExtractorAdapter.
       * Shell: The FastAPI/Tailwind frontend acting as the user interface to the underlying runtime.


   * System Contracts:
       * Extractor Contract: Rigorous run() -> extract() -> normalize() pipeline ensuring a universal IngestResult envelope for all
         data types.
       * Scheduling Contract: Guaranteed task execution via aging bonuses, preventing starvation for low-priority vaults.
       * Resource Contract: Autonomous hardware sampling and adaptive throttling via the HardwareMonitor daemon.


   * Security Review:
       * Strengths: Local-first architecture ensures data privacy for local LLM providers (Ollama).
       * Risks: Identified a total lack of API authentication, which presents a significant risk if the service is exposed to a
         network. The open_path utility is a powerful primitive that requires jailing to prevent unauthorized file access.
       * Recommendations: Bind the API to 127.0.0.1 by default, implement an authentication layer, and enforce strict path jailing
         for utility endpoints.
