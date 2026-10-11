import { useEffect, useState } from 'react';
import {
  AlertCircle, ArrowDownToLine, ArrowRight, BookOpen, Check, CheckCircle2, ChevronRight,
  Clock3, Code2, Copy, FileText, Layers3, LoaderCircle, MoreHorizontal, RefreshCw,
  Sparkles, Trash2, Workflow,
} from 'lucide-react';
import { ApiError, downloadUrl, getChapterForEdit, getChapterMarkdown, saveChapterEdit, undoChapterEdit } from '../api/client';
import type { EditableChapter, Job, JobDetail, LibrarySearchHit, ProgressEvent } from '../api/types';
import { canDownload, formatDate, isTerminal, prettyStage, statusLabel } from '../lib/format';
import MarkdownPreview from './MarkdownPreview';
import DiagramReviewPanel from './DiagramReviewPanel';
import QualityReviewPanel from './QualityReviewPanel';

interface Props {
  job: Job | null;
  detail: JobDetail | null;
  events: ProgressEvent[];
  streamState: 'idle' | 'live' | 'reconnecting';
  searchTarget?: { hit: LibrarySearchHit; source: boolean; key: number } | null;
  onDelete: (id: string) => void;
  onStop: (id: string) => void;
  onRefresh: () => void;
  onDirtyChange?: (dirty: boolean) => void;
}

export default function JobWorkspace({ job, detail, events, streamState, searchTarget, onDelete, onStop, onRefresh, onDirtyChange }: Props) {
  const [order, setOrder] = useState<number | null>(null);
  const [markdown, setMarkdown] = useState('');
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const [mode, setMode] = useState<'rendered' | 'source'>('rendered');
  const [copied, setCopied] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [qualityOpen, setQualityOpen] = useState(false);
  const [previewRevision, setPreviewRevision] = useState(0);
  const [editing, setEditing] = useState(false);
  const [editVersion, setEditVersion] = useState<EditableChapter | null>(null);
  const [draft, setDraft] = useState('');
  const [editBusy, setEditBusy] = useState(false);
  const [editError, setEditError] = useState('');
  const [editMessage, setEditMessage] = useState('');
  const dirty = editing && editVersion !== null && draft !== editVersion.markdown;

  const chapters = [...(detail?.chapters ?? [])].sort((a, b) => a.order_index - b.order_index);
  const selectedChapter = chapters.find(chapter => chapter.order_index === order);
  const latest = events.at(-1);
  const percent = isTerminal(job?.status ?? 'QUEUED') ? 100 : latest?.percent ?? (job?.status === 'RUNNING' ? 8 : 0);

  useEffect(() => {
    onDirtyChange?.(dirty);
    return () => onDirtyChange?.(false);
  }, [dirty, onDirtyChange]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);
  useEffect(() => { setOrder(null); setMarkdown(''); setMode('rendered'); setReviewOpen(false); setQualityOpen(false); setEditing(false); setEditVersion(null); setEditError(''); }, [job?.id]);
  useEffect(() => {
    if (job?.id && searchTarget?.hit.job_id === job.id) {
      if (dirty && !window.confirm('Discard unsaved chapter changes?')) return;
      setEditing(false);
      setOrder(searchTarget.hit.chapter_order);
      setQualityOpen(searchTarget.source);
    }
  }, [job?.id, searchTarget?.key]);
  useEffect(() => {
    if (chapters.length && !chapters.some(chapter => chapter.order_index === order)) {
      setOrder(chapters[0].order_index);
    }
  }, [detail?.chapters, order]);

  useEffect(() => {
    if (!job || order === null) { setMarkdown(''); return; }
    const controller = new AbortController();
    setPreviewLoading(true);
    setPreviewError('');
    setMarkdown('');
    getChapterMarkdown(job.id, order, controller.signal)
      .then(text => { if (!controller.signal.aborted) setMarkdown(text); })
      .catch(error => { if (!controller.signal.aborted) setPreviewError(error instanceof Error ? error.message : 'Preview unavailable.'); })
      .finally(() => { if (!controller.signal.aborted) setPreviewLoading(false); });
    return () => controller.abort();
  }, [job?.id, order, previewRevision]);

  function chooseChapter(next: number) {
    if (next === order) return;
    if (dirty && !window.confirm('Discard unsaved chapter changes?')) return;
    setEditing(false); setEditVersion(null); setEditError(''); setEditMessage('');
    setOrder(next);
  }

  async function beginEdit() {
    if (!job || order === null || editBusy) return;
    setEditBusy(true); setEditError(''); setEditMessage('');
    try {
      const current = await getChapterForEdit(job.id, order);
      setEditVersion(current); setDraft(current.markdown); setEditing(true);
    } catch (cause) {
      setEditError(cause instanceof Error ? cause.message : 'Could not open chapter editor.');
    } finally { setEditBusy(false); }
  }

  async function saveEdit() {
    if (!job || order === null || !editVersion || !dirty || editBusy) return;
    setEditBusy(true); setEditError(''); setEditMessage('');
    try {
      await saveChapterEdit(job.id, order, editVersion.hash, draft);
      setEditing(false);
      setEditMessage('Chapter saved. ZIP updated; search results follow this revision when indexing is available. Shelf copies are unchanged.');
      try {
        const latest = await getChapterForEdit(job.id, order);
        setEditVersion(latest); setDraft(latest.markdown); setMarkdown(latest.markdown);
      } catch {
        setEditError('Saved on the server, but the preview could not refresh. Try Refresh job.');
      }
      setPreviewRevision(value => value + 1); onRefresh();
    } catch (cause) {
      setEditError(cause instanceof ApiError && cause.status === 409
        ? 'Chapter changed elsewhere. Copy your draft before reloading the latest version.'
        : cause instanceof Error ? cause.message : 'Could not save chapter.');
    } finally { setEditBusy(false); }
  }

  async function undoEdit() {
    if (!job || order === null || !editVersion?.can_undo || editBusy) return;
    if (dirty && !window.confirm('Discard unsaved changes and undo the last saved edit?')) return;
    if (!window.confirm('Undo the last saved chapter edit?')) return;
    setEditBusy(true); setEditError(''); setEditMessage('');
    try {
      await undoChapterEdit(job.id, order, editVersion.hash);
      setEditing(false);
      setEditMessage('Last chapter edit undone; ZIP updated. Search results follow when indexing is available.');
      try {
        const latest = await getChapterForEdit(job.id, order);
        setEditVersion(latest); setDraft(latest.markdown); setMarkdown(latest.markdown);
      } catch {
        setEditError('Undo succeeded on the server, but the preview could not refresh. Try Refresh job.');
      }
      setPreviewRevision(value => value + 1); onRefresh();
    } catch (cause) { setEditError(cause instanceof Error ? cause.message : 'Could not undo edit.'); }
    finally { setEditBusy(false); }
  }

  function discardEdit() {
    if (dirty && !window.confirm('Discard unsaved chapter changes?')) return;
    setEditing(false); setEditVersion(null); setEditError('');
  }

  async function reloadLatest() {
    if (!job || order === null) return;
    if (dirty && !window.confirm('Discard your unsaved draft and load the latest chapter?')) return;
    setEditBusy(true); setEditError('');
    try {
      const current = await getChapterForEdit(job.id, order);
      setEditVersion(current); setDraft(current.markdown);
    } catch (cause) { setEditError(cause instanceof Error ? cause.message : 'Could not reload chapter.'); }
    finally { setEditBusy(false); }
  }

  async function copyMarkdown() {
    try {
      await navigator.clipboard.writeText(editing ? draft : markdown);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch { setCopied(false); }
  }

  if (!job) return <section className="workspace-empty card" aria-label="Workspace preview">
    <div className="empty-editor-top"><span className="editor-dots"><i /><i /><i /></span><span>CHAPTER PREVIEW</span><MoreHorizontal size={18} /></div>
    <div className="empty-editor-body">
      <div className="floating-page"><div className="page-fold" /><div className="page-kicker">A NEW KIND OF READING</div>
        <h3>Every page,<br/><em>a possibility.</em></h3><span className="mock-line wide" /><span className="mock-line" /><span className="mock-line medium" />
        <div className="mock-divider" /><span className="mock-line medium" /><span className="mock-line short" />
        <div className="mock-diagram"><span>PDF</span><i>→</i><span>MD</span><i>→</i><span>✳</span></div>
      </div>
      <div className="empty-editor-caption"><span className="caption-asterisk">✳</span><span>YOUR WORKSPACE AWAITS</span>
        <h2>The good stuff<br/>starts with <em>one upload.</em></h2>
        <p>Your chapters, diagrams, tables and images — thoughtfully converted and ready to make your own.</p>
        <span className="empty-editor-hint">Choose a PDF on the left to begin <ArrowRight size={15} /></span>
      </div>
    </div>
    <div className="empty-editor-footer"><span><BookOpen size={15} /> Chapter-aware</span><span><Workflow size={15} /> Diagram-ready</span><span><Layers3 size={15} /> Neatly organized</span></div>
  </section>;

  return <section className="workspace card" aria-label={`Conversion workspace for ${job.filename}`}>
    <div className="workspace-head">
      <div className="workspace-head-text"><div className="card-eyebrow"><span className="eyebrow-mark" /> CONVERSION WORKSPACE <span className="eyebrow-number">JOB / {job.id.slice(0, 8).toUpperCase()}</span></div>
        <h2 title={job.filename}>{job.filename}</h2>
        <div className="workspace-subtitle">Uploaded {formatDate(job.created_at)} <span className="sub-dot">·</span> {job.page_count || '—'} pages <span className="sub-dot">·</span> {job.chapter_count || chapters.length || '—'} chapters</div>
      </div>
      <span className={`status-pill pill-${job.status.toLowerCase()}`}>
        {job.status === 'RUNNING' ? <LoaderCircle className="spin" size={14} /> : job.status === 'FAILED' ? <AlertCircle size={14} /> : <span className="pill-dot" />}
        {job.cancel_requested_at && (job.status === 'QUEUED' || job.status === 'RUNNING') ? 'Stopping…' : statusLabel(job.status, job.review_total, job.review_pending)}
      </span>
    </div>

    <div className="job-progress" aria-live="polite">
      <div className="progress-heading"><span>{isTerminal(job.status) ? job.status === 'FAILED' ? 'Conversion failed' : 'Conversion complete' : (job.cancel_requested_at ? 'Stopping… Waiting for worker cleanup.' : latest?.message || (job.status === 'QUEUED' ? 'Waiting for a worker…' : 'Working through your book…'))}</span><strong>{percent}%</strong></div>
      <div className="track"><div style={{ width: `${Math.min(100, Math.max(0, percent))}%` }} /></div>
      <div className="progress-footer"><span>{streamState === 'live' && !isTerminal(job.status) ? <><span className="live-dot" /> LIVE UPDATES</> : streamState === 'reconnecting' ? 'RECONNECTING · POLLING CONTINUES' : isTerminal(job.status) ? 'ALL STAGES FINISHED' : 'UPDATES EVERY FEW SECONDS'}</span><span>{detail?.stages.filter(stage => stage.status === 'SUCCEEDED').length ?? 0} / {detail?.stages.length ?? '—'} stages</span></div>
    </div>

    {canDownload(job.status) && job.search_index_state && job.search_index_state !== 'READY' && <div className="notice notice-amber"><AlertCircle size={17} /><span>{job.search_index_state === 'ERROR' ? 'This book is not searchable right now.' : 'This book is awaiting search indexing.'} Your ZIP is still available; an administrator can run the search reindex command.</span></div>}
    {job.status === 'PARTIAL' && <div className="notice notice-amber"><AlertCircle size={17} /><span>{job.review_total ? (job.review_pending === 0 ? `Review complete for ${job.review_total} fallback diagrams.` : `${job.review_pending ?? job.review_total} of ${job.review_total} fallback diagrams awaiting review.`) : `${job.error || 'Some diagrams stayed as images.'} Re-upload this older PDF for item-level review.`} Conversion is complete; the ZIP remains downloadable.</span><button type="button" className="review-link" onClick={() => setReviewOpen(!reviewOpen)}>{reviewOpen ? 'Hide review' : 'Review diagrams'}</button></div>}
    {job.status === 'PARTIAL' && reviewOpen && <DiagramReviewPanel jobId={job.id} onSelectChapter={chooseChapter} onChanged={() => { setPreviewRevision(value => value + 1); onRefresh(); }} />}
    {job.status === 'FAILED' && <div className="notice notice-red"><AlertCircle size={17} /><span>{job.error || 'The conversion did not finish. Check the stages below.'}</span></div>}

    {qualityOpen && isTerminal(job.status) && job.status !== 'FAILED' && <QualityReviewPanel jobId={job.id} initialChapterOrder={order}
      initialPage={searchTarget?.source && searchTarget.hit.job_id === job.id ? searchTarget.hit.first_page : null}
      onChanged={() => { setPreviewRevision(value => value + 1); onRefresh(); }} />}

    <div className="workspace-toolbar">
      <div className="toolbar-label"><BookOpen size={17} /><span>THE BOOK</span><span className="toolbar-count">{chapters.length} CHAPTERS</span></div>
      <div className="toolbar-actions">
        {canDownload(job.status) && <button className="secondary-button" type="button" onClick={() => setQualityOpen(!qualityOpen)}>{qualityOpen ? 'Hide quality review' : 'Review quality'}</button>}
        <button className="icon-button" onClick={onRefresh} title="Refresh job" aria-label="Refresh job"><RefreshCw size={16} /></button>
        {(job.status === 'QUEUED' || job.status === 'RUNNING') && <button className="secondary-button danger-hover" type="button" onClick={() => onStop(job.id)} disabled={Boolean(job.cancel_requested_at)} aria-label="Stop conversion">{job.cancel_requested_at && (job.status === 'QUEUED' || job.status === 'RUNNING') ? 'Stopping…' : 'Stop conversion'}</button>}
        {isTerminal(job.status) && <button className="icon-button danger-hover" onClick={() => onDelete(job.id)} title="Delete job" aria-label="Delete job"><Trash2 size={16} /></button>}
        {canDownload(job.status) && <a className="download-button" href={downloadUrl(job.id)} download><ArrowDownToLine size={16} /> Download ZIP</a>}
      </div>
    </div>

    <div className="chapter-workspace">
      <aside className="chapter-sidebar" aria-label="Chapters">
        <div className="sidebar-label">TABLE OF CONTENTS <span>{String(chapters.length).padStart(2, '0')}</span></div>
        {chapters.length ? <nav className="chapter-nav">
          {chapters.map(chapter => <button key={chapter.order_index} type="button" onClick={() => chooseChapter(chapter.order_index)}
            className={`chapter-item ${order === chapter.order_index ? 'chapter-item-active' : ''}`} aria-current={order === chapter.order_index ? 'page' : undefined}>
            <span className="chapter-index">{String(chapter.order_index).padStart(2, '0')}</span>
            <span className="chapter-item-text"><strong>{chapter.title}</strong><small>{chapter.word_count.toLocaleString()} words {chapter.mermaid_count ? `· ${chapter.mermaid_count} diagrams` : ''}</small></span>
            <ChevronRight size={15} />
          </button>)}
        </nav> : <div className="chapters-pending"><LoaderCircle size={20} className={isTerminal(job.status) ? '' : 'spin'} /><span>{job.status === 'FAILED' ? 'No chapters were generated.' : 'Chapters will appear as your book is processed.'}</span></div>}
        {detail?.stages.length ? <div className="stage-history"><div className="sidebar-label">PIPELINE <span>{detail.stages.length} STEPS</span></div>
          {detail.stages.map(stage => <div className="stage-row" key={stage.id} title={stage.error || stage.stage_name}>
            {stage.status === 'SUCCEEDED' ? <CheckCircle2 size={14} className="stage-done" /> : stage.status === 'RUNNING' ? <LoaderCircle size={14} className="spin stage-active" /> : stage.status === 'FAILED' ? <AlertCircle size={14} className="stage-failed" /> : <Clock3 size={14} />}
            <span>{prettyStage(stage.stage_name)}</span>
          </div>)}
        </div> : null}
      </aside>

      <div className="preview-panel">
        <div className="preview-topbar"><div className="preview-title"><FileText size={16} /> {selectedChapter ? `${selectedChapter.folder_name}/chapter.md` : 'No chapter selected'}</div>
          {selectedChapter && <div className="preview-controls"><div className="view-toggle" role="group" aria-label="Preview mode"><button className={mode === 'rendered' ? 'active' : ''} onClick={() => setMode('rendered')} type="button">Preview</button><button className={mode === 'source' ? 'active' : ''} onClick={() => setMode('source')} type="button"><Code2 size={13} /> Source</button></div>
            {canDownload(job.status) && !editing && <button className="secondary-button" type="button" disabled={editBusy || previewLoading || Boolean(previewError)} onClick={() => void beginEdit()}>Edit chapter</button>}
            {editing && <button className="secondary-button" type="button" disabled={editBusy} onClick={discardEdit}>Close editor</button>}
            <button className="icon-button copy-button" type="button" onClick={() => void copyMarkdown()} title={editing ? "Copy draft Markdown" : "Copy Markdown"} aria-label={editing ? "Copy draft Markdown" : "Copy Markdown"}>{copied ? <Check size={16} /> : <Copy size={16} />}</button></div>}
        </div>
        {editError && <div className="notice notice-red" role="alert">{editError}</div>}
        {editMessage && <div className="notice notice-green" role="status">{editMessage}</div>}
        <div className="preview-scroll">
          {editing && editVersion ? <div className="direct-editor">
            <p>Clean up headings, text, tables, links and Mermaid. Keep generated metadata and diagram-review regions unchanged; edit those diagrams under Review diagrams.</p>
            <label htmlFor="direct-chapter-editor">Chapter Markdown</label>
            <textarea id="direct-chapter-editor" value={draft} onChange={event => setDraft(event.target.value)} maxLength={2 * 1024 * 1024} rows={18} disabled={editBusy} />
            <div className="direct-editor-actions">
              <button type="button" className="download-button" disabled={!dirty || editBusy} onClick={() => void saveEdit()}>{editBusy ? 'Saving…' : 'Save chapter & ZIP'}</button>
              <button type="button" className="secondary-button" disabled={editBusy} onClick={discardEdit}>Discard changes</button>
              <button type="button" className="secondary-button" disabled={editBusy} onClick={() => void reloadLatest()}>Reload latest</button>
              {editVersion.can_undo && <button type="button" className="secondary-button" disabled={editBusy} onClick={() => void undoEdit()}>Undo last edit</button>}
            </div>
            <h3>Live preview</h3><MarkdownPreview markdown={draft} jobId={job.id} mode="rendered" />
          </div> : !selectedChapter ? <div className="preview-placeholder"><Sparkles size={25} /><h3>Almost there.</h3><p>When chapters are ready, they’ll appear right here, beautifully arranged for reading.</p></div>
            : previewLoading ? <div className="preview-placeholder"><LoaderCircle className="spin" size={25} /><p>Opening chapter…</p></div>
            : previewError ? <div className="preview-placeholder"><AlertCircle size={25} /><h3>Preview unavailable</h3><p>{previewError}</p><button className="secondary-button" onClick={() => { setOrder(null); window.setTimeout(() => setOrder(selectedChapter.order_index), 0); }} type="button">Try again</button></div>
            : <MarkdownPreview markdown={markdown} jobId={job.id} mode={mode} />}
        </div>
        <div className="preview-bottom"><span><span className="tiny-spark">✳</span> {selectedChapter ? `CHAPTER ${String(selectedChapter.order_index).padStart(2, '0')} OF ${String(chapters.length).padStart(2, '0')}` : 'FOLIO READER'}</span><span>{selectedChapter ? `${selectedChapter.image_count} IMAGES · ${selectedChapter.mermaid_count} DIAGRAMS` : 'READY WHEN YOU ARE'}</span></div>
      </div>
    </div>
  </section>;
}
