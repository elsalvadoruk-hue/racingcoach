import { useEffect } from "react";

export function useCollectorPolling({
  collectorAutoContextRef, collectorHandledJobRef, collectorTerminalRetryRef, garage61Collector,
  loadExternalReferences, loadGarage61CollectorStatus, maybeStartGarage61Collection, pitRule,
  selectedFileRef, sessionMode, setPendingReferences
}) {
  useEffect(() => {
    if (!garage61Collector?.job?.job_id) return undefined;

    let cancelled = false;
    let stopped = false;
    let inFlight = false;
    let timer = null;

    const stopPolling = () => {
      stopped = true;
      if (timer) {
        window.clearTimeout(timer);
        timer = null;
      }
    };

    const schedule = (delayMs) => {
      if (cancelled || stopped) return;
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(poll, delayMs);
    };

    const poll = async () => {
      if (cancelled || stopped || inFlight) return;
      inFlight = true;
      let nextDelayMs = 5000;

      try {
        const filename = selectedFileRef.current;
        if (!filename) {
          stopPolling();
          return;
        }

        const payload = await loadGarage61CollectorStatus(filename);
        if (cancelled) return;
        const job = payload?.job;
        if (!job?.job_id) {
          nextDelayMs = 5000;
          return;
        }

        if (job.busy) {
          nextDelayMs = 5000;
          return;
        }

        const terminal = ['COMPLETE', 'PARTIAL', 'FAILED', 'CANCELLED', 'SKIPPED_CACHE_SUFFICIENT', 'SKIPPED_COOLDOWN'].includes(job.state);
        if (!terminal) {
          nextDelayMs = 5000;
          return;
        }

        if (collectorHandledJobRef.current === job.job_id) {
          stopPolling();
          return;
        }

        collectorAutoContextRef.current = '';
        const currentContext = payload?.health?.context?.context_key;
        if (job.context_key && currentContext && job.context_key !== currentContext) {
          collectorHandledJobRef.current = job.job_id;
          stopPolling();
          void maybeStartGarage61Collection(filename);
          return;
        }

        if (!['COMPLETE', 'PARTIAL', 'SKIPPED_CACHE_SUFFICIENT', 'SKIPPED_COOLDOWN'].includes(job.state)) {
          collectorHandledJobRef.current = job.job_id;
          stopPolling();
          return;
        }

        // IMPORTANT: do not auto-select here. Updating selected references changes
        // runAnalysis's callback identity. In v2.7.0 that restarted this effect
        // before terminal handling could finish, producing an endless status /
        // external-reference request loop and preventing the final /api/analyze.
        const referencePayload = await loadExternalReferences(filename, false);
        if (cancelled || selectedFileRef.current !== filename) {
          stopPolling();
          return;
        }

        const expectedCached = Number(
          job.context_csv_count ?? job.cached_csv_count ?? job.downloaded_count ?? 0
        );
        const compatible = Number(referencePayload?.total_compatible ?? 0);
        if (collectorTerminalRetryRef.current.jobId !== job.job_id) {
          collectorTerminalRetryRef.current = { jobId: job.job_id, attempts: 0 };
        }
        if (expectedCached > 0 && compatible === 0 && collectorTerminalRetryRef.current.attempts < 6) {
          collectorTerminalRetryRef.current.attempts += 1;
          nextDelayMs = Math.min(3000 + collectorTerminalRetryRef.current.attempts * 1000, 8000);
          return;
        }

        const recommended = (referencePayload.applied_selection || referencePayload.recommended_selection || []).slice(
          0,
          referencePayload.display_limit || referencePayload.max_selection || 9
        );
        setPendingReferences(recommended);
        collectorHandledJobRef.current = job.job_id;
        stopPolling();
        return;


      } catch {
        // Garage61 polling must never interrupt the local telemetry analysis UI.
        nextDelayMs = 5000;
      } finally {
        inFlight = false;
        if (!cancelled && !stopped) schedule(nextDelayMs);
      }
    };

    void poll();
    return () => {
      cancelled = true;
      stopPolling();
    };
  }, [garage61Collector?.job?.job_id, garage61Collector?.job?.busy, garage61Collector?.job?.state,
    loadExternalReferences, loadGarage61CollectorStatus, maybeStartGarage61Collection, pitRule, sessionMode,
    collectorAutoContextRef, collectorHandledJobRef, collectorTerminalRetryRef, selectedFileRef, setPendingReferences]);
}
