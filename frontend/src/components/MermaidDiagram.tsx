import { useEffect, useId, useState } from 'react';
import DOMPurify from 'dompurify';
import { AlertCircle, Workflow } from 'lucide-react';

export default function MermaidDiagram({ chart, onValidationChange }: { chart: string; onValidationChange?: (valid: boolean) => void }) {
  const id = `diagram-${useId().replace(/[^a-zA-Z0-9-]/g, '')}`;
  const [svg, setSvg] = useState('');
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setSvg('');
    setError(false);
    onValidationChange?.(false);
    async function render() {
      try {
        // Import only when a chapter actually contains a Mermaid block.
        const { default: mermaid } = await import('mermaid');
        mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'neutral',
          flowchart: { htmlLabels: false, useMaxWidth: true }, suppressErrorRendering: true });
        const parsed = await mermaid.parse(chart);
        if (!parsed) throw new Error('Invalid Mermaid syntax');
        if (!cancelled) onValidationChange?.(true);
        const result = await mermaid.render(id, chart);
        if (!cancelled) setSvg(DOMPurify.sanitize(result.svg, { USE_PROFILES: { svg: true, svgFilters: true } }));
      } catch {
        if (!cancelled) { setError(true); onValidationChange?.(false); }
      }
    }
    void render();
    return () => { cancelled = true; };
  }, [chart, id, onValidationChange]);

  return <figure className="mermaid-figure">
    <figcaption><Workflow size={14} /> MERMAID DIAGRAM <span>RENDERED FROM PDF</span></figcaption>
    {error ? <div className="mermaid-error"><AlertCircle size={17} /> Couldn't render this diagram. The source is below.<pre>{chart}</pre></div>
      : svg ? <div className="mermaid-svg" role="img" aria-label="Rendered Mermaid diagram"
          dangerouslySetInnerHTML={{ __html: svg }} />
        : <div className="mermaid-loading">Rendering diagram…</div>}
  </figure>;
}
