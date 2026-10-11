import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import SettingsPage from './SettingsPage';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });
const ok = (body: unknown) => ({ ok: true, json: async () => body });
const config = {
  vlm_provider: 'hybrid', ollama_base_url: 'http://ollama:11434', ollama_vision_model: 'llama3.2-vision',
  openrouter_vision_model: 'openai/gpt-4o-mini', openrouter_site_url: '', openrouter_app_name: 'PDF2MD Converter',
  openai_vision_model: 'gpt-4o-mini', anthropic_vision_model: 'claude-sonnet-4-5',
  vlm_timeout_seconds: 90, max_pages: 1000, max_upload_bytes: 52428800,
  keys_configured: { openrouter_api_key: true, openai_api_key: false, anthropic_api_key: false },
};

it('sets up admin, masks keys and saves provider settings with CSRF token', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok({ setup_required: true, authenticated: false, csrf_token: null }))
    .mockResolvedValueOnce(ok({ authenticated: true, csrf_token: 'csrf-123' }))
    .mockResolvedValueOnce(ok(config))
    .mockResolvedValueOnce(ok([]))
    .mockResolvedValueOnce(ok({ ...config, vlm_provider: 'openrouter' }));
  vi.stubGlobal('fetch', fetcher);
  render(<SettingsPage />);
  await screen.findByText('Create administrator account');
  expect(screen.getByText(/docker compose exec api python/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText('One-time setup code'), { target: { value: 'one-time' } });
  fireEvent.change(screen.getByLabelText('Administrator password'), { target: { value: 'a-long-password' } });
  fireEvent.click(screen.getByText('Create account'));
  await screen.findByText('Provider strategy');
  expect(screen.queryByText('private-secret')).toBeNull();
  fireEvent.change(screen.getByLabelText('Mode'), { target: { value: 'openrouter' } });
  fireEvent.click(screen.getByText('Save settings'));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(5));
  expect(fetcher.mock.calls[4][1].headers['X-CSRF-Token']).toBe('csrf-123');
  expect(JSON.parse(fetcher.mock.calls[4][1].body).vlm_provider).toBe('openrouter');
  expect(JSON.parse(fetcher.mock.calls[4][1].body).openrouter_api_key).toBeUndefined();
});


it('refreshes requests manually and when another user signs up while Settings remains open', async () => {
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
  let requests = 0;
  const fetcher = vi.fn().mockImplementation(async (url: string) => {
    if (url.endsWith('/session')) return ok({ authenticated: true, setup_required: false, csrf_token: 'csrf-123' });
    if (url.endsWith('/settings')) return ok(config);
    if (url.endsWith('/users')) {
      requests += 1;
      return ok(requests === 1 ? [] : requests === 2 ? [
        { id: 2, username: 'reader@example.com', status: 'PENDING', created_at: '2026-10-10' },
      ] : [
        { id: 2, username: 'reader@example.com', status: 'PENDING', created_at: '2026-10-10' },
        { id: 3, username: 'later', status: 'PENDING', created_at: '2026-10-10' },
      ]);
    }
    throw new Error(`Unexpected URL: ${url}`);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<SettingsPage />);
  await screen.findByText('No account requests yet.');
  fireEvent.click(screen.getByRole('button', { name: 'Refresh requests' }));
  await screen.findByText('reader@example.com');
  expect(screen.queryByText('No account requests yet.')).toBeNull();
  await act(async () => { vi.advanceTimersByTime(20_000); });
  await screen.findByText('later');
  expect(requests).toBe(3);
});

it('does not report an empty queue when loading account requests fails', async () => {
  let attempts = 0;
  const fetcher = vi.fn().mockImplementation(async (url: string) => {
    if (url.endsWith('/session')) return ok({ authenticated: true, setup_required: false, csrf_token: 'csrf-123' });
    if (url.endsWith('/settings')) return ok(config);
    if (url.endsWith('/users')) {
      attempts += 1;
      if (attempts === 1) throw new Error('Network unavailable');
      return ok([{ id: 2, username: 'reader@example.com', status: 'PENDING', created_at: '2026-10-10' }]);
    }
    throw new Error(`Unexpected URL: ${url}`);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<SettingsPage />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Network unavailable');
  expect(screen.queryByText('No account requests yet.')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Refresh requests' }));
  await screen.findByText('reader@example.com');
  expect(screen.queryByRole('alert')).toBeNull();
});

it('stores the admin-managed GitHub token only on explicit save', async () => {
  const fetcher = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/session')) return ok({ authenticated: true, setup_required: false, csrf_token: 'csrf-admin' });
    if (url.endsWith('/settings') && init?.method === 'PUT') return ok({ ...config, keys_configured: { ...config.keys_configured, github_pat: true } });
    if (url.endsWith('/settings')) return ok({ ...config, keys_configured: { ...config.keys_configured, github_pat: false } });
    if (url.endsWith('/users')) return ok([]);
    throw new Error(`Unexpected ${url}`);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<SettingsPage />);
  await screen.findByLabelText('GitHub personal access token Not configured');
  fireEvent.change(screen.getByLabelText('GitHub personal access token Not configured'), { target: { value: 'github-secret' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
  await waitFor(() => expect(fetcher.mock.calls.some(([url, init]) => url === '/api/v1/admin/settings' && init?.method === 'PUT')).toBe(true));
  const save = fetcher.mock.calls.find(([url, init]) => url === '/api/v1/admin/settings' && init?.method === 'PUT');
  expect(JSON.parse(save![1].body).github_pat).toBe('github-secret');
  expect(save![1].headers['X-CSRF-Token']).toBe('csrf-admin');
  await screen.findByLabelText('GitHub personal access token Configured');
  expect(screen.queryByDisplayValue('github-secret')).not.toBeInTheDocument();
});
