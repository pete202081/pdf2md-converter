import { useEffect, useRef, useState } from 'react';
import { BookOpen, Search } from 'lucide-react';
import { searchLibrary } from '../api/client';
import type { LibrarySearchHit, LibrarySearchResponse } from '../api/types';

interface Props { onOpen: (hit: LibrarySearchHit, openSource: boolean) => void }

export default function LibrarySearch({ onOpen }: Props) {
  const [query, setQuery] = useState('');
  const [result, setResult] = useState<LibrarySearchResponse | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);

  async function run() {
    const term = query.trim();
    if (term.length < 2) { setError('Enter at least two characters.'); return; }
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setError(''); setResult(null); setLoading(true);
    try { const response = await searchLibrary(term, current.signal);
      if (!current.signal.aborted) setResult(response);
    } catch (cause) {
      if (!current.signal.aborted) setError(cause instanceof Error ? cause.message : 'Search is unavailable.');
    } finally { if (!current.signal.aborted) setLoading(false); }
  }
  return <section className="library-search card" aria-label="Search your book library">
    <div className="card-eyebrow"><span className="eyebrow-mark" /> SEARCH THE SHELF</div>
    <h2>Find it in your books.</h2>
    <form className="library-search-form" onSubmit={event => { event.preventDefault(); void run(); }}>
      <label htmlFor="book-search">Search filenames, chapters &amp; text</label>
      <div className="library-search-input"><Search size={17} /><input id="book-search" value={query}
        maxLength={100} autoComplete="off" placeholder="Try a title, table value, or phrase…"
        onChange={event => { setQuery(event.target.value); setResult(null); setError(''); controller.current?.abort(); setLoading(false); }} />
        <button type="submit" disabled={loading || query.trim().length < 2}>Search</button></div>
    </form>
    {loading && <p role="status" className="search-message">Searching your library…</p>}
    {error && <p role="alert" className="search-message search-error">{error}</p>}
    {result && <div className="search-results" role="region" aria-label="Search results">
      {result.pending_books > 0 && <p className="search-message">{result.pending_books} completed book{result.pending_books === 1 ? ' is' : 's are'} not searchable yet. An administrator can run the search reindex command.</p>}
      {!result.results.length ? <p className="search-message">No matches in indexed books. Try another term.</p> : <>
        <p className="search-message">{result.results.length} chapter match{result.results.length === 1 ? '' : 'es'}{result.results.length === 30 ? ' (first 30)' : ''}</p>
        {result.results.map(hit => <article className="search-hit" key={`${hit.job_id}-${hit.chapter_order}`}>
          <button type="button" className="search-hit-main" onClick={() => onOpen(hit, false)}>
            <span className="search-hit-title"><BookOpen size={15} /> {hit.filename} · {hit.chapter_title}</span>
            <span className="search-hit-excerpt">{hit.excerpt}</span>
            <span className="search-hit-pages">{hit.first_page !== null && hit.last_page !== null
              ? `Chapter source pages ${hit.first_page}–${hit.last_page}` : 'Source-page range unavailable'}</span>
          </button>
          {hit.first_page !== null && hit.last_page !== null && <button type="button" className="search-source-link"
            onClick={() => onOpen(hit, true)}>View first source page</button>}
        </article>)}
      </>}
    </div>}
  </section>;
}
