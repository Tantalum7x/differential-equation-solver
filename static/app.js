const form = document.getElementById('solve-form');
const APP_CONFIG = window.APP_CONFIG || {};
const ACCESS_TOKEN_KEY = 'diff-eq-solver-access-token-v1';
const accessTokenPanel = document.getElementById('access-token-panel');

// ------------------------------------------------------------------
// 预览：方程 / 条件失焦时把每行渲染成 LaTeX，错误明示
// ------------------------------------------------------------------
function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ------------------------------------------------------------------
// 复制工具
// ------------------------------------------------------------------
function flash(btn, msg, ok = true) {
  const old = btn.dataset.origLabel || btn.textContent;
  btn.dataset.origLabel = old;
  btn.textContent = msg;
  btn.classList.add(ok ? 'flash-ok' : 'flash-err');
  setTimeout(() => {
    btn.textContent = old;
    btn.classList.remove('flash-ok', 'flash-err');
  }, 1400);
}

async function copyText(text, btn) {
  if (!text) { flash(btn, '内容为空', false); return; }
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      // Fallback for non-secure context
      const ta = document.createElement('textarea');
      ta.value = text; ta.style.position = 'fixed'; ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }
    flash(btn, '已复制 ✓');
  } catch (e) {
    flash(btn, '失败: ' + e.message, false);
  }
}

// 全局保存当前结果图像的 Blob，避免反复 atob，也方便 Safari 的剪贴板调用
let currentImageBlob = null;
let currentImageUrl = null;

function base64ToBlob(b64, mime = 'image/png') {
  // 兜底：去掉任何空白；Safari 的 atob 不容忍空白字符
  const clean = String(b64).replace(/\s+/g, '');
  const bin = atob(clean);
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  return new Blob([arr], { type: mime });
}

function setResultImage(b64) {
  // 释放上一次的对象 URL，避免内存泄漏
  if (currentImageUrl) {
    URL.revokeObjectURL(currentImageUrl);
    currentImageUrl = null;
  }
  currentImageBlob = null;
  if (!b64) { plotImg.removeAttribute('src'); return; }
  try {
    currentImageBlob = base64ToBlob(b64, 'image/png');
    currentImageUrl = URL.createObjectURL(currentImageBlob);
    plotImg.src = currentImageUrl;
  } catch (e) {
    plotImg.removeAttribute('src');
    console.error('图像解码失败：', e);
  }
}

async function copyCurrentImage(btn) {
  if (!currentImageBlob) { flash(btn, '无图像', false); return; }
  if (!navigator.clipboard || !window.ClipboardItem) {
    flash(btn, '浏览器不支持图像剪贴板', false);
    return;
  }
  try {
    // Safari 要求 ClipboardItem 在用户手势同步栈里构造；不能在它前面 await
    const item = new ClipboardItem({ 'image/png': currentImageBlob });
    await navigator.clipboard.write([item]);
    flash(btn, '已复制 ✓');
  } catch (e) {
    flash(btn, '失败: ' + e.message, false);
  }
}

const previewCache = new Map();   // key=textareaName, value=last text we previewed
let accessTokenPreviewRefresh = Promise.resolve();

function getAccessToken() {
  return form.access_token ? form.access_token.value.trim() : '';
}

function persistAccessToken() {
  if (!form.access_token) return;
  const token = getAccessToken();
  if (token) localStorage.setItem(ACCESS_TOKEN_KEY, token);
  else localStorage.removeItem(ACCESS_TOKEN_KEY);
}

function authHeaders() {
  const headers = { 'Content-Type': 'application/json' };
  const token = getAccessToken();
  if (token) headers['X-Access-Token'] = token;
  return headers;
}

async function readJsonResponse(response) {
  const bodyText = await response.text();
  if (!bodyText) return {};
  try {
    return JSON.parse(bodyText);
  } catch {
    if (!response.ok) return { ok: false, error: `请求失败：HTTP ${response.status}` };
    throw new Error('服务器返回了无法解析的响应');
  }
}

function initAccessTokenPanel() {
  if (!accessTokenPanel || !form.access_token) return;
  if (APP_CONFIG.accessTokenRequired) {
    accessTokenPanel.hidden = false;
    form.access_token.value = localStorage.getItem(ACCESS_TOKEN_KEY) || '';
    form.access_token.addEventListener('input', () => {
      persistAccessToken();
      previewCache.clear();
    });
    form.access_token.addEventListener('blur', () => {
      persistAccessToken();
      previewCache.clear();
      accessTokenPreviewRefresh = accessTokenPreviewRefresh
        .catch(() => {})
        .then(() => refreshAllPreviews());
    });
  } else {
    accessTokenPanel.hidden = true;
  }
}

async function runPreview(textarea) {
  const previewId = textarea.dataset.preview;
  const box = document.getElementById(previewId);
  if (!box) return;

  const text = textarea.value.trim();
  if (!text) {
    box.hidden = true;
    box.innerHTML = '';
    previewCache.delete(textarea.name);
    return;
  }

  const fmt = form.querySelector('input[name="fmt"]:checked').value;
  const functions = form.functions.value;
  const variables = form.variables.value;
  const cacheKey = `${fmt}${functions}${variables}${getAccessToken()}${text}`;
  if (previewCache.get(textarea.name) === cacheKey) return;
  previewCache.set(textarea.name, cacheKey);

  box.hidden = false;
  box.classList.add('loading');
  box.innerHTML = '渲染中…';

  let data;
  try {
    const r = await fetch('/preview', {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ text, fmt, functions, variables }),
    });
    data = await readJsonResponse(r);
    if (!r.ok) throw new Error(data.error || `请求失败：HTTP ${r.status}`);
  } catch (err) {
    previewCache.delete(textarea.name);
    box.classList.remove('loading');
    const message = err && err.message ? err.message : String(err);
    box.innerHTML = `<div class="preview-line err"><span class="icon">✗</span><div class="body err-text">${escapeHtml(message)}</div></div>`;
    return;
  }

  const lines = data.lines || [];
  const html = ['<div class="preview-title">预览（每行单独识别）</div>'];
  lines.forEach((ln) => {
    const warnHtml = (ln.warnings || []).map((w) =>
      `<div class="warn">⚠️ ${escapeHtml(w)}</div>`
    ).join('');
    if (ln.ok) {
      html.push(`
        <div class="preview-line ok"
             data-latex="${escapeHtml(ln.latex || '')}"
             data-wolfram="${escapeHtml(ln.wolfram || '')}">
          <span class="icon">✓</span>
          <div class="body">
            <div class="rendered">\\( ${escapeHtml(ln.latex || '')} \\)</div>
            <div class="src">→ <code>${escapeHtml(ln.wolfram || '')}</code></div>
            <div class="copy-row">
              <button type="button" class="copy-btn" data-copy="latex">复制 LaTeX</button>
              <button type="button" class="copy-btn" data-copy="wolfram">复制 Wolfram</button>
            </div>
            ${warnHtml}
          </div>
        </div>`);
    } else {
      html.push(`
        <div class="preview-line err">
          <span class="icon">✗</span>
          <div class="body">
            <div class="src">${escapeHtml(ln.input || '')}</div>
            <div class="err-text">${escapeHtml(ln.error || '解析失败')}</div>
            ${warnHtml}
          </div>
        </div>`);
    }
  });
  box.classList.remove('loading');
  box.innerHTML = html.join('');
  // 委托：预览行内复制按钮
  box.querySelectorAll('.preview-line.ok .copy-btn[data-copy]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const line = btn.closest('.preview-line');
      const text = btn.dataset.copy === 'latex' ? line.dataset.latex : line.dataset.wolfram;
      copyText(text, btn);
    });
  });
  if (window.MathJax && window.MathJax.typesetPromise) {
    try { await window.MathJax.typesetPromise([box]); } catch {}
  }
}

document.querySelectorAll('textarea[data-preview]').forEach((ta) => {
  ta.addEventListener('input', () => autoResizeTextarea(ta));
  ta.addEventListener('blur', () => runPreview(ta));
});

// 结果区 3 个复制按钮
document.getElementById('copy-latex').addEventListener('click', (e) => {
  copyText(latexSource.textContent, e.currentTarget);
});
document.getElementById('copy-raw').addEventListener('click', (e) => {
  copyText(rawOut.dataset.fullRaw || rawOut.textContent, e.currentTarget);
});
document.getElementById('copy-image').addEventListener('click', (e) => {
  copyCurrentImage(e.currentTarget);
});

// 切换格式后清空缓存，下次失焦会重新预览
form.querySelectorAll('input[name="fmt"]').forEach((r) => {
  r.addEventListener('change', () => {
    previewCache.clear();
    document.querySelectorAll('textarea[data-preview]').forEach((ta) => {
      if (ta.value.trim()) runPreview(ta);
    });
  });
});

const statusEl = document.getElementById('status');
const resultEl = document.getElementById('result');
const recognizedEl = document.getElementById('recognized');
const warningsEl = document.getElementById('warnings');
const warningsListEl = document.getElementById('warnings-list');
const errorEl = document.getElementById('error');
const latexBlock = document.getElementById('latex-block');
const latexRender = document.getElementById('latex-render');
const latexSource = document.getElementById('latex-source');
const rawBlock = document.getElementById('raw-block');
const rawOut = document.getElementById('raw-out');
const toggleRawBtn = document.getElementById('toggle-raw');
const imageBlock = document.getElementById('image-block');
const plotImg = document.getElementById('plot-img');
const button = form.querySelector('button[type=submit]');
const historyListEl = document.getElementById('history-list');
const historyEmptyEl = document.getElementById('history-empty');
const clearHistoryBtn = document.getElementById('clear-history');
const formWarningEl = document.getElementById('form-warning');
const parameterPanelEl = document.getElementById('parameter-panel');
const parameterListEl = document.getElementById('parameter-list');
const HISTORY_KEY = 'diff-eq-solver-history-v1';
const HISTORY_LIMIT = 16;
const RAW_PREVIEW_LIMIT = 1400;
const PARAM_MIN = -10;
const PARAM_MAX = 10;
const PARAM_STEP = 0.1;
const WOLFRAM_RESERVED = new Set([
  'D', 'Derivative', 'Sin', 'Cos', 'Tan', 'Cot', 'Sec', 'Csc', 'ArcSin', 'ArcCos', 'ArcTan',
  'Sinh', 'Cosh', 'Tanh', 'Exp', 'Log', 'Sqrt', 'Abs', 'Power', 'Plus', 'Times', 'Equal',
  'Pi', 'E', 'I', 'True', 'False', 'Infinity', 'NDSolve', 'DSolve', 'Plot', 'Plot3D',
  'Evaluate', 'First', 'Rule', 'List', 'Set', 'SetDelayed',
]);
let parameterValues = {};

function getRadio(name) {
  return form.querySelector(`input[name="${name}"]:checked`).value;
}

function setRadio(name, value) {
  const input = Array.from(form.querySelectorAll(`input[name="${name}"]`))
    .find((el) => el.value === value);
  if (input) input.checked = true;
}

function getFormData() {
  return {
    fmt: getRadio('fmt'),
    kind: getRadio('kind'),
    mode: getRadio('mode'),
    equations: form.equations.value,
    functions: form.functions.value,
    variables: form.variables.value,
    conditions: form.conditions.value,
    plot_range: form.plot_range.value,
    plot: form.plot.checked,
  };
}

function getParameterValues() {
  const out = {};
  Object.entries(parameterValues).forEach(([name, value]) => {
    const n = Number(value);
    if (Number.isFinite(n)) out[name] = n;
  });
  return out;
}

function autoResizeTextarea(textarea) {
  textarea.style.height = 'auto';
  textarea.style.height = `${textarea.scrollHeight}px`;
}

function autoResizeTextareas() {
  document.querySelectorAll('textarea').forEach(autoResizeTextarea);
}

function updateKindPlaceholders(kind) {
  if (kind === 'pde') {
    form.equations.placeholder = 'D[u[x,t],t] == D[u[x,t],{x,2}]';
    form.functions.placeholder = 'u';
    form.variables.placeholder = 'x, t';
    form.conditions.placeholder = 'u[x,0] == Sin[x]\nu[0,t] == 0\nu[Pi,t] == 0';
    form.plot_range.placeholder = '{x, 0, Pi}, {t, 0, 1}';
  } else if (kind === 'ode_system') {
    form.equations.placeholder = "x'[t] == y[t]\ny'[t] == -x[t]";
    form.functions.placeholder = 'x, y';
    form.variables.placeholder = 't';
    form.conditions.placeholder = 'x[0] == 1\ny[0] == 0';
    form.plot_range.placeholder = '{t, 0, 2 Pi}';
  } else {
    form.equations.placeholder = "y''[x] + y[x] == 0";
    form.functions.placeholder = 'y';
    form.variables.placeholder = 'x';
    form.conditions.placeholder = "y[0] == 1\ny'[0] == 0";
    form.plot_range.placeholder = '';
  }
}

function applyKindDefaults() {
  updateKindPlaceholders(getRadio('kind'));
}

function splitNonEmptyLines(text) {
  return (text || '').split('\n').map((line) => line.trim()).filter(Boolean);
}

function splitCsv(text) {
  return (text || '').split(',').map((part) => part.trim()).filter(Boolean);
}

function stripStringsAndEscapes(text) {
  return (text || '')
    .replace(/"[^"]*"/g, ' ')
    .replace(/\\[a-zA-Z]+/g, ' ');
}

function detectParameters(data) {
  if (data.mode !== 'numeric') return [];
  const functions = new Set(splitCsv(data.functions));
  const variables = new Set(splitCsv(data.variables));
  if (!variables.size) return [];
  const text = stripStringsAndEscapes(`${data.equations}\n${data.conditions}\n${data.plot_range}`);
  const params = new Set();
  const tokenRe = /\b[A-Za-z][A-Za-z0-9_]*\b/g;
  let match;
  while ((match = tokenRe.exec(text))) {
    const name = match[0];
    const before = text[match.index - 1] || '';
    const after = text[tokenRe.lastIndex] || '';
    if (before === '\\') continue;
    if (after === '[' || after === "'") continue;
    if (functions.has(name) || variables.has(name) || WOLFRAM_RESERVED.has(name)) continue;
    params.add(name);
  }
  return Array.from(params).sort();
}

function renderParameterControls() {
  const params = detectParameters(getFormData());
  params.forEach((name) => {
    if (parameterValues[name] === undefined) parameterValues[name] = 1;
  });
  Object.keys(parameterValues).forEach((name) => {
    if (!params.includes(name)) delete parameterValues[name];
  });

  if (!params.length) {
    parameterPanelEl.hidden = true;
    parameterListEl.innerHTML = '';
    return;
  }

  parameterPanelEl.hidden = false;
  parameterListEl.innerHTML = params.map((name) => {
    const value = Number(parameterValues[name]);
    return `
      <div class="parameter-row" data-param="${escapeHtml(name)}">
        <label class="parameter-name">${escapeHtml(name)}</label>
        <input class="parameter-slider" type="range" min="${PARAM_MIN}" max="${PARAM_MAX}" step="${PARAM_STEP}" value="${value}" />
        <input class="parameter-value" type="number" min="${PARAM_MIN}" max="${PARAM_MAX}" step="${PARAM_STEP}" value="${value}" />
      </div>`;
  }).join('');
}

function replaceSymbol(text, name, value) {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return (text || '').replace(
    new RegExp(`(^|[^A-Za-z0-9_])${escaped}(?![A-Za-z0-9_])`, 'g'),
    (_, prefix) => `${prefix}${value}`,
  );
}

function applyParameterValues(data, params) {
  const next = { ...data };
  Object.entries(params).forEach(([name, value]) => {
    next.equations = replaceSymbol(next.equations, name, value);
    next.conditions = replaceSymbol(next.conditions, name, value);
    next.plot_range = replaceSymbol(next.plot_range, name, value);
  });
  return next;
}

function validateEquationFunctionCount(data) {
  const equations = splitNonEmptyLines(data.equations);
  const functions = splitCsv(data.functions);
  const warnings = [];

  if (!equations.length) {
    return warnings;
  }

  if (data.kind === 'ode_system' && equations.length === 1) {
    warnings.push('已选择 ODE 组，但方程只有 1 行。ODE 组通常需要每个未知函数至少对应一条方程。');
  }

  if (functions.length > 1 && equations.length === 1) {
    warnings.push(`未知函数有 ${functions.length} 个（${functions.join(', ')}），但方程只有 1 行。请补充方程，或把未知函数改成 1 个。`);
  }

  if (functions.length > 0 && equations.length > 0 && functions.length !== equations.length) {
    warnings.push(`当前方程数是 ${equations.length}，未知函数数是 ${functions.length}。方程组通常需要二者数量一致。`);
  }

  return Array.from(new Set(warnings));
}

function showFormWarnings(warnings) {
  if (!warnings.length) {
    formWarningEl.hidden = true;
    formWarningEl.innerHTML = '';
    return;
  }
  formWarningEl.innerHTML = `<b>提交前检查：</b><ul>${warnings
    .map((w) => `<li>${escapeHtml(w)}</li>`).join('')}</ul>`;
  formWarningEl.hidden = false;
}

function updateFormWarnings() {
  showFormWarnings(validateEquationFunctionCount(getFormData()));
}

function fillFormData(data) {
  setRadio('fmt', data.fmt || 'wolfram');
  setRadio('kind', data.kind || 'ode');
  setRadio('mode', data.mode || 'analytic');
  updateKindPlaceholders(data.kind || 'ode');
  form.equations.value = data.equations || '';
  form.functions.value = data.functions || '';
  form.variables.value = data.variables || '';
  form.conditions.value = data.conditions || '';
  form.plot_range.value = data.plot_range || '';
  form.plot.checked = Boolean(data.plot);
  previewCache.clear();
  document.querySelectorAll('.preview-box').forEach((box) => {
    box.hidden = true;
    box.innerHTML = '';
  });
  parameterValues = { ...(data.parameter_values || {}) };
  renderParameterControls();
  autoResizeTextareas();
  updateFormWarnings();
}

async function refreshAllPreviews() {
  const boxes = Array.from(document.querySelectorAll('textarea[data-preview]'));
  for (const ta of boxes) {
    if (ta.value.trim()) {
      await runPreview(ta);
    } else {
      const box = document.getElementById(ta.dataset.preview);
      if (box) {
        box.hidden = true;
        box.innerHTML = '';
      }
    }
  }
}

const EXAMPLES = {
  'wolfram-ode': {
    fmt: 'wolfram',
    kind: 'ode',
    mode: 'analytic',
    equations: "y''[x] + y[x] == 0",
    functions: 'y',
    variables: 'x',
    conditions: '',
    plot_range: '',
    plot: false,
  },
  'wolfram-ivp': {
    fmt: 'wolfram',
    kind: 'ode',
    mode: 'analytic',
    equations: "y''[x] + y[x] == 0",
    functions: 'y',
    variables: 'x',
    conditions: "y[0] == 1\ny'[0] == 0",
    plot_range: '',
    plot: false,
  },
  'ode-system': {
    fmt: 'wolfram',
    kind: 'ode_system',
    mode: 'numeric',
    equations: "x'[t] == y[t]\ny'[t] == -x[t]",
    functions: 'x, y',
    variables: 't',
    conditions: 'x[0] == 1\ny[0] == 0',
    plot_range: '{t, 0, 2 Pi}',
    plot: true,
  },
  'pde-numeric': {
    fmt: 'wolfram',
    kind: 'pde',
    mode: 'numeric',
    equations: 'D[u[x,t],t] == D[u[x,t],{x,2}]',
    functions: 'u',
    variables: 'x, t',
    conditions: 'u[x,0] == Sin[x]\nu[0,t] == 0\nu[Pi,t] == 0',
    plot_range: '{x, 0, Pi}, {t, 0, 1}',
    plot: true,
  },
  'latex-ode': {
    fmt: 'latex',
    kind: 'ode',
    mode: 'analytic',
    equations: '\\frac{d^2 y}{dx^2} + y = 0',
    functions: 'y',
    variables: 'x',
    conditions: '',
    plot_range: '',
    plot: false,
  },
};

async function applyExample(id, btn) {
  const example = EXAMPLES[id];
  if (!example) return;
  parameterValues = {};
  fillFormData(example);
  await refreshAllPreviews();
  resetResultView();
  resultEl.hidden = true;
  statusEl.textContent = '已填入示例';
  flash(btn, '已填入');
  form.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function showRecognized(eqs, conds) {
  const lines = [];
  if (eqs && eqs.length) lines.push(`<span class="label">方程：</span><code>${escapeHtml(eqs.join(' ; '))}</code>`);
  if (conds && conds.length) lines.push(`<span class="label">条件：</span><code>${escapeHtml(conds.join(' ; '))}</code>`);
  recognizedEl.innerHTML = lines.length
    ? `<div><b>已识别为 Wolfram 表达式：</b></div>` + lines.map(l => `<div>${l}</div>`).join('')
    : '';
}

function resetResultView() {
  resultEl.hidden = false;
  errorEl.hidden = true;
  errorEl.textContent = '';
  warningsEl.hidden = true;
  warningsListEl.innerHTML = '';
  latexBlock.hidden = true;
  latexRender.innerHTML = '';
  latexSource.textContent = '';
  rawBlock.hidden = true;
  rawOut.textContent = '';
  rawOut.dataset.fullRaw = '';
  rawOut.dataset.expanded = 'false';
  toggleRawBtn.hidden = true;
  toggleRawBtn.textContent = '展开';
  imageBlock.hidden = true;
  setResultImage(null);
  recognizedEl.innerHTML = '';
}

function setRawOutput(raw) {
  rawOut.dataset.fullRaw = raw;
  rawOut.dataset.expanded = 'false';
  if (raw.length > RAW_PREVIEW_LIMIT) {
    rawOut.textContent = `${raw.slice(0, RAW_PREVIEW_LIMIT)}\n\n...（原始输出较长，已折叠）`;
    toggleRawBtn.hidden = false;
    toggleRawBtn.textContent = '展开';
  } else {
    rawOut.textContent = raw;
    toggleRawBtn.hidden = true;
  }
  rawBlock.hidden = false;
}

async function renderSolveResult(j, statusText) {
  resetResultView();
  showRecognized(j.normalized_eqs, j.normalized_conds);

  if (j.warnings && j.warnings.length) {
    warningsListEl.innerHTML = j.warnings
      .map((w) => `<li>${escapeHtml(w)}</li>`).join('');
    warningsEl.hidden = false;
  }

  if (!j.ok) {
    errorEl.textContent = j.error || '未知错误';
    errorEl.hidden = false;
  } else {
    if (j.latex) {
      latexSource.textContent = j.latex;
      latexRender.textContent = `\\[ ${j.latex} \\]`;
      latexBlock.hidden = false;
      if (window.MathJax && window.MathJax.typesetPromise) {
        try { await window.MathJax.typesetPromise([latexRender]); } catch {}
      }
    }
    if (j.raw) {
      setRawOutput(j.raw);
    }
    if (j.image_base64) {
      setResultImage(j.image_base64);
      imageBlock.hidden = false;
    }
  }
  statusEl.textContent = statusText || (j.ok ? '完成' : '失败');
}

toggleRawBtn.addEventListener('click', () => {
  const full = rawOut.dataset.fullRaw || '';
  const expanded = rawOut.dataset.expanded === 'true';
  if (expanded) {
    rawOut.textContent = `${full.slice(0, RAW_PREVIEW_LIMIT)}\n\n...（原始输出较长，已折叠）`;
    rawOut.dataset.expanded = 'false';
    toggleRawBtn.textContent = '展开';
  } else {
    rawOut.textContent = full;
    rawOut.dataset.expanded = 'true';
    toggleRawBtn.textContent = '收起';
  }
});

function loadHistory() {
  try {
    const parsed = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveHistory(items) {
  let trimmed = items.slice(0, HISTORY_LIMIT);
  while (trimmed.length) {
    try {
      localStorage.setItem(HISTORY_KEY, JSON.stringify(trimmed));
      return trimmed;
    } catch {
      trimmed = trimmed.slice(0, -1);
    }
  }
  localStorage.removeItem(HISTORY_KEY);
  return [];
}

function historyTitle(data) {
  const firstEq = (data.equations || '').split('\n')[0] || '';
  return firstEq.trim() || '空方程';
}

function formatHistoryTime(ts) {
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(new Date(ts));
}

function renderHistory() {
  const items = loadHistory();
  historyEmptyEl.hidden = items.length > 0;
  historyListEl.innerHTML = items.map((item) => {
    const okClass = item.result && item.result.ok ? 'ok' : 'err';
    const mode = item.input?.mode === 'numeric' ? '数值' : '解析';
    const kindMap = { ode: 'ODE', ode_system: 'ODE 组', pde: 'PDE' };
    const kind = kindMap[item.input?.kind] || item.input?.kind || 'ODE';
    return `
      <article class="history-item" data-id="${escapeHtml(item.id)}">
        <button type="button" class="history-open">
          <span class="history-title">${escapeHtml(item.title)}</span>
          <span class="history-meta">${escapeHtml(formatHistoryTime(item.createdAt))} · ${escapeHtml(kind)} · ${escapeHtml(mode)}</span>
          <span class="history-status ${okClass}">${item.result?.ok ? '完成' : '失败'}</span>
        </button>
        <button type="button" class="history-delete" title="删除记录" aria-label="删除记录">×</button>
      </article>`;
  }).join('');
}

function rememberHistory(input, result) {
  const signature = JSON.stringify({
    fmt: input.fmt, kind: input.kind, mode: input.mode,
    equations: input.equations, functions: input.functions,
    variables: input.variables, conditions: input.conditions,
    plot_range: input.plot_range, plot: input.plot,
    parameter_values: input.parameter_values || {},
  });
  const entry = {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    createdAt: Date.now(),
    title: historyTitle(input),
    input,
    result,
    signature,
  };
  const rest = loadHistory().filter((item) => item.signature !== signature);
  saveHistory([entry, ...rest]);
  renderHistory();
}

historyListEl.addEventListener('click', async (e) => {
  const itemEl = e.target.closest('.history-item');
  if (!itemEl) return;
  const id = itemEl.dataset.id;
  const items = loadHistory();
  const item = items.find((h) => h.id === id);
  if (!item) return;

  if (e.target.closest('.history-delete')) {
    saveHistory(items.filter((h) => h.id !== id));
    renderHistory();
    return;
  }

  fillFormData(item.input || {});
  await refreshAllPreviews();
  await renderSolveResult(item.result || { ok: false, error: '历史记录损坏' }, '已从历史记录恢复');
  itemEl.classList.add('active');
  historyListEl.querySelectorAll('.history-item').forEach((el) => {
    if (el !== itemEl) el.classList.remove('active');
  });
});

clearHistoryBtn.addEventListener('click', () => {
  localStorage.removeItem(HISTORY_KEY);
  renderHistory();
});

renderHistory();
initAccessTokenPanel();

document.querySelectorAll('.example-apply[data-example]').forEach((btn) => {
  btn.addEventListener('click', () => applyExample(btn.dataset.example, btn));
});

function handleParameterSourceChange() {
  renderParameterControls();
  updateFormWarnings();
}

form.equations.addEventListener('input', handleParameterSourceChange);
form.conditions.addEventListener('input', handleParameterSourceChange);
form.plot_range.addEventListener('input', handleParameterSourceChange);
form.functions.addEventListener('input', handleParameterSourceChange);
form.variables.addEventListener('input', renderParameterControls);
form.querySelectorAll('input[name="kind"]').forEach((r) => {
  r.addEventListener('change', () => {
    applyKindDefaults();
    handleParameterSourceChange();
  });
});
form.querySelectorAll('input[name="mode"]').forEach((r) => {
  r.addEventListener('change', renderParameterControls);
});
parameterListEl.addEventListener('input', (e) => {
  const row = e.target.closest('.parameter-row');
  if (!row) return;
  const name = row.dataset.param;
  const slider = row.querySelector('.parameter-slider');
  const number = row.querySelector('.parameter-value');
  const value = Number(e.target.value);
  parameterValues[name] = Number.isFinite(value) ? value : 0;
  if (e.target === slider) number.value = parameterValues[name];
  if (e.target === number) slider.value = parameterValues[name];
});

updateKindPlaceholders(getRadio('kind'));
renderParameterControls();
autoResizeTextareas();

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  if (APP_CONFIG.accessTokenRequired && !getAccessToken()) {
    statusEl.textContent = '请输入访问口令';
    showFormWarnings(['当前服务启用了远程访问口令，请先输入主机提供的口令。']);
    return;
  }
  persistAccessToken();
  const data = getFormData();
  const formWarnings = validateEquationFunctionCount(data);
  if (formWarnings.length) {
    showFormWarnings(formWarnings);
    statusEl.textContent = '请先处理提示';
    return;
  }
  showFormWarnings([]);
  const params = getParameterValues();
  const historyInput = { ...data, parameter_values: params };
  const requestData = applyParameterValues(data, params);
  button.disabled = true;
  statusEl.textContent = '求解中…';
  resetResultView();

  try {
    const r = await fetch('/solve', {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify(requestData),
    });
    const j = await readJsonResponse(r);
    if (!r.ok && !j.error) j.error = `请求失败：HTTP ${r.status}`;
    await renderSolveResult(j);
    rememberHistory(historyInput, j);
  } catch (err) {
    const j = { ok: false, error: String(err), normalized_eqs: [], normalized_conds: [], warnings: [] };
    await renderSolveResult(j);
    rememberHistory(historyInput, j);
  } finally {
    button.disabled = false;
  }
});
