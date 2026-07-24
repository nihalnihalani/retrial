// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { NarrationPlayer } from './NarrationPlayer';
import type { NarrationState } from '../types';

afterEach(cleanup);

const narration: NarrationState = {
  url: '/narration/run-abc',
  durationS: 51.36,
  script: '[serious] Flake autopsy. Subject: test dict order. 50 percent flake rate.',
  voiceId: 'XrExE9yKIg1WjnnlVkGX',
  modelId: 'eleven_v3',
};

describe('NarrationPlayer', () => {
  it('points the audio element at the engine, not a relative path', () => {
    // The UI is served by Vite on :5173/:5174 while the mp3 lives on the engine
    // at :8000 — a relative src would 404 against the dev server.
    const { container } = render(<NarrationPlayer narration={narration} />);
    const audio = container.querySelector('audio');
    expect(audio).toHaveAttribute('src', 'http://localhost:8000/narration/run-abc');
  });

  it('does NOT autoplay', () => {
    // Load-bearing: unprompted audio talks over the presenter mid-pitch, and
    // browsers block it anyway. The operator decides when the room hears it.
    const { container } = render(<NarrationPlayer narration={narration} />);
    const audio = container.querySelector('audio');
    expect(audio).not.toHaveAttribute('autoplay');
  });

  it('shows the duration and offers playback', () => {
    render(<NarrationPlayer narration={narration} />);
    expect(screen.getByText('51s')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /play the spoken autopsy/i }),
    ).toBeInTheDocument();
  });

  it('reveals the transcript and the voice/model provenance on demand', () => {
    // Spoken claims must be readable and checkable against the board; the
    // provenance line makes the audio as attributable as a Braintrust permalink.
    render(<NarrationPlayer narration={narration} />);
    expect(screen.queryByText(/Flake autopsy/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /transcript/i }));

    expect(screen.getByText(/Flake autopsy/)).toBeInTheDocument();
    expect(screen.getByText(/ElevenLabs · eleven_v3 · voice XrExE9yKIg1WjnnlVkGX/)).toBeInTheDocument();
  });

  it('degrades to a disabled control when the audio cannot load', () => {
    // A 404/decode failure must read as "unavailable", never as a live button
    // that silently does nothing when clicked on stage.
    const { container } = render(<NarrationPlayer narration={narration} />);
    fireEvent.error(container.querySelector('audio')!);

    const btn = screen.getByRole('button', { name: /play the spoken autopsy/i });
    expect(btn).toBeDisabled();
    expect(btn).toHaveTextContent('autopsy audio unavailable');
  });

  it('surfaces a rejected play() instead of throwing an unhandled rejection', async () => {
    // jsdom has no media stack, so play() is stubbed to reject the way a real
    // browser does under autoplay policy.
    const { container } = render(<NarrationPlayer narration={narration} />);
    const audio = container.querySelector('audio')!;
    vi.spyOn(audio, 'play').mockRejectedValue(new Error('NotAllowedError'));

    fireEvent.click(screen.getByRole('button', { name: /play the spoken autopsy/i }));

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /play the spoken autopsy/i })).toBeDisabled(),
    );
  });
});
