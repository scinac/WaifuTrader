# Design

A dark, dense market terminal for long evening sessions. Color carries state only, so a glance answers whether a price is rising, falling, live or stale.

## Tokens

Defined on `:root` in `web/src/styles.css`. The chart reads the same values at startup.

| Token | Value | Job |
| --- | --- | --- |
| `--ground` | `#10151a` | Page and chart |
| `--panel` | `#151b21` | Rail, top bar, health panel |
| `--raised` | `#1f2830` | Hover, selection, controls |
| `--line` | `#2a353f` | Dividers |
| `--grid` | `#1b232a` | Chart grid |
| `--text` | `#e1e8ec` | Primary text |
| `--muted` | `#9aa8b3` | Labels |
| `--up` | `#43c6a6` | Rising, live |
| `--down` | `#f27f73` | Falling, disconnected, errors |
| `--warn` | `#eab853` | Stale, market-data-only mode |
| `--accent` | `#8abbef` | Focus, current selection, primary action |

## Rules

- Use the system UI stack with tabular numerals for all data.
- Keep panels flat and separated by 1px lines. Do not use cards.
- Always pair a status color with a text label.
- The only ambient motion is a brief color tick when a price changes.
- Desktop has three columns. Below 1120px the health panel moves under the chart. Below 720px everything stacks.
