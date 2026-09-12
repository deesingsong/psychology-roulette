import type { RoomSessionView, RoomView } from "./types";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });

  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(payload?.detail ?? "Something went wrong.");
  }

  return response.json() as Promise<T>;
}

export const api = {
  createRoom(hostName: string) {
    return request<RoomSessionView>("/api/rooms", {
      method: "POST",
      body: JSON.stringify({ host_name: hostName }),
    });
  },
  joinRoom(code: string, name: string) {
    return request<RoomSessionView>(`/api/rooms/${code}/players`, {
      method: "POST",
      body: JSON.stringify({ name }),
    });
  },
  getRoom(code: string) {
    return request<RoomView>(`/api/rooms/${code}`);
  },
  startRoom(code: string, playerId: string) {
    return request<RoomView>(`/api/rooms/${code}/start`, {
      method: "POST",
      body: JSON.stringify({ player_id: playerId }),
    });
  },
  submitAnswer(
    code: string,
    playerId: string,
    position: number,
    confidence: number,
  ) {
    return request<RoomView>(`/api/rooms/${code}/answers`, {
      method: "POST",
      body: JSON.stringify({ player_id: playerId, position, confidence }),
    });
  },
  submitModifier(code: string, playerId: string, value: number | string) {
    return request<RoomView>(`/api/rooms/${code}/modifier-submissions`, {
      method: "POST",
      body: JSON.stringify({ player_id: playerId, value }),
    });
  },
  reveal(code: string, playerId: string) {
    return request<RoomView>(`/api/rooms/${code}/reveal`, {
      method: "POST",
      body: JSON.stringify({ player_id: playerId }),
    });
  },
  advance(code: string, playerId: string) {
    return request<RoomView>(`/api/rooms/${code}/advance`, {
      method: "POST",
      body: JSON.stringify({ player_id: playerId }),
    });
  },
};
