import { useEffect, useState } from 'react';
import { AlertCircle, Check, ChevronRight, PencilLine } from 'lucide-react';
import { decideDiagramReview, getAuthSession, listDiagramReviews, resolveAssetUrl } from '../api/client';
import type { DiagramReviewItem } from '../api/types';
import MermaidDiagram from './MermaidDiagram';

interface Props { jobId: string; onSelectChapter: (order: number) => void; onChanged: () => void }

export default function DiagramReviewPanel({ jobId, onSelectChapter, onChanged }: Props) {
  const [items, setItems] = useState<DiagramReviewItem[]>([]);
  const [csrf, setCsrf] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState<string | null>(null);
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [codeValid, setCodeValid] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setItems([]); setError(''); setEditing(null);
    void Promise.all([listDiagramReviews(jobId, controller.signal), getAuthSession()])
      .then(([reviews, session]) => { if (!controller.signal.aborted) { setItems(reviews); setCsrf(session.authenticated ? session.csrf_token : null); } })
      .catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Could not load reviews.'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [jobId]);
  async function decide(item: DiagramReviewItem, decision: 'ACCEPTED_IMAGE' | 'EDITED_MERMAID') {
    if (!csrf) { setError('Sign in to your approved account before saving a review.'); return; }
    setBusy(true); setError('');
    try {
      const changed = await decideDiagramReview(jobId, item.id, decision, csrf, decision === 'EDITED_MERMAID' ? code : undefined);
      setItems(previous => previous.map(review => review.id === item.id ? changed : review));
      setEditing(null); setCode(''); onChanged();
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not save review.'); }
    finally { setBusy(false); }
  }
  const pending = items.filter(item => item.decision === 'PENDING').length;
  const accepted = items.filter(item => item.decision === 'ACCEPTED_IMAGE').length;
  const edited = items.filter(item => item.decision === 'EDITED_MERMAID').length;
  return <section className="diagram-review" aria-label="Diagram review queue">
    <div className="diagram-review-head"><div><h3>Review fallback diagrams</h3><p>{loading ? 'Loading…' : items.length ? `${items.length - pending} of ${items.length} reviewed · ${accepted} images accepted · ${edited} Mermaid edits` : 'No item-level review records for this job.'}</p></div></div>
    {error && <div className="notice notice-red" role="alert"><AlertCircle size={16} />{error}</div>}
    {!loading && !items.length && <p className="review-legacy">This conversion predates item-level tracking. Re-upload the PDF to review each fallback diagram.</p>}
    {!loading && items.length > 0 && !csrf && <p className="review-signin">Sign in to your approved account to save decisions.</p>}
    {items.map(item => <article className="review-item" key={item.id}>
      <div className="review-item-image"><img src={resolveAssetUrl(`assets/${item.asset_name}`, jobId) || ''} alt={`Original diagram from PDF page ${item.page_number}`} loading="lazy" /></div>
      <div className="review-item-body"><div className="review-item-title"><strong>Chapter {item.chapter_order} · PDF page {item.page_number}{item.occurrence_index ? ` · candidate #${item.occurrence_index}` : ''}</strong>
        <span className={`review-decision review-${item.decision.toLowerCase()}`}>{item.decision === 'PENDING' ? 'Pending' : item.decision === 'ACCEPTED_IMAGE' ? 'Original accepted' : 'Mermaid edited'}</span></div>
        <button className="review-link" type="button" onClick={() => onSelectChapter(item.chapter_order)}>Open chapter <ChevronRight size={14} /></button>
        {csrf && <div className="review-actions"><button type="button" disabled={busy} onClick={() => void decide(item, 'ACCEPTED_IMAGE')}><Check size={14} /> Accept original image</button>
          <button type="button" disabled={busy} onClick={() => { setEditing(item.id); setCodeValid(false); setCode(item.mermaid_code || 'flowchart TD\nA[Start] --> B[Finish]'); }}><PencilLine size={14} /> {item.decision === 'EDITED_MERMAID' ? 'Revise Mermaid' : 'Edit as Mermaid'}</button></div>}
        {editing === item.id && <div className="review-editor"><label>Mermaid source<textarea aria-label={`Mermaid source for page ${item.page_number}`} value={code} onChange={event => { setCodeValid(false); setCode(event.target.value); }} rows={7} maxLength={8000} /></label>
          <MermaidDiagram chart={code} onValidationChange={setCodeValid} /><div className="review-actions"><button type="button" disabled={busy || !codeValid} onClick={() => void decide(item, 'EDITED_MERMAID')}>Save Mermaid &amp; update ZIP</button><button type="button" onClick={() => setEditing(null)}>Cancel</button></div></div>}
      </div>
    </article>)}
  </section>;
}
