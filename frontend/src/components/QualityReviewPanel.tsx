import { useEffect, useState } from 'react';
import { getEditableChapter, getQualitySummary, reviewQualityPage, saveQualityChapter, sourcePageUrl, undoQualityChapter } from '../api/client';
import type { EditableChapter, QualitySummary } from '../api/types';
import MarkdownPreview from './MarkdownPreview';

interface Props { jobId: string; initialChapterOrder: number | null; onChanged: () => void }

export default function QualityReviewPanel({ jobId, initialChapterOrder, onChanged }: Props) {
  const [summary, setSummary] = useState<QualitySummary | null>(null);
  const [order, setOrder] = useState<number | null>(initialChapterOrder);
  const [page, setPage] = useState(1);
  const [chapter, setChapter] = useState<EditableChapter | null>(null);
  const [draft, setDraft] = useState('');
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [imageError, setImageError] = useState(false);
  const mapping = summary?.chapters.find(item => item.order === order);
  const pages = mapping?.source_pages;
  const pageFlags = summary?.flags.filter(flag => flag.chapter_order === order && flag.page_number === page) || [];
  const pendingPages = new Set(summary?.flags.filter(flag => !flag.reviewed_at)
    .map(flag => `${flag.chapter_order}:${flag.page_number}`)).size;

  useEffect(() => { setOrder(initialChapterOrder); }, [initialChapterOrder]);
  useEffect(() => {
    let active = true;
    setSummary(null); setError(''); setMessage('');
    getQualitySummary(jobId).then(result => {
      if (!active) return;
      setSummary(result);
      setOrder(current => current && result.chapters.some(item => item.order === current)
        ? current : result.chapters[0]?.order ?? null);
    }).catch(cause => { if (active) setError(cause instanceof Error ? cause.message : 'Could not load quality review.'); });
    return () => { active = false; };
  }, [jobId]);
  useEffect(() => {
    if (!pages) { setChapter(null); return; }
    let active = true;
    setChapter(null); setDraft(''); setEditing(false); setImageError(false); setError('');
    setPage(pages[0]);
    getEditableChapter(jobId, order!).then(result => {
      if (active) { setChapter(result); setDraft(result.markdown); }
    }).catch(cause => { if (active) setError(cause instanceof Error ? cause.message : 'Could not load chapter.'); });
    return () => { active = false; };
  }, [jobId, order, pages?.[0], pages?.[1]]);

  async function reloadChapter() {
    if (order === null) return;
    setError('');
    try { const latest = await getEditableChapter(jobId, order); setChapter(latest); setDraft(latest.markdown); setEditing(false); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not reload chapter.'); }
  }
  async function save() {
    if (order === null || !chapter || !editing || draft === chapter.markdown) return;
    setBusy(true); setError(''); setMessage('');
    try {
      await saveQualityChapter(jobId, order, chapter.hash, draft);
      await reloadChapter(); setSummary(await getQualitySummary(jobId));
      onChanged(); setMessage('Chapter saved. The updated ZIP is ready to download.');
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not save chapter.'); }
    finally { setBusy(false); }
  }
  async function undo() {
    if (order === null || !chapter || !window.confirm('Undo the last Markdown edit to this chapter?')) return;
    setBusy(true); setError(''); setMessage('');
    try {
      await undoQualityChapter(jobId, order, chapter.hash);
      await reloadChapter(); setSummary(await getQualitySummary(jobId));
      onChanged(); setMessage('Last Markdown edit undone; ZIP updated.');
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not undo edit.'); }
    finally { setBusy(false); }
  }
  async function markReviewed() {
    setBusy(true); setError('');
    try { setSummary(await reviewQualityPage(jobId, page)); setMessage(`Page ${page} marked reviewed.`); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not mark page reviewed.'); }
    finally { setBusy(false); }
  }
  return <section className="quality-review" aria-label="Conversion quality review">
    <div className="quality-heading"><div><h3>Compare &amp; correct</h3><p>Review the source page beside its chapter. Hints are rules, not OCR accuracy scores.</p></div>
      {summary?.inventory_ready && <strong>{pendingPages} flagged page{pendingPages === 1 ? '' : 's'} awaiting review</strong>}</div>
    {error && <div className="notice notice-red" role="alert">{error}</div>}
    {message && <div className="notice notice-green" role="status">{message}</div>}
    {!summary && !error && <p>Loading quality review…</p>}
    {summary && !summary.inventory_ready && <p className="quality-note">This older conversion has no quality-warning inventory. Re-upload the PDF to generate review hints; source pages may still be available below.</p>}
    {summary && summary.chapters.length === 0 && <p className="quality-note">No source-page mapping is available for this conversion. Re-upload the PDF to compare and edit chapters.</p>}
    {summary && summary.chapters.length > 0 && <>
      <div className="quality-controls"><label>Chapter <select aria-label="Quality chapter" value={order ?? ''} onChange={event => setOrder(Number(event.target.value))}>
        {summary.chapters.map(item => <option key={item.order} value={item.order}>Chapter {item.order}</option>)}</select></label>
        {pages && <><button type="button" className="secondary-button" disabled={page <= pages[0]} onClick={() => { setPage(page - 1); setImageError(false); }}>Previous page</button>
          <span>Page {page} of {pages[0]}–{pages[1]}</span>
          <button type="button" className="secondary-button" disabled={page >= pages[1]} onClick={() => { setPage(page + 1); setImageError(false); }}>Next page</button></>}
      </div>
      {pages && <div className="quality-page-list" aria-label="Source pages">{Array.from({ length: pages[1] - pages[0] + 1 }, (_, i) => pages[0] + i).map(p => {
        const flagged = summary.flags.some(flag => flag.chapter_order === order && flag.page_number === p && !flag.reviewed_at);
        return <button key={p} type="button" aria-label={`Page ${p}${flagged ? ' needs review' : ''}`} aria-current={page === p ? 'page' : undefined} className={page === p ? 'quality-page-active' : ''} onClick={() => { setPage(p); setImageError(false); }}>{p}{flagged ? ' •' : ''}</button>;
      })}</div>}
      {pages && <div className="quality-split"><div className="quality-source"><h4>Original PDF · page {page}</h4>
        {imageError ? <div role="alert" className="quality-note">Source page unavailable. Check the stored PDF and try again.</div>
          : <img src={sourcePageUrl(jobId, order!, page)} alt={`Original PDF page ${page}`} onError={() => setImageError(true)} />}
        {pageFlags.length > 0 && <div className="quality-flags"><h4>Review hints</h4>
          {pageFlags.map(flag => <p key={flag.id}>{flag.reviewed_at ? '✓ ' : '⚠ '} {flag.message}</p>)}
          {summary.inventory_ready && pageFlags.some(flag => !flag.reviewed_at) && <button type="button" className="secondary-button" disabled={busy} onClick={() => void markReviewed()}>Mark page reviewed</button>}
        </div>}
      </div><div className="quality-chapter"><div className="quality-chapter-head"><h4>Chapter Markdown</h4><div className="quality-buttons">
        {!editing && <button type="button" className="secondary-button" disabled={!chapter || busy} onClick={() => setEditing(true)}>Edit text &amp; tables</button>}
        {chapter?.can_undo && !editing && <button type="button" className="secondary-button" disabled={busy} onClick={() => void undo()}>Undo last edit</button>}
      </div></div>
      {editing ? <><p className="quality-note">Keep the metadata at the top and diagram-review blocks unchanged. Reload discards unsaved changes.</p>
        <label htmlFor="quality-editor">Markdown source</label><textarea id="quality-editor" value={draft} onChange={event => setDraft(event.target.value)} rows={16} maxLength={2 * 1024 * 1024} />
        <div className="quality-buttons"><button type="button" className="download-button" disabled={busy || draft === chapter?.markdown} onClick={() => void save()}>Save chapter &amp; ZIP</button>
          <button type="button" className="secondary-button" disabled={busy} onClick={() => void reloadChapter()}>Discard changes / reload</button></div>
        <h4>Preview changes</h4><MarkdownPreview markdown={draft} jobId={jobId} mode="rendered" /></>
        : chapter ? <MarkdownPreview markdown={chapter.markdown} jobId={jobId} mode="rendered" /> : <p>Loading chapter…</p>}
      </div></div>}
    </>}
  </section>;
}
