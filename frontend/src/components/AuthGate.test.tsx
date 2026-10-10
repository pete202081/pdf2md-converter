import { afterEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import AuthGate from './AuthGate';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('requests an account, explains approval, and signs in only after approval', async () => {
  const fetcher = vi.fn().mockImplementation(async (url: string) => {
    if (url.endsWith('/signup')) return { ok: true, json: async () => ({ status: 'PENDING' }) };
    if (url.endsWith('/login')) return { ok: true, json: async () => ({ authenticated: true }) };
    return { ok: true, json: async () => ({ authenticated: true, username: 'alice', role: 'USER', csrf_token: 'csrf' }) };
  });
  vi.stubGlobal('fetch', fetcher);
  const onAuthenticated = vi.fn();
  render(<AuthGate onAuthenticated={onAuthenticated} />);
  fireEvent.click(screen.getByText('Request account'));
  fireEvent.change(screen.getByLabelText('Username or email-shaped sign-in name'), { target: { value: 'Reader.One@Example.com' } });
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'long-alice-password' } });
  fireEvent.click(screen.getByText('Request approval'));
  await screen.findByText(/Request saved as pending. Ask an administrator/);
  expect(onAuthenticated).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'long-alice-password' } });
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
  await waitFor(() => expect(onAuthenticated).toHaveBeenCalledWith(expect.objectContaining({ username: 'alice', role: 'USER' })));
  expect(fetcher.mock.calls[0][0]).toBe('/api/v1/auth/signup');
  expect(JSON.parse(fetcher.mock.calls[0][1].body).username).toBe('Reader.One@Example.com');
});


it('shows signup errors rather than claiming an account request was saved', async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: false, status: 422, json: async () => ({ detail: 'Use a 3–64 character username or email-shaped sign-in name' }) });
  vi.stubGlobal('fetch', fetcher);
  render(<AuthGate onAuthenticated={vi.fn()} />);
  fireEvent.click(screen.getByText('Request account'));
  fireEvent.change(screen.getByLabelText('Username or email-shaped sign-in name'), { target: { value: 'bad@' } });
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'long-password-here' } });
  fireEvent.click(screen.getByText('Request approval'));
  expect(await screen.findByRole('alert')).toHaveTextContent('Use a 3–64 character username');
  expect(screen.queryByText(/Request saved as pending/)).toBeNull();
});
