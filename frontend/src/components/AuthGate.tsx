import { useState } from 'react';
import { BookOpen, LockKeyhole } from 'lucide-react';
import { authAction, getAuthSession } from '../api/client';
import type { AuthSession } from '../api/client';

export default function AuthGate({ onAuthenticated, error: connectionError }: {
  onAuthenticated: (session: AuthSession) => void; error?: string;
}) {
  const [mode, setMode] = useState<'login' | 'signup' | 'reset'>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError(''); setMessage('');
    try {
      const result = await authAction(mode === 'signup' ? 'signup' : mode === 'reset' ? 'reset-password' : 'login',
        { username: username.trim(), password, ...(mode === 'reset' ? { code } : {}) });
      if (mode === 'signup' && result.status !== 'PENDING') throw new Error('Could not confirm your request. Please contact the administrator.');
      setPassword(''); setCode('');
      if (mode === 'login') onAuthenticated(await getAuthSession());
      else { setMessage(mode === 'signup' ? 'Request saved as pending. Ask an administrator to open Settings → Account requests and approve it before signing in. No email is sent.' : 'Password changed. You can sign in now.'); setMode('login'); }
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Request failed.'); }
    finally { setBusy(false); }
  }
  return <div className="app-shell"><header className="site-header"><span className="brand"><span className="brand-icon"><BookOpen size={21} /></span><span className="brand-name">folio<span className="brand-period">.</span></span></span><a href="#settings">Administrator setup / settings</a></header>
    <main className="main-content" style={{ maxWidth: 520, margin: '5rem auto' }}>
      <div className="settings-card settings-auth">
        <LockKeyhole size={26} /><h1>{mode === 'login' ? 'Welcome back' : mode === 'signup' ? 'Request an account' : 'Reset your password'}</h1>
        <p>{mode === 'signup' ? 'Your request stays pending until an administrator approves it.' : mode === 'reset' ? 'Ask an administrator for a one-time reset code.' : 'Your books belong to your private workspace.'}</p>
        {(error || connectionError) && <div className="notice notice-red" role="alert">{error || connectionError}</div>}
        {message && <div className="notice notice-green" role="status">{message}</div>}
        <form onSubmit={event => void submit(event)}>
          <label>Username or email-shaped sign-in name<input autoComplete="username" value={username} onChange={event => setUsername(event.target.value)} required minLength={3} maxLength={64} /></label>
          {mode === 'signup' && <p>Use 3–64 characters. An email address is only a sign-in name; it is not verified and no email is sent.</p>}
          {mode === 'reset' && <label>One-time code<input value={code} onChange={event => setCode(event.target.value)} required /></label>}
          <label>Password<input type="password" autoComplete={mode === 'login' ? 'current-password' : 'new-password'} value={password} onChange={event => setPassword(event.target.value)} required minLength={mode === 'login' ? 1 : 12} /></label>
          <button className="download-button" type="submit" disabled={busy}>{mode === 'login' ? 'Sign in' : mode === 'signup' ? 'Request approval' : 'Change password'}</button>
        </form>
        <div className="settings-actions" style={{ marginTop: 20 }}>{(['login', 'signup', 'reset'] as const).filter(next => next !== mode).map(next => <button key={next} className="secondary-button" type="button" onClick={() => { setMode(next); setError(''); setMessage(''); }}>{next === 'login' ? 'Sign in' : next === 'signup' ? 'Request account' : 'Forgot password?'}</button>)}</div>
      </div>
    </main></div>;
}
