import { cleanup, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, describe, expect, it, vi } from 'vitest';
import MermaidDiagram from './MermaidDiagram';

const parse = vi.fn();
const renderDiagram = vi.fn();
vi.mock('mermaid', () => ({ default: { initialize: vi.fn(), parse, render: renderDiagram } }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe('Mermaid rendering', () => {
  it('sanitizes generated SVG before insertion into the DOM', async () => {
    parse.mockResolvedValue(true);
    renderDiagram.mockResolvedValue({ svg: '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><text>Safe label</text></svg>' });
    const { container } = render(<MermaidDiagram chart={'flowchart TD\nA-->B'} />);
    await waitFor(() => expect(screen.getByRole('img', { name: 'Rendered Mermaid diagram' })).toBeInTheDocument());
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('svg text')?.textContent).toBe('Safe label');
  });

  it('falls back to source code when a diagram cannot be parsed', async () => {
    parse.mockRejectedValue(new Error('invalid syntax'));
    render(<MermaidDiagram chart={'flowchart TD\nnot valid'} />);
    await waitFor(() => expect(screen.getByText(/Couldn't render this diagram/)).toBeInTheDocument());
    expect(screen.getByText(/not valid/)).toBeInTheDocument();
  });
});
