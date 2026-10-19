
export const API_BASE =
  (typeof process !== 'undefined' && process.env?.REACT_APP_API_BASE_URL) ||
  'http://localhost:24158';

export async function requestJson(path, options = {}) {
  const controller = new AbortController();
  const { signal, timeoutMs = 180000, ...fetchOptions } = options;
  const cancel = () => controller.abort();
  if (signal?.aborted) controller.abort();
  signal?.addEventListener('abort', cancel, { once: true });
  let timedOut = false;
  const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  try {
  const response = await fetch(`${API_BASE}${path}`, { ...fetchOptions, signal: controller.signal });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    throw new Error(payload?.detail || `HTTP ${response.status}`);
  }
  return payload;
  } catch (error) {
    if (timedOut) throw new Error('응답 대기 시간이 초과되었습니다. 분석 서버 연결을 확인하고 다시 시도해 주세요.');
    throw error;
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener('abort', cancel);
  }
}
