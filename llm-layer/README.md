# Digital Twin LLM Layer — Full Codebase Documentation

> **Project**: Network Digital Twin with LLM Infrastructure  
> **Layer documented**: `llm-layer` (FastAPI microservice)  
> **Model**: Llama 3.2 3B Instruct (Q4_K_M GGUF, CPU inference via llama-cpp-python)  
> **Status**: Pipeline complete end-to-end with stub data stores

---

## Table of Contents

1. [Project Structure](#1-project-structure)
2. [Architecture Overview](#2-architecture-overview)
3. [Request Lifecycle](#3-request-lifecycle)
4. [Component Reference](#4-component-reference)
   - 4.1 [Entry Point — `run.py`](#41-entry-point--runpy)
   - 4.2 [Application — `app/main.py`](#42-application--appmainpy)
   - 4.3 [Configuration — `app/utils/config.py`](#43-configuration--apputilsconfigpy)
   - 4.4 [Logger — `app/utils/logger.py`](#44-logger--apputilsloggerpy)
   - 4.5 [API Models — `app/api/models/chat_models.py`](#45-api-models--appapimodelschat_modelspy)
   - 4.6 [Health Route — `app/api/routes/health.py`](#46-health-route--appapirouteshealthpy)
   - 4.7 [Chat Route — `app/api/routes/chat.py`](#47-chat-route--appapirouteschatpy)
   - 4.8 [Context Builder — `app/core/context_builder.py`](#48-context-builder--appcorecontext_builderpy)
   - 4.9 [Prompt Engine — `app/core/prompt_engine.py`](#49-prompt-engine--appcoreprompt_enginepy)
   - 4.10 [LLM Core — `app/core/llm_core.py`](#410-llm-core--appcorell_corepy)
   - 4.11 [Intent Parser — `app/core/intent_parser.py`](#411-intent-parser--appcoretintent_parserpy)
   - 4.12 [Human Verification — `app/core/human_verification.py`](#412-human-verification--appcorehuman_verificationpy)
   - 4.13 [Response Formatter — `app/core/response_formatter.py`](#413-response-formatter--appcoreresponse_formatterpy)
   - 4.14 [Control Layer Service — `app/services/control_layer.py`](#414-control-layer-service--appservicescontrol_layerpy)
5. [API Reference](#5-api-reference)
6. [Data Flow Diagrams](#6-data-flow-diagrams)
7. [Configuration Reference](#7-configuration-reference)
8. [Known Limitations and TODOs](#8-known-limitations-and-todos)
9. [Running and Testing](#9-running-and-testing)

---

## 1. Project Structure

```
llm-layer/
├── run.py                          # Server entry point
├── requirements.txt                # Python dependencies
├── test_llm.py                     # Standalone model smoke test
├── test_context_builder.py         # Context builder smoke test
├── models/                         # GGUF model files (not in repo)
└── app/
    ├── main.py                     # FastAPI app, lifespan, approval endpoints
    ├── __init__.py
    ├── api/
    │   ├── models/
    │   │   └── chat_models.py      # Pydantic request/response schemas
    │   └── routes/
    │       ├── chat.py             # POST /chat — full pipeline
    │       └── health.py           # GET /health, GET /
    ├── core/
    │   ├── context_builder.py      # RAG pipeline + data store clients (stubbed)
    │   ├── prompt_engine.py        # Llama 3.2 Instruct prompt construction
    │   ├── llm_core.py             # llama-cpp-python wrapper + singleton
    │   ├── intent_parser.py        # Query classification + entity extraction
    │   ├── human_verification.py   # Policy gate + approval store
    │   └── response_formatter.py   # Output cleaning + truncation
    ├── services/
    │   └── control_layer.py        # Control plane dispatch (stubbed)
    └── utils/
        ├── config.py               # Env-driven configuration
        └── logger.py               # Logging setup
```

---

## 2. Architecture Overview

The LLM Layer is a stateless FastAPI microservice. All components are initialized once at startup as singletons and reused across requests. The layer sits between the user-facing UI/Chat API and the rest of the digital twin system (Network Information Layer, Control Layer).

```
User / UI
    │
    ▼
POST /chat  (Chat API)
    │
    ├─► Context Builder ──► Neo4j (stub)
    │                  ──► Time Series DB (stub)
    │                  ──► Vector DB (stub)
    │
    ├─► Prompt Engine  (Llama 3.2 Instruct template)
    │
    ├─► LLM Core       (llama-cpp-python, Q4_K_M GGUF)
    │
    ├─► Intent Parser  (classify + extract entities)
    │
    ├─► Human Verification Gate
    │       ├─ auto-approve → Control Layer Service (stub)
    │       └─ hold → /approvals/pending
    │
    └─► Response Formatter → ChatResponse
```

**Singleton pattern**: every component (LLMCore, ContextBuilder, PromptEngine, IntentParser, HumanVerification, ControlLayerService, ResponseFormatter) is instantiated exactly once via a `get_*()` factory function that caches the instance in a module-level variable. This avoids reloading the model (which takes several seconds) on every request.

---

## 3. Request Lifecycle

The following describes exactly what happens when `POST /chat` receives a query.

```
1.  FastAPI validates the ChatRequest payload (Pydantic)
2.  ContextBuilder.build_context(query)
      ├── Extracts device hints from query text (regex)
      ├── get_topology(device_id)       → List[TopologyNode]
      ├── get_telemetry(device_id)      → List[TelemetryData]
      └── get_semantic_context(query)   → List[SemanticDoc]
          Returns: ContextBundle
3.  PromptEngine.build_prompt(query, bundle)  OR  build_action_prompt(...)
      ├── summarize_context(bundle)     → structured text block
      └── _build_llama_prompt(system, user) → full prompt string
4.  LLMCore.generate(prompt, max_tokens, temperature, stop)
      └── llama-cpp-python inference   → raw text response
5.  IntentParser.parse(query, llm_response)
      ├── _classify_query(query)        → (type, action, confidence)
      ├── _extract_entities(query)      → {devices, metrics}
      └── _extract_json_action(response)→ structured action dict (if present)
          Returns: Intent
6.  HumanVerification.evaluate(intent, query)
      ├── Non-action → (False, None) — pass through
      ├── Action in REQUIRE_HUMAN_ACTIONS → (True, approval_id)
      └── confidence < threshold → (True, approval_id)
7.  [If auto-approved action] ControlLayerService.dispatch(intent)
      └── Maps action → REST endpoint (stubbed)
8.  ResponseFormatter.format(raw_response, intent, requires_review, status)
      ├── _clean_llm_output()           strips special tokens + JSON blocks
      ├── _truncate()                   sentence-boundary truncation
      └── _build_summary_line()         action summary for action intents
9.  Return ChatResponse
```

---

## 4. Component Reference

---

### 4.1 Entry Point — `run.py`

**Purpose**: Starts the uvicorn ASGI server.

```python
uvicorn.run(
    "app.main:app",
    host=config.API_HOST,   # default: 0.0.0.0
    port=config.API_PORT,   # default: 8000
    reload=config.DEBUG,    # default: True
    log_level="info"
)
```

**Usage**: `python run.py`

---

### 4.2 Application — `app/main.py`

**Purpose**: Creates the FastAPI application instance, registers middleware, includes routers, and manages component lifecycle.

#### Lifespan

Uses FastAPI's `lifespan` context manager (replacing the deprecated `@app.on_event` pattern). At startup, all seven singletons are initialized in order:

```
LLMCore → ContextBuilder → PromptEngine → IntentParser →
ResponseFormatter → HumanVerification → ControlLayerService
```

Initializing all singletons at startup ensures that:
- The model load cost (multi-second) is paid once, not per-request
- Any configuration or import errors surface immediately rather than on first request
- The `🟢 All components initialized` log confirms the service is ready

#### Middleware

CORS is configured with `allow_origins=["*"]` for development. This should be restricted to specific origins in production.

#### Approval Endpoints

Two endpoints are defined directly in `main.py` (not in a separate router) for managing the human verification queue:

| Endpoint | Method | Description |
|---|---|---|
| `/approvals/pending` | GET | Returns all approval records with `status == "pending"` |
| `/approvals/{approval_id}/resolve` | POST | Approves or rejects a pending action. Query params: `approved: bool`, `reviewer_id: str` |

---

### 4.3 Configuration — `app/utils/config.py`

**Purpose**: Centralizes all runtime configuration. Values are read from environment variables with sensible defaults, allowing override via a `.env` file (loaded by `python-dotenv`).

#### Config fields

| Field | Type | Default | Description |
|---|---|---|---|
| `MODEL_PATH` | str | `/home/agreal2016/btp/models/Llama-3.2-3B-Instruct-Q4_K_M.gguf` | Absolute path to GGUF model file |
| `MODEL_N_CTX` | int | `4096` | Context window size in tokens |
| `MODEL_N_THREADS` | int | `4` | CPU threads for inference |
| `API_HOST` | str | `0.0.0.0` | Host to bind the FastAPI server |
| `API_PORT` | int | `8000` | Port to bind the FastAPI server |
| `DEBUG` | bool | `True` | Enables uvicorn hot reload |
| `AUTO_APPROVE_CONFIDENCE` | float | `0.9` | Minimum confidence for auto-approving actions |
| `REQUIRE_HUMAN_ACTIONS` | List[str] | `restart_device,update_config,delete_interface` | Comma-separated action names that always require human review |

#### Usage

```python
from app.utils.config import config
print(config.MODEL_PATH)
print(config.AUTO_APPROVE_CONFIDENCE)
```

A single `config` instance is created at module import time. All other modules import this singleton.

---

### 4.4 Logger — `app/utils/logger.py`

**Purpose**: Provides consistent log formatting across all modules.

#### Functions

**`setup_logger(name, level)`**  
Creates and returns a logger with a `StreamHandler` writing to stdout. Format: `YYYY-MM-DD HH:MM:SS - name - LEVEL - message`. Checks for existing handlers before adding to avoid duplicate output when modules are reloaded.

**`get_logger(name)`**  
Thin wrapper around `logging.getLogger(name)`. All modules use standard `logging.getLogger(__name__)` directly rather than calling this.

#### Usage convention

Every module gets its own logger at module level:
```python
import logging
logger = logging.getLogger(__name__)
```

---

### 4.5 API Models — `app/api/models/chat_models.py`

**Purpose**: Pydantic schemas for all API request/response payloads and internal data structures.

#### `ChatRequest`

Incoming payload for `POST /chat`.

| Field | Type | Default | Description |
|---|---|---|---|
| `query` | str | required | Natural language query from user |
| `user_id` | Optional[str] | None | User identifier for audit logging |
| `session_id` | Optional[str] | None | Session ID (reserved for conversation history) |
| `max_tokens` | Optional[int] | 500 | Max tokens the model should generate |
| `temperature` | Optional[float] | 0.7 | Sampling temperature (0 = deterministic, 1 = creative) |
| `require_human_approval` | Optional[bool] | False | Force human review regardless of confidence |

#### `ChatResponse`

Outgoing payload from `POST /chat`.

| Field | Type | Description |
|---|---|---|
| `response` | str | Cleaned natural language response |
| `intent` | Optional[str] | Classified intent: `informational`, `diagnostic`, `action`, `unknown` |
| `confidence` | Optional[float] | 0.0–1.0 confidence of intent classification |
| `tokens_used` | Optional[int] | Approximate token count of generated response |
| `latency_ms` | Optional[float] | Total pipeline latency in milliseconds |
| `human_review_required` | Optional[bool] | Whether the action was held for human approval |
| `approval_status` | Optional[str] | `pending`, `auto_approved`, or None |

#### `Intent`

Internal structured representation of a parsed query intent. Passed between IntentParser, HumanVerification, ControlLayer, and ResponseFormatter.

| Field | Type | Description |
|---|---|---|
| `type` | str | `informational`, `diagnostic`, `action`, `unknown` |
| `confidence` | float | 0.0–1.0 |
| `entities` | Dict[str, Any] | Extracted entities: `devices`, `metrics`, etc. |
| `action` | Optional[str] | Action name if type is `action` (e.g., `restart`, `configure`) |
| `target` | Optional[str] | Target device ID (e.g., `router-1`) |
| `raw_text` | Optional[str] | Original user query |

#### `VerificationRequest` / `VerificationResponse`

Used by the Human Verification gate.

`VerificationRequest` carries the `Intent`, original query, proposed commands, impact analysis, and session info to a human reviewer.

`VerificationResponse` carries the reviewer's decision: `approved: bool`, optional `reviewer_id`, optional comments, optional modified intent, and a timestamp.

---

### 4.6 Health Route — `app/api/routes/health.py`

**Purpose**: Operational endpoints for monitoring.

#### `GET /health`

Returns service health and model statistics.

```json
{
  "status": "healthy",
  "model_loaded": true,
  "model_stats": {
    "total_calls": 3,
    "total_tokens": 430,
    "total_latency": 59.98,
    "avg_latency": 19.99,
    "avg_tokens_per_call": 143.33
  },
  "service": "digital-twin-llm",
  "version": "0.1.0"
}
```

If `get_llm()` raises (model not loaded), returns `{"status": "unhealthy", "error": "..."}`.

#### `GET /`

Returns service name, version, and list of available endpoints.

---

### 4.7 Chat Route — `app/api/routes/chat.py`

**Purpose**: The core endpoint. Orchestrates the full 7-step pipeline for every request.

This is the most important file to understand — it is the spine of the entire LLM Layer. All seven pipeline components are called from here in sequence.

```python
@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    # Step 1: Build context
    # Step 2: Build prompt (standard or action variant)
    # Step 3: LLM inference
    # Step 4: Parse intent
    # Step 5: Human verification gate
    # Step 6: Dispatch to control layer (auto-approved only)
    # Step 7: Format and return response
```

#### Action prompt selection logic

The action prompt variant (which requests JSON output from the model) is selected when:
- The query contains any of: `restart`, `reboot`, `configure`, `shutdown`, `enable`, `disable`, `delete`, `update config`
- OR `request.require_human_approval` is `True`

Otherwise the standard prompt is used.

#### Stop tokens

Inference is called with `stop=["<|eot_id|>", "<|start_header_id|>"]`. These are Llama 3.2's end-of-turn and role header tokens. Setting them as stop sequences prevents the model from simulating a second user turn after its response.

#### Error handling

Any exception in the pipeline is caught, logged with `exc_info=True` (full traceback), and returned as an HTTP 500 with the error message in the `detail` field.

---

### 4.8 Context Builder — `app/core/context_builder.py`

**Purpose**: The RAG (Retrieval-Augmented Generation) pipeline. Aggregates network state from three data sources and compresses it into structured text for the prompt.

This is the most architecturally significant component. Its quality directly determines the quality of the model's responses. The model can only reason about what is in the context.

#### Data Model Classes

These are plain Python classes (not Pydantic) defined in this file. They will be moved to a dedicated models module in a future refactor.

**`TopologyNode`**
```
id: str          — e.g. "router-1"
type: str        — "router", "switch", "server", "firewall"
name: str        — human-readable name, e.g. "Core Router"
status: str      — "up", "degraded", "down", "unknown"
neighbors: list  — list of connected device IDs
```

**`TelemetryData`**
```
device_id: str   — e.g. "switch-2"
metric: str      — e.g. "cpu_usage", "packet_loss"
value: float     — numeric metric value
unit: str        — e.g. "%", "ms", "Mbps"
timestamp: datetime
```

**`SemanticDoc`**
```
id: str
title: str
content: str
relevance_score: float  — 0.0–1.0
```

**`ContextBundle`**
```
query: str
topology: List[TopologyNode]
telemetry: List[TelemetryData]
docs: List[SemanticDoc]
timestamp: datetime
```

#### `ContextBuilder` Methods

**`__init__()`**  
Logs initialization. Contains commented-out TODO lines for real DB driver initialization:
```python
# self.neo4j_driver = Neo4jDriver(...)
# self.tsdb_client = InfluxDBClient(...)
# self.vector_db = ChromaDB(...)
```

**`get_topology(device_id=None) → List[TopologyNode]`**  
Returns stub topology data for 7 devices: router-1, switch-1, switch-2, server-1, server-2, server-3, fw-1. Notable states: switch-2 is `degraded`, server-3 is `down`.

If `device_id` is provided, filters to nodes whose `id` contains the device_id string. **Known bug**: the filter uses `device_id in n.id` which matches substring, so passing `"server"` returns all three servers. Should be an exact match: `n.id == device_id`.

**`get_telemetry(device_id=None, metric=None, time_range=5min) → List[TelemetryData]`**  
Returns stub telemetry for 5 metrics × 6 devices. Key values:
- `server-3`: cpu=98.5%, memory=95.2% (simulates a failing server)
- `switch-2`: packet_loss=2.3% (simulates degraded switch)
- `router-1`: latency=12.5ms, bandwidth=340.2Mbps

**Known bug**: device extraction in `build_context()` matches generic words ("server", "router") not specific IDs ("server-3"), so the device filter never activates for specific device queries and `get_telemetry` always returns all devices' data. Fix: use the regex `\b([a-zA-Z]+-\d+)\b` to extract specific IDs from the query.

**`get_semantic_context(query, limit=3) → List[SemanticDoc]`**  
Performs keyword-based relevance scoring over 5 stub documents (Network Troubleshooting Guide, Router Configuration, Switch Troubleshooting, Server Performance Monitoring, Network Digital Twin Concepts). Title match scores 0.3/keyword, content match scores 0.2/keyword. Returns top `limit` documents sorted by score.

**Note**: This is stub RAG — a keyword matcher, not a real vector similarity search. The real implementation will use embedding-based cosine similarity against a vector database.

**`build_context(query) → ContextBundle`**  
Orchestrates retrieval:
1. Extracts device hint from query (checks for "router", "switch", "server", "firewall" as substring — see known bug above)
2. Calls `get_topology`, `get_telemetry`, `get_semantic_context`
3. Returns populated `ContextBundle`

**`summarize_context(bundle) → str`**  
Converts a `ContextBundle` into a structured text block for the prompt. Format:

```
=== NETWORK TOPOLOGY ===
✅ Core Router [id: router-1] (router): up
   Connected to: switch-1, switch-2, fw-1
⚠️ Distribution Switch [id: switch-2] (switch): degraded
   Connected to: router-1, server-3, storage-1

=== CURRENT TELEMETRY ===
router-1:
   • cpu_usage: 45.6 %
   • memory_usage: 62.3 %
   • latency: 12.5 ms

=== RELEVANT DOCUMENTATION ===
📄 Network Troubleshooting Guide:
   When a server shows degraded status, check CPU and memory...
```

Limits output: max 3 devices' telemetry, 3 metrics per device, 2 docs at 150 chars each. This is the token budget enforcement.

**Note**: The `[id: ...]` tag in node display was added after initial testing revealed the model was failing to match user queries using device IDs (e.g., "server-3") because the context only showed names (e.g., "Application Server").

#### Singleton

```python
_context_builder = None

def get_context_builder() -> ContextBuilder:
    global _context_builder
    if _context_builder is None:
        _context_builder = ContextBuilder()
    return _context_builder
```

---

### 4.9 Prompt Engine — `app/core/prompt_engine.py`

**Purpose**: Constructs prompts in the exact format Llama 3.2 Instruct expects.

#### Why prompt format matters

Llama 3.2 Instruct was fine-tuned on a specific chat template. Sending a raw string without the template tokens results in significantly degraded instruction-following. The model expects:

```
<|begin_of_text|>
<|start_header_id|>system<|end_header_id|>
{system instructions}<|eot_id|>
<|start_header_id|>user<|end_header_id|>
{user message}<|eot_id|>
<|start_header_id|>assistant<|end_header_id|>
```

The trailing `<|start_header_id|>assistant<|end_header_id|>` without a closing `<|eot_id|>` is intentional — it primes the model to begin generating the assistant turn.

#### System Prompt

The system prompt instructs the model to:
- Act as a network operations assistant
- Answer based on the provided context
- Diagnose issues and suggest remediation steps
- For action requests, produce a structured action plan
- Keep responses under 200 words

#### `PromptEngine` Methods

**`build_prompt(query, context_bundle=None) → str`**  
Standard prompt for informational and diagnostic queries. If `context_bundle` is not provided, it calls `ContextBuilder.build_context(query)` internally. The user message is:

```
Network Context:
{summarized context text}

Question: {query}
```

**`build_action_prompt(query, context_bundle=None) → str`**  
Action variant. Extends the system prompt to request a specific JSON block at the end of the response:

```json
{
  "action": "<action_name>",
  "target": "<device_id>",
  "parameters": {},
  "confidence": 0.0,
  "requires_human_approval": true
}
```

The user message label changes from `"Question:"` to `"Action Request:"` to further cue the model.

**Known limitation**: The model does not reliably emit the JSON block. It often produces a good action plan in prose but skips the structured JSON. Strengthening the instruction (e.g., "You MUST end your response with the JSON block") improves but does not fully solve this. A more robust approach is a second small inference call specifically for structured extraction.

---

### 4.10 LLM Core — `app/core/llm_core.py`

**Purpose**: Wraps `llama-cpp-python` to provide a clean generation interface with performance tracking.

#### Model

- **File**: `Llama-3.2-3B-Instruct-Q4_K_M.gguf`
- **Parameters**: 3 billion
- **Quantization**: Q4_K_M (4-bit, K-quant method M) — reduces memory from ~6GB to ~2GB
- **Format**: GGUF (GPT-Generated Unified Format) — the standard format for llama.cpp
- **Inference backend**: `llama-cpp-python`, a Python binding for `llama.cpp`

#### `LLMCore` Class

**`__init__(model_path, n_ctx, n_threads)`**  
Loads the model from the GGUF file. Key parameters:

| Parameter | Value | Effect |
|---|---|---|
| `model_path` | from config | Path to .gguf file |
| `n_ctx` | 4096 | Context window (max prompt + response tokens) |
| `n_threads` | 4 | CPU threads (set to `nproc` for best performance) |
| `n_batch` | 512 | Prompt processing batch size (to be added — reduces prompt ingestion time) |
| `verbose` | False | Suppresses llama.cpp internal logging |

Also initializes three counters: `total_tokens`, `total_latency`, `call_count`.

**`generate(prompt, max_tokens=500, temperature=0.7, stop=None, echo=False) → dict`**

Runs inference and returns:

```python
{
    "response": str,          # generated text (stripped)
    "completion_tokens": int, # word-count approximation (not exact)
    "latency": float,         # seconds
    "tokens_per_second": float,
    "model_status": "success" | "error"
}
```

On error, returns `{"response": "Error: ...", "error": ..., "model_status": "error"}` — does not raise, so the pipeline can handle it gracefully.

**Note on token counting**: `completion_tokens` uses `len(generated_text.split())` which counts words, not tokens. Actual token count is typically 1.3–1.5× the word count. Use `self.llm.tokenize(text.encode())` for accurate counting.

**`get_stats() → dict`**

Returns aggregated performance stats:
```python
{
    "total_calls": int,
    "total_tokens": int,
    "total_latency": float,
    "avg_latency": float,
    "avg_tokens_per_call": float
}
```

Surfaced via `GET /health`.

#### Singleton

```python
_llm_instance = None

def get_llm() -> LLMCore:
    global _llm_instance
    if _llm_instance is None:
        _llm_instance = LLMCore()
    return _llm_instance
```

#### Observed Performance (CPU, 4 threads)

| Query type | Tokens | Latency |
|---|---|---|
| Informational | ~28 | ~8s |
| Diagnostic | ~138 | ~17s |
| Action | ~83 | ~14s |

---

### 4.11 Intent Parser — `app/core/intent_parser.py`

**Purpose**: Converts the raw LLM text output and original query into a structured `Intent` object.

#### Design: Two-stage classification

**Stage 1 — Rule-based query classification** (always runs)

Keyword dictionaries and regex patterns applied to the original user query:

```python
ACTION_KEYWORDS = {
    "restart":       ["restart", "reboot", "reset", "reload"],
    "configure":     ["configure", "config", "set", "apply", ...],
    "shutdown":      ["shutdown", "shut down", "disable", ...],
    "enable":        ["enable", "turn on", "start", ...],
    "delete":        ["delete", "remove", "drop", ...],
    "update_config": ["update config", "change config", ...],
}

DIAGNOSTIC_KEYWORDS = ["what's wrong", "why is", "diagnose", "issue", "down", "degraded", ...]
INFORMATIONAL_KEYWORDS = ["show", "list", "what is", "status", "explain", ...]
```

Returns `(intent_type, action_name_or_None, confidence)`. Confidence values: action=0.75, diagnostic=0.80, informational=0.85, unknown=0.50.

**Stage 2 — JSON extraction from LLM response** (only for action intents)

Attempts to extract a structured JSON block from the model output using two approaches:
1. Fenced block: regex for ` ```json { ... } ``` `
2. Bare object: regex for `{ ... "action" ... }`

If extraction succeeds, the JSON values for `action`, `target`, `confidence`, and `parameters` override the rule-based values in the Intent object.

#### Entity Extraction

`_extract_entities(query)` runs two patterns against the query:

- **Device IDs**: `\b([a-zA-Z]+-\d+)\b` — matches patterns like `router-1`, `switch-2`, `server-3`
- **Metric names**: substring search for `cpu`, `memory`, `latency`, `bandwidth`, `packet loss`, `throughput`, `uptime`, `error rate`

Results are stored in `intent.entities = {"devices": [...], "metrics": [...]}`.

#### `IntentParser.parse(query, llm_response) → Intent`

Main method combining both stages. Returns a fully populated `Intent` object.

---

### 4.12 Human Verification — `app/core/human_verification.py`

**Purpose**: Policy gate that prevents action intents from reaching the Control Layer without appropriate authorization.

#### Design principle

The LLM, however accurate, should not have unilateral authority to modify live network state. This component enforces that principle programmatically.

#### Decision logic in `evaluate(intent, query)`

```
if intent.type != "action":
    return (False, None)   # non-actions always pass

if intent.action in REQUIRE_HUMAN_ACTIONS:
    → hold for review (reason: "action is in always-require list")

elif intent.confidence < AUTO_APPROVE_CONFIDENCE:
    → hold for review (reason: "confidence below threshold")

else:
    → auto-approve
```

`REQUIRE_HUMAN_ACTIONS` (from config): `{"restart_device", "update_config", "delete_interface"}`  
`AUTO_APPROVE_CONFIDENCE` (from config): `0.9`

**Note**: In current testing, the rule-based classifier assigns `confidence=0.75` to all action intents. Since 0.75 < 0.9, virtually all action queries are held for review. This is intentional for early development — the threshold can be lowered as the system matures.

#### Approval store

`_pending_approvals: dict[str, dict]` is a module-level dict keyed by `approval_id` (8-char UUID prefix).

Each record:
```python
{
    "approval_id":  str,
    "query":        str,
    "intent":       dict,     # intent.dict() serialization
    "reason":       str,      # why review was required
    "status":       str,      # "pending" | "approved" | "rejected"
    "created_at":   str,      # ISO timestamp
    "resolved_at":  str | None,
    "reviewer_id":  str | None
}
```

**Known limitation**: In-memory storage. Resets on service restart. Should be replaced with SQLite or Redis for persistence.

#### `resolve(approval_id, approved, reviewer_id) → VerificationResponse | None`

Updates the record status and returns a `VerificationResponse`. Returns `None` if the approval_id is not found.

#### `get_pending() → List[dict]`

Returns all records where `status == "pending"`. Surfaced via `GET /approvals/pending`.

---

### 4.13 Response Formatter — `app/core/response_formatter.py`

**Purpose**: Post-processes raw LLM output into clean, user-facing text before it is returned in the API response.

#### Three processing steps

**Step 1 — `_clean_llm_output(raw) → str`**

Removes two categories of artifacts:
- **JSON action blocks**: strips ` ```json ... ``` ` fenced blocks and bare `{"action": ...}` objects (these were consumed by the Intent Parser and should not appear in the user-facing response)
- **Llama special tokens**: strips `<|eot_id|>`, `<|begin_of_text|>`, `<|end_of_text|>`, `<|start_header_id|>`, `<|end_header_id|>`, and bare role strings (`assistant`, `user`, `system`) that occasionally appear when the model generates beyond its intended output boundary

Also normalizes runs of 3+ newlines to 2 newlines.

**Step 2 — `_truncate(text, max_chars=1200) → (str, bool)`**

Enforces a 1,200 character ceiling. If the text exceeds this, it finds the last sentence end (`. ` or `.\n`) within the limit and cuts there. Appends ` [...]` to indicate truncation. Returns `(truncated_text, was_truncated_bool)`.

1,200 chars ≈ 300 words, which is sufficient for all three query types observed in testing.

**Step 3 — `_build_summary_line(intent) → str | None`**

For action intents only, appends a one-line summary:
```
→ Proposed action: `restart` on router-1 (confidence: 80%)
```

This makes the system's interpretation of the request transparent to the user and confirms what action would be dispatched.

#### `ResponseFormatter.format(...) → dict`

Main method. Returns a dict with keys matching `ChatResponse` fields:
```python
{
    "response": str,
    "intent": str,
    "confidence": float,
    "human_review_required": bool,
    "approval_status": str | None
}
```

---

### 4.14 Control Layer Service — `app/services/control_layer.py`

**Purpose**: Bridge between the LLM Layer and the Control Layer. Dispatches auto-approved action intents as HTTP calls to the control plane.

**Current status**: Fully stubbed. All methods return simulated success responses without making any real HTTP calls.

#### Action → Endpoint Mapping

```python
ACTION_HANDLERS = {
    "restart":       "POST /control/device/{target}/restart",
    "configure":     "POST /control/device/{target}/configure",
    "shutdown":      "POST /control/device/{target}/shutdown",
    "enable":        "POST /control/device/{target}/enable",
    "delete":        "DELETE /control/resource/{target}",
    "update_config": "PUT /control/device/{target}/config",
}
```

#### `ControlLayerService.dispatch(intent) → dict`

Maps the intent's `action` field to an endpoint pattern, substitutes `{target}` with the device ID from `intent.target`, and logs the dispatch. Returns:

```python
{
    "status":   "dispatched",
    "action":   str,
    "target":   str,
    "endpoint": str,    # full URL with base_url
    "result":   str     # stub message
}
```

The TODO comment shows exactly what to replace the stub with:

```python
# import httpx
# response = httpx.post(
#     f"{self.base_url}{endpoint}",
#     json={"intent": intent.dict(), "parameters": intent.entities},
#     timeout=10.0
# )
# return response.json()
```

#### `get_device_status(device_id) → dict`

Stub for fetching live device status from the control plane. Returns hardcoded `{"status": "up"}`.

---

## 5. API Reference

### `POST /chat`

Run a natural language query through the full LLM pipeline.

**Request body** (`ChatRequest`):
```json
{
  "query": "What is wrong with server-3?",
  "user_id": "optional",
  "session_id": "optional",
  "max_tokens": 500,
  "temperature": 0.7,
  "require_human_approval": false
}
```

**Response** (`ChatResponse`):
```json
{
  "response": "Server-3 (Application Server) is down...",
  "intent": "informational",
  "confidence": 0.85,
  "tokens_used": 156,
  "latency_ms": 25421.25,
  "human_review_required": false,
  "approval_status": null
}
```

### `GET /health`

Returns model load status and performance statistics.

### `GET /approvals/pending`

Returns all action intents currently awaiting human review.

```json
{
  "pending": [
    {
      "approval_id": "3fc255c3",
      "query": "Restart router-1",
      "intent": {"type": "action", "action": "restart", "target": "router-1", ...},
      "reason": "confidence 0.80 below threshold 0.9",
      "status": "pending",
      "created_at": "2026-03-10T16:41:21.734494",
      "resolved_at": null,
      "reviewer_id": null
    }
  ]
}
```

### `POST /approvals/{approval_id}/resolve`

Approve or reject a pending action.

**Query parameters**:
- `approved: bool` — true to approve, false to reject
- `reviewer_id: str` — identifier of the reviewer (default: `"api-user"`)

**Example**:
```bash
curl -X POST "http://localhost:8000/approvals/3fc255c3/resolve?approved=true&reviewer_id=operator-1"
```

---

## 6. Data Flow Diagrams

### Informational query flow

```
Query: "What is wrong with server-3?"
    │
    ▼
ContextBuilder
    ├── device hint: "server" (generic — bug: should match "server-3")
    ├── topology: all 7 nodes returned
    ├── telemetry: all devices' metrics
    └── docs: Network Troubleshooting Guide, Server Performance Monitoring
    │
    ▼
PromptEngine.build_prompt()   (standard variant)
    │
    ▼
LLMCore.generate()
    → "Server-3 (Application Server) is down. It is connected to
       switch-2 which is degraded. CPU at 98.5%, memory at 95.2%..."
    │
    ▼
IntentParser.parse()
    ├── _classify_query → type="informational", confidence=0.85
    └── _extract_entities → devices=["server-3"]
    │
    ▼
HumanVerification.evaluate()
    └── type != "action" → pass through (False, None)
    │
    ▼
ResponseFormatter.format()
    └── clean → truncate → no action summary
    │
    ▼
ChatResponse: intent="informational", human_review_required=false
```

### Action query flow

```
Query: "Restart router-1"
    │
    ▼
ContextBuilder → context bundle (topology + telemetry + docs)
    │
    ▼
PromptEngine.build_action_prompt()   (action variant — requests JSON)
    │
    ▼
LLMCore.generate()
    → "To restart router-1... impact analysis...
       → Proposed action: restart on router-1 (confidence: 80%)"
       [JSON block may or may not be present]
    │
    ▼
IntentParser.parse()
    ├── _classify_query → type="action", action="restart", confidence=0.75
    ├── _extract_entities → devices=["router-1"]
    └── _extract_json_action → (overrides if JSON present)
    │
    ▼
HumanVerification.evaluate()
    ├── "restart" not in REQUIRE_HUMAN_ACTIONS
    └── confidence 0.75 < threshold 0.9 → HOLD
        approval_id = "3fc255c3"
        record written to _pending_approvals
    │
    ▼
[ControlLayer NOT called — held for review]
    │
    ▼
ResponseFormatter.format()
    └── clean → truncate → append action summary line
    │
    ▼
ChatResponse: intent="action", human_review_required=true, approval_status="pending"
```

---

## 7. Configuration Reference

### Environment variables

Create a `.env` file in `llm-layer/` to override defaults:

```env
MODEL_PATH=/home/youruser/models/Llama-3.2-3B-Instruct-Q4_K_M.gguf
MODEL_N_CTX=4096
MODEL_N_THREADS=8
API_HOST=0.0.0.0
API_PORT=8000
DEBUG=True
AUTO_APPROVE_CONFIDENCE=0.75
REQUIRE_HUMAN_ACTIONS=restart_device,update_config,delete_interface
```

### Tuning recommendations

| Scenario | Setting | Recommended value |
|---|---|---|
| Maximize CPU throughput | `MODEL_N_THREADS` | Set to output of `nproc` |
| Faster prompt ingestion | Add `n_batch=512` to `LLMCore.__init__` | 512 |
| Test auto-approve path | `AUTO_APPROVE_CONFIDENCE` | `0.7` |
| Allow all actions without review | `REQUIRE_HUMAN_ACTIONS` | `` (empty string) |
| Longer responses | `max_tokens` in request | 800–1000 |
| More deterministic output | `temperature` in request | 0.1–0.3 |

---

## 8. Known Limitations and TODOs

### Bugs

| Location | Issue | Fix |
|---|---|---|
| `context_builder.py` `get_topology()` | Device filter uses `device_id in n.id` (substring match) | Change to `n.id == device_id` |
| `context_builder.py` `build_context()` | Device extraction matches "server" not "server-3" | Use regex `\b([a-zA-Z]+-\d+)\b` |
| `llm_core.py` `generate()` | Token count uses word split, not actual tokenizer | Use `self.llm.tokenize(text.encode())` |

### Stubs to replace

| File | Stub | Real implementation |
|---|---|---|
| `context_builder.py` `get_topology()` | Hardcoded 7-node network | `neo4j-driver` Cypher query |
| `context_builder.py` `get_telemetry()` | Hardcoded metric values | InfluxDB / Prometheus query |
| `context_builder.py` `get_semantic_context()` | Keyword matching | Embedding + ANN search (Chroma/Weaviate) |
| `control_layer.py` `dispatch()` | Logs and returns stub dict | `httpx.post()` to Control Layer API |
| `control_layer.py` `get_device_status()` | Returns hardcoded "up" | HTTP GET to Control Layer API |

### Missing features

| Feature | Location | Notes |
|---|---|---|
| Conversation history | `chat.py` | `session_id` exists in schema but unused; needs session store |
| Approval store persistence | `human_verification.py` | In-memory dict resets on restart; replace with SQLite/Redis |
| Real vector embeddings | `context_builder.py` | Needs an embedding model to convert docs to vectors |
| GPU inference | `llm_core.py` | Add `n_gpu_layers=-1` to `Llama()` init when GPU available |
| Rate limiting | `main.py` | No throttling on `/chat` endpoint |
| Auth | `main.py` | No authentication on any endpoint |

---

## 9. Running and Testing

### Start the server

```bash
cd llm-layer/
python run.py
```

Watch for `🟢 All components initialized — pipeline ready` in the logs.

### Stop the server

**Always use Ctrl+C** (sends SIGINT, triggers FastAPI lifespan shutdown).  
**Never use Ctrl+Z** — this suspends the process without releasing the port. If you accidentally Ctrl+Z:

```bash
# Kill whatever is holding port 8000
kill $(lsof -t -i:8000)

# Or bring the suspended process back first, then Ctrl+C
fg
# then Ctrl+C
```

### Test queries

```bash
# 1. Informational
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "What is wrong with server-3?"}'

# 2. Diagnostic
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "Why is switch-2 degraded?"}'

# 3. Action (triggers human review)
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "Restart router-1"}'

# 4. Check pending approvals
curl http://localhost:8000/approvals/pending

# 5. Resolve an approval (replace ID with actual value from step 4)
curl -X POST "http://localhost:8000/approvals/3fc255c3/resolve?approved=true&reviewer_id=operator-1"

# 6. Health check
curl http://localhost:8000/health
```

### Swagger UI

Open `http://localhost:8000/docs` in a browser for interactive API testing with auto-generated documentation.

### Standalone model test

```bash
cd llm-layer/
python test_llm.py
```

Tests that the GGUF model loads and generates a response. Does not use the pipeline — bypasses all components except llama-cpp-python directly.

### Context builder test

```bash
cd llm-layer/
python test_context_builder.py
```

Tests the full context builder pipeline (stub data) for four predefined queries, printing the summarized context for each. Pauses between queries for manual inspection.
