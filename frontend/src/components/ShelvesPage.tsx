import { useEffect, useState } from 'react';
import { ArrowRight, BookOpen, Check, Globe2, LockKeyhole, Plus, UploadCloud } from 'lucide-react';
import type { AuthSession } from '../api/client';
import { addConvertedToShelf, importShelfBook, shelfEligibleJobs, privateShelf, publicShelf, removeShelfBook, updateShelfBook } from '../api/client';
import type { ShelfBook } from '../api/types';
import ShelfReader from './ShelfReader';

export default function ShelvesPage({ auth }: { auth: AuthSession | null }) {
  const [jobs, setJobs] = useState<{ id: string; filename: string }[]>([]);
  const [route, setRoute] = useState(() => window.location.hash);
  const [publicBooks, setPublicBooks] = useState<ShelfBook[]>([]);
  const [publicPage, setPublicPage] = useState(1);
  const [owned, setOwned] = useState<ShelfBook[]>([]);
  const [tab, setTab] = useState<'public' | 'private'>('public');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [upload, setUpload] = useState<File | null>(null);
  const [singleTitle, setSingleTitle] = useState('');
  const [selectedJob, setSelectedJob] = useState('');
  const [editing, setEditing] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState('');
  const [editAuthor, setEditAuthor] = useState('');
  const [editDescription, setEditDescription] = useState('');
  const signedIn = Boolean(auth?.authenticated);
  function refresh() {
    void publicShelf(publicPage).then(result => setPublicBooks(result.books)).catch(cause => setError(cause instanceof Error ? cause.message : 'Public shelf unavailable.'));
    if (signedIn) void privateShelf().then(setOwned).catch(cause => setError(cause instanceof Error ? cause.message : 'Private shelf unavailable.'));
  }
  useEffect(() => { const update = () => setRoute(window.location.hash);
    window.addEventListener('hashchange', update); return () => window.removeEventListener('hashchange', update); }, []);
  useEffect(() => { refresh(); }, [signedIn, publicPage]);
  useEffect(() => { if (signedIn) void shelfEligibleJobs().then(setJobs).catch(() => {}); }, [signedIn]);
  const match = /^#read\/(public|mine)\/([a-f0-9]{32})(?:\/(\d+))?$/.exec(route);
  if (match) return <ShelfReader bookId={match[2]} scope={match[1] as 'public' | 'mine'}
    requestedChapter={match[3] ? Number(match[3]) : null} authenticated={signedIn} />;
  async function doAction(action: () => Promise<unknown>) {
    setBusy(true); setError('');
    try { await action(); refresh(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not update shelf.'); }
    finally { setBusy(false); }
  }
  const books = tab === 'public' ? publicBooks : owned;
  return <div className="shelves-shell"><header className="shelves-header"><a href="#shelves" className="shelves-brand">folio<span>.</span></a>
    <nav aria-label="Shelves navigation"><a href="#workspace">Conversion workspace</a><a href="#shelves" aria-current="page">Bookshelves</a>
      {!signedIn && <a href="#workspace">Sign in to save your reading</a>}</nav></header>
    <main className="shelves-main"><div className="shelves-hero"><div><span className="shelf-kicker">FOLIO · READING ROOM</span>
      <h1>A shelf full of <em>possibility.</em></h1><p>Turn converted chapters and your own Markdown into books you'll want to open again. Follow the story, one chapter at a time.</p></div>
      <div className="shelf-hero-art" aria-hidden="true"><span>READ<br />MORE<br />OFTEN.</span><i>✳</i></div></div>
      <div className="shelves-tabs" role="group" aria-label="Shelf visibility"><button type="button" className={tab === 'public' ? 'selected' : ''} onClick={() => setTab('public')}><Globe2 size={17} /> Public shelf <span>{publicBooks.length}</span></button>
        {signedIn && <button type="button" className={tab === 'private' ? 'selected' : ''} onClick={() => setTab('private')}><LockKeyhole size={17} /> My shelf <span>{owned.length}</span></button>}</div>
      {error && <p role="alert" className="shelves-error">{error}</p>}
      {signedIn && tab === 'private' && <section className="shelves-add" aria-label="Add a book to my shelf"><div><span className="shelf-kicker">MAKE IT YOURS</span><h2>Bring a book to your shelf</h2><p>Every new book starts private. Publishing it later requires your confirmation.</p></div>
        <div className="shelves-add-actions"><label><BookOpen size={16} /> From a finished conversion<select aria-label="Choose finished conversion" value={selectedJob} onChange={event => setSelectedJob(event.target.value)}><option value="">Choose a book…</option>
          {jobs.map(job => <option value={job.id} key={job.id}>{job.filename}</option>)}</select></label>
          <button type="button" disabled={!selectedJob || busy} onClick={() => void doAction(async () => { await addConvertedToShelf(selectedJob); setSelectedJob(''); })}><Plus size={16} /> Add converted book</button>
          <form onSubmit={event => { event.preventDefault(); if (upload) void doAction(async () => { await importShelfBook(upload, singleTitle || upload.name.replace(/\.md$/i, '')); setUpload(null); setSingleTitle(''); }); }}>
            <label><UploadCloud size={16} /> Import Markdown book<input type="file" accept=".md,.zip" aria-label="Choose Markdown or book ZIP" onChange={event => setUpload(event.target.files?.[0] ?? null)} /></label>
            {upload?.name.toLowerCase().endsWith('.md') && <input aria-label="Book title" placeholder="Book title" maxLength={200} value={singleTitle} onChange={event => setSingleTitle(event.target.value)} />}
            <button type="submit" disabled={!upload || busy}>Import to private shelf</button></form>
          <details><summary>Multi-chapter ZIP format</summary><a href="/shelf-book-example.zip" download="shelf-book-example.zip">Download example ZIP</a><p>Include <code>book.json</code>, chapter Markdown under <code>chapters/</code> and optional PNG/JPEG/WebP/GIF files in <code>assets/</code>. Use <code>../assets/name.png</code> in chapters.</p>
            <pre>{'{"title":"My Book","chapters":[{"title":"Chapter One","file":"chapters/one.md"}]}'}</pre></details>
        </div></section>}
      <div className="shelves-section-label"><span className="shelf-kicker">{tab === 'public' ? 'OPEN FOR EVERYONE' : 'ONLY YOU CAN SEE PRIVATE BOOKS'}</span><strong>{books.length} BOOK{books.length === 1 ? '' : 'S'}</strong></div>
      {!books.length ? <div className="shelves-empty"><BookOpen size={32} /><h2>{tab === 'public' ? 'The public shelf is waiting for its first story.' : 'Your shelf is ready.'}</h2><p>{tab === 'public' ? 'Published Markdown books will appear here.' : 'Add a conversion or import Markdown to begin reading.'}</p></div> :
        <div className="shelves-grid">{books.map((book, i) => <article className="shelf-card" key={book.id}>
          <a className={`shelf-cover shelf-cover-${i % 5}`} href={`#read/${tab === 'public' ? 'public' : 'mine'}/${book.id}`} aria-label={`Read ${book.title}`}>
            <span className="shelf-cover-top">FOLIO · EDITION {String(i + 1).padStart(2, '0')}</span><span className="shelf-cover-title">{book.title}</span><span className="shelf-cover-foot">{book.author || 'A BOOK TO KEEP'} <span>✳</span></span></a>
          <div className="shelf-card-body"><span className="shelf-card-type">{book.visibility === 'PUBLIC' ? <Globe2 size={12} /> : <LockKeyhole size={12} />}{book.visibility === 'PUBLIC' ? 'PUBLIC' : 'PRIVATE'} · {book.chapter_count} CHAPTER{book.chapter_count === 1 ? '' : 'S'}</span>
            <h2>{book.title}</h2><p>{book.description || (book.author ? `By ${book.author}` : 'A new place to lose yourself in reading.')}</p>
            <a className="shelf-open" href={`#read/${tab === 'public' ? 'public' : 'mine'}/${book.id}`}>{signedIn ? 'Continue reading' : 'Open book'} <ArrowRight size={15} /></a>
            {tab === 'private' && <div className="shelf-manage"><button disabled={busy} type="button" onClick={() => {
              if (book.visibility !== 'PUBLIC' && !window.confirm(`Publish "${book.title}"? Its title, description, chapter text and images will be readable by anyone without signing in. Only share content you have permission to publish.`)) return;
              void doAction(() => updateShelfBook(book.id, { visibility: book.visibility === 'PUBLIC' ? 'PRIVATE' : 'PUBLIC' }));
            }}>{book.visibility === 'PUBLIC' ? 'Unpublish' : 'Publish publicly'}</button>
              <button type="button" onClick={() => { setEditing(editing === book.id ? null : book.id); setEditTitle(book.title); setEditAuthor(book.author || ''); setEditDescription(book.description || ''); }}>Rename</button>
              <button disabled={busy} type="button" onClick={() => { if (window.confirm(`Delete "${book.title}" from your shelf and erase all readers' progress? This cannot be undone.`)) void doAction(() => removeShelfBook(book.id)); }}>Remove</button>
            </div>}
            {tab === 'private' && editing === book.id && <form className="shelf-rename" onSubmit={event => { event.preventDefault(); void doAction(async () => { await updateShelfBook(book.id, { title: editTitle, author: editAuthor, description: editDescription }); setEditing(null); }); }}><label>Title<input aria-label="New book title" value={editTitle} maxLength={200} onChange={event => setEditTitle(event.target.value)} /></label><label>Author<input aria-label="Book author" value={editAuthor} maxLength={120} onChange={event => setEditAuthor(event.target.value)} /></label><label>Description<input aria-label="Book description" value={editDescription} maxLength={1000} onChange={event => setEditDescription(event.target.value)} /></label><button type="submit">Save</button></form>}
          </div></article>)}</div>}
      {tab === 'public' && (publicPage > 1 || publicBooks.length === 24) && <nav className="shelves-pagination" aria-label="Public shelf pages"><button type="button" disabled={publicPage === 1} onClick={() => setPublicPage(page => page - 1)}>Previous</button><span>Page {publicPage}</span><button type="button" disabled={publicBooks.length < 24} onClick={() => setPublicPage(page => page + 1)}>Next</button></nav>}
      {signedIn && tab === 'public' && <p className="shelf-guest-note"><Check size={14} /> Signed in: your chapter progress here belongs only to you.</p>}
      {!signedIn && <p className="shelf-guest-note">Everyone can read public books. Sign in to remember your chapters.</p>}
    </main><footer className="shelves-footer"><span>folio.</span><span>MAKE SPACE FOR STORIES.</span></footer></div>;
}
