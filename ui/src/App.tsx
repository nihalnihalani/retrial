import { useState } from 'react';
import { TournamentBoard } from './components/TournamentBoard';

export default function App() {
  // Bumping the key remounts the board, which re-inits the reducer and the
  // event stream — a clean restart for rehearsals / repeated demos.
  const [runId, setRunId] = useState(0);
  return <TournamentBoard key={runId} onRestart={() => setRunId((n) => n + 1)} />;
}
