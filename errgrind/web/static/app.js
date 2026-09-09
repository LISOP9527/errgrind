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

function begin(label) {
  active = true;
  errorBox.hidden = true;
  working.hidden = false;
  const start = performance.now();
  let stage = '';
  const update = () => {
    working.textContent = `${label}${stage ? ' · ' + stage : ''} · 正在工作，已等待 ${((performance.now() - start) / 1000).toFixed(1)} 秒`;
  };
  update();
  const interval = setInterval(update, 100);
  const controls = [...document.querySelectorAll('button,input[type=file]')];
  const wasDisabled = controls.map(c => c.disabled);
  controls.forEach(c => { c.disabled = true; });
  working.scrollIntoView({block: 'nearest'});
  return {
    stage(value) { stage = value; },
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

document.querySelectorAll('form[data-action]').forEach(form => {
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (active) return;
    const data = new FormData(form);
    const state = begin(event.submitter?.textContent || '提交');
    let polling;
    if (form.dataset.stageUrl) {
      state.stage('spec · 提炼出题规格');
      polling = setInterval(async () => {
        try {
          const response = await fetch(form.dataset.stageUrl);
          const result = await response.json();
          if (result.stage === 'spec') state.stage('spec · 提炼出题规格');
          if (result.stage === 'draft') state.stage('draft · 生成题目');
        } catch (_) { /* The POST result remains authoritative. */ }
      }, 500);
    }
    try {
      const {response, result} = await send(form.action, data);
      if (result.submit_token) form.elements.submit_token.value = result.submit_token;
      if (!response.ok) { showError(result.error); return; }
      if (result.redirect) location.assign(result.redirect);
    } catch (error) {
      showError(error.message || '请求中断，请保留输入并查看记录。');
    } finally {
      clearInterval(polling);
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
      if (!response.ok) { showError(result.error); return; }
      const target = document.getElementById(input.dataset.ocrField);
      // Read the latest editor value: typing during OCR must not be overwritten.
      target.value += (target.value ? '\n\n' : '') + result.text;
      input.form.elements.ocr_used.value = '1';
      target.focus();
    } catch (error) { showError(error.message || '图片识别失败，已有草稿仍在。'); }
    finally { input.value = ''; state.end(); }
  });
});

window.addEventListener('beforeunload', event => {
  if (active) { event.preventDefault(); event.returnValue = ''; }
});
// Browser back/forward cache may preserve an old disabled working state.
window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
