import { useEffect, useState } from 'react';
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ArrowLeft, ArrowRight, BookOpen, Check, ChevronLeft, ChevronRight, Moon, Sun } from 'lucide-react';
import type { ReadingProgress, ShelfBookDetail } from '../api/types';
import { readProgress, resetReadProgress, saveReadProgress, shelfAssetUrl, shelfDetail, shelfMarkdown } from '../api/client';
import MermaidDiagram from './MermaidDiagram';
import { stripFrontmatter } from './MarkdownPreview';

const IMAGE = /^(?:\.\.\/)?assets\/([A-Za-z0-9][A-Za-z0-9._-]*\.(?:png|jpe?g|webp|gif))$/i;

function ReaderMarkdown({ markdown, bookId, scope }: { markdown: string; bookId: string; scope: 'public' | 'mine' }) {
  return <article className="shelf-prose"><ReactMarkdown remarkPlugins={[remarkGfm]}
    urlTransform={(url, key) => {
      if (key === 'src') { const match = IMAGE.exec(url); return match ? shelfAssetUrl(bookId, match[1], scope) : ''; }
      return /^(?:https?:|#)/i.test(url) ? defaultUrlTransform(url) : '';
    }} components={{
      pre({ children }) { return <>{children}</>; },
      code({ className, children }) {
        const code = String(children).replace(/\n$/, '');
        if (className?.includes('language-mermaid')) return code.length <= 8000
          ? <MermaidDiagram chart={code} /> : <pre><code>{code}</code></pre>;
        return className?.startsWith('language-') ? <pre><code>{code}</code></pre> : <code>{children}</code>;
      },
      img({ src, alt }) { return src ? <img loading="lazy" src={src} alt={alt || 'Book illustration'} /> : null; },
      a({ href, children }) { return href ? <a href={href} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">{children}</a> : <span>{children}</span>; },
    }}>{stripFrontmatter(markdown.replace(/<!-- pdf2md-review:[A-Za-z0-9_-]+:(?:start|end) -->/g, ''))}</ReactMarkdown></article>;
}

export default function ShelfReader({ bookId, scope, requestedChapter, authenticated }: {
  bookId: string; scope: 'public' | 'mine'; requestedChapter: number | null; authenticated: boolean;
}) {
  const [book, setBook] = useState<ShelfBookDetail | null>(null);
  const [progress, setProgress] = useState<ReadingProgress | null>(null);
  const [chapterId, setChapterId] = useState<number | null>(null);
  const [markdown, setMarkdown] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [fontSize, setFontSize] = useState(18);
  const [night, setNight] = useState(false);
  useEffect(() => {
    let active = true; setBook(null); setError(''); setChapterId(null); setProgress(null);
    void Promise.all([shelfDetail(bookId, scope), authenticated ? readProgress(bookId).catch(() => null) : Promise.resolve(null)])
      .then(([b, p]) => { if (active) { setBook(b); setProgress(p);
        const selected = b.chapters.find(ch => ch.id === requestedChapter)?.id ?? p?.resume_chapter_id ?? b.chapters[0]?.id ?? null;
        setChapterId(selected);
      } }).catch(cause => { if (active) setError(cause instanceof Error ? cause.message : 'Could not open book.'); });
    return () => { active = false; };
  }, [bookId, scope, authenticated]);
  useEffect(() => {
    if (requestedChapter && book?.chapters.some(ch => ch.id === requestedChapter)) setChapterId(requestedChapter);
  }, [requestedChapter, book]);
  useEffect(() => {
    if (!chapterId) return;
    const controller = new AbortController(); setMarkdown(''); setError('');
    void shelfMarkdown(bookId, chapterId, scope, controller.signal)
      .then(text => { if (!controller.signal.aborted) setMarkdown(text); })
      .catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Chapter unavailable.'); });
    if (authenticated) void saveReadProgress(bookId, chapterId)
      .then(value => { if (!controller.signal.aborted) setProgress(value); })
      .catch(() => { if (!controller.signal.aborted) setError('Could not save reading position.'); });
    return () => controller.abort();
  }, [bookId, chapterId, scope, authenticated]);
  const index = book?.chapters.findIndex(ch => ch.id === chapterId) ?? -1;
  const current = index >= 0 ? book?.chapters[index] : null;
  const complete = Boolean(chapterId && progress?.completed_chapter_ids.includes(chapterId));
  function move(id: number) { window.location.hash = `#read/${scope}/${bookId}/${id}`; setChapterId(id); window.scrollTo?.({ top: 0, behavior: 'smooth' }); }
  async function toggle() {
    if (!chapterId) return; setBusy(true);
    try { setProgress(await saveReadProgress(bookId, chapterId, !complete)); setError(''); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not save progress.'); }
    finally { setBusy(false); }
  }
  return <div className={`shelf-reader ${night ? 'shelf-night' : ''}`}>
    <header className="shelf-reader-top"><a href="#shelves"><ArrowLeft size={16} /> Shelves</a><span>folio<span className="brand-period">.</span> <i>/</i> READING ROOM</span>
      <div className="shelf-reader-tools"><button type="button" aria-label="Decrease font size" onClick={() => setFontSize(Math.max(15, fontSize - 1))}>A−</button>
        <button type="button" aria-label="Increase font size" onClick={() => setFontSize(Math.min(24, fontSize + 1))}>A+</button>
        <button type="button" aria-label={night ? 'Light theme' : 'Dark theme'} onClick={() => setNight(!night)}>{night ? <Sun size={16} /> : <Moon size={16} />}</button></div></header>
    {!book && !error && <div className="shelf-reader-message">Opening your book…</div>}
    {error && <p className="shelf-reader-message" role="alert">{error}</p>}
    {book && <div className="shelf-reader-layout">
      <aside className="shelf-reader-toc"><span className="shelf-kicker">THE BOOK</span><h2>{book.title}</h2><p>{book.author || 'A little space to read'}</p>
        {authenticated && progress && <div className="shelf-progress"><span>{progress.completed_chapter_ids.length} / {book.chapters.length} complete</span><div><i style={{ width: `${100 * progress.completed_chapter_ids.length / Math.max(1, book.chapters.length)}%` }} /></div></div>}
        <nav aria-label="Book chapters">{book.chapters.map(ch => <button key={ch.id} type="button"
          className={chapterId === ch.id ? 'shelf-chapter-active' : ''} onClick={() => move(ch.id)}>
          <span>{String(ch.order_index).padStart(2, '0')}</span><span>{ch.title}</span>
          {authenticated && progress?.completed_chapter_ids.includes(ch.id) && <Check size={14} aria-label="Completed" />}</button>)}</nav>
        {authenticated && progress && <button className="shelf-reset" type="button" onClick={() => {
          if (window.confirm('Clear your reading progress for this book?')) void resetReadProgress(bookId).then(() => readProgress(bookId)).then(setProgress).catch(() => setError('Could not clear progress.'));
        }}>Clear my progress</button>}
      </aside>
      <main className="shelf-reader-main"><div className="shelf-reader-breadcrumb"><BookOpen size={15} /> {book.title} <ChevronRight size={14} /> Chapter {current?.order_index}</div>
        <div className="shelf-reader-page" style={{ fontSize }}><p className="shelf-chapter-number">CHAPTER {String(current?.order_index ?? 1).padStart(2, '0')}</p>
          <h1>{current?.title}</h1><div className="shelf-ornament">✳ <span /> ✳</div>
          {markdown ? <ReaderMarkdown markdown={markdown} bookId={bookId} scope={scope} /> : !error && <p>Turning the page…</p>}
        </div>
        <div className="shelf-reader-bottom">{authenticated ? <button type="button" disabled={busy || !chapterId} className={`shelf-complete ${complete ? 'shelf-done' : ''}`}
          onClick={() => void toggle()}>{complete ? <><Check size={17} /> Chapter complete · Undo</> : 'Mark chapter complete'}</button>
          : <span className="shelf-guest-note">Sign in to save your reading progress.</span>}
          <div className="shelf-prev-next">{index > 0 && <button type="button" onClick={() => move(book.chapters[index - 1].id)}><ChevronLeft size={16} /> Previous</button>}
            {index < book.chapters.length - 1 && <button type="button" onClick={() => move(book.chapters[index + 1].id)}>Next chapter <ArrowRight size={16} /></button>}</div></div>
      </main>
    </div>}
  </div>;
}
