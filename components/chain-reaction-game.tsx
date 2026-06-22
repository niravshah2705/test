"use client";

import {
  type CSSProperties,
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

export const ROWS = 6;
export const COLUMNS = 8;
export const MAX_PLAYERS = 4;
export const PLAYER_COLORS = ["#e03f3f", "#0f8f7d", "#d88a16", "#6f54d9"];

export type Cell = {
  ownerId: string | null;
  count: number;
};

export type Player = {
  id: string;
  name: string;
  email: string | null;
  avatarUrl: string | null;
  color: string;
  isGuest: boolean;
  joinedAt: number;
  connectedAt: number;
  turnsTaken: number;
};

export type GamePhase = "waiting" | "playing" | "finished";

export type GameState = {
  roomId: string;
  board: Cell[][];
  players: Player[];
  currentPlayerId: string | null;
  phase: GamePhase;
  winnerId: string | null;
  moveLog: string[];
  version: number;
  createdAt: number;
  updatedAt: number;
};

export type SignedInUser = {
  id: string;
  name: string;
  email: string | null;
  avatarUrl: string | null;
  isGuest: boolean;
};

type RoomMessage = {
  type: "state";
  state: GameState;
};

type GoogleCredentialResponse = {
  credential?: string;
};

type GoogleCredentialPayload = {
  sub: string;
  name?: string;
  email?: string;
  picture?: string;
};

type GoogleIdentityApi = {
  accounts: {
    id: {
      initialize(options: {
        client_id: string;
        callback: (response: GoogleCredentialResponse) => void;
      }): void;
      renderButton(
        element: HTMLElement,
        options: {
          theme: "outline" | "filled_black";
          size: "large" | "medium" | "small";
          text: "signin_with";
          shape: "rectangular";
          width: number;
        },
      ): void;
      prompt(): void;
      cancel(): void;
    };
  };
};

declare global {
  interface Window {
    google?: GoogleIdentityApi;
  }
}

export function createEmptyBoard(): Cell[][] {
  return Array.from({ length: ROWS }, () =>
    Array.from({ length: COLUMNS }, () => ({ ownerId: null, count: 0 })),
  );
}

function createRoomCode(): string {
  const value =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID().slice(0, 8)
      : Math.random().toString(36).slice(2, 10);

  return `CR-${value}`.toUpperCase();
}

function createSessionId(prefix: string): string {
  if (typeof window === "undefined") {
    return `${prefix}:server`;
  }

  const storageKey = `chain-reaction-${prefix}-id`;
  const existing = window.sessionStorage.getItem(storageKey);
  if (existing) {
    return existing;
  }

  const generated =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? `${prefix}:${crypto.randomUUID()}`
      : `${prefix}:${Math.random().toString(36).slice(2)}`;
  window.sessionStorage.setItem(storageKey, generated);
  return generated;
}

function normalizeRoomCode(value: string): string {
  const cleaned = value
    .trim()
    .toUpperCase()
    .replace(/[^A-Z0-9-]/g, "")
    .slice(0, 18);

  return cleaned || createRoomCode();
}

function getRoomStorageKey(roomId: string): string {
  return `chain-reaction-room:${roomId}`;
}

function getRoomChannelName(roomId: string): string {
  return `chain-reaction:${roomId}`;
}

export function getCapacity(row: number, column: number): number {
  let capacity = 4;

  if (row === 0 || row === ROWS - 1) {
    capacity -= 1;
  }

  if (column === 0 || column === COLUMNS - 1) {
    capacity -= 1;
  }

  return capacity;
}

function getNeighbors(row: number, column: number): Array<[number, number]> {
  return [
    [row - 1, column],
    [row + 1, column],
    [row, column - 1],
    [row, column + 1],
  ].filter(
    ([neighborRow, neighborColumn]) =>
      neighborRow >= 0 &&
      neighborRow < ROWS &&
      neighborColumn >= 0 &&
      neighborColumn < COLUMNS,
  ) as Array<[number, number]>;
}

function cloneBoard(board: Cell[][]): Cell[][] {
  return board.map((row) => row.map((cell) => ({ ...cell })));
}

function readRoomState(roomId: string): GameState | null {
  if (typeof window === "undefined") {
    return null;
  }

  const stored = window.localStorage.getItem(getRoomStorageKey(roomId));
  if (!stored) {
    return null;
  }

  try {
    return JSON.parse(stored) as GameState;
  } catch {
    return null;
  }
}

export function createGame(roomId: string, now = Date.now()): GameState {
  return {
    roomId,
    board: createEmptyBoard(),
    players: [],
    currentPlayerId: null,
    phase: "waiting",
    winnerId: null,
    moveLog: [],
    version: 0,
    createdAt: now,
    updatedAt: now,
  };
}

function toPlayer(user: SignedInUser, existingCount: number): Player {
  return {
    id: user.id,
    name: user.name,
    email: user.email,
    avatarUrl: user.avatarUrl,
    color: PLAYER_COLORS[existingCount % PLAYER_COLORS.length],
    isGuest: user.isGuest,
    joinedAt: Date.now(),
    connectedAt: Date.now(),
    turnsTaken: 0,
  };
}

export function addOrRefreshPlayer(
  state: GameState,
  user: SignedInUser,
): GameState {
  const players = [...state.players];
  const existingIndex = players.findIndex((player) => player.id === user.id);

  if (existingIndex >= 0) {
    players[existingIndex] = {
      ...players[existingIndex],
      name: user.name,
      email: user.email,
      avatarUrl: user.avatarUrl,
      connectedAt: Date.now(),
    };
  } else if (players.length < MAX_PLAYERS) {
    players.push(toPlayer(user, players.length));
  }

  return {
    ...state,
    players,
    currentPlayerId: state.currentPlayerId ?? players[0]?.id ?? null,
    phase: players.length > 1 ? "playing" : "waiting",
  };
}

function decodeGoogleCredential(
  credential: string,
): GoogleCredentialPayload | null {
  try {
    const [, payload] = credential.split(".");
    if (!payload) {
      return null;
    }

    const normalized = payload.replace(/-/g, "+").replace(/_/g, "/");
    return JSON.parse(window.atob(normalized)) as GoogleCredentialPayload;
  } catch {
    return null;
  }
}

function applyExplosion(board: Cell[][], row: number, column: number): void {
  const queue: Array<[number, number]> = [[row, column]];
  let guard = 0;

  while (queue.length > 0 && guard < 10000) {
    guard += 1;
    const [currentRow, currentColumn] = queue.shift()!;
    const cell = board[currentRow][currentColumn];
    const capacity = getCapacity(currentRow, currentColumn);

    if (cell.count < capacity || !cell.ownerId) {
      continue;
    }

    const ownerId = cell.ownerId;
    cell.count -= capacity;
    cell.ownerId = cell.count > 0 ? ownerId : null;

    for (const [neighborRow, neighborColumn] of getNeighbors(
      currentRow,
      currentColumn,
    )) {
      const neighbor = board[neighborRow][neighborColumn];
      neighbor.ownerId = ownerId;
      neighbor.count += 1;

      if (neighbor.count >= getCapacity(neighborRow, neighborColumn)) {
        queue.push([neighborRow, neighborColumn]);
      }
    }
  }
}

function getOwnedPlayerIds(board: Cell[][]): Set<string> {
  const owners = new Set<string>();

  for (const row of board) {
    for (const cell of row) {
      if (cell.ownerId) {
        owners.add(cell.ownerId);
      }
    }
  }

  return owners;
}

function selectNextPlayer(
  players: Player[],
  candidateIds: Set<string>,
  currentPlayerId: string,
): string | null {
  const currentIndex = players.findIndex((player) => player.id === currentPlayerId);

  for (let offset = 1; offset <= players.length; offset += 1) {
    const candidate = players[(currentIndex + offset + players.length) % players.length];
    if (candidateIds.has(candidate.id)) {
      return candidate.id;
    }
  }

  return players.find((player) => candidateIds.has(player.id))?.id ?? null;
}

export function applyMove(
  state: GameState,
  row: number,
  column: number,
  playerId: string,
): { nextState: GameState | null; error: string | null } {
  if (state.phase === "finished") {
    return { nextState: null, error: "This room already has a winner." };
  }

  if (state.currentPlayerId !== playerId) {
    return { nextState: null, error: "It is another player's turn." };
  }

  const player = state.players.find((candidate) => candidate.id === playerId);
  if (!player) {
    return { nextState: null, error: "Join the room before placing an orb." };
  }

  const currentCell = state.board[row][column];
  if (currentCell.ownerId && currentCell.ownerId !== playerId) {
    return { nextState: null, error: "Choose an empty cell or one you already own." };
  }

  const board = cloneBoard(state.board);
  board[row][column] = {
    ownerId: playerId,
    count: board[row][column].count + 1,
  };
  applyExplosion(board, row, column);

  const players = state.players.map((candidate) =>
    candidate.id === playerId
      ? { ...candidate, turnsTaken: candidate.turnsTaken + 1 }
      : candidate,
  );
  const ownedPlayerIds = getOwnedPlayerIds(board);
  const everyoneHasMoved =
    players.length > 1 && players.every((candidate) => candidate.turnsTaken > 0);
  const remainingPlayers = players.filter((candidate) =>
    ownedPlayerIds.has(candidate.id),
  );
  const winner =
    everyoneHasMoved && remainingPlayers.length === 1 ? remainingPlayers[0] : null;
  const candidateIds = new Set(
    (everyoneHasMoved ? remainingPlayers : players).map((candidate) => candidate.id),
  );
  const nextPlayerId = winner
    ? null
    : selectNextPlayer(players, candidateIds, playerId) ?? players[0]?.id ?? null;
  const moveLog = [
    `${player.name} placed at ${String.fromCharCode(65 + column)}${row + 1}`,
    ...state.moveLog,
  ].slice(0, 8);

  return {
    error: null,
    nextState: {
      ...state,
      board,
      players,
      currentPlayerId: nextPlayerId,
      phase: winner ? "finished" : players.length > 1 ? "playing" : "waiting",
      winnerId: winner?.id ?? null,
      moveLog,
    },
  };
}

export function resetGame(state: GameState): GameState {
  const players = state.players.map((player) => ({ ...player, turnsTaken: 0 }));

  return {
    ...state,
    board: createEmptyBoard(),
    players,
    currentPlayerId: players[0]?.id ?? null,
    phase: players.length > 1 ? "playing" : "waiting",
    winnerId: null,
    moveLog: ["New round started.", ...state.moveLog].slice(0, 8),
  };
}

export function formatPlayerName(name: string): string {
  return name.trim().slice(0, 28) || "Guest player";
}

export default function ChainReactionGame() {
  const googleClientId = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID?.trim();
  const googleButtonRef = useRef<HTMLDivElement | null>(null);
  const channelRef = useRef<BroadcastChannel | null>(null);
  const [user, setUser] = useState<SignedInUser | null>(null);
  const [guestName, setGuestName] = useState("Guest player");
  const [roomInput, setRoomInput] = useState(createRoomCode());
  const [roomId, setRoomId] = useState<string | null>(null);
  const [game, setGame] = useState<GameState | null>(null);
  const [notice, setNotice] = useState<string>(
    googleClientId
      ? "Google sign-in is ready when the provider finishes loading."
      : "Google sign-in needs NEXT_PUBLIC_GOOGLE_CLIENT_ID. Guest mode is available for local play.",
  );

  const persistAndBroadcast = useCallback((nextState: GameState) => {
    const stampedState = {
      ...nextState,
      version: nextState.version + 1,
      updatedAt: Date.now(),
    };

    window.localStorage.setItem(
      getRoomStorageKey(stampedState.roomId),
      JSON.stringify(stampedState),
    );
    channelRef.current?.postMessage({
      type: "state",
      state: stampedState,
    } satisfies RoomMessage);
    setGame(stampedState);
  }, []);

  useEffect(() => {
    if (!googleClientId) {
      return;
    }

    let cancelled = false;

    const handleCredential = (response: GoogleCredentialResponse) => {
      if (!response.credential) {
        setNotice("Google did not return an identity token.");
        return;
      }

      const payload = decodeGoogleCredential(response.credential);
      if (!payload?.sub) {
        setNotice("Google sign-in returned an unreadable identity token.");
        return;
      }

      setUser({
        id: `google:${payload.sub}`,
        name: formatPlayerName(payload.name ?? payload.email ?? "Google player"),
        email: payload.email ?? null,
        avatarUrl: payload.picture ?? null,
        isGuest: false,
      });
      setNotice("Signed in with Google.");
    };

    const renderGoogleButton = () => {
      if (cancelled || !window.google || !googleButtonRef.current) {
        return;
      }

      window.google.accounts.id.initialize({
        client_id: googleClientId,
        callback: handleCredential,
      });
      googleButtonRef.current.replaceChildren();
      window.google.accounts.id.renderButton(googleButtonRef.current, {
        theme: "filled_black",
        size: "large",
        text: "signin_with",
        shape: "rectangular",
        width: 240,
      });
      setNotice("Google sign-in is ready.");
    };

    if (window.google) {
      renderGoogleButton();
      return;
    }

    const script = document.createElement("script");
    script.src = "https://accounts.google.com/gsi/client";
    script.async = true;
    script.defer = true;
    script.onload = renderGoogleButton;
    script.onerror = () => {
      setNotice("Google sign-in could not load. Guest mode is still available.");
    };
    document.head.appendChild(script);

    return () => {
      cancelled = true;
    };
  }, [googleClientId]);

  useEffect(() => {
    if (!roomId) {
      return;
    }

    const channel = new BroadcastChannel(getRoomChannelName(roomId));
    channelRef.current = channel;

    const applyRemoteState = (incomingState: GameState) => {
      if (incomingState.roomId !== roomId) {
        return;
      }

      setGame((currentState) => {
        if (
          !currentState ||
          incomingState.version > currentState.version ||
          incomingState.updatedAt > currentState.updatedAt
        ) {
          return incomingState;
        }

        return currentState;
      });
    };

    channel.onmessage = (event: MessageEvent<RoomMessage>) => {
      if (event.data?.type === "state") {
        applyRemoteState(event.data.state);
      }
    };

    const handleStorage = (event: StorageEvent) => {
      if (event.key !== getRoomStorageKey(roomId) || !event.newValue) {
        return;
      }

      try {
        applyRemoteState(JSON.parse(event.newValue) as GameState);
      } catch {
        setNotice("Received an unreadable room update.");
      }
    };

    window.addEventListener("storage", handleStorage);

    return () => {
      window.removeEventListener("storage", handleStorage);
      channel.close();
      channelRef.current = null;
    };
  }, [roomId]);

  const playerById = useMemo(() => {
    return new Map(game?.players.map((player) => [player.id, player]) ?? []);
  }, [game?.players]);

  const currentPlayer = game?.currentPlayerId
    ? playerById.get(game.currentPlayerId)
    : null;
  const winner = game?.winnerId ? playerById.get(game.winnerId) : null;

  const joinRoom = useCallback(
    (rawRoomId: string, forceNewRoom = false) => {
      if (!user) {
        setNotice("Sign in or continue as a guest before joining a room.");
        return;
      }

      const normalizedRoomId = normalizeRoomCode(rawRoomId);
      const existingState = forceNewRoom ? null : readRoomState(normalizedRoomId);
      const nextState = addOrRefreshPlayer(
        existingState ?? createGame(normalizedRoomId),
        user,
      );

      setRoomId(normalizedRoomId);
      setRoomInput(normalizedRoomId);
      persistAndBroadcast(nextState);
      setNotice(
        nextState.players.length > 1
          ? `Joined room ${normalizedRoomId}.`
          : `Room ${normalizedRoomId} is ready.`,
      );
    },
    [persistAndBroadcast, user],
  );

  const handleGuestSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setUser({
      id: createSessionId("guest"),
      name: formatPlayerName(guestName),
      email: null,
      avatarUrl: null,
      isGuest: true,
    });
    setNotice("Guest player ready.");
  };

  const handleRoomSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    joinRoom(roomInput);
  };

  const handleCreateRoom = () => {
    const nextRoomId = createRoomCode();
    joinRoom(nextRoomId, true);
  };

  const handleCellClick = (row: number, column: number) => {
    if (!game || !user) {
      return;
    }

    const result = applyMove(game, row, column, user.id);
    if (result.error || !result.nextState) {
      setNotice(result.error ?? "Move unavailable.");
      return;
    }

    persistAndBroadcast(result.nextState);
    setNotice("Move synced to the room.");
  };

  const handleResetGame = () => {
    if (!game) {
      return;
    }

    persistAndBroadcast(resetGame(game));
    setNotice("New round synced.");
  };

  const handleCopyRoom = async () => {
    if (!roomId) {
      return;
    }

    try {
      await navigator.clipboard.writeText(roomId);
      setNotice("Room code copied.");
    } catch {
      setNotice("Room code is visible in the room field.");
    }
  };

  const handleLeaveRoom = () => {
    channelRef.current?.close();
    channelRef.current = null;
    setRoomId(null);
    setGame(null);
    setNotice("Left the room.");
  };

  const canUseGoogle = Boolean(googleClientId);
  const canPlay =
    Boolean(game && user && game.currentPlayerId === user.id) &&
    game?.phase !== "finished";

  return (
    <main className="game-shell">
      <section className="game-header" aria-label="Game status">
        <div>
          <p className="game-kicker">Realtime room game</p>
          <h1>Chain Reaction</h1>
        </div>
        <div className="status-strip" role="status">
          <span>{winner ? `${winner.name} wins` : currentPlayer ? `${currentPlayer.name}'s turn` : "Waiting"}</span>
          <strong>{roomId ?? "No room"}</strong>
        </div>
      </section>

      <section className="game-layout">
        <div className="play-surface">
          {!user ? (
            <section className="auth-panel" aria-label="Sign in">
              <div>
                <h2>Choose a player identity</h2>
                <p>{notice}</p>
              </div>
              <div className="auth-actions">
                {canUseGoogle ? (
                  <div className="google-button" ref={googleButtonRef} />
                ) : (
                  <button className="button secondary" type="button" disabled>
                    Continue with Google
                  </button>
                )}
                <form className="guest-form" onSubmit={handleGuestSubmit}>
                  <label htmlFor="guest-name">Guest name</label>
                  <div className="input-row">
                    <input
                      id="guest-name"
                      maxLength={28}
                      value={guestName}
                      onChange={(event) => setGuestName(event.target.value)}
                    />
                    <button className="button primary" type="submit">
                      Continue as guest
                    </button>
                  </div>
                </form>
              </div>
            </section>
          ) : !roomId || !game ? (
            <section className="room-panel" aria-label="Room setup">
              <div>
                <h2>Join a room</h2>
                <p>{notice}</p>
              </div>
              <form className="room-form" onSubmit={handleRoomSubmit}>
                <label htmlFor="room-code">Room code</label>
                <div className="input-row">
                  <input
                    id="room-code"
                    value={roomInput}
                    onChange={(event) => setRoomInput(event.target.value)}
                    maxLength={18}
                  />
                  <button className="button primary" type="submit">
                    Join
                  </button>
                </div>
              </form>
              <div className="button-row">
                <button className="button secondary" type="button" onClick={handleCreateRoom}>
                  New room
                </button>
                <button className="button ghost" type="button" onClick={() => setUser(null)}>
                  Switch player
                </button>
              </div>
            </section>
          ) : (
            <section className="board-section" aria-label="Chain Reaction board">
              <div className="board-toolbar">
                <div>
                  <p className="room-label">Room</p>
                  <h2>{roomId}</h2>
                </div>
                <div className="button-row">
                  <button className="button secondary" type="button" onClick={handleCopyRoom}>
                    Copy code
                  </button>
                  <button className="button secondary" type="button" onClick={handleResetGame}>
                    New round
                  </button>
                  <button className="button ghost" type="button" onClick={handleLeaveRoom}>
                    Leave
                  </button>
                </div>
              </div>

              <div
                className="game-board"
                style={{ "--columns": COLUMNS } as CSSProperties}
              >
                {game.board.map((row, rowIndex) =>
                  row.map((cell, columnIndex) => {
                    const owner = cell.ownerId ? playerById.get(cell.ownerId) : null;
                    const isPlayable =
                      canPlay && (!cell.ownerId || cell.ownerId === user.id);

                    return (
                      <button
                        aria-label={`Cell ${String.fromCharCode(65 + columnIndex)}${rowIndex + 1}`}
                        className={`board-cell${owner ? " owned" : ""}${isPlayable ? " playable" : ""}`}
                        disabled={!isPlayable}
                        key={`${rowIndex}-${columnIndex}`}
                        onClick={() => handleCellClick(rowIndex, columnIndex)}
                        style={{ "--player-color": owner?.color ?? "#a6a09a" } as CSSProperties}
                        type="button"
                      >
                        <span className="cell-capacity">{getCapacity(rowIndex, columnIndex)}</span>
                        <span className="cell-orbs" data-count={Math.min(cell.count, 4)}>
                          {Array.from({ length: cell.count }).map((_, orbIndex) => (
                            <span className="orb" key={orbIndex} />
                          ))}
                        </span>
                      </button>
                    );
                  }),
                )}
              </div>
            </section>
          )}
        </div>

        <aside className="side-panel" aria-label="Room details">
          <section>
            <div className="panel-heading">
              <h2>Player</h2>
              <span>{user ? (user.isGuest ? "Guest" : "Google") : "Signed out"}</span>
            </div>
            {user ? (
              <div className="profile-row">
                {user.avatarUrl ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img alt="" src={user.avatarUrl} />
                ) : (
                  <span className="avatar-fallback">{user.name.slice(0, 1).toUpperCase()}</span>
                )}
                <div>
                  <strong>{user.name}</strong>
                  <small>{user.email ?? "Local room identity"}</small>
                </div>
              </div>
            ) : (
              <p className="muted">{notice}</p>
            )}
          </section>

          <section>
            <div className="panel-heading">
              <h2>Players</h2>
              <span>{game?.players.length ?? 0}/{MAX_PLAYERS}</span>
            </div>
            <div className="player-list">
              {game?.players.map((player) => (
                <div
                  className={`player-row${player.id === game.currentPlayerId ? " active" : ""}`}
                  key={player.id}
                >
                  <span
                    className="player-dot"
                    style={{ "--player-color": player.color } as CSSProperties}
                  />
                  <div>
                    <strong>{player.name}</strong>
                    <small>
                      {player.turnsTaken} turns · {player.isGuest ? "Guest" : "Google"}
                    </small>
                  </div>
                </div>
              )) ?? <p className="muted">No room joined.</p>}
            </div>
          </section>

          <section>
            <div className="panel-heading">
              <h2>Moves</h2>
              <span>{game?.phase ?? "idle"}</span>
            </div>
            <ol className="move-log">
              {game?.moveLog.length ? (
                game.moveLog.map((entry, index) => <li key={`${entry}-${index}`}>{entry}</li>)
              ) : (
                <li>{notice}</li>
              )}
            </ol>
          </section>
        </aside>
      </section>
    </main>
  );
}
