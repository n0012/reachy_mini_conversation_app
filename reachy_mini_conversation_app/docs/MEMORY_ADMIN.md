# Agent Engine Memory Administration

Recipes for inspecting, migrating, and deleting memories in the Vertex AI Agent Engine Memory Bank.

All scripts run from the repo root on Mac with the SA key:

```bash
cd /path/to/reachy_mini_conversation_app
export GOOGLE_APPLICATION_CREDENTIALS=reachy-mini-sa-key.json
```

---

## Setup boilerplate

Paste this at the top of any admin script:

```python
import os, sys
sys.path.insert(0, 'src')
os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = 'reachy-mini-sa-key.json'
os.environ['GCP_PROJECT_ID'] = 'losiern-vibe'
os.environ['GCP_LOCATION'] = 'us-central1'
os.environ['VERTEX_REASONING_ENGINE_ID'] = 'YOUR_ENGINE_ID'

from reachy_mini_conversation_app.memory import _api, VertexMemoryBackend
b = VertexMemoryBackend()
```

---

## List all memories

```python
resp = _api('GET', b._url('memories'))
for m in resp.get('memories', []):
    print(m['name'].split('/')[-1], m.get('scope'), m.get('fact', '')[:80])
```

Filter by user/profile:

```python
all_mems = resp.get('memories', [])
nick_yuki = [m for m in all_mems
             if m.get('scope', {}).get('user_id') == 'nick'
             and m.get('scope', {}).get('profile') == 'yuki']
```

---

## Retrieve memories (semantic search)

```python
resp = _api('POST', b._url('memories:retrieve'), {
    'scope': {'user_id': 'nick', 'profile': 'yuki'},
    'similarity_search_params': {
        'search_query': 'What do I know about this user?',
        'top_k': 20,
    },
})
for m in resp.get('retrievedMemories', []):
    print(m.get('distance'), m.get('memory', {}).get('fact', '')[:80])
```

---

## Delete a memory by ID

```python
mem_id = '702826249294184448'   # last segment of the memory name
result = _api('DELETE', b._url(f'memories/{mem_id}'))
print(result)
```

---

## Migrate memories from one scope to another

The scope field is immutable via PATCH — migrate by deleting and re-creating.
Used when memories accumulated under the wrong profile (e.g. `default` → `yuki`).

```python
resp = _api('GET', b._url('memories'))
to_migrate = [m for m in resp.get('memories', [])
              if m.get('scope') == {'user_id': 'nick', 'profile': 'default'}]

for m in to_migrate:
    mem_id = m['name'].split('/')[-1]
    fact = m.get('fact', '')
    # Create under new scope
    _api('POST', b._url('memories:generate'), {
        'scope': {'user_id': 'nick', 'profile': 'yuki'},
        'direct_memories_source': {'direct_memories': [{'fact': fact}]},
        'revision_labels': {'source': 'scope-migration'},
    })
    # Delete old
    _api('DELETE', b._url(f'memories/{mem_id}'))
    print(f'migrated: {fact[:60]}')
```

---

## Add a memory manually

```python
_api('POST', b._url('memories:generate'), {
    'scope': {'user_id': 'nick', 'profile': 'yuki'},
    'direct_memories_source': {
        'direct_memories': [{'fact': 'Nick prefers concise answers.'}]
    },
    'revision_labels': {'source': 'manual'},
})
```

---

## Notes

- `memories:generate` starts an LRO — extraction takes 1–5 minutes before memories become retrievable
- The distance filter in `load_context` rejects results with distance > 1.5 (lower = more relevant)
- Memory scope keys: `user_id` (person) + `profile` (Reachy personality) — both must match on retrieval
- Engine: `losiern-vibe` / `us-central1` / `YOUR_ENGINE_ID`
