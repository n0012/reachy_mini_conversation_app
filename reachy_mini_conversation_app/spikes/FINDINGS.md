# Phase 0 Spike Findings — Vertex AI Memory Bank
Generated: 2026-05-02
Status: ✅ PASSED

## Config used
- Project: `losiern-vibe` (Argolis)
- Location: `us-central1`
- Reasoning Engine ID: `YOUR_ENGINE_ID`
- Engine display name: `reachy-memory-spike`

## What works

### Reasoning Engine creation
- `POST /reasoningEngines` with just `display_name` (no `spec.memoryBankConfig` — that field doesn't exist)
- Returns an LRO — completes in ~15s
- Service account `service-{project_number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com` needs `roles/aiplatform.user` granted to call Gemini internally

### Conversation ingestion (`memories:generate` with `direct_contents_source`)
- Accepts `events[]` with `role` + `parts[].text` — same shape as standard Gemini API messages
- Returns an LRO — completes in ~2s
- Server-side Gemini extracts individual facts from the conversation automatically
- Auto-categorizes into topics: `USER_PERSONAL_INFO`, `USER_PREFERENCES`, etc.
- From 4 conversation turns, extracted 4 distinct facts:
  - `"My name is Nick."` → USER_PERSONAL_INFO
  - `"I work at Google."` → USER_PERSONAL_INFO
  - `"I have a son named Zach."` → USER_PERSONAL_INFO
  - `"I prefer concise answers."` → USER_PREFERENCES

### Direct fact storage (`memories:generate` with `direct_memories_source`)
- Accepts `direct_memories[].fact` strings
- Also returns an LRO — completes in ~2s
- Use this for the `remember` tool (Reachy explicitly saves a fact mid-conversation)
- Stored: `"Nick drinks black coffee, no sugar."`

### Memory retrieval (`memories:retrieve`)
- **Requires `similarity_search_params.search_query`** (string) — not optional
- Recommended query: `"What do I know about this user and their preferences?"`
- Returns `retrievedMemories[]` with `memory.fact`, `memory.scope`, `memory.topics`, and `distance` score
- Lower distance = more relevant
- All 5 stored facts retrieved correctly for the query above
- `scope` filtering works — `user_id` + `profile` isolates memories per persona

## Critical API corrections vs the OpenClaw plugin

| Assumption | Reality |
|---|---|
| `spec.memoryBankConfig` on engine create | Field doesn't exist — just use `display_name` |
| `similarity_search_params.top_k` only | `search_query` is **required** alongside `top_k` |
| `memories:generate` is synchronous | Returns LRO — must fire-and-forget or poll |

## IAM setup required (one-time)

```bash
gcloud projects add-iam-policy-binding YOUR_PROJECT \
  --member="serviceAccount:service-{PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"
```

## Phase 4 implementation — confirmed design

```python
# After each Live session (fire-and-forget LRO — don't wait)
POST /{parent}/memories:generate
{
  "scope": {"user_id": "nick", "profile": "default"},
  "direct_contents_source": {"events": [...transcript turns...]}
}

# remember() tool — also fire-and-forget
POST /{parent}/memories:generate
{
  "scope": {"user_id": "nick", "profile": "default"},
  "direct_memories_source": {"direct_memories": [{"fact": "..."}]}
}

# At session start — synchronous, inject result into system_instruction
POST /{parent}/memories:retrieve
{
  "scope": {"user_id": "nick", "profile": "default"},
  "similarity_search_params": {
    "search_query": "What do I know about this user and their preferences?",
    "top_k": 10
  }
}
# → format retrievedMemories[].memory.fact as bullet list → prepend to system_instruction
```

## .env for losiern-vibe testing

```
GCP_PROJECT_ID=losiern-vibe
GCP_LOCATION=us-central1
VERTEX_REASONING_ENGINE_ID=YOUR_ENGINE_ID
```
