import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import TireOcrPrompt from './TireOcrPrompt';

const response = data => ({ ok: true, json: async () => data });
const base = { version: 1, running: true, enabled: true, voice_enabled: false, ocr_english: false, ocr_language: 'ko', ocr_install: null };

test('asks for English OCR when the iRacing tire overlay runs without it, then rechecks after the install', async () => {
  let state = { ...base };
  const fetcher = jest.fn(async (url, options = {}) => {
    if (url.includes('/ocr-language/install')) state = { ...state, ocr_install: 'requested' };
    if (url.includes('/ocr-language/recheck')) state = { ...state, ocr_english: true, ocr_install: null };
    return response({ ...state });
  });
  render(<TireOcrPrompt apiBase="" fetcher={fetcher} />);
  expect(await screen.findByRole('dialog', { name: '영어 Windows OCR을 설치해 주세요' })).toBeVisible();
  expect(screen.getByText(/현재 언어\(ko\)로/)).toBeVisible();
  expect(fetcher).toHaveBeenCalledWith('/api/tire-overlay?game=iracing', expect.anything());
  fireEvent.click(screen.getByRole('button', { name: '영어 OCR 설치 (관리자 승인)' }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/tire-overlay/ocr-language/install?game=iracing', { method: 'POST' }));
  fireEvent.click(await screen.findByRole('button', { name: '다시 확인' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
});

test('stays quiet when English OCR is present, the tire features are off, or the driver says later', async () => {
  for (const state of [{ ...base, ocr_english: true }, { ...base, enabled: false }, { ...base, running: false }, { ...base, ocr_english: null }]) {
    const fetcher = jest.fn(async () => response(state));
    const { unmount } = render(<TireOcrPrompt apiBase="" fetcher={fetcher} />);
    await waitFor(() => expect(fetcher).toHaveBeenCalled());
    await act(async () => {});
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    unmount();
  }
  const fetcher = jest.fn(async () => response({ ...base, voice_enabled: true, enabled: false }));
  render(<TireOcrPrompt apiBase="" fetcher={fetcher} />);
  fireEvent.click(await screen.findByRole('button', { name: '나중에' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
});

test('an install that Windows refuses is shown and can be tried again', async () => {
  const fetcher = jest.fn(async url => url.includes('/install')
    ? { ok: false, json: async () => ({ detail: '관리자 승인이 취소되었습니다.' }) } : response(base));
  render(<TireOcrPrompt apiBase="" fetcher={fetcher} />);
  fireEvent.click(await screen.findByRole('button', { name: '영어 OCR 설치 (관리자 승인)' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('관리자 승인이 취소되었습니다.');
  expect(screen.getByRole('button', { name: '영어 OCR 설치 (관리자 승인)' })).toBeEnabled();
});
