import { useEffect, useState } from 'react';
import { AlertCircle, CheckCircle2, LockKeyhole, Save, Settings2, ShieldCheck } from 'lucide-react';

type Provider = 'ollama' | 'openrouter' | 'openai' | 'anthropic';
type Secret = 'openrouter_api_key' | 'openai_api_key' | 'anthropic_api_key';
interface SessionState { authenticated: boolean; setup_required: boolean; csrf_token: string | null }
interface Account { id: number; username: string; status: string; created_at: string }
interface RuntimeSettings {
  vlm_provider: 'hybrid' | 'cloud' | 'local' | 'openrouter';
  ollama_base_url: string; ollama_vision_model: string;
  openrouter_vision_model: string; openrouter_site_url: string; openrouter_app_name: string;
  openai_vision_model: string; anthropic_vision_model: string;
  vlm_timeout_seconds: number; max_pages: number; max_upload_bytes: number;
  keys_configured: Record<Secret, boolean>;
}
const base = '/api/v1/admin';
async function call<T>(path: string, init?: RequestInit, csrf?: string | null): Promise<T> {
  const response = await fetch(`${base}${path}`, {
    ...init, credentials: 'same-origin',
    headers: { ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...(csrf ? { 'X-CSRF-Token': csrf } : {}), ...init?.headers },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.message || body.detail || `Request failed (${response.status})`);
  return body as T;
}
const names: { id: Provider; label: string; model: keyof RuntimeSettings; secret?: Secret }[] = [
  { id: 'ollama', label: 'Local Ollama', model: 'ollama_vision_model' },
  { id: 'openrouter', label: 'OpenRouter', model: 'openrouter_vision_model', secret: 'openrouter_api_key' },
  { id: 'openai', label: 'OpenAI', model: 'openai_vision_model', secret: 'openai_api_key' },
  { id: 'anthropic', label: 'Anthropic', model: 'anthropic_vision_model', secret: 'anthropic_api_key' },
];

export default function SettingsPage() {
  const [auth, setAuth] = useState<SessionState | null>(null);
  const [settings, setSettings] = useState<RuntimeSettings | null>(null);
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [keys, setKeys] = useState<Partial<Record<Secret, string | null>>>({});
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [accountsLoaded, setAccountsLoaded] = useState(false);
  const [accountsBusy, setAccountsBusy] = useState(false);
  const [accountsError, setAccountsError] = useState('');
  const [resetCode, setResetCode] = useState('');
  useEffect(() => {
    let active = true;
    call<SessionState>('/session').then(async result => {
      if (!active) return;
      setAuth(result);
      if (result.authenticated) {
        const config = await call<RuntimeSettings>('/settings');
        if (!active) return;
        setSettings(config);
        try {
          const users = await call<Account[]>('/users');
          if (active) { setAccounts(users); setAccountsLoaded(true); }
        } catch (cause) {
          if (active) setAccountsError(cause instanceof Error ? cause.message : 'Could not load account requests.');
        }
      }
    }).catch(cause => { if (active) setError(cause instanceof Error ? cause.message : 'Could not connect.'); });
    return () => { active = false; };
  }, []);
  // A request may arrive while the administrator keeps Settings open.
  useEffect(() => {
    if (!auth?.authenticated) return;
    let active = true;
    const timer = window.setInterval(() => {
      void call<Account[]>('/users').then(result => {
        if (active) { setAccounts(result); setAccountsLoaded(true); setAccountsError(''); }
      }).catch(cause => {
        if (active) setAccountsError(cause instanceof Error ? cause.message : 'Could not refresh account requests.');
      });
    }, 20_000);
    return () => { active = false; window.clearInterval(timer); };
  }, [auth?.authenticated]);
  async function refreshAccounts() {
    setAccountsBusy(true); setAccountsError('');
    try { setAccounts(await call<Account[]>('/users')); setAccountsLoaded(true); }
    catch (cause) { setAccountsError(cause instanceof Error ? cause.message : 'Could not refresh account requests.'); }
    finally { setAccountsBusy(false); }
  }
  async function authenticate(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try {
      const setup = auth?.setup_required;
      const result = await call<{ csrf_token: string }>(setup ? '/setup' : '/login', {
        method: 'POST', body: JSON.stringify(setup ? { code, password } : { password }),
      });
      setAuth({ authenticated: true, setup_required: false, csrf_token: result.csrf_token });
      setSettings(await call<RuntimeSettings>('/settings'));
      try { setAccounts(await call<Account[]>('/users')); setAccountsLoaded(true); setAccountsError(''); }
      catch (cause) { setAccountsError(cause instanceof Error ? cause.message : 'Could not load account requests.'); }
      window.dispatchEvent(new Event('pdf2md-auth-changed'));
      setPassword(''); setCode('');
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Sign-in failed.'); }
    finally { setBusy(false); }
  }
  async function save(event: React.FormEvent) {
    event.preventDefault(); if (!settings) return;
    setBusy(true); setError(''); setMessage('');
    const { keys_configured: _flags, ...values } = settings;
    try {
      const updated = await call<RuntimeSettings>('/settings', {
        method: 'PUT', body: JSON.stringify({ ...values, ...keys }),
      }, auth?.csrf_token);
      setSettings(updated); setKeys({});
      setMessage('Settings saved. New uploads and conversions will use these values.');
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not save settings.'); }
    finally { setBusy(false); }
  }
  async function check(provider: Provider) {
    setBusy(true); setError(''); setMessage('');
    const cloud = provider !== 'ollama';
    if (cloud && !window.confirm(`Testing ${provider} sends a small sample diagram and may incur a charge. Continue?`)) { setBusy(false); return; }
    try {
      const result = await call<{ reachable: boolean; model_available: boolean; message?: string }>('/settings/check', {
        method: 'POST', body: JSON.stringify({ provider, acknowledge_cloud_cost: cloud }),
      }, auth?.csrf_token);
      setMessage(result.reachable && result.model_available ? `${provider} is available.`
        : result.message || (result.reachable ? `Ollama is reachable but the configured model is not installed.` : 'Ollama is unreachable. Start its Compose profile or set a reachable URL.'));
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Check failed.'); }
    finally { setBusy(false); }
  }
  function text(field: keyof RuntimeSettings, value: string | number) {
    setSettings(previous => previous ? { ...previous, [field]: value } : previous);
  }
  async function manage(id: number, action: 'approve' | 'reject' | 'disable' | 'enable') {
    setError(''); setResetCode('');
    try {
      await call(`/users/${id}`, { method: 'PUT', body: JSON.stringify({ action }) }, auth?.csrf_token);
      setAccounts(await call<Account[]>('/users')); setAccountsLoaded(true);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Account update failed.'); }
  }
  async function issueReset(id: number) {
    setError('');
    try {
      const result = await call<{ code: string }> (`/users/${id}/reset-code`, { method: 'POST' }, auth?.csrf_token);
      setResetCode(result.code);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Reset failed.'); }
  }
  async function logout() {
    try { await call('/logout', { method: 'POST' }, auth?.csrf_token); setAuth({ authenticated: false, setup_required: false, csrf_token: null }); setSettings(null); setAccounts([]); setAccountsLoaded(false); setAccountsError(''); window.dispatchEvent(new Event('pdf2md-auth-changed')); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not sign out.'); }
  }
  return <section className="settings-page" aria-label="Settings">
    <div className="settings-heading"><div><span className="section-kicker">PRIVATE WORKSPACE CONFIGURATION</span><h1>Settings <Settings2 size={25} /></h1><p>Configure vision providers for new conversions without editing an environment file.</p></div>
      {auth?.authenticated && <button className="secondary-button" onClick={() => void logout()} type="button">Sign out</button>}
    </div>
    {error && <div className="notice notice-red" role="alert"><AlertCircle size={16} /> {error}</div>}
    {message && <div className="notice notice-green" role="status"><CheckCircle2 size={16} /> {message}</div>}
    {!auth ? <div className="settings-card">Loading administrator session…</div> : !auth.authenticated ?
      <form className="settings-card settings-auth" onSubmit={event => void authenticate(event)}>
        <LockKeyhole size={25} /><h2>{auth.setup_required ? 'Create administrator account' : 'Administrator sign-in'}</h2>
        {auth.setup_required && <><p>From the repository root, run <code>docker compose exec api python -m app.admin_cli</code> to generate a one-time setup code (valid for 30 minutes).</p>
          <label>One-time setup code<input autoComplete="off" value={code} required onChange={event => setCode(event.target.value)} /></label></>}
        <label>Administrator password<input type="password" autoComplete={auth.setup_required ? 'new-password' : 'current-password'} minLength={auth.setup_required ? 12 : 1} value={password} required onChange={event => setPassword(event.target.value)} /></label>
        <button className="download-button" disabled={busy} type="submit">{auth.setup_required ? 'Create account' : 'Sign in'}</button>
      </form> : settings && <form onSubmit={event => void save(event)}>
        <div className="settings-card"><h2><ShieldCheck size={19} /> Account requests</h2><p>Approve new users before they can access private books. New requests appear automatically within 20 seconds. Reset codes appear once; share them privately.</p>
          <button type="button" className="secondary-button" disabled={accountsBusy} onClick={() => void refreshAccounts()}>Refresh requests</button>
          {accountsError && <div className="notice notice-red" role="alert">{accountsError}</div>}
          {!accountsLoaded && !accountsError && <p>Loading account requests…</p>}
          {accountsLoaded && accounts.length === 0 && <p>No account requests yet.</p>}
          {accounts.map(account => <div className="account-row" key={account.id}><strong>{account.username}</strong><span>{account.status}</span>
            {account.status === 'PENDING' && <><button type="button" className="secondary-button" onClick={() => void manage(account.id, 'approve')}>Approve</button><button type="button" className="secondary-button" onClick={() => void manage(account.id, 'reject')}>Reject</button></>}
            {account.status === 'ACTIVE' && <><button type="button" className="secondary-button" onClick={() => void manage(account.id, 'disable')}>Disable</button><button type="button" className="secondary-button" onClick={() => void issueReset(account.id)}>Reset code</button></>}
            {account.status === 'DISABLED' && <button type="button" className="secondary-button" onClick={() => void manage(account.id, 'enable')}>Enable</button>}
          </div>)}
          {resetCode && <div className="notice notice-green" role="status">Copy this one-time reset code now (15 minutes): <code>{resetCode}</code></div>}
        </div>
        <div className="settings-card"><h2><ShieldCheck size={19} /> Provider strategy</h2><p>Hybrid tries Ollama → OpenRouter → OpenAI → Anthropic, skipping unconfigured cloud providers.</p>
          <label>Mode<select value={settings.vlm_provider} onChange={event => text('vlm_provider', event.target.value)}><option value="hybrid">Hybrid: local, then cloud fallback</option><option value="cloud">Configured cloud providers only</option><option value="local">Local Ollama only</option><option value="openrouter">OpenRouter only</option></select></label></div>
        <div className="settings-providers">{names.map(provider => <div className="settings-card" key={provider.id}>
          <h2>{provider.label}</h2>
          {provider.id === 'ollama' && <><label>Ollama URL<input value={settings.ollama_base_url} onChange={event => text('ollama_base_url', event.target.value)} /></label><p>The URL must be reachable from the worker container. To run the optional service: <code>docker compose --profile local-vlm up -d</code></p></>}
          <label>Vision model<input value={String(settings[provider.model])} onChange={event => text(provider.model, event.target.value)} required /></label>
          {provider.secret && <label>API key <span className="key-state">{settings.keys_configured[provider.secret] ? 'Configured' : 'Not configured'}</span><input type="password" autoComplete="off" placeholder={settings.keys_configured[provider.secret] ? 'Leave empty to keep saved key' : 'Enter a new key'} value={keys[provider.secret] || ''} onChange={event => setKeys({ ...keys, [provider.secret!]: event.target.value || undefined })} />
            {settings.keys_configured[provider.secret] && <button className="clear-key" type="button" onClick={() => setKeys({ ...keys, [provider.secret!]: null })}>Clear saved key</button>}{keys[provider.secret] === null && <span className="key-state">Will be cleared on save</span>}</label>}
          {provider.id === 'openrouter' && <><label>Site URL (optional)<input value={settings.openrouter_site_url} onChange={event => text('openrouter_site_url', event.target.value)} /></label><label>App name<input value={settings.openrouter_app_name} onChange={event => text('openrouter_app_name', event.target.value)} /></label></>}
          <button className="secondary-button" disabled={busy} type="button" onClick={() => void check(provider.id)}>Test saved configuration{provider.id !== 'ollama' ? ' (may incur a charge)' : ''}</button>
        </div>)}</div>
        <div className="settings-card"><h2>Conversion limits</h2><p>New jobs only. PDF size is capped here at 50 MiB; nginx has its own 64 MiB multipart request limit.</p><div className="settings-limits"><label>Vision timeout (seconds)<input type="number" min={5} max={600} value={settings.vlm_timeout_seconds} onChange={event => text('vlm_timeout_seconds', Number(event.target.value))} /></label><label>Maximum pages<input type="number" min={1} max={10000} value={settings.max_pages} onChange={event => text('max_pages', Number(event.target.value))} /></label><label>Maximum PDF (MiB)<input type="number" min={1} max={50} value={settings.max_upload_bytes / 1048576} onChange={event => text('max_upload_bytes', Math.round(Number(event.target.value) * 1048576))} /></label></div></div>
        <div className="settings-actions"><span>Saved provider keys are encrypted and never shown again. Existing partial jobs are not rerun automatically.</span><button className="download-button" disabled={busy} type="submit"><Save size={16} /> Save settings</button></div>
      </form>}
  </section>;
}
