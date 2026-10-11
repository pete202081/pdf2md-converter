import { ArrowUpRight, BookOpen, FileText, Layers3 } from 'lucide-react';
import type { Job } from '../api/types';
import { formatDate, statusLabel } from '../lib/format';

interface Props {
  jobs: Job[];
  selectedId: string;
  loading: boolean;
  onSelect: (id: string) => void;
}

export default function JobList({ jobs, selectedId, loading, onSelect }: Props) {
  return <section className="library-card card" aria-label="Recent conversions">
    <div className="card-eyebrow"><span className="eyebrow-mark" /> YOUR LIBRARY <span className="eyebrow-number">02 / 02</span></div>
    <div className="library-title"><h2>Recent books</h2><span className="count-pill">{jobs.length}</span></div>
    {jobs.length === 0 ? <div className="library-empty">
      <div className="empty-books"><BookOpen size={33} strokeWidth={1.2} /><span className="empty-orbit" /></div>
      <strong>{loading ? 'Looking for books…' : 'A blank shelf, for now.'}</strong>
      <span>Upload your first PDF and it will appear here.</span>
    </div> : <div className="job-list">
      {jobs.map(job => <button key={job.id} type="button" onClick={() => onSelect(job.id)}
        className={`job-item ${job.id === selectedId ? 'job-item-active' : ''}`}
        aria-current={job.id === selectedId ? 'true' : undefined}>
        <span className="job-file-icon"><FileText size={19} strokeWidth={1.7} /></span>
        <span className="job-item-main">
          <strong title={job.filename}>{job.filename}</strong>
          <span className="job-item-meta"><span className={`status-dot status-${job.status.toLowerCase()}`} />{job.cancel_requested_at && (job.status === 'QUEUED' || job.status === 'RUNNING') ? 'Stopping…' : statusLabel(job.status, job.review_total, job.review_pending)} <i>·</i> {formatDate(job.created_at)}{(job.status === 'SUCCEEDED' || job.status === 'PARTIAL') && job.search_index_state && job.search_index_state !== 'READY' && <> <i>·</i> {job.search_index_state === 'ERROR' ? 'Search unavailable' : 'Search pending'}</>}</span>
        </span>
        <ArrowUpRight className="job-arrow" size={17} />
      </button>)}
    </div>}
    <div className="library-footer"><Layers3 size={14} /><span>Chapter by chapter. Always in order.</span></div>
  </section>;
}
