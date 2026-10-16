# Battleship: Land of 10,000 Lakes Edition

A text-based, single-player Battleship game in Python 3 with a Minnesota theme.
You command the **North Star Fleet** on Lake Superior against the **Cheesehead Navy**,
a computer opponent sailing over from Wisconsin. Uff da!

## Play in your browser

**https://kschnugg.github.io/minnesota-battleship/**

No install needed. It works on phones too. The page runs the same Python game in the browser
with [Pyodide](https://pyodide.org) (Python compiled to WebAssembly), so the first visit can take
10-20 seconds to load. On an iPhone, turn off silent mode to hear the explosions.

## Play on your computer

### Requirements

- Python 3.7 or newer
- No third-party packages

### Run it

```bash
python3 battleship_mn.py            # with explosion sound on hits
python3 battleship_mn.py --no-sound # silent
```

## How to play

- **Board:** 10x10. Rows are `A`-`J` (down the side), columns are `1`-`10` (across the top).
- **Coordinates:** type `A5` (row A, column 5) or `1 5` (row 1, column 5). Both mean the same square.
- **Placing ships:** choose `M` to place each ship yourself or `R` for random placement.
  A ship starts at the coordinate you give and extends in the direction you choose:
  `H` = right, `V` = down, `L` = left, `U` = up.
- **Turns:** you fire first, then the computer. You can't fire at the same square twice.
- **Winning:** sink all 17 enemy ship segments before the computer sinks yours.

| Symbol | Meaning |
| ------ | ------- |
| `O` | Open water |
| `S` | Your ship (only shown on your own board) |
| `X` | Hit |
| `M` | Miss |

### The fleet

| Ship | Size | Nickname |
| ---- | ---- | -------- |
| Carrier | 5 | Iron Range |
| Battleship | 4 | Paul Bunyan |
| Destroyer | 3 | North Star |
| Submarine | 3 | Walleye |
| Patrol Boat | 2 | Common Loon |

### The computer opponent

The Cheesehead Navy fires at random until it scores a hit, then tries the squares around it.
Once it has two hits in a row it knows which way the ship lies and fires only along that line
until the ship sinks.

### Sound

Every time you hit an enemy ship, a synthesized explosion plays (generated with the standard
library, no audio files needed). It uses `winsound` on Windows, `afplay` on macOS and
`paplay`/`aplay`/`pw-play`/`ffplay` on Linux, falling back to the terminal bell.
Pass `--no-sound` to turn it off.

## Tests

```bash
python3 -m unittest -v
```

## Project layout

| File | What it is |
| ---- | ---------- |
| `battleship_mn.py` | The game |
| `battleship_web.py` | Lets the game run in a web page (replays the game on each answer) |
| `index.html`, `web/` | The web page served by GitHub Pages |
| `test_*.py` | Tests |
