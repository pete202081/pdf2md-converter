import { useCallback, useEffect, useRef, useState } from 'react';
import { getJob, listJobs } from '../api/client';
import type { Job, JobDetail } from '../api/types';

const POLL_MS = 5000; // SSE is primary; polling catches missed events / worker crashes.

export function useJobs() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedId, setSelectedId] = useState(() => {
    try { return localStorage.getItem('folio-selected-job') || ''; } catch { return ''; }
  });
  const selectedRef = useRef(selectedId);
  selectedRef.current = selectedId;
  const [detail, setDetail] = useState<JobDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [connection, setConnection] = useState<'checking' | 'online' | 'offline'>('checking');
  const [loading, setLoading] = useState(true);

  const selectJob = useCallback((id: string) => {
    selectedRef.current = id;
    setSelectedId(id);
    setDetail(null);
    try { localStorage.setItem('folio-selected-job', id); } catch { /* private browsing */ }
  }, []);

  const refreshJobs = useCallback(async () => {
    try {
      const items = await listJobs();
      setJobs(items);
      setConnection('online');
      setError(null);
      if (!items.some(item => item.id === selectedRef.current)) {
        selectJob(items[0]?.id || '');
      }
    } catch (cause) {
      setConnection('offline');
      setError(cause instanceof Error ? cause.message : 'Could not load jobs.');
    } finally {
      setLoading(false);
    }
  }, [selectJob]);

  const refreshDetail = useCallback(async () => {
    if (!selectedId) return;
    const id = selectedId;
    try {
      const updated = await getJob(id);
      if (selectedRef.current === id) setDetail(updated);
    } catch (cause) {
      if (selectedRef.current === id) setError(cause instanceof Error ? cause.message : 'Could not load job.');
    }
  }, [selectedId]);

  useEffect(() => {
    void refreshJobs();
    const timer = window.setInterval(() => void refreshJobs(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [refreshJobs]);

  useEffect(() => {
    if (!selectedId) return;
    void refreshDetail();
    const timer = window.setInterval(() => void refreshDetail(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [selectedId, refreshDetail]);

  return { jobs, selectedId, selectJob, detail, error, setError,
    connection, loading, refreshJobs, refreshDetail };
}
