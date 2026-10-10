import ReactMarkdown, { defaultUrlTransform } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { resolveAssetUrl } from '../api/client';
import MermaidDiagram from './MermaidDiagram';

interface Props { markdown: string; jobId: string; mode: 'rendered' | 'source' }

export function stripFrontmatter(markdown: string): string {
  return markdown.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, '');
}

function stripReviewMarkers(markdown: string): string {
  // Keep stable edit anchors in raw source/ZIP, but hide them in rendered previews.
  return markdown.replace(/<!-- pdf2md-review:[A-Za-z0-9_-]+:(?:start|end) -->/g, '');
}

export default function MarkdownPreview({ markdown, jobId, mode }: Props) {
  if (mode === 'source') return <pre className="markdown-source"><code>{markdown}</code></pre>;
  return <article className="markdown-body">
    <ReactMarkdown remarkPlugins={[remarkGfm]}
      urlTransform={url => resolveAssetUrl(url, jobId) || (/^(?:https?:|#)/i.test(url) ? defaultUrlTransform(url) : '')}
      components={{
        pre({ children }) { return <>{children}</>; },
        code({ className, children }) {
          const code = String(children).replace(/\n$/, '');
          if (className?.includes('language-mermaid')) return <MermaidDiagram chart={code} />;
          if (className?.startsWith('language-')) return <pre className="code-fence"><code>{code}</code></pre>;
          return <code>{children}</code>;
        },
        img({ src, alt }) { return <img src={src} alt={alt || 'Extracted book illustration'} loading="lazy" />; },
        a({ href, children }) { return href ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a> : <span>{children}</span>; },
      }}>
      {stripReviewMarkers(stripFrontmatter(markdown))}
    </ReactMarkdown>
  </article>;
}
