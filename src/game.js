export const BOARD_SIZE = 100;
export const START_SQUARE = 1;

export const PORTALS = Object.freeze({
  4: { to: 14, type: "ladder" },
  9: { to: 31, type: "ladder" },
  20: { to: 38, type: "ladder" },
  28: { to: 84, type: "ladder" },
  40: { to: 59, type: "ladder" },
  51: { to: 67, type: "ladder" },
  63: { to: 81, type: "ladder" },
  71: { to: 91, type: "ladder" },
  17: { to: 7, type: "snake" },
  54: { to: 34, type: "snake" },
  62: { to: 19, type: "snake" },
  64: { to: 60, type: "snake" },
  87: { to: 24, type: "snake" },
  93: { to: 73, type: "snake" },
  95: { to: 75, type: "snake" },
  99: { to: 78, type: "snake" }
});

export const PLAYERS = Object.freeze([
  { id: "coral", name: "Coral", token: "C" },
  { id: "teal", name: "Teal", token: "T" }
]);

export function createInitialState() {
  return {
    currentPlayerIndex: 0,
    players: PLAYERS.map((player) => ({ ...player, position: START_SQUARE })),
    turn: 1,
    winnerId: null,
    lastRoll: null
  };
}

export function buildBoardSquares() {
  const rows = [];

  for (let rowFromTop = 0; rowFromTop < 10; rowFromTop += 1) {
    const rowFromBottom = 9 - rowFromTop;
    const firstSquare = rowFromBottom * 10 + 1;
    const row = Array.from({ length: 10 }, (_, index) => firstSquare + index);

    if (rowFromBottom % 2 === 1) {
      row.reverse();
    }

    rows.push(...row);
  }

  return rows;
}

export function rollDie(random = Math.random) {
  return Math.floor(random() * 6) + 1;
}

export function resolveMove(position, roll) {
  validatePosition(position);
  validateRoll(roll);

  const target = position + roll;

  if (target > BOARD_SIZE) {
    return {
      start: position,
      roll,
      target: position,
      final: position,
      exactRollRequired: true,
      portal: null
    };
  }

  const portal = PORTALS[target] ?? null;

  return {
    start: position,
    roll,
    target,
    final: portal ? portal.to : target,
    exactRollRequired: false,
    portal
  };
}

export function applyTurn(state, roll) {
  if (state.winnerId) {
    return { state, result: null };
  }

  validateRoll(roll);

  const activePlayer = state.players[state.currentPlayerIndex];
  const move = resolveMove(activePlayer.position, roll);
  const nextPlayers = state.players.map((player) =>
    player.id === activePlayer.id ? { ...player, position: move.final } : player
  );
  const winnerId = move.final === BOARD_SIZE ? activePlayer.id : null;
  const nextPlayerIndex = winnerId
    ? state.currentPlayerIndex
    : (state.currentPlayerIndex + 1) % state.players.length;

  return {
    state: {
      ...state,
      players: nextPlayers,
      currentPlayerIndex: nextPlayerIndex,
      turn: state.turn + 1,
      winnerId,
      lastRoll: roll
    },
    result: {
      ...move,
      playerId: activePlayer.id,
      playerName: activePlayer.name,
      winnerId
    }
  };
}

function validateRoll(roll) {
  if (!Number.isInteger(roll) || roll < 1 || roll > 6) {
    throw new RangeError("Roll must be an integer from 1 to 6.");
  }
}

function validatePosition(position) {
  if (!Number.isInteger(position) || position < START_SQUARE || position > BOARD_SIZE) {
    throw new RangeError(`Position must be an integer from ${START_SQUARE} to ${BOARD_SIZE}.`);
  }
}
