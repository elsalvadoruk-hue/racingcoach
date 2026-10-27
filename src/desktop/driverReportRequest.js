export const DRIVER_REPORT_TIMEOUT_MS = 15000;

export async function readReportJson(url, controller, method, body) {
  let timer, abort;
  const deadline = new Promise((resolve, reject) => {
    abort = () => reject(new DOMException('Read cancelled', 'AbortError'));
    controller.signal.addEventListener('abort', abort, { once: true });
    timer = setTimeout(() => {
      const error = new Error('응답이 늦어 조회를 중단했습니다. 다시 시도해 주세요.');
      error.name = 'TimeoutError'; reject(error); controller.abort();
    }, DRIVER_REPORT_TIMEOUT_MS);
  });
  try {
    return await Promise.race([deadline, (async () => {
      const response = await fetch(url, { signal: controller.signal, ...(method ? { method } : {}),
        ...(body !== undefined ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) });
      let payload;
      try { payload = await response.json(); }
      catch { throw new Error('리포트 응답을 읽지 못했습니다. 다시 조회해 주세요.'); }
      if (!response.ok) {
        const error = new Error(typeof payload?.detail === 'string' ? payload.detail : `리포트를 가져오지 못했습니다. (HTTP ${response.status})`);
        error.status = response.status;
        throw error;
      }
      return payload;
    })()]);
  } finally { clearTimeout(timer); controller.signal.removeEventListener('abort', abort); }
}
