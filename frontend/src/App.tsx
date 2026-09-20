import {
  AttachmentPrimitive,
  AssistantRuntimeProvider,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  type AppendMessage,
  type AttachmentAdapter,
  type ImageMessagePartProps,
  type ThreadMessageLike,
  useExternalStoreRuntime,
} from "@assistant-ui/react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import ReactMarkdown from "react-markdown";
import {
  MarkdownTextPrimitive,
  rewriteLatexBracketDelimiters,
} from "@assistant-ui/react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import {
  answerConversation,
  ApiError,
  createDrill,
  finalizeRecord,
  finishTeach,
  loadBootstrap,
  loadConfig,
  discoverModels,
  loadDrill,
  loadWorkspace,
  prepareDraft,
  prepareDrill,
  resetRecordDraft,
  judgeDrill,
  saveConfig,
  startGrill,
  startTeach,
  type Bootstrap,
  type ConfigPayload,
  type ModelInfo,
  type Draft,
  type DrillPayload,
  type ErrorResponse,
  type HistoryItem,
  type Workspace as WorkspaceData,
} from "./api";

type UiContent = Exclude<ThreadMessageLike["content"], string>;
type UiMessage = {
  id: string;
  role: "user" | "assistant";
  content: UiContent;
  attachments?: ThreadMessageLike["attachments"];
};
type LocalAttachment = {
  id?: string | number;
  url?: string;
  file?: File;
  name?: string;
  content?: readonly any[];
};

const EMPTY_DRAFT: Draft = {
  question: "",
  user_thoughts: "",
  reference_answer: "",
};
const DRAFT_STORAGE_KEY = "errgrind-assistant-ui-draft-v1";

function parts(content: UiContent | string): readonly any[] {
  return typeof content === "string"
    ? [{ type: "text", text: content }]
    : content;
}

function textParts(content: UiContent | string): string {
  return parts(content)
    .filter(
      (part): part is { type: "text"; text: string } => part.type === "text",
    )
    .map((part) => part.text)
    .join("\n");
}

function imageUrls(content: UiContent | string): string[] {
  return parts(content)
    .filter(
      (part): part is { type: "image"; image: string } => part.type === "image",
    )
    .map((part) => part.image);
}

function draftText(draft: Draft): string {
  return [
    `题目\n${draft.question || "（还没有整理出题目）"}`,
    `当时思路\n${draft.user_thoughts || "（未提供；不会由模型补写）"}`,
    `参考答案\n${draft.reference_answer || "（未提供）"}`,
  ].join("\n\n");
}

function message(
  role: UiMessage["role"],
  content: UiContent,
  attachments?: ThreadMessageLike["attachments"],
): UiMessage {
  return { id: `${role}-${crypto.randomUUID()}`, role, content, attachments };
}

function workspaceMessages(data: WorkspaceData): UiMessage[] {
  return data.messages.map((item) => ({
    id: `${item.role}-${crypto.randomUUID()}`,
    role: item.role,
    content: item.content
      ? [{ type: "text" as const, text: item.content }]
      : [],
    attachments: item.attachments.map((attachment) => ({
      id: String(attachment.id),
      type: "image",
      name: "原始图片",
      contentType: attachment.mime_type,
      content: [{ type: "image", image: attachment.url }],
      status: { type: "complete" },
    })) as never,
  }));
}

function AttachmentThumb({
  attachment,
  remove,
}: {
  attachment: LocalAttachment;
  remove: boolean;
}) {
  const imageUrl =
    attachment.url ||
    attachment.content?.find((part) => part.type === "image")?.image;
  return (
    <AttachmentPrimitive.Root className="attachment-chip">
      <AttachmentPrimitive.unstable_Thumb className="attachment-thumb">
        {imageUrl && <img src={imageUrl} alt={attachment.name || "原始图片"} />}
      </AttachmentPrimitive.unstable_Thumb>
      <span className="attachment-name">
        <AttachmentPrimitive.Name />
      </span>
      {remove && (
        <AttachmentPrimitive.Remove aria-label="移除图片">
          ×
        </AttachmentPrimitive.Remove>
      )}
    </AttachmentPrimitive.Root>
  );
}

function MessageImage({ image, filename }: ImageMessagePartProps) {
  return (
    <img
      className="message-image"
      src={image}
      alt={filename || "原始图片附件"}
    />
  );
}

function MarkdownText() {
  return (
    <MarkdownTextPrimitive
      className="message-markdown"
      preprocess={rewriteLatexBracketDelimiters}
      remarkPlugins={[remarkGfm, remarkMath]}
      rehypePlugins={[rehypeKatex]}
      smooth={false}
    />
  );
}

function StaticMarkdown({
  text,
  className = "message-markdown",
}: {
  text: string;
  className?: string;
}) {
  return (
    <div className={className}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
      >
        {rewriteLatexBracketDelimiters(text)}
      </ReactMarkdown>
    </div>
  );
}

function Composer({ disabled }: { disabled: boolean }) {
  return (
    <ComposerPrimitive.Root className="composer" data-disabled={disabled}>
      <ComposerPrimitive.Attachments>
        {({ attachment }) => (
          <AttachmentThumb attachment={attachment as LocalAttachment} remove />
        )}
      </ComposerPrimitive.Attachments>
      <ComposerPrimitive.Input
        placeholder="输入你的错题，当时思路，及参考答案（可选）"
        submitMode="enter"
        disabled={disabled}
        aria-label="输入消息"
      />
      <div className="composer-actions">
        <ComposerPrimitive.AddAttachment multiple aria-label="添加图片">
          ＋ 图片
        </ComposerPrimitive.AddAttachment>
        <ComposerPrimitive.Send aria-label="发送">↑</ComposerPrimitive.Send>
      </div>
    </ComposerPrimitive.Root>
  );
}

function UserMessage() {
  return (
    <MessagePrimitive.Root className="message-row user-row">
      <div className="message-bubble user-bubble">
        <MessagePrimitive.Parts
          components={{ Text: MarkdownText, Image: MessageImage }}
        />
        <MessagePrimitive.Attachments>
          {({ attachment }) => (
            <AttachmentThumb
              attachment={attachment as LocalAttachment}
              remove={false}
            />
          )}
        </MessagePrimitive.Attachments>
      </div>
    </MessagePrimitive.Root>
  );
}

function AssistantMessage() {
  return (
    <MessagePrimitive.Root className="message-row assistant-row">
      <div className="message-bubble assistant-bubble">
        <MessagePrimitive.Parts
          components={{ Text: MarkdownText, Image: MessageImage }}
        />
        <MessagePrimitive.Attachments>
          {({ attachment }) => (
            <AttachmentThumb
              attachment={attachment as LocalAttachment}
              remove={false}
            />
          )}
        </MessagePrimitive.Attachments>
      </div>
    </MessagePrimitive.Root>
  );
}

function navigate(path: string) {
  if (window.location.pathname !== path) window.history.pushState({}, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

function errorIdFromLocation(): number | undefined {
  const match = window.location.pathname.match(/^\/errors\/(\d+)$/);
  return match ? Number(match[1]) : undefined;
}

function Sidebar({
  history,
  collapsed,
  open,
  onNew,
  onDrill,
  onToggle,
  onClose,
}: {
  history: HistoryItem[];
  collapsed: boolean;
  open: boolean;
  onNew: () => void;
  onDrill: () => void;
  onToggle: () => void;
  onClose: () => void;
}) {
  return (
    <aside
      className={`sidebar ${collapsed ? "collapsed" : ""} ${open ? "open" : ""}`}
      aria-label="工作区导航"
    >
      <div className="brand">
        <a href="/" onClick={(event) => { event.preventDefault(); navigate("/"); }}>ErrGrind</a>
        <button
          className="sidebar-collapse"
          type="button"
          onClick={onToggle}
          aria-label="收起侧栏"
        >
          收起
        </button>
        <button
          className="drawer-close"
          type="button"
          onClick={onClose}
          aria-label="关闭导航"
        >
          ×
        </button>
      </div>
      <nav className="primary-nav" aria-label="快速操作">
        <button
          className="nav-link new-error-link"
          type="button"
          onClick={onNew}
        >
          <span className="nav-icon" aria-hidden="true">
            +
          </span>
          <span>New error</span>
        </button>
        <a className="nav-link" href="/drill" onClick={(event) => { event.preventDefault(); onDrill(); }}>
          <span className="nav-icon drill-icon" aria-hidden="true">
            ◇
          </span>
          <span>Drill</span>
        </a>
      </nav>
      <div className="history" aria-label="最近 Error">
        <div className="history-heading">Recent errors</div>
        {history.map((item) => (
          <a
            className="history-item"
            href={`/errors/${item.id}`}
            onClick={(event) => { event.preventDefault(); navigate(`/errors/${item.id}`); onClose(); }}
            key={item.id}
            aria-label={item.title}
          >
            <span className="history-title">{item.title}</span>
            {item.status_dot && (
              <span
                className={`status-dot ${item.status_dot}`}
                aria-hidden="true"
              />
            )}
          </a>
        ))}
        {history.length === 0 && (
          <p className="muted sidebar-empty">还没有 Error</p>
        )}
      </div>
      <a className="config-link" href="/config" onClick={(event) => { event.preventDefault(); navigate("/config"); onClose(); }}>
        <span className="nav-icon settings-icon" aria-hidden="true">
          ⚙
        </span>
        <span>Settings</span>
      </a>
    </aside>
  );
}

function StaticShell({
  title,
  bootstrap,
  children,
}: {
  title: string;
  bootstrap: Bootstrap | null;
  children: React.ReactNode;
}) {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  return (
    <div className="shell">
      <Sidebar
        history={bootstrap?.history || []}
        collapsed={sidebarCollapsed}
        open={sidebarOpen}
        onNew={() => {
          if (!bootstrap) { navigate("/"); return; }
          resetRecordDraft(bootstrap, bootstrap.tokens.record_reset)
            .then(() => { sessionStorage.removeItem(DRAFT_STORAGE_KEY); navigate("/"); })
            .catch(() => navigate("/"));
        }}
        onDrill={() => {
          if (!bootstrap) { window.location.href = "/drill"; return; }
          createDrill(bootstrap).then((result) => navigate(result.next_url)).catch(() => { window.location.href = "/drill"; });
        }}
        onToggle={() => setSidebarCollapsed((value) => !value)}
        onClose={() => setSidebarOpen(false)}
      />
      {sidebarCollapsed && (
        <button
          className="sidebar-reopen"
          type="button"
          onClick={() => setSidebarCollapsed(false)}
          aria-label="打开侧栏"
        >
          ☰
        </button>
      )}
      <main className="workspace">
        <header className="workspace-header">
          <button
            className="menu-button"
            onClick={() => setSidebarOpen((value) => !value)}
            aria-label="打开菜单"
          >
            ☰
          </button>
          <div className="workspace-title">
            <h1>{title}</h1>
          </div>
        </header>
        <div className="page-scroll">
          <section className="static-page">{children}</section>
        </div>
      </main>
    </div>
  );
}

type ConfigFormState = {
  provider: string;
  model: string;
  reasoning_effort: string;
  api_key: string;
  base_url: string;
  drill_context_n: string;
  grill_max_turns: string;
  clear_api_key: boolean;
};

function configForm(payload: ConfigPayload): ConfigFormState {
  return {
    provider: payload.settings.provider,
    model: payload.settings.model,
    reasoning_effort: payload.settings.reasoning_effort || "",
    api_key: "",
    base_url: payload.settings.base_url || "",
    drill_context_n: String(payload.settings.drill_context_n),
    grill_max_turns: String(payload.settings.grill_max_turns),
    clear_api_key: false,
  };
}

type SaveState = "saved" | "pending" | "saving" | "error";

function isPositiveIntegerString(val: string): boolean {
  const trimmed = val.trim();
  if (!trimmed || !/^\d+$/.test(trimmed)) return false;
  const n = Number(trimmed);
  return Number.isSafeInteger(n) && n >= 1;
}

function isConfigFormValid(
  state: ConfigFormState | null,
  payload: ConfigPayload | null,
  formElement?: HTMLFormElement | null,
): boolean {
  if (!state || !payload) return false;
  if (!state.provider.trim()) return false;
  if (!state.model.trim()) return false;
  if (!isPositiveIntegerString(state.drill_context_n)) return false;
  if (!isPositiveIntegerString(state.grill_max_turns)) return false;
  const changedProvider = state.provider !== payload.settings.provider;
  const changedOpenCodeUrl =
    state.provider === "opencode" &&
    state.base_url.replace(/\/+$/, "") !==
      (payload.settings.base_url || payload.opencode_base_url).replace(/\/+$/, "");
  if (
    state.provider !== "codex" &&
    (changedProvider || changedOpenCodeUrl) &&
    !state.api_key.trim()
  ) {
    return false;
  }
  if (formElement && !formElement.checkValidity()) return false;
  return true;
}

function isConfigFormEqual(a: ConfigFormState, b: ConfigFormState): boolean {
  return (
    a.provider === b.provider &&
    a.model === b.model &&
    a.reasoning_effort === b.reasoning_effort &&
    a.api_key === b.api_key &&
    a.base_url === b.base_url &&
    a.drill_context_n === b.drill_context_n &&
    a.grill_max_turns === b.grill_max_turns &&
    a.clear_api_key === b.clear_api_key
  );
}

function ConfigPage() {
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null);
  const [payload, setPayload] = useState<ConfigPayload | null>(null);
  const [form, setForm] = useState<ConfigFormState | null>(null);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("saved");
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelsError, setModelsError] = useState<string | null>(null);

  const modelRequest = useRef(0);
  const formRef = useRef<ConfigFormState | null>(null);
  const bootstrapRef = useRef<Bootstrap | null>(null);
  const payloadRef = useRef<ConfigPayload | null>(null);
  const lastSavedRef = useRef<ConfigFormState | null>(null);
  const formRevisionRef = useRef(0);
  const secretRevisionRef = useRef(0);
  const isSavingRef = useRef(false);
  const pendingSaveRef = useRef(false);
  const deferredSaveRef = useRef(false);
  const debounceTimerRef = useRef<number | null>(null);
  const formElementRef = useRef<HTMLFormElement | null>(null);
  const isMountedRef = useRef(true);

  const triggerSaveRef = useRef<() => Promise<void>>(() => Promise.resolve());
  const performSaveRef = useRef<() => Promise<void>>(() => Promise.resolve());

  useEffect(() => {
    isMountedRef.current = true;
    Promise.all([loadBootstrap(), loadConfig()])
      .then(([boot, config]) => {
        if (!isMountedRef.current) return;
        setBootstrap(boot);
        bootstrapRef.current = boot;
        setPayload(config);
        payloadRef.current = config;
        const initial = configForm(config);
        setForm(initial);
        formRef.current = initial;
        lastSavedRef.current = initial;
        setSaveState("saved");
      })
      .catch((error: Error) => {
        if (isMountedRef.current) setErrorText(error.message);
      });

    return () => {
      isMountedRef.current = false;
      if (debounceTimerRef.current) {
        window.clearTimeout(debounceTimerRef.current);
      }
    };
  }, []);

  const triggerSave = useCallback(async () => {
    if (isSavingRef.current) {
      pendingSaveRef.current = true;
      return;
    }
    const current = formRef.current;
    if (!isConfigFormValid(current, payloadRef.current, formElementRef.current)) {
      return;
    }
    if (lastSavedRef.current && current && isConfigFormEqual(current, lastSavedRef.current)) {
      pendingSaveRef.current = false;
      if (isMountedRef.current) setSaveState("saved");
      return;
    }
    await performSaveRef.current();
  }, []);

  const performSave = useCallback(async () => {
    if (isSavingRef.current) {
      pendingSaveRef.current = true;
      return;
    }
    const currentBootstrap = bootstrapRef.current;
    const currentForm = formRef.current;
    if (!currentBootstrap || !currentForm) return;
    if (!isConfigFormValid(
      currentForm,
      payloadRef.current,
      formElementRef.current,
    )) return;

    const snapshot = { ...currentForm };
    const formRevision = formRevisionRef.current;
    const secretRevision = secretRevisionRef.current;
    isSavingRef.current = true;
    pendingSaveRef.current = false;
    if (isMountedRef.current) setSaveState("saving");

    const token = currentBootstrap.tokens.config_save;
    let success = false;
    let savedConfig: ConfigPayload | null = null;

    try {
      const result = await saveConfig(currentBootstrap, token, {
        provider: snapshot.provider,
        model: snapshot.model,
        reasoning_effort: snapshot.reasoning_effort,
        api_key: snapshot.api_key,
        base_url: snapshot.base_url,
        drill_context_n: snapshot.drill_context_n,
        grill_max_turns: snapshot.grill_max_turns,
        clear_api_key: snapshot.clear_api_key ? "1" : "0",
      });
      success = true;
      savedConfig = result.config;
      if (isMountedRef.current) setErrorText(null);
    } catch (error) {
      if (error instanceof ApiError && error.submitToken) {
        const nextToken = error.submitToken;
        if (isMountedRef.current) {
          setBootstrap((cur) => cur ? { ...cur, tokens: { ...cur.tokens, config_save: nextToken } } : cur);
        }
        if (bootstrapRef.current) {
          bootstrapRef.current = {
            ...bootstrapRef.current,
            tokens: { ...bootstrapRef.current.tokens, config_save: nextToken },
          };
        }
      }
      if (isMountedRef.current) {
        setErrorText(error instanceof Error ? error.message : "配置保存失败。");
        setSaveState("error");
      }
    } finally {
      if (success && savedConfig) {
        if (isMountedRef.current) setPayload(savedConfig);
        payloadRef.current = savedConfig;
        const savedForm = configForm(savedConfig);
        lastSavedRef.current = savedForm;

        const current = formRef.current;
        if (current) {
          const next = formRevisionRef.current === formRevision
            ? savedForm
            : { ...current };
          if (
            formRevisionRef.current !== formRevision &&
            secretRevisionRef.current === secretRevision
          ) {
            next.api_key = "";
            next.clear_api_key = false;
          }
          formRef.current = next;
          if (isMountedRef.current) setForm(next);
        }

        try {
          const fresh = await loadBootstrap();
          if (isMountedRef.current) setBootstrap(fresh);
          bootstrapRef.current = fresh;
        } catch {
          // Recover on subsequent navigation/retry
        }
      } else if (!success) {
        try {
          const fresh = await loadBootstrap();
          if (isMountedRef.current) setBootstrap(fresh);
          bootstrapRef.current = fresh;
        } catch {
          // Recover on subsequent navigation/retry
        }
      }

      isSavingRef.current = false;

      const newest = formRef.current;
      const hasUnsavedChanges =
        pendingSaveRef.current ||
        (newest && lastSavedRef.current && !isConfigFormEqual(newest, lastSavedRef.current));
      const changedSinceSnapshot =
        newest && !isConfigFormEqual(newest, snapshot);

      if (
        hasUnsavedChanges &&
        !deferredSaveRef.current &&
        (success || changedSinceSnapshot)
      ) {
        if (!debounceTimerRef.current) {
          if (isConfigFormValid(
            newest,
            payloadRef.current,
            formElementRef.current,
          )) {
            void triggerSaveRef.current();
          } else if (isMountedRef.current) {
            setSaveState("pending");
          }
        } else if (isMountedRef.current) {
          setSaveState("pending");
        }
      } else if (hasUnsavedChanges && isMountedRef.current) {
        setSaveState("pending");
      } else if (success && isMountedRef.current) {
        setSaveState("saved");
      }
    }
  }, []);

  useEffect(() => {
    triggerSaveRef.current = triggerSave;
  }, [triggerSave]);

  useEffect(() => {
    performSaveRef.current = performSave;
  }, [performSave]);

  const markPending = useCallback(() => {
    setSaveState("pending");
    pendingSaveRef.current = true;
    if (debounceTimerRef.current) {
      window.clearTimeout(debounceTimerRef.current);
      debounceTimerRef.current = null;
    }
  }, []);

  const scheduleSave = useCallback(() => {
    markPending();
    deferredSaveRef.current = false;
    debounceTimerRef.current = window.setTimeout(() => {
      debounceTimerRef.current = null;
      void triggerSaveRef.current();
    }, 500);
  }, [markPending]);

  const flushSave = useCallback(() => {
    if (debounceTimerRef.current) {
      window.clearTimeout(debounceTimerRef.current);
      debounceTimerRef.current = null;
    }
    const current = formRef.current;
    if (!isConfigFormValid(
      current,
      payloadRef.current,
      formElementRef.current,
    )) {
      formElementRef.current?.reportValidity();
      return;
    }
    void triggerSaveRef.current();
  }, []);

  const initialProvider = payload?.settings.provider;
  const sameProvider = !!form && form.provider === initialProvider;
  const loadModels = useCallback(async (snapshot: ConfigFormState) => {
    if (!bootstrap || !payload) return;
    const requestId = ++modelRequest.current;
    setModelsLoading(true);
    setModelsError(null);
    try {
      const result = await discoverModels(bootstrap, {
        provider: snapshot.provider,
        api_key: snapshot.api_key,
        base_url: snapshot.base_url,
      });
      if (requestId !== modelRequest.current) return;
      setModels(result.models);
      setForm((current) => {
        if (!current || current.provider !== snapshot.provider) return current;
        const currentEntry = result.models.find((item) => item.id === current.model);
        const keepConfigured =
          current.provider === payload.settings.provider &&
          current.model === payload.settings.model;
        const fallback =
          result.models.find(
            (item) => item.id === payload.provider_models[current.provider],
          ) || result.models.find((item) => item.is_default) || result.models[0];
        const model = currentEntry || keepConfigured
          ? current.model
          : fallback?.id || "";
        const metadata = result.models.find((item) => item.id === model);
        const efforts = metadata?.supported_reasoning_efforts || [];
        const reasoning_effort =
          current.reasoning_effort && metadata &&
          !efforts.includes(current.reasoning_effort)
            ? ""
            : current.reasoning_effort;
        const next = { ...current, model, reasoning_effort };
        if (!isConfigFormEqual(current, next)) {
          formRevisionRef.current += 1;
        }
        formRef.current = next;
        return next;
      });
    } catch (error) {
      if (requestId !== modelRequest.current) return;
      setModels([]);
      setModelsError(
        error instanceof Error ? error.message : "模型目录获取失败，请重试。",
      );
    } finally {
      if (requestId === modelRequest.current) setModelsLoading(false);
    }
  }, [bootstrap, payload]);

  const savedOpenCodeEndpointMatches = !!form && !!payload && (
    form.provider !== "opencode" ||
    form.base_url.replace(/\/+$/, "") ===
      (payload.settings.base_url || payload.opencode_base_url).replace(/\/+$/, "")
  );
  const hasDiscoveryCredentials = !!form && (
    form.provider === "codex" ||
    !!form.api_key.trim() ||
    (sameProvider && !!payload?.api_key_set && savedOpenCodeEndpointMatches)
  );
  useEffect(() => {
    if (!bootstrap || !payload || !form) return;
    if (!hasDiscoveryCredentials) {
      modelRequest.current += 1;
      setModels([]);
      setModelsError(null);
      setModelsLoading(false);
      return;
    }
    const snapshot = form;
    const changedOpenCodeUrl =
      snapshot.provider === "opencode" &&
      snapshot.base_url !== payload.settings.base_url;
    const timer = window.setTimeout(() => {
      void loadModels(snapshot);
    }, snapshot.api_key || changedOpenCodeUrl ? 500 : 0);
    return () => {
      window.clearTimeout(timer);
      modelRequest.current += 1;
    };
  }, [
    bootstrap,
    payload,
    form?.provider,
    form?.api_key,
    form?.base_url,
    hasDiscoveryCredentials,
    loadModels,
  ]);

  const set = (
    key: keyof ConfigFormState,
    value: string | boolean,
    saveAfterDebounce = true,
  ) => {
    formRevisionRef.current += 1;
    if (key === "api_key" || key === "clear_api_key") {
      secretRevisionRef.current += 1;
    }
    setForm((current) => {
      if (!current) return current;
      const next = { ...current, [key]: value };
      formRef.current = next;
      return next;
    });
    if (saveAfterDebounce) {
      scheduleSave();
    } else {
      markPending();
      deferredSaveRef.current = true;
    }
  };

  const changeProvider = (provider: string) => {
    if (!payload || !formRef.current) return;
    formRevisionRef.current += 1;
    secretRevisionRef.current += 1;
    setModels([]);
    setModelsError(null);
    const next: ConfigFormState = {
      ...formRef.current,
      provider,
      model: provider === payload.settings.provider
        ? payload.settings.model
        : payload.provider_models[provider] || "",
      reasoning_effort: provider === "codex"
        ? (provider === payload.settings.provider
          ? payload.settings.reasoning_effort || ""
          : formRef.current.reasoning_effort)
        : "",
      api_key: "",
      clear_api_key: false,
      base_url:
        provider === "opencode"
          ? (provider === payload.settings.provider
            ? payload.settings.base_url || payload.opencode_base_url
            : payload.opencode_base_url)
          : "",
    };
    formRef.current = next;
    setForm(next);
    if (provider === "codex") {
      scheduleSave();
    } else {
      markPending();
      deferredSaveRef.current = true;
    }
  };

  const providerLabel: Record<string, string> = { gemini: "Gemini", deepseek: "DeepSeek", opencode: "OpenCode", codex: "Codex" };
  const isCodex = form?.provider === "codex";
  const isOpenCode = form?.provider === "opencode";
  const selectedModel = models.find((item) => item.id === form?.model);
  const effortOptions = selectedModel
    ? selectedModel.supported_reasoning_efforts
    : (form?.reasoning_effort ? [form.reasoning_effort] : []);
  const changeModel = (model: string) => {
    const metadata = models.find((item) => item.id === model);
    formRevisionRef.current += 1;
    setForm((current) => {
      if (!current) return current;
      const efforts = metadata?.supported_reasoning_efforts || [];
      const reasoning_effort =
        current.reasoning_effort && metadata &&
        !efforts.includes(current.reasoning_effort)
          ? ""
          : current.reasoning_effort;
      const next = { ...current, model, reasoning_effort };
      formRef.current = next;
      return next;
    });
    scheduleSave();
  };

  const statusLabel = useMemo(() => {
    switch (saveState) {
      case "saving":
        return "保存中…";
      case "pending":
        return "待保存…";
      case "saved":
        return "已保存";
      case "error":
        return "保存失败";
    }
  }, [saveState]);

  return (
    <StaticShell title="Settings" bootstrap={bootstrap}>
      <div className="page-heading settings-heading">
        <p>调整模型服务、提供商及运行参数。</p>
        {form && payload && (
          <div
            className={`settings-status ${saveState}`}
            role="status"
            aria-live="polite"
          >
            <span className={`settings-status-dot ${saveState}`} aria-hidden="true" />
            <span>{statusLabel}</span>
            {saveState === "error" && (
              <button
                type="button"
                className="settings-retry-button"
                onClick={() => void flushSave()}
              >
                重试
              </button>
            )}
          </div>
        )}
      </div>
      {errorText && (
        <div className="error-banner static-error" role="alert">
          {errorText}
        </div>
      )}
      {!form || !payload ? (
        <p className="muted">加载中…</p>
      ) : (
        <form
          ref={formElementRef}
          className="settings-form"
          onSubmit={(e) => {
            e.preventDefault();
            void flushSave();
          }}
        >
          <div className="field">
            <label htmlFor="config-provider">提供商 (Provider)</label>
            <select
              id="config-provider"
              value={form.provider}
              onChange={(e) => changeProvider(e.target.value)}
            >
              <option value="gemini">Gemini</option>
              <option value="deepseek">DeepSeek</option>
              <option value="opencode">OpenCode</option>
              <option value="codex">Codex</option>
            </select>
            <p className="hint">选择用于诊断和练习生成的 AI 服务提供商。</p>
          </div>
          <div className="field">
            <label htmlFor="config-model">模型 (Model)</label>
            <div className="model-select-row">
              <select
                id="config-model"
                value={form.model}
                onChange={(e) => changeModel(e.target.value)}
                required
              >
                {form.model && !models.some((item) => item.id === form.model) && (
                  <option value={form.model}>
                    {sameProvider && form.model === payload.settings.model
                      ? "当前配置（目录未返回）"
                      : "默认回退（目录未验证）"}: {form.model}
                  </option>
                )}
                {!form.model && <option value="">{modelsLoading ? "检测模型中…" : "请选择模型"}</option>}
                {models.map((model) => <option key={model.id} value={model.id}>
                  {model.display_name && model.display_name !== model.id
                    ? `${model.display_name} (${model.id})`
                    : model.id}
                </option>)}
              </select>
              <button
                type="button"
                className="secondary-button"
                disabled={modelsLoading || !hasDiscoveryCredentials}
                onClick={() => void loadModels(form)}
              >
                重新检测
              </button>
            </div>
            <p className="hint">
              {modelsError || (!hasDiscoveryCredentials
                ? "输入 API Key 后将自动检测当前账号的模型目录。"
                : "目录由提供商返回，仅表示账号可见，不代表逐模型生成验证。")}
            </p>
          </div>
          {isCodex && (
            <div className="field">
              <label htmlFor="config-reasoning-effort">
                推理深度 (Reasoning effort)
              </label>
              <select
                id="config-reasoning-effort"
                value={form.reasoning_effort}
                onChange={(e) => set("reasoning_effort", e.target.value)}
              >
                <option value="">默认 (Default)</option>
                {effortOptions.map((effort) => <option key={effort} value={effort}>{effort}</option>)}
              </select>
              <p className="hint">
                仅适用于 Codex 提供商，用于控制思考与推理深度。
              </p>
            </div>
          )}
          {!isCodex ? (
            <div className="field">
              <label htmlFor="config-api-key">API Key</label>
              <input
                id="config-api-key"
                type="password"
                autoComplete="new-password"
                value={form.api_key}
                onChange={(e) => set("api_key", e.target.value, false)}
                onBlur={scheduleSave}
                placeholder={
                  sameProvider && payload.api_key_set
                    ? "已配置 API Key（留空保持不变）"
                    : `输入 ${providerLabel[form.provider] || form.provider} API Key`
                }
              />
              {sameProvider && (
                <div className="api-key-status">
                  <span
                    className={
                      payload.api_key_set
                        ? "key-status-indicator is-set"
                        : "key-status-indicator is-unset"
                    }
                  >
                    {payload.api_key_set
                      ? "当前提供商已配置 API Key"
                      : "当前提供商未配置 API Key"}
                  </span>
                </div>
              )}
              {sameProvider && (
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={form.clear_api_key}
                    onChange={(e) => set("clear_api_key", e.target.checked)}
                  />
                  <span>清除当前已保存的 API Key</span>
                </label>
              )}
              <p className="hint">
                留空保存将保持当前提供商已有
                Key；若切换提供商，必须输入新提供商的 API Key。
              </p>
            </div>
          ) : (
            <div className="field">
              <p className="hint codex-terminal-hint">
                Codex 提供商通过终端 <code>errgrind</code> CLI
                认证与登录，无需在此输入 API Key。
              </p>
            </div>
          )}
          {isOpenCode && (
            <div className="field">
              <label htmlFor="config-base-url">API 地址 (Base URL)</label>
              <input
                id="config-base-url"
                value={form.base_url}
                onChange={(e) => set("base_url", e.target.value, false)}
                onBlur={scheduleSave}
                placeholder={payload.opencode_base_url}
                autoComplete="off"
              />
              <p className="hint">
                仅适用于 OpenCode 提供商。填写兼容接口的服务基础地址（如{" "}
                <code>{payload.opencode_base_url}</code>）。
              </p>
            </div>
          )}
          <div className="field">
            <label htmlFor="config-drill-context-n">
              Drill 上下文条数 (drill_context_n)
            </label>
            <input
              id="config-drill-context-n"
              type="number"
              min="1"
              step="1"
              value={form.drill_context_n}
              onChange={(e) => set("drill_context_n", e.target.value)}
              required
            />
            <p className="hint">
              生成 Drill 练习时最多参考的历史诊断 Error 条数（至少为 1，默认
              10）。
            </p>
          </div>
          <div className="field">
            <label htmlFor="config-grill-max-turns">
              Grill 最大追问轮数 (grill_max_turns)
            </label>
            <input
              id="config-grill-max-turns"
              type="number"
              min="1"
              step="1"
              value={form.grill_max_turns}
              onChange={(e) => set("grill_max_turns", e.target.value)}
              required
            />
            <p className="hint">
              单次 Grill 诊断对话的最大交互轮数上限（至少为 1，默认 30）。
            </p>
          </div>
        </form>
      )}
    </StaticShell>
  );
}

type DrillFile = { file: File; url: string };

function DrillPage({ drillKey }: { drillKey: string }) {
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null);
  const [drill, setDrill] = useState<DrillPayload | null>(null);
  const [answer, setAnswer] = useState("");
  const [files, setFiles] = useState<DrillFile[]>([]);
  const filesRef = useRef<DrillFile[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const data = await loadDrill(drillKey);
    setDrill(data);
    setAnswer(data.answer || "");
  }, [drillKey]);
  useEffect(() => {
    Promise.all([loadBootstrap(), loadDrill(drillKey)])
      .then(([boot, data]) => {
        setBootstrap(boot);
        setDrill(data);
        setAnswer(data.answer || "");
      })
      .catch((error: Error) => setErrorText(error.message));
  }, [drillKey]);
  useEffect(() => {
    filesRef.current = files;
  }, [files]);
  useEffect(
    () => () => {
      filesRef.current.forEach((item) => URL.revokeObjectURL(item.url));
    },
    [],
  );

  const start = async () => {
    if (!drill || isRunning) return;
    setIsRunning(true);
    setErrorText(null);
    try {
      await prepareDrill(drill);
      await refresh();
    } catch (error) {
      setErrorText(error instanceof Error ? error.message : "生成练习失败。");
      await refresh().catch(() => undefined);
    } finally {
      setIsRunning(false);
    }
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!drill || isRunning || (!answer.trim() && files.length === 0 && drill.pending_attachment_count === 0)) return;
    setIsRunning(true);
    setErrorText(null);
    try {
      await judgeDrill(
        drill,
        answer,
        files.map((item) => item.file),
      );
      await refresh();
      files.forEach((item) => URL.revokeObjectURL(item.url));
      setFiles([]);
    } catch (error) {
      setErrorText(
        error instanceof Error ? error.message : "判分失败，请保留答案后重试。",
      );
      await refresh().catch(() => undefined);
    } finally {
      setIsRunning(false);
    }
  };
  const addFiles = (selected: FileList | null) => {
    if (!selected) return;
    setFiles((current) => [
      ...current,
      ...Array.from(selected).map((file) => ({
        file,
        url: URL.createObjectURL(file),
      })),
    ]);
  };
  const removeFile = (index: number) =>
    setFiles((current) =>
      current.filter((item, i) => {
        if (i === index) URL.revokeObjectURL(item.url);
        return i !== index;
      }),
    );

  return (
    <StaticShell title="Drill" bootstrap={bootstrap}>
      {errorText && (
        <div className="error-banner static-error" role="alert">
          {errorText}
        </div>
      )}
      {!drill ? (
        <p className="muted">加载中…</p>
      ) : !drill.question ? (
        <div className="drill-start-react" aria-live="polite">
          {drill.state === "preparing" || isRunning ? (
            <p className="drill-lead">正在根据历史 Error 生成练习…</p>
          ) : drill.state === "failed" ? (
            <>
              <p className="drill-lead">生成练习失败。</p>
              <button
                className="primary-button"
                onClick={start}
                disabled={isRunning}
              >
                重试
              </button>
            </>
          ) : (
            <>
              <div className="drill-intro">
                <p className="drill-lead">根据历史 Error 生成一题新的练习。</p>
                <p className="drill-description">
                  开始后会从已完成诊断的 Error 中选择一个目标并生成题目。
                </p>
              </div>
              <button
                className="primary-button"
                onClick={start}
                disabled={isRunning}
              >
                开始 Drill
              </button>
            </>
          )}
        </div>
      ) : (
        <div className="drill-active-react">
          <div className="drill-question-react">
            <p className="drill-context">根据历史 Error 生成</p>
            <StaticMarkdown text={drill.question} />
          </div>
          {drill.pending_attachment_count > 0 && (
            <p className="attachment-status">
              ＋ 图片附件已保留，可直接重试提交
            </p>
          )}
          {drill.result ? (
            <section
              className={`verdict-react ${drill.result.is_correct ? "correct" : "incorrect"}`}
              aria-live="polite"
            >
              <strong>{drill.result.is_correct ? "正确" : "错误"}</strong>
              {drill.result.is_correct ? (
                <>
                  <p>这次练习完成了。</p>
                  <div className="inline-actions">
                    <a href="/drill" onClick={(event) => {
                      event.preventDefault();
                      if (!bootstrap) return;
                      createDrill(bootstrap)
                        .then((result) => navigate(result.next_url))
                        .catch((error) => setErrorText(error instanceof Error ? error.message : "进入 Drill 失败。"));
                    }}>
                      再做一道 <span aria-hidden="true">→</span>
                    </a>
                    <a className="muted-link" href="/" onClick={(event) => { event.preventDefault(); navigate("/"); }}>
                      返回
                    </a>
                  </div>
                </>
              ) : (
                <>
                  <p>这次回答已经记录，可以继续查看由此产生的 Error。</p>
                  <a
                    href={
                      drill.result.derived_error_id
                        ? `/errors/${drill.result.derived_error_id}`
                        : "/"
                    }
                    onClick={(event) => {
                      event.preventDefault();
                      navigate(drill.result?.derived_error_id ? `/errors/${drill.result.derived_error_id}` : "/");
                    }}
                  >
                    继续查看这道 Error <span aria-hidden="true">→</span>
                  </a>
                </>
              )}
            </section>
          ) : (
            <form className="drill-answer-form" onSubmit={submit}>
              {files.length > 0 && (
                <div className="drill-file-strip">
                  {files.map((item, index) => (
                    <div
                      className="drill-file-chip"
                      key={`${item.file.name}-${index}`}
                    >
                      <img src={item.url} alt={item.file.name} />
                      <button
                        type="button"
                        onClick={() => removeFile(index)}
                        aria-label="移除图片"
                      >
                        ×
                      </button>
                    </div>
                  ))}
                </div>
              )}
              <textarea
                value={answer}
                onChange={(e) => setAnswer(e.target.value)}
                rows={3}
                placeholder="输入答案…"
                aria-label="你的答案"
              />
              <div className="drill-composer-actions">
                <label className="attach-label-react">
                  ＋ 图片
                  <input
                    type="file"
                    accept="image/png,image/jpeg,image/webp"
                    multiple
                    hidden
                    onChange={(e) => {
                      addFiles(e.target.files);
                      e.currentTarget.value = "";
                    }}
                  />
                </label>
                <button
                  className="send-button-react"
                  type="submit"
                  disabled={isRunning || (!answer.trim() && files.length === 0 && drill.pending_attachment_count === 0)}
                  aria-label="提交答案"
                >
                  ↑
                </button>
              </div>
            </form>
          )}
        </div>
      )}
    </StaticShell>
  );
}

function Workspace() {
  const requestedErrorId = errorIdFromLocation();
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null);
  const [workspace, setWorkspace] = useState<WorkspaceData | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [draft, setDraft] = useState<Draft>(() => {
    try {
      return (
        JSON.parse(sessionStorage.getItem(DRAFT_STORAGE_KEY) || "null") ||
        EMPTY_DRAFT
      );
    } catch {
      return EMPTY_DRAFT;
    }
  });
  const [errorText, setErrorText] = useState<string | null>(null);
  const [recordAttachmentCount, setRecordAttachmentCount] = useState(0);
  const [isRunning, setIsRunning] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const fileByAttachment = useRef(new Map<string, File>());
  const fileByUrl = useRef(new Map<string, File>());

  const resetRecord = useCallback(async () => {
    if (bootstrap) {
      try {
        const result = await resetRecordDraft(bootstrap, bootstrap.tokens.record_reset);
        setBootstrap((current) => current ? {
          ...current, tokens: { ...current.tokens, record_reset: result.submit_token },
        } : current);
      } catch (error) {
        if (error instanceof ApiError && error.submitToken) {
          setBootstrap((current) => current ? {
            ...current,
            tokens: { ...current.tokens, record_reset: error.submitToken! },
          } : current);
        }
        setErrorText(error instanceof Error ? error.message : "新建 Error 失败，请重试。");
        return;
      }
    }
    navigate("/");
    setWorkspace(null);
    setMessages([]);
    setDraft(EMPTY_DRAFT);
    setRecordAttachmentCount(0);
    sessionStorage.removeItem(DRAFT_STORAGE_KEY);
    setErrorText(null);
    setSidebarOpen(false);
  }, [bootstrap]);

  useEffect(() => {
    loadBootstrap(requestedErrorId)
      .then((loaded) => {
        setBootstrap(loaded);
        setRecordAttachmentCount(loaded.record_pending_attachment_count || 0);
        if (loaded.workspace) {
          setWorkspace(loaded.workspace);
          setMessages(workspaceMessages(loaded.workspace));
        }
      })
      .catch((error: Error) => setErrorText(error.message));
  }, [requestedErrorId]);

  useEffect(() => {
    sessionStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(draft));
  }, [draft]);

  const attachmentAdapter = useMemo<AttachmentAdapter>(
    () => ({
      accept: "image/png,image/jpeg,image/webp",
      async add({ file }) {
        if (!file.type.startsWith("image/"))
          throw new Error("这里只接受图片。");
        const id = `local-${crypto.randomUUID()}`;
        const url = URL.createObjectURL(file);
        fileByAttachment.current.set(id, file);
        fileByUrl.current.set(url, file);
        return {
          id,
          type: "image",
          name: file.name,
          contentType: file.type,
          file,
          url,
          status: { type: "requires-action", reason: "composer-send" },
        } as never;
      },
      async send(attachment) {
        const item = attachment as LocalAttachment;
        const file =
          item.id !== undefined
            ? fileByAttachment.current.get(String(item.id))
            : item.url
              ? fileByUrl.current.get(item.url)
              : undefined;
        if (item.id !== undefined && file)
          fileByAttachment.current.set(String(item.id), file);
        return {
          ...attachment,
          status: { type: "complete" },
          content: [{ type: "image", image: item.url! }],
        } as never;
      },
      async remove(attachment) {
        const item = attachment as LocalAttachment;
        if (item.id !== undefined)
          fileByAttachment.current.delete(String(item.id));
        if (item.url) {
          URL.revokeObjectURL(item.url);
          fileByUrl.current.delete(item.url);
        }
      },
    }),
    [],
  );

  const filesFromIncoming = useCallback((incoming: AppendMessage) => {
    const files: File[] = [];
    const seen = new Set<File>();
    const add = (file: File | undefined) => {
      if (file && !seen.has(file)) {
        seen.add(file);
        files.push(file);
      }
    };
    for (const attachment of (incoming.attachments ||
      []) as readonly LocalAttachment[]) {
      add(attachment.file);
      if (attachment.id !== undefined)
        add(fileByAttachment.current.get(String(attachment.id)));
      if (attachment.url) add(fileByUrl.current.get(attachment.url));
      const ref = (attachment as LocalAttachment & { ref?: string }).ref;
      if (ref) add(fileByAttachment.current.get(ref));
    }
    for (const url of imageUrls(incoming.content as UiContent))
      add(fileByUrl.current.get(url));
    return files;
  }, []);

  const append = useCallback(
    (items: UiMessage[]) => setMessages((current) => [...current, ...items]),
    [],
  );

  const onNew = useCallback(
    async (incoming: AppendMessage) => {
      if (!bootstrap) return;
      const text = textParts(incoming.content as UiContent);
      const files = filesFromIncoming(incoming);
      if (!text.trim() && files.length === 0) return;
      const visibleContent = parts(incoming.content as UiContent).filter(
        (part) => part.type !== "image",
      ) as UiContent;
      append([message("user", visibleContent, incoming.attachments)]);
      setErrorText(null);
      setIsRunning(true);
      try {
        if (!workspace) {
          const result = await prepareDraft(
            bootstrap,
            bootstrap.tokens.record_draft,
            draft,
            text,
            files,
          );
          setDraft(result.draft);
          setRecordAttachmentCount(result.pending_attachment_count || 0);
          setBootstrap((current) =>
            current
              ? {
                  ...current,
                  tokens: {
                    ...current.tokens,
                    record_draft: result.submit_token,
                  },
                }
              : current,
          );
          append([
            message("assistant", [
              { type: "text", text: draftText(result.draft) },
            ]),
          ]);
        } else if (workspace.composer) {
          const token =
            workspace.composer === "grill"
              ? bootstrap.tokens.grill_answer
              : bootstrap.tokens.teach_answer;
          const result = await answerConversation(
            bootstrap,
            token,
            workspace.composer,
            workspace.error.id,
            text,
            files,
          );
          setWorkspace(result.workspace);
          setMessages(workspaceMessages(result.workspace));
          setBootstrap((current) =>
            current
              ? {
                  ...current,
                  tokens: {
                    ...current.tokens,
                    [workspace.composer === "grill"
                      ? "grill_answer"
                      : "teach_answer"]: result.submit_token,
                  },
                }
              : current,
          );
        }
      } catch (error) {
        if (error instanceof ApiError && error.submitToken) {
          const tokenKey = !workspace
            ? "record_draft"
            : workspace.composer === "grill"
              ? "grill_answer"
              : "teach_answer";
          setBootstrap((current) => current ? {
            ...current,
            tokens: { ...current.tokens, [tokenKey]: error.submitToken! },
          } : current);
        }
        setErrorText(
          error instanceof Error
            ? error.message
            : "请求未完成，请保留当前输入后重试。",
        );
      } finally {
        setIsRunning(false);
      }
    },
    [append, bootstrap, draft, filesFromIncoming, workspace],
  );

  const runtime = useExternalStoreRuntime({
    messages,
    isRunning,
    convertMessage: (stored: UiMessage): ThreadMessageLike => stored,
    onNew,
    adapters: { attachments: attachmentAdapter },
  });

  const updateWorkspace = useCallback((data: WorkspaceData) => {
    setWorkspace(data);
    setMessages(workspaceMessages(data));
  }, []);

  const runNextStep = async () => {
    if (!bootstrap || !workspace?.next_step || isRunning) return;
    const step = workspace.next_step;
    let retryTokenKey: keyof Bootstrap["tokens"] | undefined;
    setIsRunning(true);
    setErrorText(null);
    try {
      if (step.code === "start_grill" || step.code === "resume_grill") {
        retryTokenKey = "grill_start";
        const result = (await startGrill(
          bootstrap,
          bootstrap.tokens.grill_start,
          workspace.error.id,
        )) as { submit_token: string };
        setBootstrap((current) =>
          current
            ? {
                ...current,
                tokens: {
                  ...current.tokens,
                  grill_answer: result.submit_token,
                },
              }
            : current,
        );
        const data = await loadWorkspace(workspace.error.id);
        updateWorkspace(data);
      } else if (step.code === "start_teach") {
        retryTokenKey = "teach_start";
        const result = await startTeach(
          bootstrap,
          bootstrap.tokens.teach_start,
          workspace.error.id,
        );
        setBootstrap((current) =>
          current
            ? {
                ...current,
                tokens: {
                  ...current.tokens,
                  teach_answer: result.submit_token,
                },
              }
            : current,
        );
        updateWorkspace(result.workspace);
      } else if (step.code === "finish_teach_and_drill") {
        retryTokenKey = "teach_finish";
        const result = await finishTeach(
          bootstrap,
          bootstrap.tokens.teach_finish,
          workspace.error.id,
        );
        navigate(result.next_url);
      } else if (step.code === "start_drill") {
        retryTokenKey = "drill_new";
        const result = await createDrill(bootstrap);
        navigate(result.next_url);
      }
    } catch (error) {
      if (retryTokenKey && error instanceof ApiError && error.submitToken) {
        setBootstrap((current) => current ? {
          ...current,
          tokens: { ...current.tokens, [retryTokenKey!]: error.submitToken! },
        } : current);
      }
      setErrorText(
        error instanceof Error
          ? error.message
          : "请求未完成，请保留当前输入后重试。",
      );
    } finally {
      setIsRunning(false);
    }
  };

  const clickGrill = async () => {
    if (!bootstrap || (!draft.question.trim() && recordAttachmentCount === 0) || isRunning) return;
    setIsRunning(true);
    setErrorText(null);
    let retryTokenKey: keyof Bootstrap["tokens"] = "record_finalize";
    let finalized: ErrorResponse | null = null;
    try {
      finalized = await finalizeRecord(
        bootstrap,
        bootstrap.tokens.record_finalize,
        draft,
        [],
      );
      // Finalize is durable. Clear the record draft and move to the saved
      // Error before any follow-up Grill request can fail.
      setDraft(EMPTY_DRAFT);
      setRecordAttachmentCount(0);
      sessionStorage.removeItem(DRAFT_STORAGE_KEY);
      window.history.replaceState({}, "", `/errors/${finalized.error.id}`);
      retryTokenKey = "grill_start";
      const result = (await startGrill(
        bootstrap,
        finalized.submit_token,
        finalized.error.id,
      )) as { submit_token: string };
      setBootstrap((current) =>
        current
          ? {
              ...current,
              tokens: { ...current.tokens, grill_answer: result.submit_token },
            }
          : current,
      );
      const loaded = await loadBootstrap(finalized.error.id);
      setBootstrap((current) =>
        current
          ? {
              ...current,
              ...loaded,
              tokens: { ...loaded.tokens, grill_answer: result.submit_token },
            }
          : loaded,
      );
      if (loaded.workspace) updateWorkspace(loaded.workspace);
    } catch (error) {
      const retrySubmitToken =
        error instanceof ApiError ? error.submitToken : undefined;
      if (error instanceof ApiError && error.submitToken) {
        setBootstrap((current) => current ? {
          ...current,
          tokens: { ...current.tokens, [retryTokenKey]: error.submitToken! },
        } : current);
      }
      if (finalized) {
        // The Error already exists even when starting Grill or refreshing its
        // workspace fails. Recover its public workspace when possible so a
        // retry starts Grill instead of submitting the record again.
        try {
          const loaded = await loadBootstrap(finalized.error.id);
          setBootstrap((current) => current ? {
            ...current,
            ...loaded,
            tokens: {
              ...loaded.tokens,
              ...(retrySubmitToken ? { grill_start: retrySubmitToken } : {}),
            },
          } : loaded);
          setRecordAttachmentCount(loaded.record_pending_attachment_count || 0);
          if (loaded.workspace) updateWorkspace(loaded.workspace);
        } catch {
          // The URL already identifies the durable Error; a later refresh can
          // recover its workspace if this bootstrap request also failed.
        }
      }
      setErrorText(
        error instanceof Error
          ? error.message
          : "还不能开始 Grill，请保留当前输入后重试。",
      );
    } finally {
      setIsRunning(false);
    }
  };

  const deleteCurrent = async () => {
    if (
      !bootstrap ||
      !workspace ||
      !window.confirm("删除后不可恢复。确定要删除这条 Error 吗？")
    )
      return;
    const token = bootstrap.tokens.delete_error;
    if (!token) return;
    try {
      await fetch(`/errors/${workspace.error.id}/delete`, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "X-CSRFToken": bootstrap.csrf,
          "X-Submission-Token": token,
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body: "confirm_delete=1",
      });
      navigate("/");
    } catch (error) {
      setErrorText(
        error instanceof Error ? error.message : "删除失败，请稍后重试。",
      );
    }
  };

  const title = workspace?.error.title || "What’s your error?";
  const recordLanding =
    !workspace && messages.length === 0 && !draft.question.trim() && recordAttachmentCount === 0;
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <div className="shell">
        <Sidebar
          history={bootstrap?.history || []}
          collapsed={sidebarCollapsed}
          open={sidebarOpen}
          onNew={resetRecord}
          onDrill={() => {
            if (!bootstrap) return;
            createDrill(bootstrap)
              .then((result) => navigate(result.next_url))
              .catch((error) => setErrorText(error instanceof Error ? error.message : "进入 Drill 失败。"));
          }}
          onToggle={() => setSidebarCollapsed((value) => !value)}
          onClose={() => setSidebarOpen(false)}
        />
        {sidebarCollapsed && (
          <button
            className="sidebar-reopen"
            type="button"
            onClick={() => setSidebarCollapsed(false)}
            aria-label="打开侧栏"
          >
            ☰
          </button>
        )}
        <main className="workspace">
          {recordLanding ? (
            <>
              <button
                className="menu-button landing-menu"
                onClick={() => setSidebarOpen((value) => !value)}
                aria-label="打开菜单"
              >
                ☰
              </button>
              <div className="record-landing">
                <h1>What’s your error?</h1>
                {errorText && (
                  <div className="error-banner" role="alert">
                    {errorText}
                  </div>
                )}
                <div className="record-landing-composer">
                  <Composer disabled={!bootstrap || isRunning} />
                </div>
              </div>
            </>
          ) : (
            <>
              <header className="workspace-header">
                <button
                  className="menu-button"
                  onClick={() => setSidebarOpen((value) => !value)}
                  aria-label="打开菜单"
                >
                  ☰
                </button>
                <div className="workspace-title">
                  {workspace ? (
                    <details className="title-context">
                      <summary aria-label="查看当前 Error 的上下文">
                        <span>{title}</span>
                        <span aria-hidden="true">⌄</span>
                      </summary>
                      <div className="header-popover context-popover">
                        <div className="popover-heading">Current Error</div>
                        <div className="context-block">
                          <span className="context-label">题目</span>
                          <div className="context-copy">
                            <StaticMarkdown text={workspace.error.question} />
                          </div>
                        </div>
                        {workspace.error.user_thoughts && (
                          <div className="context-block">
                            <span className="context-label">
                              当时的作答 / 思路
                            </span>
                            <div className="context-copy">
                              <StaticMarkdown
                                text={workspace.error.user_thoughts}
                              />
                            </div>
                          </div>
                        )}
                        {workspace.error.reference_answer && (
                          <div className="context-block">
                            <span className="context-label">参考答案</span>
                            <div className="context-copy">
                              <StaticMarkdown
                                text={workspace.error.reference_answer}
                              />
                            </div>
                          </div>
                        )}
                        <dl className="metadata-list">
                          <div>
                            <dt>来源</dt>
                            <dd>{workspace.error.origin}</dd>
                          </div>
                          <div>
                            <dt>记录时间</dt>
                            <dd>
                              {new Date(
                                workspace.error.created_at,
                              ).toLocaleString("zh-CN")}
                            </dd>
                          </div>
                          {workspace.error.source_error_id && (
                            <div>
                              <dt>关联 Error</dt>
                              <dd>
                                <a
                                  href={`/errors/${workspace.error.source_error_id}`}
                                  onClick={(event) => {
                                    event.preventDefault();
                                    navigate(`/errors/${workspace.error.source_error_id}`);
                                  }}
                                >
                                  查看来源 Error
                                </a>
                              </dd>
                            </div>
                          )}
                        </dl>
                      </div>
                    </details>
                  ) : (
                    <h1>{title}</h1>
                  )}
                </div>
                {workspace && (
                  <details className="header-menu management-menu">
                    <summary aria-label="管理当前 Error">…</summary>
                    <div className="header-popover management-popover">
                      <span className="menu-heading">Manage error</span>
                      <button
                        className="danger-action"
                        type="button"
                        onClick={deleteCurrent}
                      >
                        Delete error
                      </button>
                    </div>
                  </details>
                )}
                {isRunning && <span className="work-indicator">处理中</span>}
              </header>
              {errorText && (
                <div className="error-banner" role="alert">
                  {errorText}
                </div>
              )}
              <ThreadPrimitive.Root className="thread-root">
                <ThreadPrimitive.Viewport
                  className="thread-viewport"
                  turnAnchor="top"
                  autoScroll
                  scrollToBottomOnRunStart
                >
                  <ThreadPrimitive.Messages>
                    {({ message: current }) =>
                      current.role === "user" ? (
                        <UserMessage />
                      ) : (
                        <AssistantMessage />
                      )
                    }
                  </ThreadPrimitive.Messages>
                  <ThreadPrimitive.ViewportFooter className="thread-footer">
                    {workspace?.next_step && (
                      <div className="next-step">
                        <span>{workspace.next_step.description}</span>
                        <button onClick={runNextStep} disabled={isRunning}>
                          {workspace.next_step.label}
                        </button>
                      </div>
                    )}
                    {!workspace && messages.length === 0 && recordAttachmentCount > 0 && (
                      <p className="attachment-status">＋ 图片附件已保留</p>
                    )}
                    {!workspace && (draft.question.trim() || recordAttachmentCount > 0) && (
                      <div className="next-step">
                        <span>
                          先还原当时的思路，确认这次错误是怎么发生的。
                        </span>
                        <button onClick={clickGrill} disabled={isRunning}>
                          Grill
                        </button>
                      </div>
                    )}
                    <Composer disabled={!bootstrap || isRunning} />
                  </ThreadPrimitive.ViewportFooter>
                </ThreadPrimitive.Viewport>
              </ThreadPrimitive.Root>
            </>
          )}
        </main>
      </div>
    </AssistantRuntimeProvider>
  );
}

export function App() {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const update = () => setPath(window.location.pathname);
    window.addEventListener("popstate", update);
    return () => window.removeEventListener("popstate", update);
  }, []);
  if (path === "/config") return <ConfigPage key={path} />;
  const drill = path.match(/^\/drill\/([^/]+)$/);
  if (drill) return <DrillPage key={path} drillKey={decodeURIComponent(drill[1])} />;
  return <Workspace key={path} />;
}
