'use strict';
const working = document.querySelector('#working');
const errorBox = document.querySelector('#request-error');
const csrf = document.querySelector('meta[name="csrf-token"]').content;
let active = false;

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

function storageKey(form) {
  return form.dataset.draftKey ? `errgrind:draft:${form.dataset.draftKey}` : '';
}
function readDraft(form) {
  const key = storageKey(form);
  if (!key) return;
  try {
    const saved = JSON.parse(sessionStorage.getItem(key) || '{}');
    form.querySelectorAll('textarea[name]').forEach(field => {
      if (!field.value && typeof saved[field.name] === 'string') field.value = saved[field.name];
    });
  } catch (_) { /* Private browsing may disable sessionStorage. */ }
}
function saveDraft(form) {
  const key = storageKey(form);
  if (!key) return;
  const values = {};
  form.querySelectorAll('textarea[name]').forEach(field => { values[field.name] = field.value; });
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
    try {
      const {response, result} = await send(button.dataset.draftUrl, data);
      if (result.submit_token) button.dataset.submitToken = result.submit_token;
      if (!response.ok) { showError(result.error || '整理失败，请保留草稿。'); return; }
      const draft = result.draft || {};
      ['question', 'user_thoughts', 'reference_answer'].forEach(field => {
        const target = form.elements[field];
        if (target) target.value = draft[field] || '';
      });
      saveDraft(form);
      form.querySelector('[data-preview]').hidden = false;
      form.querySelector('[data-preview] textarea')?.focus();
      if (draft.origin === 'ocr') form.elements.ocr_used.value = '1';
    } catch (error) { showError(error.message || '整理失败，请保留草稿。'); }
    finally { state.end(); }
  });
});

document.querySelectorAll('[data-show-preview]').forEach(button => {
  button.addEventListener('click', () => {
    const preview = document.getElementById(button.getAttribute('aria-controls'));
    if (!preview) return;
    preview.hidden = false;
    preview.querySelector('textarea')?.focus();
  });
});

document.querySelectorAll('form[data-action]').forEach(form => {
  readDraft(form);
  form.querySelectorAll('textarea[name]').forEach(field => field.addEventListener('input', () => saveDraft(form)));
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (active) return;
    const data = new FormData(form);
    const state = begin(event.submitter?.textContent || '提交');
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

document.querySelectorAll('input[data-ocr-field]').forEach(input => {
  input.addEventListener('change', async () => {
    const file = input.files[0];
    if (!file || active) return;
    const data = new FormData();
    data.append('image', file);
    data.append('submit_token', input.dataset.token);
    const state = begin('识别图片');
    try {
      const {response, result} = await send(input.dataset.url, data);
      if (result.submit_token) input.dataset.token = result.submit_token;
      if (!response.ok) { showError(result.error || '图片识别失败，请保留当前草稿。'); return; }
      const target = document.getElementById(input.dataset.ocrField);
      // Read the latest editor value: typing during OCR must not be overwritten.
      target.value += (target.value ? '\n\n' : '') + result.text;
      saveDraft(input.form);
      input.form.elements.ocr_used.value = '1';
      target.focus();
    } catch (error) { showError(error.message || '图片识别失败，已有草稿仍在。'); }
    finally { input.value = ''; state.end(); }
  });
});

document.querySelectorAll('input[data-drill-ocr]').forEach(input => {
  input.addEventListener('change', async () => {
    const file = input.files[0];
    if (!file || active) return;
    const data = new FormData();
    data.append('image', file);
    data.append('csrf', csrf);
    data.append('submit_token', input.dataset.token);
    data.append('answer', document.getElementById(input.dataset.target).value);
    const state = begin('识别答案图片');
    try {
      const {response, result} = await send(input.dataset.url, data);
      if (result.submit_token) input.dataset.token = result.submit_token;
      if (!response.ok) { showError(result.error || '图片识别失败，请保留答案草稿。'); return; }
      const target = document.getElementById(input.dataset.target);
      target.value += (target.value ? '\n\n' : '') + result.text;
      saveDraft(input.form);
      target.focus();
    } catch (error) { showError(error.message || '图片识别失败，请保留答案草稿。'); }
    finally { input.value = ''; state.end(); }
  });
});

window.addEventListener('beforeunload', event => {
  if (active) { event.preventDefault(); event.returnValue = ''; }
});
// Browser back/forward cache may preserve an old disabled working state.
window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
