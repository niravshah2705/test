import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  BOARD_SIZE,
  applyTurn,
  buildBoardSquares,
  createInitialState,
  resolveMove,
  rollDie
} from "../src/game.js";

describe("snakes and ladders game logic", () => {
  it("builds a 100-square serpentine board from top to bottom", () => {
    const squares = buildBoardSquares();

    assert.equal(squares.length, BOARD_SIZE);
    assert.deepEqual(squares.slice(0, 10), [100, 99, 98, 97, 96, 95, 94, 93, 92, 91]);
    assert.deepEqual(squares.slice(90), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
  });

  it("rolls values from 1 through 6", () => {
    assert.equal(rollDie(() => 0), 1);
    assert.equal(rollDie(() => 0.999), 6);
  });

  it("resolves ladders, snakes, and exact-roll overshoots", () => {
    assert.deepEqual(resolveMove(1, 3), {
      start: 1,
      roll: 3,
      target: 4,
      final: 14,
      exactRollRequired: false,
      portal: { to: 14, type: "ladder" }
    });

    assert.deepEqual(resolveMove(12, 5), {
      start: 12,
      roll: 5,
      target: 17,
      final: 7,
      exactRollRequired: false,
      portal: { to: 7, type: "snake" }
    });

    assert.deepEqual(resolveMove(98, 3), {
      start: 98,
      roll: 3,
      target: 98,
      final: 98,
      exactRollRequired: true,
      portal: null
    });
  });

  it("advances turns, applies portals, and records the latest roll", () => {
    const { state, result } = applyTurn(createInitialState(), 3);

    assert.equal(result.playerName, "Coral");
    assert.equal(result.target, 4);
    assert.equal(result.final, 14);
    assert.equal(state.players[0].position, 14);
    assert.equal(state.players[1].position, 1);
    assert.equal(state.currentPlayerIndex, 1);
    assert.equal(state.turn, 2);
    assert.equal(state.lastRoll, 3);
  });

  it("keeps a winner active after reaching square 100", () => {
    const winningSetup = {
      ...createInitialState(),
      players: [
        { id: "coral", name: "Coral", token: "C", position: 94 },
        { id: "teal", name: "Teal", token: "T", position: 1 }
      ],
      turn: 12
    };

    const { state, result } = applyTurn(winningSetup, 6);

    assert.equal(result.winnerId, "coral");
    assert.equal(state.winnerId, "coral");
    assert.equal(state.players[0].position, 100);
    assert.equal(state.currentPlayerIndex, 0);
    assert.equal(state.turn, 13);
  });
});
