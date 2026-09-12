'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { apiClient, getApiBaseUrl, getAuthToken } from '@/services/api';
import { demoStore } from '@/services/demo-store';
import type { JobEventRead, JobRead, JobStage, JobStatus } from '@/types/api';

/**
 * Job statuses from which a job never advances. REVIEW_REQUIRED is terminal —
 * the backend workflow sets it whenever an outcome needs a human, and treating
 * it as non-terminal left the drawer spinning and polling forever.
 * Mirrors TERMINAL_JOB_STATUSES in services/api/app/schemas/canonical.py.
 */
const TERMINAL_JOB_STATUSES: readonly JobStatus[] = ['COMPLETED', 'FAILED', 'REVIEW_REQUIRED'];

function isTerminalStatus(status: string | null | undefined): boolean {
  return !!status && (TERMINAL_JOB_STATUSES as readonly string[]).includes(status);
}

interface UseJobStreamOptions {
  jobId: string | null;
  onCompleted?: (job: JobRead) => void;
  onFailed?: (error: string) => void;
  pollingFallbackIntervalMs?: number;
}

export function useJobStream({
  jobId,
  onCompleted,
  onFailed,
  pollingFallbackIntervalMs = 2500,
}: UseJobStreamOptions) {
  const [job, setJob] = useState<JobRead | null>(null);
  const [events, setEvents] = useState<JobEventRead[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const lastSeqRef = useRef<number>(0);
  const isTerminalRef = useRef(false);
  const abortControllerRef = useRef<AbortController | null>(null);

  const checkJobStatus = useCallback(
    async (id: string) => {
      try {
        const currentJob = await apiClient.getJob(id);
        setJob(currentJob);

        if (isTerminalStatus(currentJob.status)) {
          isTerminalRef.current = true;
          setIsStreaming(false);
          if (currentJob.status === 'FAILED') {
            onFailed?.(currentJob.error_message || 'Job execution failed');
          } else {
            onCompleted?.(currentJob);
          }
        }
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : 'Unable to read job status.';
        setError(msg);
      }
    },
    [onCompleted, onFailed]
  );

  useEffect(() => {
    if (!jobId) {
      setJob(null);
      setEvents([]);
      setIsStreaming(false);
      isTerminalRef.current = false;
      lastSeqRef.current = 0;
      return;
    }

    isTerminalRef.current = false;
    setIsStreaming(true);
    setError(null);

    // DEMO MODE SAFEGUARD: Check if this is a demo job from demoStore
    const demoJob = demoStore.getJob(jobId);
    if (demoJob || (typeof sessionStorage !== 'undefined' && sessionStorage.getItem('argus_workspace_mode') === 'demo')) {
      let isMounted = true;
      const demoTicker = setInterval(() => {
        if (!isMounted) return;
        const currentDemoJob = demoStore.getJob(jobId);
        const currentDemoEvents = demoStore.getJobEvents(jobId);
        if (currentDemoJob) {
          setJob(currentDemoJob);
        }
        setEvents(currentDemoEvents);

        if (currentDemoJob && isTerminalStatus(currentDemoJob.status)) {
          isTerminalRef.current = true;
          setIsStreaming(false);
          clearInterval(demoTicker);
          if (currentDemoJob.status === 'FAILED') {
            onFailed?.(currentDemoJob.error_message || 'Demo job execution failed');
          } else {
            onCompleted?.(currentDemoJob);
          }
        }
      }, 200);

      return () => {
        isMounted = false;
        clearInterval(demoTicker);
      };
    }

    // Initial check for real backend job
    checkJobStatus(jobId);

    // Abort previous stream
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const abortController = new AbortController();
    abortControllerRef.current = abortController;

    // Start fetch-based authenticated SSE streaming
    async function startStream() {
      const token = getAuthToken();
      const baseUrl = getApiBaseUrl();
      const url = `${baseUrl}/api/v1/jobs/${encodeURIComponent(jobId!)}/events`;

      const headers: Record<string, string> = {
        Accept: 'text/event-stream',
      };
      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }
      if (lastSeqRef.current > 0) {
        headers['Last-Event-ID'] = String(lastSeqRef.current);
      }

      try {
        const response = await fetch(url, {
          headers,
          signal: abortController.signal,
        });

        if (!response.ok || !response.body) {
          throw new Error(`SSE stream connection failed: HTTP ${response.status}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (!isTerminalRef.current) {
          const { value, done } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            const trimmed = line.trim();
            if (trimmed.startsWith('data:')) {
              try {
                const jsonStr = trimmed.slice(5).trim();
                if (jsonStr) {
                  const eventData: JobEventRead = JSON.parse(jsonStr);
                  if (eventData.progress !== undefined) {
                    lastSeqRef.current = Math.max(lastSeqRef.current, eventData.progress);
                  }
                  setEvents((prev) => {
                    if (prev.some((existing) => existing.id === eventData.id)) {
                      return prev;
                    }
                    return [...prev, eventData];
                  });

                  if (isTerminalStatus(eventData.status)) {
                    isTerminalRef.current = true;
                    setIsStreaming(false);
                    abortController.abort();
                    checkJobStatus(jobId!);
                  }
                }
              } catch {
                // Ignore SSE line parse errors
              }
            }
          }
        }
      } catch {
        if (!abortController.signal.aborted) {
          // Fallback gracefully to polling
        }
      }
    }

    startStream();

    // Polling fallback ticker
    const interval = setInterval(() => {
      if (!isTerminalRef.current) {
        checkJobStatus(jobId);
      }
    }, pollingFallbackIntervalMs);

    return () => {
      abortController.abort();
      clearInterval(interval);
    };
  }, [jobId, checkJobStatus, pollingFallbackIntervalMs, onCompleted, onFailed]);

  const currentStage: JobStage =
    job?.current_stage ?? (events.length > 0 ? events[events.length - 1].stage : 'UPLOAD');

  return {
    job,
    events,
    isStreaming,
    error,
    progress: job?.progress ?? (events.length > 0 ? events[events.length - 1].progress : 0),
    currentStage,
    isCompleted: job?.status === 'COMPLETED',
    isFailed: job?.status === 'FAILED',
    isReviewRequired: job?.status === 'REVIEW_REQUIRED',
    isTerminal: isTerminalStatus(job?.status),
  };
}
