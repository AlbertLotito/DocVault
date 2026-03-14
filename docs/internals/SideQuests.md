# Side Quests & Known Limitations

This document tracks non-critical issues, environmental requirements, and potential future improvements identified during the DocVault review and testing.

---

## 1. WMI Access Denied (CPU Temperature Sensor)

**Issue**: `[monitor] WMICPUTempSensor sensor failed to init: <x_wmi: Unexpected COM Error (-2147217405, 'OLE error 0x80041003', None, None)>`

### **Context**
DocVault's Resource Governor attempts to read the `MSAcpi_ThermalZoneTemperature` class via Windows Management Instrumentation (WMI) to monitor CPU thermals.

### **The Constraint**
On Windows, accessing motherboard thermal zones via WMI is a privileged operation. 
*   **Error 0x80041003**: Specifically means "Access Denied."
*   **Requirement**: Requires the process (terminal/script) to be **Run as Administrator**.

### **Current Workaround**
DocVault implements a **Resilient Sensor Registry**. When the WMI sensor fails to initialize:
1.  It logs the warning.
2.  It automatically swaps the failed sensor for a `DummySensor` (returning 0°C).
3.  **The system remains functional**: GPU temperature monitoring (`NvidiaSensor`) and CPU/RAM load monitoring (`PSUtilSensor`) do **not** require Admin and continue to work perfectly.

### **Future Fixes (Side Quests)**
- [ ] **Sensor Abstraction**: Implement a secondary CPU sensor path using `OpenHardwareMonitor` or `LibreHardwareMonitor` WMI namespaces (which can often be read by non-admin users if the background service is already running).
- [ ] **Graceful Silencing**: Add a check to see if the process is running as a standard user and silence the WMI warning if it's a known permission issue.

---

## 2. Ollama Concurrency Tuning

**Issue**: `[vision] no slots available after 10 retries (status code: 500)`

### **Context**
Ollama's default configuration (`OLLAMA_NUM_PARALLEL=1`) can lead to slot exhaustion when multiple DocVault workers (Extraction, Embedding, and Chat) hit the API simultaneously.

### **Implemented Solution**
We have implemented an **Ollama Governor** (`core/monitor.py`) that uses a Semaphore to limit concurrent requests. It also forces strict serialization (1 request at a time) if the hardware governor detects thermal pressure.

### **Side Quests**
- [ ] **Dynamic Slot Detection**: Attempt to probe Ollama's `OLLAMA_NUM_PARALLEL` setting at startup to automatically configure `ollama:max_parallel`.
- [ ] **VRAM-Aware Concurrency**: Adjust allowed parallel requests based on available VRAM sampled from `NvidiaSensor`.

---

## 3. RAG Granularity (Chunk-Level Hybrid Search)

**Issue**: Mismatch between document-level keyword search and chunk-level semantic search.

### **Implemented Solution**
Migrated `fts_index` to a chunk-level schema. Keyword and Semantic scores are now fused via RRF at the 600-character chunk level.

### **Side Quests**
- [ ] **Porter Stemming**: Enable the Porter Stemming tokenizer in FTS5 to allow "run" to match "running."
- [ ] **Re-ranking**: Implement a cross-encoder re-ranking step (e.g., using `bge-reranker`) on the top 10 hybrid results to further refine RAG accuracy.
