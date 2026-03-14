# Project Status & Design Notes — 2026-03-06

This document captures the major optimizations and architectural refinements completed on March 6, 2026.

## 1. Major Performance & Stability Overhaul

### 1.1 Zero-Weight Startup (Lazy Loading)
- **Problem**: System startup and kernel activation were taking minutes because every AI kernel (Torch, MediaPipe, Whisper) was being fully initialized just to read metadata.
- **Solution**: Implemented **LazyPythonKernel** proxies in the Router.
- **Result**: Router initialization is now instantaneous. Heavy AI models only load into memory at the exact moment a worker needs them for a specific file.

### 1.2 Static Registry Analysis (AST)
- **Problem**: Kernels were "unregistering" themselves on restart if their heavy imports caused a timeout or crash during the initial disk sync.
- **Solution**: Switched from code execution to **Static Manifest Analysis** using Python's `ast` module.
- **Result**: The system can now read `MANIFEST` and descriptions from kernels without running a single line of their code. Startup is 100x faster and rock-solid stable.

---

## 2. Advanced Vision Intelligence

### 2.1 The Bridge Architecture
- **PDF Image Harvester** updated to a "Sub-Router" bridge.
- When an image is found inside a PDF, it is now automatically dispatched to the specialized **Face Identity**, **Face Analytics**, and **OCR** kernels.
- **Unified Search**: Faces and text found *inside* PDF images are now fully indexed and searchable alongside the document's main text.

### 2.2 Surgical Face Analytics (Historical Recovery)
- Optimized `FACE_ANALYTICS_EXTRACTOR` specifically for old scanned photos and high-res DSLRs.
- **Auto-Crop**: Automatically strips white/black letterboxing borders that confuse AI detectors.
- **Bilateral Denoising**: Removes film grain and scanner noise while preserving facial edges.
- **Inference Capping**: Limits analysis to the 3 most prominent faces to prevent "infinite loops" on noisy background textures.
- **Result**: Processing time for complex photos dropped from 10 minutes to ~2 seconds.

---

## 3. UX & Laboratory Enhancements

### 3.1 Advanced Lab Controls
- **Kill Test**: Added a dynamic red "Kill" button that instantly terminates hanging tests or stuck external binaries.
- **Dynamic Browsing**: The "Browse" button now automatically switches between File and Folder selection based on the kernel's requirements (e.g., Music Collection Intelligence).
- **Two-Level Sorting**: Sidebar now sorts by **Certification Status (Descending)** then **Name (Ascending)**.
- **Sync Feedback**: Added visual pulse indicators and explicit loading states while the registry is scanning the disk.

### 3.2 Global UI Refinements
- **Notification Persistence**: Alerts now track `lastAlertId` in `localStorage`. Notifications appear once and don't repeat when navigating between pages.
- **TF Silence**: Suppressed low-level TensorFlow/oneDNN environment noise to keep logs high-signal.

---

## 4. Current TODO List
- [ ] **Face Crop Gallery**: Implement a dedicated UI in the Identity Hub to view the actual isolated crops from documents.
- [ ] **Prompt Injection Hardening**: Secure the Vision and Aural LLM stages against adversarial document text.
- [ ] **Archive X-Ray**: Implement header-level search for ZIP/TAR archives.
- [ ] **Unified Settings**: Move kernel-specific env vars into the main Settings UI for centralized management.
