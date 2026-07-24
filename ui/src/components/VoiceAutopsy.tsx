import { useRef, useState } from 'react';

// The engine serves the generated mp3 at /voice/<name> on the same host the
// board already talks to (see TOURNAMENT_URL / WS_URL). voice_ready carries a
// path relative to that origin.
const ENGINE_ORIGIN = 'http://localhost:8000';

/**
 * Play control for the ElevenLabs "flake autopsy" — a spoken, disclosed summary
 * of the verdict the engine emits (VOICE=1). Output only; nothing is recorded.
 */
export function VoiceAutopsy({ url, text }: { url: string; text?: string }) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const src = url.startsWith('http') ? url : `${ENGINE_ORIGIN}${url}`;

  const toggle = () => {
    const a = audioRef.current;
    if (!a) return;
    if (playing) a.pause();
    else void a.play().catch(() => setPlaying(false));
  };

  return (
    <div className="voice-autopsy">
      <button
        type="button"
        className="voice-autopsy-btn"
        onClick={toggle}
        aria-label={playing ? 'Pause the flake autopsy narration' : 'Play the flake autopsy narration'}
      >
        <span className="voice-autopsy-icon" aria-hidden="true">{playing ? '❚❚' : '►'}</span>
        {playing ? 'Pause autopsy' : 'Play the flake autopsy'}
        <span className="voice-autopsy-tag">ElevenLabs</span>
      </button>
      {text && <p className="voice-autopsy-caption">{text}</p>}
      <audio
        ref={audioRef}
        src={src}
        preload="none"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
      />
    </div>
  );
}
