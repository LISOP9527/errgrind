export type Draft = {
  question: string;
  user_thoughts: string;
  reference_answer: string;
};

export type Attachment = { id: number; url: string; mime_type: string };

export type PublicMessage = {
  role: "user" | "assistant";
  content: string;
  attachments: Attachment[];
};

export type HistoryItem = { id: number; title: string; status_dot?: "red" | "amber" };

export type NextStep = { code: string; label: string; description: string };

export type Workspace = {
  error: {
    id: number;
    title: string;
    origin: string;
    created_at: string;
    source_error_id: number | null;
    question: string;
    user_thoughts: string | null;
    reference_answer: string | null;
    initial_attachments: Attachment[];
  };
  messages: PublicMessage[];
  composer: "grill" | "teach" | null;
  next_step: NextStep | null;
};

export type Bootstrap = {
  csrf: string;
  tokens: {
    record_draft: string;
    record_finalize: string;
    record_reset: string;
    drill_new: string;
    grill_start: string;
    grill_answer: string;
    teach_start: string;
    teach_answer: string;
    teach_finish: string;
    config_save: string;
    delete_error?: string;
  };
  history: HistoryItem[];
  configured: boolean;
  record_pending_attachment_count: number;
  workspace?: Workspace;
};


export type ConfigPayload = {
  settings: {
    provider: string; model: string; reasoning_effort: string | null;
    base_url: string; drill_context_n: number | string; grill_max_turns: number | string;
  };
  form_error: string | null;
  api_key_set: boolean;
  provider_models: Record<string, string>;
  opencode_base_url: string;
};

export type ModelInfo = {
  id: string;
  display_name: string;
  is_default: boolean;
  supported_reasoning_efforts: string[];
  default_reasoning_effort: string | null;
};

export type DrillPayload = {
  key: string;
  question: string | null;
  target_error: { id: number; title: string } | null;
  target_options: { id: number; title: string }[];
  result: { is_correct: boolean; derived_error_id: number | null } | null;
  answer: string;
  pending_attachment_count: number;
  state: string;
  csrf: string;
  tokens: { prepare: string; judge: string };
};

export type DraftResponse = {
  draft: Draft;
  ready: boolean;
  status: "ready" | "incomplete";
  missing_fields: string[];
  message: string;
  submit_token: string;
  pending_attachment_count: number;
};

export type ErrorResponse = { error: { id: number }; submit_token: string };

export type ConversationResponse = {
  workspace: Workspace;
  assistant_response: string | null;
  submit_token: string;
};

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly submitToken?: string,
  ) { super(message); }
}

async function jsonRequest<T>(url: string, init: RequestInit): Promise<T> {
  const response = await fetch(url, { credentials: "same-origin", ...init });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError(
      response.status,
      body.error || "请求未完成，请保留当前输入后重试。",
      typeof body.submit_token === "string" ? body.submit_token : undefined,
    );
  }
  return body as T;
}

function headers(bootstrap: Bootstrap, token: string): HeadersInit {
  return { "X-CSRFToken": bootstrap.csrf, "X-Submission-Token": token };
}

export function loadBootstrap(errorId?: number): Promise<Bootstrap> {
  const query = errorId === undefined ? "" : `?error_id=${encodeURIComponent(errorId)}`;
  return jsonRequest<Bootstrap>(`/assistant-ui/bootstrap${query}`, { method: "GET" });
}

export function loadWorkspace(errorId: number): Promise<Workspace> {
  return jsonRequest<Workspace>(`/api/assistant/workspace/${errorId}`, { method: "GET" });
}

function multipart(fields: Record<string, string>, files: File[]): FormData {
  const form = new FormData();
  Object.entries(fields).forEach(([key, value]) => form.append(key, value));
  files.forEach((file) => form.append("images", file, file.name));
  return form;
}

export function prepareDraft(bootstrap: Bootstrap, token: string, draft: Draft, rawInput: string, files: File[]): Promise<DraftResponse> {
  return jsonRequest<DraftResponse>("/api/assistant/record/draft", {
    method: "POST", headers: headers(bootstrap, token), body: multipart({ raw_input: rawInput, ...draft }, files),
  });
}

export function resetRecordDraft(bootstrap: Bootstrap, token: string): Promise<{ submit_token: string }> {
  return jsonRequest<{ submit_token: string }>("/api/assistant/record/reset", {
    method: "POST", headers: headers(bootstrap, token), body: new URLSearchParams(),
  });
}

export function finalizeRecord(bootstrap: Bootstrap, token: string, draft: Draft, files: File[]): Promise<ErrorResponse> {
  return jsonRequest<ErrorResponse>("/api/assistant/record/finalize", {
    method: "POST", headers: headers(bootstrap, token), body: multipart(draft, files),
  });
}

export function startGrill(bootstrap: Bootstrap, token: string, errorId: number): Promise<unknown> {
  return jsonRequest<unknown>("/api/assistant/grill/start", {
    method: "POST", headers: headers(bootstrap, token), body: new URLSearchParams({ error_id: String(errorId) }),
  });
}

export async function answerConversation(bootstrap: Bootstrap, token: string, kind: "grill" | "teach", errorId: number, answer: string, files: File[]): Promise<ConversationResponse> {
  const endpoint = kind === "grill" ? "/api/assistant/grill/answer" : "/api/assistant/teach/answer";
  const result = await jsonRequest<Partial<ConversationResponse> & Pick<ConversationResponse, "assistant_response" | "submit_token">>(endpoint, {
    method: "POST", headers: headers(bootstrap, token), body: multipart({ error_id: String(errorId), answer }, files),
  });
  return { ...result, workspace: result.workspace ?? await loadWorkspace(errorId) };
}

export function startTeach(bootstrap: Bootstrap, token: string, errorId: number): Promise<ConversationResponse> {
  return jsonRequest<ConversationResponse>("/api/assistant/teach/start", {
    method: "POST", headers: headers(bootstrap, token), body: new URLSearchParams({ error_id: String(errorId) }),
  });
}

export function finishTeach(bootstrap: Bootstrap, token: string, errorId: number): Promise<{ next_url: string }> {
  return jsonRequest<{ next_url: string }>("/api/assistant/teach/finish", {
    method: "POST", headers: headers(bootstrap, token), body: new URLSearchParams({ error_id: String(errorId) }),
  });
}


export function loadConfig(): Promise<ConfigPayload> {
  return jsonRequest<ConfigPayload>("/api/assistant/config", { method: "GET" });
}

export function discoverModels(bootstrap: Bootstrap, values: { provider: string; api_key: string; base_url: string }): Promise<{ models: ModelInfo[] }> {
  return jsonRequest<{ models: ModelInfo[] }>("/api/assistant/models", {
    method: "POST",
    headers: { "X-CSRFToken": bootstrap.csrf, "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(values),
  });
}

export function saveConfig(bootstrap: Bootstrap, token: string, values: Record<string, string>): Promise<{ message: string; config: ConfigPayload }> {
  return jsonRequest<{ message: string; config: ConfigPayload }>("/config", {
    method: "POST",
    headers: { ...headers(bootstrap, token), "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(values),
  });
}

export function createDrill(bootstrap: Bootstrap, errorId?: number): Promise<{ next_url: string }> {
  return jsonRequest<{ next_url: string }>("/api/assistant/drill/new", {
    method: "POST",
    headers: headers(bootstrap, bootstrap.tokens.drill_new),
    body: new URLSearchParams(errorId === undefined ? {} : { error_id: String(errorId) }),
  });
}

export function loadDrill(key: string): Promise<DrillPayload> {
  return jsonRequest<DrillPayload>(`/api/assistant/drill/${encodeURIComponent(key)}`, { method: "GET" });
}

export function prepareDrill(data: DrillPayload, errorId: number | null): Promise<{ redirect: string }> {
  return jsonRequest<{ redirect: string }>(`/api/drill/${encodeURIComponent(data.key)}/prepare`, {
    method: "POST",
    headers: { "X-CSRFToken": data.csrf, "X-Submission-Token": data.tokens.prepare, "Accept": "application/json" },
    body: new URLSearchParams({ error_id: errorId === null ? "" : String(errorId) }),
  });
}

export function judgeDrill(data: DrillPayload, answer: string, files: File[]): Promise<{ redirect: string }> {
  return jsonRequest<{ redirect: string }>(`/api/drill/${encodeURIComponent(data.key)}/judge`, {
    method: "POST",
    headers: { "X-CSRFToken": data.csrf, "X-Submission-Token": data.tokens.judge, "Accept": "application/json" },
    body: multipart({ answer }, files),
  });
}
