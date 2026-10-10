import { useEffect, useState } from 'react';
import { eventsUrl } from '../api/client';
import type { ProgressEvent } from '../api/types';

export function useJobStream(jobId: string, active: boolean, onTerminal: () => void) {
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [streamState, setStreamState] = useState<'idle' | 'live' | 'reconnecting'>('idle');

  useEffect(() => { setEvents([]); setStreamState('idle'); }, [jobId]);

  useEffect(() => {
    if (!jobId || !active) { setStreamState('idle'); return; }
    const source = new EventSource(eventsUrl(jobId));
    source.onopen = () => setStreamState('live');
    source.onerror = () => setStreamState('reconnecting');
    const onProgress = (event: MessageEvent) => {
      try {
        const update = JSON.parse(event.data) as ProgressEvent;
        if (update.job_id !== jobId) return;
        setEvents(previous => {
          const key = `${update.ts}|${update.stage}|${update.percent}|${update.message}`;
          if (previous.some(item => `${item.ts}|${item.stage}|${item.percent}|${item.message}` === key)) return previous;
          return [...previous, update].slice(-40);
        });
        if (update.terminal) { source.close(); setStreamState('idle'); onTerminal(); }
      } catch { /* Ignore malformed events; the next event/poll will refresh state. */ }
    };
    source.addEventListener('progress', onProgress as EventListener);
    return () => { source.removeEventListener('progress', onProgress as EventListener); source.close(); };
  }, [jobId, active, onTerminal]);

  return { events, streamState };
}
