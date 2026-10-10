import { useState } from 'react';
import { ArrowRight, BookOpen, ExternalLink, FolderOpen, Search } from 'lucide-react';
import { githubBrowse, githubConvert, githubPrivateRepositories, githubSearch } from '../api/client';
import type { GithubPDF, GithubBrowse, GithubSearch, Job } from '../api/types';
import { formatBytes } from '../lib/format';

interface Props { admin: boolean; onConverted: (job: Job) => void }

export default function GithubPDFPanel({ admin, onConverted }: Props) {
  const [query, setQuery] = useState('');
  const [repo, setRepo] = useState('');
  const [search, setSearch] = useState<GithubSearch | null>(null);
  const [browse, setBrowse] = useState<GithubBrowse | null>(null);
  const [privateRepos, setPrivateRepos] = useState<string[]>([]);
  const [privatePage, setPrivatePage] = useState(1);
  const [selected, setSelected] = useState<GithubPDF | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  async function run(action: () => Promise<void>) {
    setBusy(true); setError(''); setMessage('');
    try { await action(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'GitHub request failed.'); }
    finally { setBusy(false); }
  }
  function openRepository(name: string, directory = '') {
    void run(async () => { const result = await githubBrowse(name, directory); setBrowse(result); setSearch(null); setSelected(null); setRepo(name); });
  }
  function files(list: GithubPDF[]) {
    return list.map(file => <li className="github-file" key={`${file.repo}/${file.path}`}>
      <div><strong>{file.path.split('/').at(-1)}</strong><small>{file.repo} / {file.path} · {file.size === null ? 'Size unavailable' : formatBytes(file.size)}</small></div>
      <button type="button" disabled={busy} onClick={() => setSelected(file)}>Review PDF <ArrowRight size={14} /></button>
    </li>);
  }
  return <section className="upload-card card github-panel" aria-label="Find a GitHub PDF">
    <div className="card-eyebrow"><span className="eyebrow-mark" /> FIND A PDF ON GITHUB</div>
    <h2>Find a book, skip the upload.</h2>
    <p className="card-description">Search public repositories by topic, or browse a repository you know. GitHub does not index PDFs in Code Search, so discovery is limited.</p>
    {error && <div className="inline-error" role="alert">{error}</div>}
    {message && <p role="status">{message}</p>}
    <form className="github-form" onSubmit={event => { event.preventDefault(); void run(async () => {
      setBrowse(null); setSelected(null); setSearch(await githubSearch(query));
    }); }}><label>Search repository topics<input aria-label="Search repository topics" value={query} minLength={2} maxLength={80} onChange={event => setQuery(event.target.value)} placeholder="e.g. open textbooks" required /></label>
      <button type="submit" disabled={busy || query.trim().length < 2}><Search size={15} /> Search</button></form>
    <form className="github-form" onSubmit={event => { event.preventDefault(); openRepository(repo.trim()); }}><label>Browse exact repository<input aria-label="Browse exact repository" value={repo} onChange={event => setRepo(event.target.value)} placeholder="owner/repository" required /></label>
      <button type="submit" disabled={busy || !repo.includes('/')}><FolderOpen size={15} /> Browse</button></form>
    {admin && <button className="github-private" type="button" disabled={busy} onClick={() => void run(async () => { setPrivatePage(1); setPrivateRepos((await githubPrivateRepositories()).map(item => item.repo)); })}>Show my PAT-accessible private repositories</button>}
    {admin && privateRepos.length > 0 && <><div className="github-repos" aria-label="Private repositories">{privateRepos.map(name => <button type="button" key={name} onClick={() => openRepository(name)}>{name}</button>)}</div>
      <div className="github-pagination"><button type="button" disabled={busy || privatePage === 1} onClick={() => void run(async () => { const page = privatePage - 1; setPrivateRepos((await githubPrivateRepositories(page)).map(item => item.repo)); setPrivatePage(page); })}>Previous private page</button><span>Page {privatePage}</span><button type="button" disabled={busy || privateRepos.length < 20 || privatePage >= 10} onClick={() => void run(async () => { const page = privatePage + 1; setPrivateRepos((await githubPrivateRepositories(page)).map(item => item.repo)); setPrivatePage(page); })}>Next private page</button></div></>}
    {search && <div className="github-results"><h3>Repository matches · page {search.page}</h3>
      {search.incomplete && <p>Results are limited. Try a narrower search or browse an exact repository.</p>}
      {!search.repositories.length && <p>No matching repositories found. Try browsing an exact repository.</p>}
      {search.repositories.map(result => <div className="github-repo" key={result.repo}><h4>{result.repo}</h4>
        {result.incomplete && <small>Only part of this repository was inspected; browse its folders for more.</small>}
        {result.files.length ? <ul>{files(result.files)}</ul> : <p>No PDF found in the inspected paths.</p>}
        <button type="button" disabled={busy} onClick={() => openRepository(result.repo)}>Browse folders</button></div>)}
      <div className="github-pagination"><button type="button" disabled={busy || search.page === 1} onClick={() => void run(async () => setSearch(await githubSearch(query, search.page - 1)))}>Previous</button>
        <button type="button" disabled={busy || !search.has_more} onClick={() => void run(async () => setSearch(await githubSearch(query, search.page + 1)))}>Next</button></div>
    </div>}
    {browse && <div className="github-results"><h3>{browse.private ? 'Private' : 'Public'} · {browse.repo}</h3><p>Folder: /{browse.directory}</p>
      {browse.incomplete && <p>This folder was limited by GitHub. Try a narrower folder.</p>}
      {browse.directory && <button type="button" onClick={() => openRepository(browse.repo, browse.directory.split('/').slice(0, -1).join('/'))}>↑ Parent folder</button>}
      <div className="github-repos">{browse.folders.map(folder => <button type="button" key={folder} onClick={() => openRepository(browse.repo, folder)}><FolderOpen size={14} /> {folder.split('/').at(-1)}</button>)}</div>
      {browse.files.length ? <ul>{files(browse.files)}</ul> : <p>No PDFs in this folder. Open a subfolder or search another repository.</p>}
    </div>}
    {selected && <div className="github-review"><span className="card-eyebrow"><BookOpen size={14} /> REVIEW BEFORE CONVERSION</span>
      <h3>{selected.path.split('/').at(-1)}</h3><p>{selected.repo} / {selected.path}</p><p>{selected.size === null ? 'Size unavailable' : formatBytes(selected.size)} · Fixed Git revision {selected.revision.slice(0, 9)}</p>
      <p>Only convert PDFs you are allowed to use. This creates a private job; it does not publish the book.</p>
      <a href={selected.url} target="_blank" rel="noopener noreferrer">View source on GitHub <ExternalLink size={13} /></a>
      <div className="github-review-actions"><button type="button" onClick={() => setSelected(null)}>Cancel</button>
        <button type="button" disabled={busy} onClick={() => void run(async () => { const job = await githubConvert(selected); setMessage(`${job.filename} was queued as a private conversion.`); setSelected(null); onConverted(job); })}>Convert selected PDF</button></div>
    </div>}
  </section>;
}
