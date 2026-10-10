import { useRef, useState } from 'react';
import { ArrowUpRight, FileText, LoaderCircle, UploadCloud, X } from 'lucide-react';
import { uploadJob, validatePdf } from '../api/client';
import type { Job } from '../api/types';
import { formatBytes } from '../lib/format';

interface Props {
  onUploaded: (job: Job) => void;
}

export default function UploadPanel({ onUploaded }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState('');

  function pick(next: File | undefined) {
    if (!next) return;
    const validation = validatePdf(next);
    setError(validation || '');
    setFile(validation ? null : next);
  }

  async function submit() {
    if (!file || uploading) return;
    const controller = new AbortController();
    abortRef.current = controller;
    setUploading(true);
    setProgress(0);
    setError('');
    try {
      const job = await uploadJob(file, setProgress, controller.signal);
      setFile(null);
      if (input.current) input.current.value = '';
      onUploaded(job);
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Upload failed.');
    } finally {
      setUploading(false);
      abortRef.current = null;
    }
  }

  return (
    <section className="upload-card card" aria-label="Upload a PDF book">
      <div className="card-eyebrow"><span className="eyebrow-mark" /> NEW CONVERSION <span className="eyebrow-number">01 / 02</span></div>
      <h2>Start with a book.</h2>
      <p className="card-description">A more useful format is just one upload away.</p>
      <input ref={input} type="file" accept=".pdf,application/pdf" className="sr-only"
        aria-label="Choose PDF file" onChange={event => pick(event.target.files?.[0])} />
      <div className={`dropzone ${dragging ? 'dropzone-active' : ''} ${file ? 'dropzone-selected' : ''}`}
        role="button" tabIndex={uploading ? -1 : 0} aria-label="Choose or drop a PDF file"
        onClick={() => !uploading && input.current?.click()}
        onKeyDown={event => { if (!uploading && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); input.current?.click(); } }}
        onDragOver={event => { event.preventDefault(); if (!uploading) setDragging(true); }}
        onDragLeave={event => { event.preventDefault(); setDragging(false); }}
        onDrop={event => { event.preventDefault(); setDragging(false); if (!uploading) pick(event.dataTransfer.files[0]); }}>
        <div className="dropzone-icon">{file ? <FileText size={26} strokeWidth={1.5} /> : <UploadCloud size={27} strokeWidth={1.5} />}</div>
        {file ? <>
          <strong className="selected-name" title={file.name}>{file.name}</strong>
          <span className="dropzone-meta">{formatBytes(file.size)} · Ready to convert</span>
        </> : <>
          <strong>Drop your PDF here</strong>
          <span>or <u>browse your files</u></span>
          <small>PDF books · Server limit: 50 MiB by default</small>
        </>}
      </div>
      {error && <div className="inline-error" role="alert">{error}</div>}
      {uploading && <div className="upload-progress" aria-live="polite">
        <span>{progress === 100 ? 'Creating conversion job…' : 'Uploading your book…'}</span><b>{progress}%</b>
        <div className="track"><div style={{ width: `${progress}%` }} /></div>
      </div>}
      <div className="upload-actions">
        <button className="primary-button" type="button" disabled={!file || uploading} onClick={() => void submit()}>
          {uploading ? <LoaderCircle size={17} className="spin" /> : <ArrowUpRight size={18} />}
          {uploading ? 'Uploading…' : 'Convert to Markdown'}
        </button>
        {uploading && <button className="text-button" onClick={() => abortRef.current?.abort()} type="button"><X size={15} /> Cancel</button>}
      </div>
      <div className="upload-footnote"><span className="tiny-spark">✳</span> Your files stay in your Docker volumes.</div>
    </section>
  );
}
