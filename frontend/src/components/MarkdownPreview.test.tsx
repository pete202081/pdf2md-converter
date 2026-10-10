import { render, screen, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, describe, expect, it, vi } from 'vitest';
import MarkdownPreview, { stripFrontmatter } from './MarkdownPreview';

vi.mock('./MermaidDiagram', () => ({ default: ({ chart }: { chart: string }) => <div data-testid="diagram">{chart}</div> }));
afterEach(cleanup);

describe('chapter preview', () => {
  it('renders tables, diagram blocks, and job-scoped image URLs', () => {
    const markdown = `# Introduction

| Name | Value |
| --- | --- |
| A | B |

![Figure](../assets/img_p3_x42.png)

\`\`\`mermaid
flowchart TD
A --> B
\`\`\`

[Original image](../assets/img_p3_x42.png)`;
    render(<MarkdownPreview jobId="job-1" mode="rendered" markdown={markdown} />);
    expect(screen.getByRole('heading', { name: 'Introduction' })).toBeInTheDocument();
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Figure' })).toHaveAttribute('src', '/api/v1/jobs/job-1/assets/img_p3_x42.png');
    expect(screen.getByTestId('diagram')).toHaveTextContent('flowchart TD');
    expect(screen.getByRole('link', { name: 'Original image' })).toHaveAttribute('href', '/api/v1/jobs/job-1/assets/img_p3_x42.png');
  });

  it('displays raw Markdown as text instead of executing it', () => {
    render(<MarkdownPreview jobId="job-1" mode="source" markdown={'<script>alert(1)</script>\n# Title'} />);
    expect(screen.queryByRole('heading', { name: 'Title' })).not.toBeInTheDocument();
    expect(screen.getByText(/<script>alert/)).toBeInTheDocument();
  });

  it('does not render raw HTML or javascript links from Markdown', () => {
    const { container } = render(<MarkdownPreview jobId="job-1" mode="rendered"
      markdown={'<img src=x onerror=alert(1)>\n\n[bad](javascript:alert(1))'} />);
    expect(container.querySelector('img')).toBeNull();
    expect(screen.queryByRole('link', { name: 'bad' })).not.toBeInTheDocument();
    expect(screen.getByText('bad')).toBeInTheDocument();
  });
});

it('hides YAML metadata in rendered preview but keeps it in source', () => {
  const markdown = '---\ntitle: "Hello"\norder: 1\n---\n\n# Hello';
  expect(stripFrontmatter(markdown)).toBe('\n# Hello');
  const { rerender } = render(<MarkdownPreview jobId="job-1" mode="rendered" markdown={markdown} />);
  expect(screen.getByRole('heading', { name: 'Hello' })).toBeInTheDocument();
  expect(screen.queryByText(/title:/)).not.toBeInTheDocument();
  rerender(<MarkdownPreview jobId="job-1" mode="source" markdown={markdown} />);
  expect(screen.getByText(/title: "Hello"/)).toBeInTheDocument();
});

it('keeps review markers invisible without hiding the fallback image', () => {
  const markdown = '<!-- pdf2md-review:abc:start -->\n![Figure](../assets/img_p3_x42.png)\n<!-- pdf2md-review:abc:end -->';
  const { container } = render(<MarkdownPreview jobId="job-1" mode="rendered" markdown={markdown} />);
  expect(screen.getByRole('img', { name: 'Figure' })).toHaveAttribute('src', '/api/v1/jobs/job-1/assets/img_p3_x42.png');
  expect(container.textContent).not.toContain('pdf2md-review:');
});
