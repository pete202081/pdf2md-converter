import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useJobStream } from './useJobStream';

const sources: FakeEventSource[] = [];
class FakeEventSource {
  handlers = new Map<string, (event: MessageEvent) => void>();
  closed = false;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public readonly url: string) { sources.push(this); }
  addEventListener(type: string, handler: (event: MessageEvent) => void) { this.handlers.set(type, handler); }
  removeEventListener(type: string) { this.handlers.delete(type); }
  close() { this.closed = true; }
  emit(data: unknown) { this.handlers.get('progress')?.({ data: JSON.stringify(data) } as MessageEvent); }
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); sources.length = 0; });

describe('SSE progress stream', () => {
  it('deduplicates replayed events and closes on terminal update', () => {
    vi.stubGlobal('EventSource', FakeEventSource);
    const done = vi.fn();
    const { result, unmount } = renderHook(() => useJobStream('job-1', true, done));
    expect(sources[0].url).toBe('/api/v1/jobs/job-1/events');
    act(() => sources[0].onopen?.());
    expect(result.current.streamState).toBe('live');
    const progress = { job_id: 'job-1', stage: 'extract_text', percent: 45, message: 'Text extracted', ts: '2026-10-09T00:00:00Z', terminal: false };
    act(() => { sources[0].emit(progress); sources[0].emit(progress); });
    expect(result.current.events).toHaveLength(1);
    act(() => sources[0].emit({ ...progress, stage: 'orchestrator', percent: 100, terminal: true }));
    expect(done).toHaveBeenCalledTimes(1);
    expect(sources[0].closed).toBe(true);
    unmount();
  });

  it('reports reconnecting on SSE errors; polling can still refresh', () => {
    vi.stubGlobal('EventSource', FakeEventSource);
    const { result } = renderHook(() => useJobStream('job-1', true, vi.fn()));
    act(() => sources[0].onerror?.());
    expect(result.current.streamState).toBe('reconnecting');
  });
});
