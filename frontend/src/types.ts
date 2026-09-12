export type RoomPhase =
  "lobby" | "answering" | "modifier" | "reveal" | "complete";

export type ModifierType =
  "predict_room" | "secret_principle" | "devils_advocate";

export interface ModifierResult {
  player_name: string;
  value: number | string;
  score: number | null;
}

export interface ModifierView {
  type: ModifierType;
  timing: "pre_reveal" | "post_reveal";
  title: string;
  instructions: string;
  target_player_name: string | null;
  options: (number | string)[];
  submissions_count: number;
  required_submissions: number;
  results: ModifierResult[] | null;
}

export interface PlayerView {
  name: string;
  is_host: boolean;
  has_answered: boolean;
  has_modifier_submitted: boolean;
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
  modifier: ModifierView | null;
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
