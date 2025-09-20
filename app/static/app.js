(function () {
  const state = {
    tests: [],
    selectedTestId: null,
    currentSummary: null,
    ws: null,
    statusTimer: null,
    lastCallRefresh: 0,
    callRefreshTimer: null,
  };

  const DEFAULT_STATUS = 'Ready.';
  const STATUS_RESET_DELAY = 4000;
  const CALL_REFRESH_MIN_INTERVAL = 5000;

  const numberFormatter = new Intl.NumberFormat();
  const percentFormatter = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });
  const durationFormatter = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });

  const statusBanner = document.getElementById('status-banner');
  const testsListEl = document.getElementById('tests-list');
  const testsEmptyStateEl = document.getElementById('tests-empty-state');
  const testDetailsPlaceholder = document.getElementById('test-details');
  const summarySection = document.getElementById('summary-section');
  const summaryNameEl = document.getElementById('summary-name');
  const summaryStatusEl = document.getElementById('summary-status');
  const summaryCreatedEl = document.getElementById('summary-created');
  const summaryStartedWrapper = document.getElementById('summary-started-wrapper');
  const summaryStartedEl = document.getElementById('summary-started');
  const summaryFinishedWrapper = document.getElementById('summary-finished-wrapper');
  const summaryFinishedEl = document.getElementById('summary-finished');
  const summarySuccessRateEl = document.getElementById('summary-success-rate');
  const summaryThroughputEl = document.getElementById('summary-throughput');
  const summaryNotesWrapper = document.getElementById('notes-wrapper');
  const summaryNotesEl = document.getElementById('summary-notes');
  const outstandingWrapper = document.getElementById('outstanding-wrapper');
  const outstandingListEl = document.getElementById('outstanding-list');
  const callsContainer = document.getElementById('calls-container');
  const actionsRow = document.getElementById('test-actions');
  const refreshTestsBtn = document.getElementById('refresh-tests');
  const refreshSummaryBtn = document.getElementById('refresh-summary');
  const refreshCallsBtn = document.getElementById('refresh-calls');
  const startButton = document.getElementById('start-test');
  const stopButton = document.getElementById('stop-test');
  const createTestForm = document.getElementById('create-test-form');
  const apiBaseEl = document.getElementById('api-base');
  const appVersionEl = document.getElementById('app-version');

  const countElements = {
    expected: document.getElementById('count-expected'),
    sent: document.getElementById('count-sent'),
    dispatched: document.getElementById('count-dispatched'),
    completed: document.getElementById('count-completed'),
    failed: document.getElementById('count-failed'),
    pending: document.getElementById('count-pending'),
  };

  const responseTimeElements = {
    minimum_ms: document.getElementById('rt-min'),
    median_ms: document.getElementById('rt-median'),
    average_ms: document.getElementById('rt-average'),
    maximum_ms: document.getElementById('rt-max'),
  };

  function setStatus(message, type = 'info', options = {}) {
    const { persist = false } = options;
    statusBanner.textContent = message;
    statusBanner.dataset.type = type;

    if (state.statusTimer) {
      clearTimeout(state.statusTimer);
      state.statusTimer = null;
    }

    if (!persist) {
      state.statusTimer = window.setTimeout(() => {
        statusBanner.textContent = DEFAULT_STATUS;
        statusBanner.dataset.type = 'info';
        state.statusTimer = null;
      }, STATUS_RESET_DELAY);
    }
  }

  function formatStatus(status) {
    if (!status) {
      return 'Unknown';
    }
    return status.charAt(0).toUpperCase() + status.slice(1);
  }

  function formatTimestamp(seconds) {
    if (seconds === null || seconds === undefined) {
      return '—';
    }
    const date = new Date(seconds * 1000);
    if (Number.isNaN(date.getTime())) {
      return '—';
    }
    return date.toLocaleString();
  }

  function formatDuration(value) {
    if (value === null || value === undefined) {
      return '—';
    }
    return `${durationFormatter.format(value)} ms`;
  }

  function formatThroughput(value) {
    if (value === null || value === undefined) {
      return '—';
    }
    return `${durationFormatter.format(value)} ms`;
  }

  async function requestJSON(url, options = {}) {
    const { method = 'GET', body } = options;
    const fetchOptions = {
      method,
      headers: {
        Accept: 'application/json',
      },
    };

    if (body !== undefined) {
      fetchOptions.headers['Content-Type'] = 'application/json';
      fetchOptions.body = JSON.stringify(body);
    }

    let response;
    try {
      response = await fetch(url, fetchOptions);
    } catch (error) {
      throw new Error(`Network error: ${error.message}`);
    }

    const text = await response.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (error) {
        throw new Error('Received an unexpected response from the server.');
      }
    }

    if (!response.ok) {
      const detail = data && data.detail;
      if (typeof detail === 'string' && detail.trim()) {
        throw new Error(detail);
      }
      if (Array.isArray(detail)) {
        throw new Error(detail.join(', '));
      }
      if (data && typeof data.message === 'string') {
        throw new Error(data.message);
      }
      throw new Error(`${response.status} ${response.statusText}`);
    }

    return data;
  }

  function renderTestList() {
    testsListEl.innerHTML = '';

    if (!state.tests.length) {
      testsEmptyStateEl.classList.remove('hidden');
      return;
    }

    testsEmptyStateEl.classList.add('hidden');

    state.tests.forEach((summary) => {
      const item = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'test-item';
      if (summary.id === state.selectedTestId) {
        button.classList.add('selected');
      }

      const info = document.createElement('div');
      info.className = 'item-info';

      const title = document.createElement('div');
      title.className = 'item-title';
      title.textContent = summary.name;
      info.appendChild(title);

      const subtitle = document.createElement('div');
      subtitle.className = 'item-subtitle';
      subtitle.textContent = `${numberFormatter.format(summary.counts.sent)} sent · ${numberFormatter.format(
        summary.counts.completed,
      )} completed · ${numberFormatter.format(summary.counts.pending)} pending`;
      info.appendChild(subtitle);

      const status = document.createElement('span');
      status.className = 'status-pill';
      status.dataset.status = summary.status;
      status.textContent = formatStatus(summary.status);

      button.appendChild(info);
      button.appendChild(status);
      button.addEventListener('click', () => {
        selectTest(summary.id);
      });

      item.appendChild(button);
      testsListEl.appendChild(item);
    });
  }

  function clearSummary() {
    state.currentSummary = null;
    summarySection.classList.add('hidden');
    actionsRow.classList.add('hidden');
    testDetailsPlaceholder.classList.remove('hidden');
    testDetailsPlaceholder.textContent = state.tests.length
      ? 'Select a test to see the latest metrics.'
      : 'Create a test to begin monitoring.';
    outstandingListEl.innerHTML = '';
    outstandingWrapper.classList.add('hidden');
    summaryNotesWrapper.classList.add('hidden');
    callsContainer.innerHTML = '<p class="muted">Select a test to view recent call activity.</p>';
    document.title = 'Middleware Load Test Dashboard';
  }

  function updateSummary(summary, options = {}) {
    const { fromWebSocket = false, updateList = true } = options;
    const previousCounts = state.currentSummary && state.currentSummary.counts;

    state.currentSummary = summary;
    summarySection.classList.remove('hidden');
    actionsRow.classList.remove('hidden');
    testDetailsPlaceholder.classList.add('hidden');

    summaryNameEl.textContent = summary.name;
    summaryStatusEl.dataset.status = summary.status;
    summaryStatusEl.textContent = formatStatus(summary.status);
    summaryCreatedEl.textContent = formatTimestamp(summary.created_at);

    if (summary.started_at) {
      summaryStartedWrapper.classList.remove('hidden');
      summaryStartedEl.textContent = formatTimestamp(summary.started_at);
    } else {
      summaryStartedWrapper.classList.add('hidden');
      summaryStartedEl.textContent = '';
    }

    if (summary.finished_at) {
      summaryFinishedWrapper.classList.remove('hidden');
      summaryFinishedEl.textContent = formatTimestamp(summary.finished_at);
    } else {
      summaryFinishedWrapper.classList.add('hidden');
      summaryFinishedEl.textContent = '';
    }

    countElements.expected.textContent = numberFormatter.format(summary.counts.expected);
    countElements.sent.textContent = numberFormatter.format(summary.counts.sent);
    countElements.dispatched.textContent = numberFormatter.format(summary.counts.dispatched);
    countElements.completed.textContent = numberFormatter.format(summary.counts.completed);
    countElements.failed.textContent = numberFormatter.format(summary.counts.failed);
    countElements.pending.textContent = numberFormatter.format(summary.counts.pending);

    summarySuccessRateEl.textContent = `${percentFormatter.format(summary.success_rate)}%`;
    summaryThroughputEl.textContent = formatThroughput(summary.average_throughput_ms);

    Object.entries(responseTimeElements).forEach(([key, element]) => {
      element.textContent = formatDuration(summary.response_times[key]);
    });

    if (summary.outstanding_correlation_tokens && summary.outstanding_correlation_tokens.length) {
      outstandingWrapper.classList.remove('hidden');
      outstandingListEl.innerHTML = '';
      summary.outstanding_correlation_tokens.forEach((token) => {
        const li = document.createElement('li');
        li.textContent = token;
        outstandingListEl.appendChild(li);
      });
    } else {
      outstandingWrapper.classList.add('hidden');
      outstandingListEl.innerHTML = '';
    }

    if (summary.notes) {
      summaryNotesWrapper.classList.remove('hidden');
      summaryNotesEl.textContent = summary.notes;
    } else {
      summaryNotesWrapper.classList.add('hidden');
      summaryNotesEl.textContent = '';
    }

    startButton.disabled = summary.status === 'running';
    stopButton.disabled = summary.status !== 'running';

    document.title = `Middleware Load Test Dashboard – ${summary.name}`;

    const index = state.tests.findIndex((test) => test.id === summary.id);
    const shouldRenderList = updateList || index >= 0;
    if (index >= 0) {
      state.tests[index] = summary;
    } else if (updateList) {
      state.tests.unshift(summary);
    }

    if (shouldRenderList) {
      renderTestList();
    }

    if (
      fromWebSocket &&
      previousCounts &&
      summary.counts &&
      (summary.counts.completed > previousCounts.completed || summary.counts.failed > previousCounts.failed)
    ) {
      maybeRefreshCalls();
    }
  }

  function renderCalls(calls) {
    if (!Array.isArray(calls) || !calls.length) {
      callsContainer.innerHTML = '<p class="muted">No calls have been recorded yet.</p>';
      return;
    }

    const sorted = [...calls].sort((a, b) => (b.sent_at || 0) - (a.sent_at || 0));
    const table = document.createElement('table');
    const thead = document.createElement('thead');
    thead.innerHTML =
      '<tr><th>Token</th><th>State</th><th>Sent</th><th>Duration (ms)</th><th>Ack</th><th>Callback</th></tr>';
    table.appendChild(thead);

    const tbody = document.createElement('tbody');
    sorted.forEach((call) => {
      const row = document.createElement('tr');

      const tokenCell = document.createElement('td');
      const tokenCode = document.createElement('code');
      tokenCode.textContent = call.correlation_token;
      tokenCode.title = call.correlation_token;
      tokenCell.appendChild(tokenCode);
      row.appendChild(tokenCell);

      const stateCell = document.createElement('td');
      stateCell.textContent = formatStatus(call.state);
      row.appendChild(stateCell);

      const sentCell = document.createElement('td');
      sentCell.textContent = formatTimestamp(call.sent_at);
      row.appendChild(sentCell);

      const durationCell = document.createElement('td');
      durationCell.textContent = call.duration_ms == null ? '—' : durationFormatter.format(call.duration_ms);
      row.appendChild(durationCell);

      const ackCell = document.createElement('td');
      const ackParts = [];
      if (call.ack_status !== null && call.ack_status !== undefined) {
        ackParts.push(`Status ${call.ack_status}`);
      }
      if (call.ack_error) {
        ackParts.push(call.ack_error);
      }
      ackCell.textContent = ackParts.length ? ackParts.join(' • ') : '—';
      row.appendChild(ackCell);

      const callbackCell = document.createElement('td');
      const callbackParts = [];
      if (call.callback_status) {
        callbackParts.push(call.callback_status);
      }
      if (call.callback_code !== null && call.callback_code !== undefined) {
        callbackParts.push(`#${call.callback_code}`);
      }
      callbackCell.textContent = callbackParts.length ? callbackParts.join(' • ') : '—';
      row.appendChild(callbackCell);

      tbody.appendChild(row);
    });

    table.appendChild(tbody);
    callsContainer.innerHTML = '';
    callsContainer.appendChild(table);
  }
  async function loadCalls(options = {}) {
    const { silent = false } = options;
    if (!state.selectedTestId) {
      return;
    }

    if (!silent) {
      setStatus('Loading recent calls…', 'info');
    }

    try {
      const response = await requestJSON(`/tests/${state.selectedTestId}/calls?limit=50`);
      renderCalls(response.calls);
      state.lastCallRefresh = Date.now();
      if (state.callRefreshTimer) {
        clearTimeout(state.callRefreshTimer);
        state.callRefreshTimer = null;
      }
      if (!silent) {
        setStatus('Recent calls loaded.', 'success');
      }
    } catch (error) {
      if (!silent) {
        setStatus(`Failed to load calls: ${error.message}`, 'error', { persist: true });
      }
    }
  }

  function maybeRefreshCalls() {
    const now = Date.now();
    if (now - state.lastCallRefresh >= CALL_REFRESH_MIN_INTERVAL) {
      loadCalls({ silent: true });
    } else {
      if (state.callRefreshTimer) {
        clearTimeout(state.callRefreshTimer);
      }
      state.callRefreshTimer = window.setTimeout(() => {
        state.callRefreshTimer = null;
        loadCalls({ silent: true });
      }, Math.max(0, CALL_REFRESH_MIN_INTERVAL - (now - state.lastCallRefresh)));
    }
  }

  function closeWebSocket() {
    if (state.ws) {
      try {
        state.ws.close(1000, 'Client closing');
      } catch (error) {
        console.error('Error closing WebSocket', error);
      }
      state.ws = null;
    }
  }

  function openWebSocket(testId) {
    closeWebSocket();

    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${protocol}://${window.location.host}/ws/tests/${testId}`);
    state.ws = ws;

    ws.addEventListener('open', () => {
      setStatus('Live updates connected.', 'success');
    });

    ws.addEventListener('message', (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload && payload.error) {
          setStatus(`Live updates error: ${payload.error}`, 'error', { persist: true });
          return;
        }
        if (payload && payload.id) {
          updateSummary(payload, { fromWebSocket: true });
        }
      } catch (error) {
        console.error('Failed to parse WebSocket message', error);
      }
    });

    ws.addEventListener('close', (event) => {
      if (!event.wasClean && state.selectedTestId === testId) {
        setStatus('Live updates disconnected unexpectedly.', 'warning', { persist: true });
      }
      if (state.ws === ws) {
        state.ws = null;
      }
    });

    ws.addEventListener('error', () => {
      setStatus('WebSocket connection reported an error.', 'warning', { persist: true });
    });
  }

  async function loadTestSummary(testId) {
    if (!testId) {
      return null;
    }

    try {
      const summary = await requestJSON(`/tests/${testId}`);
      updateSummary(summary, { updateList: false });
      return summary;
    } catch (error) {
      setStatus(`Unable to load test: ${error.message}`, 'error', { persist: true });
      return null;
    }
  }

  async function selectTest(testId) {
    if (!testId) {
      return;
    }

    if (state.selectedTestId !== testId) {
      state.selectedTestId = testId;
      renderTestList();
    }

    setStatus('Loading test details…', 'info');
    const summary = await loadTestSummary(testId);
    if (!summary) {
      return;
    }

    setStatus(`Now viewing “${summary.name}”.`, 'success');
    await loadCalls({ silent: true });
    openWebSocket(testId);
  }

  async function loadTests() {
    setStatus('Loading tests…', 'info');
    try {
      const response = await requestJSON('/tests');
      const tests = Array.isArray(response.tests) ? response.tests : [];
      tests.sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
      state.tests = tests;
      renderTestList();
      setStatus('Tests loaded.', 'success');

      if (!tests.length) {
        clearSummary();
        closeWebSocket();
        state.selectedTestId = null;
        return;
      }

      if (state.selectedTestId) {
        const matching = tests.find((test) => test.id === state.selectedTestId);
        if (matching) {
          updateSummary(matching, { updateList: false });
        } else {
          state.selectedTestId = null;
          clearSummary();
          closeWebSocket();
        }
      }

      if (!state.selectedTestId && tests.length) {
        state.selectedTestId = tests[0].id;
        renderTestList();
        await selectTest(state.selectedTestId);
      }
    } catch (error) {
      setStatus(`Failed to load tests: ${error.message}`, 'error', { persist: true });
      state.tests = [];
      renderTestList();
      clearSummary();
      closeWebSocket();
      state.selectedTestId = null;
    }
  }

  async function startSelectedTest() {
    if (!state.selectedTestId) {
      return;
    }

    startButton.disabled = true;
    setStatus('Starting test…', 'info');
    try {
      const summary = await requestJSON(`/tests/${state.selectedTestId}/start`, { method: 'POST' });
      updateSummary(summary);
      setStatus('Test started.', 'success');
    } catch (error) {
      setStatus(`Unable to start test: ${error.message}`, 'error', { persist: true });
    } finally {
      startButton.disabled = state.currentSummary ? state.currentSummary.status === 'running' : false;
    }
  }

  async function stopSelectedTest() {
    if (!state.selectedTestId) {
      return;
    }

    stopButton.disabled = true;
    setStatus('Stopping test…', 'info');
    try {
      const summary = await requestJSON(`/tests/${state.selectedTestId}/stop`, { method: 'POST' });
      updateSummary(summary);
      setStatus('Test stopped.', 'success');
    } catch (error) {
      setStatus(`Unable to stop test: ${error.message}`, 'error', { persist: true });
    } finally {
      stopButton.disabled = state.currentSummary ? state.currentSummary.status !== 'running' : true;
    }
  }

  async function handleRefreshSummary() {
    if (!state.selectedTestId) {
      return;
    }

    setStatus('Refreshing test details…', 'info');
    const summary = await loadTestSummary(state.selectedTestId);
    if (summary) {
      setStatus('Latest metrics loaded.', 'success');
      await loadCalls({ silent: true });
    }
  }

  function parseJsonField(value, fieldName) {
    if (!value) {
      return fieldName === 'payload' ? null : {};
    }

    try {
      const parsed = JSON.parse(value);
      if (fieldName === 'headers' && (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed))) {
        throw new Error('Headers must be a JSON object.');
      }
      return parsed;
    } catch (error) {
      throw new Error(`Invalid ${fieldName} JSON: ${error.message}`);
    }
  }

  async function handleFormSubmit(event) {
    event.preventDefault();

    const formData = new FormData(createTestForm);
    const payloadRaw = formData.get('payload');
    const headersRaw = formData.get('headers');

    let payload;
    let headers;
    try {
      payload = parseJsonField(payloadRaw && payloadRaw.trim(), 'payload');
      headers = parseJsonField(headersRaw && headersRaw.trim(), 'headers');
    } catch (error) {
      setStatus(error.message, 'error', { persist: true });
      return;
    }

    const config = {
      name: String(formData.get('name') || '').trim(),
      target_url: String(formData.get('target_url') || '').trim(),
      method: String(formData.get('method') || 'POST'),
      rate_per_minute: Number(formData.get('rate_per_minute')),
      duration_seconds: Number(formData.get('duration_seconds')),
      headers,
      payload,
      start_immediately: formData.get('start_immediately') === 'on',
    };

    if (!config.name || !config.target_url) {
      setStatus('Name and target URL are required.', 'error', { persist: true });
      return;
    }

    const submitButton = createTestForm.querySelector('button[type="submit"]');
    submitButton.disabled = true;
    setStatus('Creating test…', 'info');

    try {
      const summary = await requestJSON('/tests', { method: 'POST', body: config });
      state.selectedTestId = summary.id;
      updateSummary(summary);
      createTestForm.reset();
      setStatus(`Created test “${summary.name}”.`, 'success');
      await loadCalls({ silent: true });
      openWebSocket(summary.id);
    } catch (error) {
      setStatus(`Failed to create test: ${error.message}`, 'error', { persist: true });
    } finally {
      submitButton.disabled = false;
    }
  }

  async function loadServiceInfo() {
    apiBaseEl.textContent = window.location.origin;
    try {
      const info = await requestJSON('/');
      if (info && info.version) {
        appVersionEl.textContent = info.version;
      } else {
        appVersionEl.textContent = '—';
      }
    } catch (error) {
      appVersionEl.textContent = 'unknown';
      setStatus(`Unable to reach API service: ${error.message}`, 'warning', { persist: true });
    }
  }

  async function init() {
    clearSummary();
    renderTestList();

    refreshTestsBtn.addEventListener('click', () => {
      loadTests();
    });
    refreshSummaryBtn.addEventListener('click', () => {
      handleRefreshSummary();
    });
    refreshCallsBtn.addEventListener('click', () => {
      loadCalls();
    });
    startButton.addEventListener('click', () => {
      startSelectedTest();
    });
    stopButton.addEventListener('click', () => {
      stopSelectedTest();
    });
    createTestForm.addEventListener('submit', (event) => {
      handleFormSubmit(event);
    });

    window.addEventListener('beforeunload', () => {
      closeWebSocket();
    });

    await loadServiceInfo();
    await loadTests();
  }

  init();
})();
