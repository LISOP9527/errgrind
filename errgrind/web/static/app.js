'use strict';

const working = document.querySelector('#working');
const errorBox = document.querySelector('#request-error');
const csrfMeta = document.querySelector('meta[name="csrf-token"]');
const csrf = csrfMeta ? csrfMeta.content : '';
let active = false;

/* ==========================================================================
   Math Rendering via KaTeX
   ========================================================================== */
function renderMath() {
  document.querySelectorAll('[data-tex]').forEach(node => {
    if (!window.katex) return;
    try {
      katex.render(node.dataset.tex, node, {
        displayMode: node.classList.contains('math-display'),
        throwOnError: false,
        trust: false,
        maxExpand: 1000,
        maxSize: 20,
      });
    } catch (_) {
      /* Escaped original TeX remains readable. */
    }
  });
}
renderMath();

/* ==========================================================================
   Working State & Elapsed Time Tracker
   ========================================================================== */
function begin(label) {
  active = true;
  if (errorBox) errorBox.hidden = true;
  if (working) working.hidden = false;
  const start = performance.now();
  let stage = '';
  const update = () => {
    if (working) {
      working.textContent = `${label}${stage ? ' · ' + stage : ''} · 正在工作，已等待 ${((performance.now() - start) / 1000).toFixed(1)} 秒`;
    }
  };
  update();
  const interval = setInterval(update, 100);
  const controls = [...document.querySelectorAll('button, input[type=file]')];
  const wasDisabled = controls.map(c => c.disabled);
  controls.forEach(c => { c.disabled = true; });
  if (working) working.scrollIntoView({ block: 'nearest' });

  return {
    stage(value) { stage = value; },
    end() {
      clearInterval(interval);
      controls.forEach((c, i) => { c.disabled = wasDisabled[i]; });
      active = false;
      if (working) working.hidden = true;
    },
  };
}

/* ==========================================================================
   Error Notification Display
   ========================================================================== */
function showError(message) {
  if (!errorBox) return;
  errorBox.replaceChildren(document.createTextNode(message));
  const p = document.createElement('p');
  p.style.marginTop = '0.5rem';
  const link = document.createElement('a');
  link.href = location.href;
  link.textContent = '查看已保存记录 / 恢复操作（请先保留下方草稿）';
  p.append(link);
  errorBox.append(p);
  errorBox.hidden = false;
  errorBox.scrollIntoView({ block: 'nearest' });
}

/* ==========================================================================
   Fetch Communication
   ========================================================================== */
async function send(url, data) {
  const response = await fetch(url, {
    method: 'POST',
    body: data,
    headers: { 'Accept': 'application/json', 'X-CSRFToken': csrf },
  });
  let result;
  try {
    result = await response.json();
  } catch (_) {
    throw new Error('服务器响应中断。请保留草稿，查看记录确认是否已保存；不要连续重复提交。');
  }
  return { response, result };
}

/* ==========================================================================
   Form Interception & Execution
   ========================================================================== */
document.querySelectorAll('form[data-action]').forEach(form => {
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (active) return;
    const data = new FormData(form);
    const label = event.submitter?.textContent?.trim() || '提交';
    const state = begin(label);
    let polling;
    if (form.dataset.stageUrl) {
      state.stage('spec · 提炼出题规格');
      polling = setInterval(async () => {
        try {
          const response = await fetch(form.dataset.stageUrl);
          const result = await response.json();
          if (result.stage === 'spec') state.stage('spec · 提炼出题规格');
          if (result.stage === 'draft') state.stage('draft · 生成题目');
        } catch (_) {
          /* The POST result remains authoritative. */
        }
      }, 500);
    }
    try {
      const { response, result } = await send(form.action, data);
      if (result.submit_token && form.elements.submit_token) {
        form.elements.submit_token.value = result.submit_token;
      }
      if (!response.ok) {
        showError(result.error);
        return;
      }
      if (result.redirect) {
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
      clearInterval(polling);
      state.end();
    }
  });
});

/* ==========================================================================
   OCR Upload Handling (File input & Drag-and-drop)
   ========================================================================== */
document.querySelectorAll('input[data-ocr-field]').forEach(input => {
  const handleFile = async (file) => {
    if (!file || active) return;
    const data = new FormData();
    data.append('image', file);
    data.append('submit_token', input.dataset.token);
    const state = begin('识别图片');
    try {
      const { response, result } = await send(input.dataset.url, data);
      if (result.submit_token) input.dataset.token = result.submit_token;
      if (!response.ok) {
        showError(result.error);
        return;
      }
      const target = document.getElementById(input.dataset.ocrField);
      if (target) {
        // Read the latest editor value: typing during OCR must not be overwritten.
        target.value += (target.value ? '\n\n' : '') + result.text;
        if (input.form && input.form.elements.ocr_used) {
          input.form.elements.ocr_used.value = '1';
        }
        target.focus();
      }
    } catch (error) {
      showError(error.message || '图片识别失败，已有草稿仍在。');
    } finally {
      input.value = '';
      state.end();
    }
  };

  input.addEventListener('change', () => {
    if (input.files && input.files[0]) {
      handleFile(input.files[0]);
    }
  });

  // Attach Drag & Drop support to the enclosing upload zone if present
  const zone = input.closest('.upload-zone');
  if (zone) {
    zone.addEventListener('dragover', (e) => {
      e.preventDefault();
      zone.classList.add('dragover');
    });
    zone.addEventListener('dragleave', (e) => {
      e.preventDefault();
      zone.classList.remove('dragover');
    });
    zone.addEventListener('drop', (e) => {
      e.preventDefault();
      zone.classList.remove('dragover');
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) {
        handleFile(e.dataTransfer.files[0]);
      }
    });
  }
});

/* ==========================================================================
   Keyboard Shortcut: Ctrl+Enter (or Cmd+Enter) in textareas to submit form
   ========================================================================== */
document.querySelectorAll('textarea').forEach(textarea => {
  textarea.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
      const form = textarea.closest('form');
      if (form) {
        event.preventDefault();
        const submitBtn = form.querySelector('button[type=submit]');
        if (submitBtn && !submitBtn.disabled) {
          submitBtn.click();
        } else if (!form.disabled) {
          form.requestSubmit();
        }
      }
    }
  });
});

/* ==========================================================================
   Client-Side Filter Tabs for Errors List
   ========================================================================== */
const filterButtons = document.querySelectorAll('.filter-btn');
if (filterButtons.length > 0) {
  const cards = document.querySelectorAll('.error-card, .error-row');
  filterButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      filterButtons.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const targetStatus = btn.dataset.filter;
      cards.forEach(card => {
        if (!targetStatus || targetStatus === 'all' || card.dataset.status === targetStatus) {
          card.style.display = '';
        } else {
          card.style.display = 'none';
        }
      });
    });
  });
}

/* ==========================================================================
   Page Lifecycle Guards
   ========================================================================== */
window.addEventListener('beforeunload', event => {
  if (active) {
    event.preventDefault();
    event.returnValue = '';
  }
});

// Browser back/forward cache may preserve an old disabled working state.
window.addEventListener('pageshow', event => {
  if (event.persisted) location.reload();
});
