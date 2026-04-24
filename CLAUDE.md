# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A single-file browser-based Tic-Tac-Toe game. Everything — markup, styles, and logic — lives in `tictactoe.html`. There is no build step, no dependencies, and no server required.

## Running the Game

Open `tictactoe.html` directly in a browser. No build, install, or server needed.

## Architecture

All code is self-contained in `tictactoe.html` with three sections:

- **HTML** — static 3×3 grid of `.cell` divs (indexed via `data-i="0"` through `data-i="8"`), a status line, a reset button, and a scoreboard.
- **CSS** — dark theme (`#1a1a2e` background). Win cells get an `outline` highlight via the `.win` class. Taken cells are blocked via `.taken`.
- **JavaScript (inline `<script>`)** — no framework. Key globals:
  - `board`: 9-element array of `''`, `'X'`, or `'O'`
  - `current`: active player (`'X'` or `'O'`)
  - `gameOver`: boolean gate on click handler
  - `scores`: `{ X, O, D }` object, persisted across `init()` calls within the same page session
  - `WINS`: hardcoded array of the 8 winning index triples
  - `init()` resets board/DOM but preserves `scores`
  - `checkWin()` iterates `WINS` and returns the winning triple or `null`

## Version Control

After every meaningful unit of work — a new feature, a bug fix, a refactor — stage the relevant files, commit with a clear and descriptive message, and push to `origin/main`. Never batch unrelated changes into one commit. This ensures the GitHub history always reflects a working, recoverable state of the project.
