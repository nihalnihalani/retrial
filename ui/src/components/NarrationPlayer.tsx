import { useEffect, useRef, useState } from 'react';
import type { NarrationState } from '../types';

const SERVER_ORIGIN = 'http://localhost:8000';

interface Props {
  narration: NarrationState;
}

/**
 * The spoken flake autopsy, offered next to the verdict.
 *
 * DELIBERATELY NOT AUTOPLAY. Two reasons, and the second is the real one:
 * browsers block unprompted audio without a user gesture anyway, but more
 * importantly a voice firing by itself mid-pitch talks over the presenter. The
 * operator decides when the room hears it.
 *
 * The transcript is one click away because the narration makes claims about
 * measured numbers; anything spoken should be readable and checkable against
 * the board. If they ever disagree, the transcript is what makes that visible
 * instead of invisible.
 */
export function NarrationPlayer({ narration }: Props) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [showScript, setShowScript] = useState(false);
  const [failed, setFailed] = useState(false);

  // A second run's narration replaces the first. Without this reset, the button
  // would keep rendering "playing" state left over from the previous audio.
  useEffect(() => {
    setPlaying(false);
    setFailed(false);
  }, [narration.url]);

  const toggle = () => {
    const el = audioRef.current;
    if (!el) return;
    if (playing) {
      el.pause();
      return;
    }
    // play() rejects on autoplay policy or a decode error; surface it as a
    // disabled-looking control rather than an unhandled rejection in console.
    void el.play().catch(() => setFailed(true));
  };

  return (
    <div className="narration">
      <audio
        ref={audioRef}
        src={`${SERVER_ORIGIN}${narration.url}`}
        preload="auto"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onError={() => setFailed(true)}
      />
      <button
        type="button"
        className="narration-play"
        onClick={toggle}
        disabled={failed}
        aria-label={playing ? 'Pause the spoken autopsy' : 'Play the spoken autopsy'}
      >
        <span aria-hidden="true">{playing ? '❚❚' : '▶'}</span>
        <span className="narration-label">
          {failed ? 'autopsy audio unavailable' : 'Hear the autopsy'}
        </span>
        <span className="narration-meta mono">{Math.round(narration.durationS)}s</span>
      </button>

      <button
        type="button"
        className="narration-toggle"
        onClick={() => setShowScript((s) => !s)}
        aria-expanded={showScript}
      >
        {showScript ? 'hide transcript' : 'transcript'}
      </button>

      {showScript && (
        <div className="narration-script">
          <p>{narration.script}</p>
          {/* Provenance, not decoration: which voice and which model spoke, so
              the audio is as attributable as the Braintrust permalinks are. */}
          <p className="narration-provenance mono">
            ElevenLabs · {narration.modelId} · voice {narration.voiceId}
          </p>
        </div>
      )}
    </div>
  );
}
