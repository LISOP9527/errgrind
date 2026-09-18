'use strict';
const working = document.querySelector('#working');
const errorBox = document.querySelector('#request-error');
const csrf = document.querySelector('meta[name="csrf-token"]').content;
let active = false;

const drawerToggle = document.querySelector('#drawer-toggle');
const sidebarMedia = window.matchMedia('(max-width: 760px)');
const sidebarStorageKey = 'errgrind:sidebar-collapsed';

function isMobileLayout() {
  return sidebarMedia.matches;
}

function readSidebarPreference() {
  try { return window.localStorage.getItem(sidebarStorageKey) === '1'; }
  catch (_) { return false; }
}

function writeSidebarPreference(collapsed) {
  try { window.localStorage.setItem(sidebarStorageKey, collapsed ? '1' : '0'); }
  catch (_) { /* Private browsing may disable localStorage. */ }
}

function updateSidebarControls() {
  const collapsed = document.body.classList.contains('sidebar-collapsed');
  const expanded = isMobileLayout() ? Boolean(drawerToggle?.checked) : !collapsed;
  document.querySelectorAll('[data-sidebar-toggle]').forEach(control => {
    control.setAttribute('aria-expanded', String(expanded));
  });
}

function syncSidebarLayout() {
  if (isMobileLayout()) {
    // A desktop preference is deliberately scoped to the desktop layout.
    document.body.classList.remove('sidebar-collapsed');
    if (drawerToggle) drawerToggle.checked = false;
  } else {
    document.body.classList.toggle('sidebar-collapsed', readSidebarPreference());
    if (drawerToggle) drawerToggle.checked = false;
  }
  updateSidebarControls();
}

document.querySelectorAll('[data-sidebar-toggle]').forEach(control => {
  control.addEventListener('click', () => {
    if (isMobileLayout()) {
      if (drawerToggle) drawerToggle.checked = !drawerToggle.checked;
      updateSidebarControls();
      return;
    }
    const collapsed = !document.body.classList.contains('sidebar-collapsed');
    document.body.classList.toggle('sidebar-collapsed', collapsed);
    writeSidebarPreference(collapsed);
    updateSidebarControls();
  });
});
drawerToggle?.addEventListener('change', updateSidebarControls);
if (sidebarMedia.addEventListener) sidebarMedia.addEventListener('change', syncSidebarLayout);
else sidebarMedia.addListener(syncSidebarLayout);
syncSidebarLayout();

function renderMath() {
  document.querySelectorAll('[data-tex]').forEach(node => {
    if (!window.katex) return;
    try {
      katex.render(node.dataset.tex, node, {
        displayMode: node.classList.contains('math-display'),
        throwOnError: false, trust: false, maxExpand: 1000, maxSize: 20,
      });
    } catch (_) { /* Escaped original TeX remains readable. */ }
  });
}
renderMath();

function landOnCurrentWork() {
  const current = document.querySelector('[data-current-work]');
  const navigation = window.performance?.getEntriesByType('navigation')?.[0];
  // A fragment (including the explicit post-submit #composer) and browser
  // history navigation remain authoritative; only a plain Error open gets
  // the chat-workspace landing position.
  if (!current || window.location.hash || navigation?.type === 'back_forward' || window.scrollY > 0) return;
  window.requestAnimationFrame(() => {
    window.requestAnimationFrame(() => {
      // The sticky composer is already visible at the viewport bottom on a
      // fresh load, so scrollIntoView() considers it satisfied.  The document
      // bottom is the actual latest-work anchor behind that sticky surface.
      const bottom = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
      window.scrollTo({top: bottom, behavior: 'auto'});
    });
  });
}
landOnCurrentWork();

function storageKey(form) {
  return form.dataset.draftKey ? `errgrind:draft:${form.dataset.draftKey}` : '';
}
const recordDraftFields = ['question', 'user_thoughts', 'reference_answer'];

function localRecordDraftStatus(form) {
  const question = form.elements.question?.value?.trim() || '';
  return question
    ? {ready: true, status: 'ready', message: '草稿已具备题目，可以继续调整或确认保存。'}
    : {ready: false, status: 'incomplete', message: '还缺少题目；请继续补充题目，参考答案和当时的思路可以留空。'};
}

function renderRecordDraftStatus(form, result = {}) {
  const statusNode = form.querySelector('[data-draft-status]');
  if (!statusNode) return;
  const fallback = localRecordDraftStatus(form);
  const ready = typeof result.ready === 'boolean' ? result.ready : fallback.ready;
  const status = result.status || (ready ? 'ready' : 'incomplete');
  statusNode.textContent = result.message || fallback.message;
  statusNode.dataset.status = status;
  statusNode.hidden = false;
  const confirm = form.querySelector('[data-draft-confirm]');
  if (confirm) confirm.disabled = !ready;
}

function readDraft(form) {
  const key = storageKey(form);
  if (!key) return {};
  try {
    const saved = JSON.parse(sessionStorage.getItem(key) || '{}');
    form.querySelectorAll('textarea[name]').forEach(field => {
      if (!field.value && typeof saved[field.name] === 'string') field.value = saved[field.name];
    });
    return saved;
  } catch (_) { /* Private browsing may disable sessionStorage. */ }
  return {};
}
function saveDraft(form) {
  const key = storageKey(form);
  if (!key) return;
  const values = {};
  form.querySelectorAll('textarea[name]').forEach(field => { values[field.name] = field.value; });
  if (form.classList.contains('record-flow')) {
    values.__preview = !form.querySelector('[data-preview]')?.hidden;
  }
  try { sessionStorage.setItem(key, JSON.stringify(values)); } catch (_) { /* Keep the live form usable. */ }
}
function clearDraft(form) {
  const key = storageKey(form);
  if (!key) return;
  try { sessionStorage.removeItem(key); } catch (_) { /* Nothing to do. */ }
}

function begin(label) {
  active = true;
  errorBox.hidden = true;
  working.hidden = false;
  const start = performance.now();
  const update = () => { working.textContent = `${label} · 正在工作，已等待 ${((performance.now() - start) / 1000).toFixed(1)} 秒`; };
  update();
  const interval = setInterval(update, 100);
  const controls = [...document.querySelectorAll('button,input[type=file]')];
  const wasDisabled = controls.map(c => c.disabled);
  controls.forEach(c => { c.disabled = true; });
  working.scrollIntoView({block: 'nearest'});
  return {
    end() {
      clearInterval(interval);
      controls.forEach((c, i) => { c.disabled = wasDisabled[i]; });
      active = false;
      working.hidden = true;
    },
  };
}
function showError(message) {
  errorBox.replaceChildren(document.createTextNode(message));
  const p = document.createElement('p');
  const link = document.createElement('a');
  link.href = location.href;
  link.textContent = '查看已保存记录 / 恢复操作（请先保留下方草稿）';
  p.append(link); errorBox.append(p);
  errorBox.hidden = false;
  errorBox.scrollIntoView({block: 'nearest'});
}
async function send(url, data) {
  const response = await fetch(url, {
    method: 'POST', body: data,
    headers: {'Accept': 'application/json', 'X-CSRFToken': csrf},
  });
  let result;
  try { result = await response.json(); }
  catch (_) { throw new Error('服务器响应中断。请保留草稿，查看记录确认是否已保存；不要连续重复提交。'); }
  return {response, result};
}

document.querySelectorAll('[data-draft-submit]').forEach(button => {
  button.addEventListener('click', async () => {
    if (active) return;
    const form = button.closest('form');
    saveDraft(form);
    const data = new FormData();
    data.append('csrf', csrf);
    data.append('submit_token', button.dataset.submitToken);
    data.append('raw_input', form.elements.raw_input.value);
    [...form.querySelector('[data-draft-images]').files].forEach(file => data.append('images', file));
    const state = begin(button.textContent);
    let updated = false;
    let draftResult = {};
    try {
      const {response, result} = await send(button.dataset.draftUrl, data);
      if (result.submit_token) button.dataset.submitToken = result.submit_token;
      if (!response.ok) { showError(result.error || '整理失败，请保留草稿。'); return; }
      draftResult = result;
      const draft = result.draft || {};
      recordDraftFields.forEach(field => {
        const target = form.elements[field];
        if (target) target.value = draft[field] || '';
      });
      // The uploaded files remain selected as the current images so the same
      // confirmed artifacts can reach /record; only the raw adjustment turn
      // is consumed by this successful update.
      form.elements.raw_input.value = '';
      form.querySelector('[data-preview]').hidden = false;
      updated = true;
      saveDraft(form);
      form.elements.raw_input.focus();
    } catch (error) { showError(error.message || '整理失败，请保留草稿。'); }
    finally {
      state.end();
      if (updated) renderRecordDraftStatus(form, draftResult);
    }
  });
});

document.querySelectorAll('form[data-action]').forEach(form => {
  const saved = readDraft(form);
  if (form.classList.contains('record-flow') &&
      (saved.__preview === true || recordDraftFields.some(field => form.elements[field]?.value))) {
    form.querySelector('[data-preview]').hidden = false;
    renderRecordDraftStatus(form);
  }
  form.querySelectorAll('textarea[name]').forEach(field => field.addEventListener('input', () => {
    saveDraft(form);
    if (form.classList.contains('record-flow') && field.name === 'question' &&
        !form.querySelector('[data-preview]')?.hidden) {
      renderRecordDraftStatus(form);
    }
  }));
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (active) return;
    const data = new FormData(form);
    const state = begin(
      event.submitter?.dataset.workingLabel || form.dataset.workingLabel || event.submitter?.textContent || '提交'
    );
    try {
      const {response, result} = await send(form.action, data);
      if (result.submit_token) form.elements.submit_token.value = result.submit_token;
      if (!response.ok) { showError(result.error || '请求未完成，请保留当前草稿。'); return; }
      if (result.redirect) {
        clearDraft(form);
        const target = new URL(result.redirect, location.href);
        // An anchor-only navigation does not fetch the updated conversation.
        if (target.pathname === location.pathname && target.search === location.search) {
          history.replaceState(null, '', target.href);
          location.reload();
        } else {
          location.assign(target.href);
        }
      }
    } catch (error) {
      showError(error.message || '请求中断，请保留输入并查看记录。');
    } finally {
      state.end();
    }
  });
});

function initSettingsForm() {
  const form = document.querySelector('#config-form');
  if (!form) return;

  const providerSelect = form.querySelector('#config-provider') || form.querySelector('[name="provider"]');
  const modelInput = form.querySelector('#config-model') || form.querySelector('[name="model"]');
  const effortRow = form.querySelector('[data-config-row="reasoning_effort"]');
  const effortSelect = form.querySelector('#config-reasoning-effort') || form.querySelector('[name="reasoning_effort"]');
  const apiKeyRow = form.querySelector('[data-config-row="api_key"]');
  const apiKeyInput = form.querySelector('#config-api-key') || form.querySelector('[name="api_key"]');
  const clearKeyRow = form.querySelector('[data-config-row="clear_api_key"]');
  const clearKeyCheckbox = form.querySelector('#config-clear-api-key') || form.querySelector('[name="clear_api_key"]');
  const apiKeyStatus = form.querySelector('[data-config-key-status]');
  const apiKeyHint = form.querySelector('[data-config-hint="api_key"]');
  const codexHint = form.querySelector('[data-config-hint="codex"]');
  const baseUrlRow = form.querySelector('[data-config-row="base_url"]');
  const baseUrlInput = form.querySelector('#config-base-url') || form.querySelector('[name="base_url"]');
  const apiKeyInputWrap = form.querySelector('.api-key-input-wrap');

  if (!providerSelect) return;

  const initialProvider = providerSelect.value;

  function syncProvider(isUserChange) {
    const provider = providerSelect.value;
    const selectedOption = providerSelect.selectedOptions?.[0] || providerSelect.querySelector(`option[value="${provider}"]`);
    const defaultModel = selectedOption?.dataset.defaultModel || '';

    if (isUserChange && modelInput && defaultModel) {
      modelInput.value = defaultModel;
    }
    if (modelInput && defaultModel) {
      modelInput.placeholder = defaultModel;
    }

    const isCodex = provider === 'codex';
    if (effortRow) effortRow.hidden = !isCodex;
    if (effortSelect) effortSelect.disabled = !isCodex;

    const isOpenCode = provider === 'opencode';
    if (baseUrlRow) baseUrlRow.hidden = !isOpenCode;
    if (baseUrlInput) baseUrlInput.disabled = !isOpenCode;

    if (isUserChange && isOpenCode && baseUrlInput) {
      const defaultBaseUrl = selectedOption?.dataset.defaultBaseUrl || selectedOption?.dataset.defaultUrl || baseUrlInput.dataset.defaultBaseUrl || baseUrlInput.placeholder || '';
      if (!baseUrlInput.value.trim() && defaultBaseUrl) {
        baseUrlInput.value = defaultBaseUrl;
      }
    }

    if (isCodex) {
      if (apiKeyInputWrap) apiKeyInputWrap.hidden = true;
      if (apiKeyInput) {
        apiKeyInput.disabled = true;
        apiKeyInput.value = '';
      }
      if (clearKeyRow) clearKeyRow.hidden = true;
      if (clearKeyCheckbox) {
        clearKeyCheckbox.disabled = true;
        clearKeyCheckbox.checked = false;
      }
      if (apiKeyStatus) apiKeyStatus.hidden = true;
      if (apiKeyHint) apiKeyHint.hidden = true;
      if (codexHint) codexHint.hidden = false;
    } else {
      if (apiKeyInputWrap) apiKeyInputWrap.hidden = false;
      if (apiKeyInput) apiKeyInput.disabled = false;
      if (codexHint) codexHint.hidden = true;
      if (apiKeyHint) apiKeyHint.hidden = false;

      const isSameAsInitial = provider === initialProvider;
      if (apiKeyStatus) apiKeyStatus.hidden = !isSameAsInitial;
      if (clearKeyRow) clearKeyRow.hidden = !isSameAsInitial;
      if (clearKeyCheckbox) {
        clearKeyCheckbox.disabled = !isSameAsInitial;
        if (!isSameAsInitial) clearKeyCheckbox.checked = false;
      }
      if (apiKeyInput) {
        apiKeyInput.placeholder = isSameAsInitial
          ? (apiKeyStatus?.querySelector('.is-set') ? '已配置 API Key（留空保持不变）' : '输入 API Key')
          : `输入 ${selectedOption?.textContent?.trim() || provider} API Key`;
      }
    }
  }

  providerSelect.addEventListener('change', () => syncProvider(true));
  syncProvider(false);

  form.addEventListener('submit', event => {
    if (typeof form.checkValidity === 'function' && !form.checkValidity()) {
      return;
    }
    if (form.dataset.submitting === 'true') {
      event.preventDefault();
      return;
    }
    form.dataset.submitting = 'true';
    const submitBtn = form.querySelector('button[type="submit"]');
    if (submitBtn) {
      setTimeout(() => {
        submitBtn.disabled = true;
      }, 0);
    }
  });
}
initSettingsForm();

document.querySelectorAll('[data-close-details]').forEach(button => {
  button.addEventListener('click', () => {
    button.closest('details')?.removeAttribute('open');
  });
});

window.addEventListener('beforeunload', event => {
  if (active) { event.preventDefault(); event.returnValue = ''; }
});
// Browser back/forward cache may preserve an old disabled working state.
window.addEventListener('pageshow', event => {
  if (event.persisted) location.reload();
  const form = document.querySelector('#config-form');
  if (form) {
    form.dataset.submitting = 'false';
    const submitBtn = form.querySelector('button[type="submit"]');
    if (submitBtn) submitBtn.disabled = false;
  }
});
