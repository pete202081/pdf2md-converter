import { useCallback, useEffect, useState } from 'react';
import { ArrowDownRight, ArrowUpRight, BookOpen, CircleHelp, ExternalLink, Leaf, Menu, X } from 'lucide-react';
import { deleteJob } from './api/client';
import type { Job } from './api/types';
import JobList from './components/JobList';
import JobWorkspace from './components/JobWorkspace';
import UploadPanel from './components/UploadPanel';
import SettingsPage from './components/SettingsPage';
import { useJobs } from './hooks/useJobs';
import { useJobStream } from './hooks/useJobStream';
import { isTerminal } from './lib/format';

export default function App() {
  const { jobs, selectedId, selectJob, detail, error, setError, connection, loading,
    refreshJobs, refreshDetail } = useJobs();
  const [toast, setToast] = useState('');
  const [menuOpen, setMenuOpen] = useState(false);
  const [page, setPage] = useState<'workspace' | 'settings'>(() => window.location.hash === '#settings' ? 'settings' : 'workspace');
  useEffect(() => {
    const syncPage = () => setPage(window.location.hash === '#settings' ? 'settings' : 'workspace');
    window.addEventListener('hashchange', syncPage);
    return () => window.removeEventListener('hashchange', syncPage);
  }, []);
  const selected = detail?.id === selectedId ? detail : jobs.find(job => job.id === selectedId) ?? null;
  const onTerminal = useCallback(() => {
    void refreshJobs();
    void refreshDetail();
  }, [refreshJobs, refreshDetail]);
  const { events, streamState } = useJobStream(
    selectedId, Boolean(selectedId && (!selected || !isTerminal(selected.status))), onTerminal,
  );
  const completed = jobs.filter(job => job.status === 'SUCCEEDED' || job.status === 'PARTIAL').length;
  const totalChapters = jobs.reduce((count, job) => count + job.chapter_count, 0);

  function onUploaded(job: Job) {
    selectJob(job.id);
    setToast(`${job.filename} was queued for conversion.`);
    window.setTimeout(() => setToast(''), 5000);
    void refreshJobs();
    void refreshDetail();
  }

  async function onDelete(id: string) {
    if (!window.confirm('Delete this job, its PDF, chapters and ZIP? This cannot be undone.')) return;
    try {
      await deleteJob(id);
      setToast('Conversion and files deleted.');
      await refreshJobs();
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not delete job.'); }
  }

  return <div className="app-shell">
    <header className="site-header">
      <a className="brand" href="/" aria-label="Folio home"><span className="brand-icon"><BookOpen size={21} strokeWidth={1.8} /></span><span className="brand-name">folio<span className="brand-period">.</span></span><span className="brand-divider" /><span className="brand-subtitle">PDF TO MARKDOWN STUDIO</span></a>
      <nav className={`top-nav ${menuOpen ? 'top-nav-open' : ''}`} aria-label="Main navigation">
        <a className={page === 'workspace' ? 'nav-active' : ''} href="#workspace" onClick={() => { setPage('workspace'); setMenuOpen(false); }}>Workspace</a>
        <a className={page === 'settings' ? 'nav-active' : ''} href="#settings" onClick={() => { setPage('settings'); setMenuOpen(false); }}>Settings</a>
        <a href="#how-it-works" onClick={() => setMenuOpen(false)}>How it works</a>
        <a href="/docs" target="_blank" rel="noopener noreferrer" onClick={() => setMenuOpen(false)}>API docs <ExternalLink size={12} /></a>
      </nav>
      <div className="header-right"><span className={`system-status system-${connection}`}><span />{connection === 'online' ? 'SYSTEM ONLINE' : connection === 'offline' ? 'API OFFLINE' : 'CONNECTING'}</span>
        <button className="menu-toggle" aria-label={menuOpen ? 'Close menu' : 'Open menu'} type="button" onClick={() => setMenuOpen(!menuOpen)}>{menuOpen ? <X size={20} /> : <Menu size={20} />}</button>
      </div>
    </header>

    <main id={page === 'workspace' ? 'workspace' : 'settings'} className="main-content">
      {page === 'settings' ? <SettingsPage /> : <>
      <div className="hero-row">
        <div className="hero-copy"><div className="hero-kicker"><span className="hero-kicker-line" /> THE READING ROOM, REIMAGINED <span className="hero-kicker-line" /></div>
          <h1>From pages to <em>possibility.</em></h1>
          <p>Give your PDFs a second life. Thoughtfully converted into clean, chapter-wise Markdown — with diagrams, tables, and every detail in its place.</p>
          <div className="hero-tags"><span><span className="tag-star">✳</span> Chapter aware</span><span><Leaf size={14} /> Image &amp; table ready</span><span><span className="tag-diamond">◇</span> Mermaid diagrams</span></div>
        </div>
        <div className="hero-aside"><div className="hero-aside-top"><span>YOUR WORK AT A GLANCE</span><ArrowUpRight size={16} /></div>
          <div className="hero-stats"><div><strong>{String(completed).padStart(2, '0')}</strong><span>BOOKS CONVERTED</span></div><span className="stats-divider" /><div><strong>{String(totalChapters).padStart(2, '0')}</strong><span>CHAPTERS MADE</span></div></div>
          <div className="hero-aside-bottom">A little more from everything you read. <ArrowDownRight size={15} /></div>
        </div>
      </div>

      {error && <div className="global-alert" role="alert"><CircleHelp size={18} /><span>{error}</span><button aria-label="Dismiss error" onClick={() => setError(null)}><X size={16} /></button></div>}
      {toast && <div className="toast" role="status">✳ &nbsp;{toast}<button onClick={() => setToast('')} aria-label="Dismiss notification"><X size={14} /></button></div>}

      <div className="dashboard-grid">
        <div className="left-column"><UploadPanel onUploaded={onUploaded} /><JobList jobs={jobs} selectedId={selectedId} loading={loading} onSelect={selectJob} /></div>
        <JobWorkspace job={selected} detail={detail?.id === selectedId ? detail : null} events={events}
          streamState={streamState} onDelete={id => void onDelete(id)}
          onRefresh={() => { void refreshJobs(); void refreshDetail(); }} />
      </div>

      <section id="how-it-works" className="how-section"><div className="how-left"><span className="section-kicker">THE PROCESS, SIMPLIFIED</span><h2>Good things happen<br/>in <em>three steps.</em></h2></div>
        <div className="how-steps"><div><span className="step-number">01</span><strong>Drop it in</strong><p>Upload a PDF book. We take care of the rest.</p></div><div><span className="step-number">02</span><strong>Watch it unfold</strong><p>Follow every stage, from chapters to diagrams.</p></div><div><span className="step-number">03</span><strong>Make it yours</strong><p>Preview each chapter and download it all as a ZIP.</p></div></div>
      </section>
      </>}
    </main>
    <footer className="site-footer"><span className="footer-brand">folio<span>.</span></span><span>GOOD IDEAS DESERVE GOOD FORMATS.</span><span>MADE FOR THE LOVE OF READING <span className="footer-flower">✳</span></span></footer>
  </div>;
}
