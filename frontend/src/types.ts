export type RoomPhase =
  | "lobby"
  | "answering"
  | "modifier"
  | "reveal"
  | "follow_up"
  | "follow_up_reveal"
  | "complete";

export type ModifierType =
  | "predict_room"
  | "secret_principle"
  | "devils_advocate"
  | "steelman"
  | "change_my_mind";

export interface ModifierResult {
  player_name: string;
  value: number | string;
  score: number | null;
  movement: number | null;
}

export interface ModifierView {
  type: ModifierType;
  timing: "pre_reveal" | "post_reveal";
  title: string;
  instructions: string;
  target_player_name: string | null;
  source_player_name: string | null;
  options: (number | string)[];
  submissions_count: number;
  required_submissions: number;
  results: ModifierResult[] | null;
}

export interface RoundAnalytics {
  number: number;
  prompt: string;
  average_position: number;
  average_confidence: number;
  position_range: number;
}

export interface PlayerAnalytics {
  player_name: string;
  title: string;
  title_description: string;
  rounds_answered: number;
  average_position: number;
  average_confidence: number;
  average_room_distance: number;
  position_span: number;
  prediction_score: number | null;
  movement_total: number;
}

export interface SessionAnalytics {
  rounds_completed: number;
  overall_average_position: number;
  overall_average_confidence: number;
  widest_round_number: number;
  total_position_changes: number;
  rounds: RoundAnalytics[];
  players: PlayerAnalytics[];
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
  session_summary: SessionAnalytics | null;
}

export interface RoomSessionView {
  access_token: string;
  invite_token?: string;
  player_id: string;
  player_name: string;
  is_host: boolean;
  room: RoomView;
}

export interface RestoredRoomSessionView {
  player_id: string;
  player_name: string;
  is_host: boolean;
  room: RoomView;
}

export interface Session {
  roomCode: string;
  playerId: string;
  playerName: string;
  isHost: boolean;
  accessToken: string;
  inviteToken: string | null;
}
