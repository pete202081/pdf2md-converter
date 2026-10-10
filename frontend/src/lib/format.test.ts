import { describe, expect, it } from 'vitest';
import { canDownload, formatBytes, formatDate, isTerminal, prettyStage, statusLabel } from './format';

describe('job presentation', () => {
  it('treats PARTIAL as finished and downloadable, not FAILED', () => {
    expect(isTerminal('PARTIAL')).toBe(true);
    expect(canDownload('PARTIAL')).toBe(true);
    expect(canDownload('FAILED')).toBe(false);
    expect(isTerminal('RUNNING')).toBe(false);
  });
  it('formats values safely', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(1536)).toBe('1.5 KB');
    expect(formatBytes(1048576)).toBe('1.0 MB');
    expect(formatDate('not-a-date')).toBe('—');
    expect(prettyStage('convert_mermaid')).toBe('Convert Mermaid');
    expect(statusLabel('PARTIAL')).toBe('Needs review');
    expect(statusLabel('PARTIAL', 18, 0)).toBe('Review complete');
    expect(statusLabel('PARTIAL', 18, 2)).toBe('Needs review');
  });
});
