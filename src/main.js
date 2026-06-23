import {
  BOARD_SIZE,
  PORTALS,
  applyTurn,
  buildBoardSquares,
  createInitialState,
  rollDie
} from "./game.js";

const board = document.querySelector("#board");
const pathLayer = document.querySelector("#pathLayer");
const rollButton = document.querySelector("#rollButton");
const resetButton = document.querySelector("#resetButton");
const diceFace = document.querySelector("#diceFace");
const turnLabel = document.querySelector("#turnLabel");
const turnCount = document.querySelector("#turnCount");
const playersPanel = document.querySelector("#players");
const eventLog = document.querySelector("#eventLog");

const cellBySquare = new Map();
const messages = [];
let state = createInitialState();
let busy = false;

function renderBoard() {
  board.innerHTML = "";
  cellBySquare.clear();

  buildBoardSquares().forEach((square, index) => {
    const cell = document.createElement("div");
    const portal = PORTALS[square];

    cell.className = "board-cell";
    cell.dataset.square = String(square);
    cell.dataset.tone = String(index % 5);
    cell.setAttribute("aria-label", `Square ${square}`);

    const number = document.createElement("span");
    number.className = "square-number";
    number.textContent = String(square);
    cell.append(number);

    if (portal) {
      const badge = document.createElement("span");
      badge.className = `portal-badge ${portal.type}`;
      badge.textContent = portal.type === "ladder" ? `↑ ${portal.to}` : `↓ ${portal.to}`;
      cell.append(badge);
    }

    board.append(cell);
    cellBySquare.set(square, cell);
  });
}

function renderGame() {
  renderTokens();
  renderPlayers();
  renderStatus();
  renderLog();
}

function renderTokens() {
  document.querySelectorAll(".token").forEach((token) => token.remove());

  state.players.forEach((player, index) => {
    const cell = cellBySquare.get(player.position);
    const token = document.createElement("span");
    const midpoint = (state.players.length - 1) / 2;

    token.className = `token token-${player.id}`;
    token.textContent = player.token;
    token.style.setProperty("--token-shift", `${(index - midpoint) * 24}px`);
    token.style.setProperty("--token-mobile-shift", `${(index - midpoint) * 12}px`);
    token.setAttribute("aria-label", `${player.name} on square ${player.position}`);

    cell.append(token);
  });
}

function renderPlayers() {
  playersPanel.innerHTML = "";

  state.players.forEach((player, index) => {
    const card = document.createElement("article");
    const active = index === state.currentPlayerIndex && !state.winnerId;

    card.className = `player-card ${active ? "active" : ""}`;
    card.innerHTML = `
      <div class="player-token token-${player.id}" aria-hidden="true">${player.token}</div>
      <div>
        <h2>${player.name}</h2>
        <p>Square ${player.position}</p>
      </div>
    `;

    playersPanel.append(card);
  });
}

function renderStatus() {
  const winner = state.players.find((player) => player.id === state.winnerId);
  const activePlayer = state.players[state.currentPlayerIndex];

  turnLabel.textContent = winner ? `${winner.name} wins` : `${activePlayer.name}'s turn`;
  turnCount.textContent = String(state.turn);
  diceFace.textContent = state.lastRoll ? String(state.lastRoll) : "-";
  rollButton.disabled = busy || Boolean(state.winnerId);
}

function renderLog() {
  eventLog.innerHTML = "";

  messages.slice(0, 6).forEach((message) => {
    const item = document.createElement("li");
    item.textContent = message;
    eventLog.append(item);
  });
}

function drawPaths() {
  const boardRect = board.getBoundingClientRect();
  pathLayer.setAttribute("viewBox", `0 0 ${boardRect.width} ${boardRect.height}`);
  pathLayer.innerHTML = "";

  Object.entries(PORTALS).forEach(([fromSquare, portal]) => {
    const from = getSquareCenter(Number(fromSquare), boardRect);
    const to = getSquareCenter(portal.to, boardRect);

    if (!from || !to) {
      return;
    }

    if (portal.type === "ladder") {
      drawLadder(from, to);
    } else {
      drawSnake(from, to);
    }
  });
}

function drawLadder(from, to) {
  const group = createSvgElement("g");
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const length = Math.hypot(dx, dy);
  const offsetX = (-dy / length) * 6;
  const offsetY = (dx / length) * 6;
  const railA = createLine(from.x + offsetX, from.y + offsetY, to.x + offsetX, to.y + offsetY);
  const railB = createLine(from.x - offsetX, from.y - offsetY, to.x - offsetX, to.y - offsetY);

  group.classList.add("ladder-path");
  group.append(railA, railB);

  for (let index = 1; index < 6; index += 1) {
    const ratio = index / 6;
    const x = from.x + dx * ratio;
    const y = from.y + dy * ratio;
    group.append(createLine(x + offsetX, y + offsetY, x - offsetX, y - offsetY));
  }

  pathLayer.append(group);
}

function drawSnake(from, to) {
  const controlX = (from.x + to.x) / 2 + (from.y - to.y) * 0.18;
  const controlY = (from.y + to.y) / 2 + (to.x - from.x) * 0.18;
  const path = createSvgElement("path");
  const head = createSvgElement("circle");

  path.classList.add("snake-path");
  path.setAttribute("d", `M ${from.x} ${from.y} Q ${controlX} ${controlY} ${to.x} ${to.y}`);
  head.classList.add("snake-head");
  head.setAttribute("cx", String(from.x));
  head.setAttribute("cy", String(from.y));
  head.setAttribute("r", "5");

  pathLayer.append(path, head);
}

function createLine(x1, y1, x2, y2) {
  const line = createSvgElement("line");

  line.setAttribute("x1", String(x1));
  line.setAttribute("y1", String(y1));
  line.setAttribute("x2", String(x2));
  line.setAttribute("y2", String(y2));

  return line;
}

function createSvgElement(tagName) {
  return document.createElementNS("http://www.w3.org/2000/svg", tagName);
}

function getSquareCenter(square, boardRect) {
  const cell = cellBySquare.get(square);

  if (!cell) {
    return null;
  }

  const rect = cell.getBoundingClientRect();

  return {
    x: rect.left - boardRect.left + rect.width / 2,
    y: rect.top - boardRect.top + rect.height / 2
  };
}

async function handleRoll(forcedRoll) {
  if (busy || state.winnerId) {
    return null;
  }

  busy = true;
  renderStatus();

  const roll = forcedRoll ?? rollDie();
  const originalState = state;
  const activePlayer = originalState.players[originalState.currentPlayerIndex];
  const { state: nextState, result } = applyTurn(originalState, roll);

  state = { ...state, lastRoll: roll };
  renderStatus();

  if (!result.exactRollRequired) {
    await animatePlayer(activePlayer.id, result.start, result.target);

    if (result.portal) {
      await wait(220);
      state = setPlayerPosition(state, activePlayer.id, result.final);
      renderGame();
      await wait(220);
    }
  }

  state = nextState;
  messages.unshift(formatMessage(result));
  busy = false;
  renderGame();
  return result;
}

async function animatePlayer(playerId, start, target) {
  const direction = target >= start ? 1 : -1;

  for (let square = start + direction; direction > 0 ? square <= target : square >= target; square += direction) {
    state = setPlayerPosition(state, playerId, square);
    renderGame();
    await wait(90);
  }
}

function setPlayerPosition(baseState, playerId, position) {
  return {
    ...baseState,
    players: baseState.players.map((player) =>
      player.id === playerId ? { ...player, position } : player
    )
  };
}

function formatMessage(result) {
  if (result.exactRollRequired) {
    return `${result.playerName} rolled ${result.roll} and stayed on ${result.start}.`;
  }

  if (result.winnerId) {
    return `${result.playerName} rolled ${result.roll} and reached ${BOARD_SIZE}.`;
  }

  if (result.portal?.type === "ladder") {
    return `${result.playerName} rolled ${result.roll}, climbed from ${result.target} to ${result.final}.`;
  }

  if (result.portal?.type === "snake") {
    return `${result.playerName} rolled ${result.roll}, slid from ${result.target} to ${result.final}.`;
  }

  return `${result.playerName} rolled ${result.roll} and moved to ${result.final}.`;
}

function resetGame() {
  state = createInitialState();
  messages.length = 0;
  busy = false;
  renderGame();
}

function wait(milliseconds) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, milliseconds);
  });
}

rollButton.addEventListener("click", () => {
  void handleRoll();
});
resetButton.addEventListener("click", resetGame);
window.addEventListener("resize", drawPaths);

renderBoard();
renderGame();
requestAnimationFrame(drawPaths);

window.__snakesLaddersTest = {
  roll: handleRoll,
  reset: resetGame,
  getState: () => JSON.parse(JSON.stringify(state)),
  setState: (nextState) => {
    state = nextState;
    busy = false;
    renderGame();
  }
};
