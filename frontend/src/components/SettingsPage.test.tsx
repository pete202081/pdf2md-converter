import { afterEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import SettingsPage from './SettingsPage';

afterEach(() => vi.unstubAllGlobals());
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
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(4));
  expect(fetcher.mock.calls[3][1].headers['X-CSRF-Token']).toBe('csrf-123');
  expect(JSON.parse(fetcher.mock.calls[3][1].body).vlm_provider).toBe('openrouter');
  expect(JSON.parse(fetcher.mock.calls[3][1].body).openrouter_api_key).toBeUndefined();
});
