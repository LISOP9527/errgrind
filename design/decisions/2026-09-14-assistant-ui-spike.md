# assistant-ui conversation workspace spike

## Context

This spike evaluates whether assistant-ui can provide commodity conversation UI
infrastructure while ErrGrind keeps its own Error object, sidebar, provenance,
workflow state, and persistence authority. It is intentionally separate from
the existing Flask/Jinja WebUI.

## Decision

Add a small React + TypeScript + Vite frontend under `frontend/`. It uses
`ExternalStoreRuntime` with a host-owned message array and an assistant-ui
attachment adapter. The adapter owns only composer behavior: multiple image
selection, selection-order thumbnails, individual removal, sent-message image
rendering, and viewport auto-scroll. It does not add a thread list or expose
backend workflow stages as product navigation.

The frontend calls narrow Flask `/api/assistant/*` adapters. Record draft,
Record finalization, and Grill start/answer all go through
`ErrGrindApplication`; React does not call the database, provider, or workflow
classes directly. The structured Record response is rendered deterministically
from the validated draft, so no formatting model call is added.

The spike-only content shape is:

```ts
type ContentPart =
  | { type: "text"; text: string }
  | { type: "image-ref"; ref: string; source: "original"; order: number };
type SemanticField = { parts: ContentPart[] };
```

Each of `question`, `user_thoughts`, and `reference_answer` is conceptually a
`SemanticField`. A source attachment can be referenced by more than one field;
there is no exclusive ownership rule. Image refs always point to the original
selected file, never to OCR or model-generated text.

## Rationale

The current Application boundary accepts Record semantic fields as strings and
accepts `image_paths` separately. It validates and stores the original image
bytes as initial Error attachments, which is enough to prove attachment
provenance through Record → Error → Grill for a text-backed draft. The frontend
keeps the richer shape visible and does not pretend that the current database
has field-level media semantics.

## Consequences and explicit limitation

The spike can finalize a draft with a non-empty text question and preserve all
original uploaded files as Error attachments. Grill then starts in the same
conversation workspace and can receive a further answer without navigation.
The current `ErrorRecord` text columns and `record_error()` contract cannot
finalize an image-only semantic question, nor persist which original image is
part of which semantic field. The API reports this as
`legacy-text-plus-initial-original-attachments`; the UI displays the limitation
near the draft. It must not OCR or flatten an image merely to satisfy the
legacy column. A production change would require a small field-content
relation/JSON contract storing ordered text parts and original attachment IDs,
with provenance and migration rules; this spike deliberately does not migrate
the production schema.

Pre-finalization draft/messages live in React memory and a small
`sessionStorage` text draft. Original `File` objects are kept only in memory so
refresh-safe attachment recovery is intentionally deferred. The existing
CSRF, same-origin, one-time mutation-token, upload validation, and provider
error-redaction boundaries remain in force.
