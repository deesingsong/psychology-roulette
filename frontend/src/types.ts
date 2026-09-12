export type RoomPhase = "lobby" | "answering" | "reveal" | "complete";

export interface PlayerView {
  name: string;
  is_host: boolean;
  has_answered: boolean;
}

export interface QuestionView {
  id: string;
  prompt: string;
  category: string;
  intensity: number;
  values: string[];
}

export interface RevealedAnswer {
  player_name: string;
  position: number;
  confidence: number;
}

export interface RoomView {
  code: string;
  phase: RoomPhase;
  players: PlayerView[];
  round_number: number | null;
  round_count: number;
  question: QuestionView | null;
  revealed_answers: RevealedAnswer[] | null;
  summary: Record<string, number> | null;
}

export interface RoomSessionView {
  player_id: string;
  room: RoomView;
}

export interface Session {
  roomCode: string;
  playerId: string;
  playerName: string;
  isHost: boolean;
}
