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

本节记录 spike 当时的限制；后续正式迁移决策和实现已改变其中的 Record 持久化结论。

At the time of this spike, it could finalize only a draft with a non-empty text
question and preserve uploaded files as Error attachments. The current
`ErrorRecord` text columns also could not persist which original image was part
of which semantic field. The API reported this as
`legacy-text-plus-initial-original-attachments`; the UI displays the limitation
near the draft. It must not OCR or flatten an image merely to satisfy the
legacy column. A production change would require a small field-content
relation/JSON contract storing ordered text parts and original attachment IDs,
with provenance and migration rules; this spike deliberately does not migrate
the production schema. The production implementation subsequently added durable
pending attachments and permits a pure-image Record confirmation, while the
field-level semantic limitation remains.

During the spike, pre-finalization draft/messages lived in React memory and a
small `sessionStorage` text draft, while original `File` objects were kept only
in memory. The production implementation now writes uploaded Record images to
server-side pending attachments before the model call, so refresh and model
failure recovery reuse those bytes. The existing CSRF, same-origin, one-time
mutation-token, upload validation, and provider error-redaction boundaries
remain in force.
