# NETRA design system — MASTER

Single always-dark theme for a police CCTV ops room: low glare, high legibility
under strip lighting, numbers that read at a glance.

## 1. Colour tokens

| Token | Value | Use |
|---|---|---|
| `--color-bg` | `#070d1a` | Page background |
| `--color-surface` | `#0e1830` | Card / panel background |
| `--color-surface-2` | `#142140` | Nested surface, hover state |
| `--color-border` | `#22304f` | Hairlines, dividers, input borders |
| `--color-text` | `#e6ecf7` | Primary text |
| `--color-muted` | `#8fa1c2` | Secondary text, captions, labels |
| `--color-accent` | `#FF6B00` | Primary CTA, active state, focus ring |
| `--color-accent-fg` | `#0a1628` | Text/icon on top of accent |
| `--color-ok` | `#22c55e` | Healthy / normal status |
| `--color-warn` | `#f59e0b` | Degraded / elevated status |
| `--color-bad` | `#ef4444` | Critical / anomalous status |
| `--color-info` | `#38bdf8` | Informational status |

Contrast check: muted `#8fa1c2` on surface `#0e1830` is 6.9:1 — comfortably
above the 4.5:1 minimum for body text.

## 2. Fonts

- `--font-sans`: Plus Jakarta Sans — headings and body copy.
- `--font-mono`: JetBrains Mono — camera IDs, plates, timestamps, counts.
  Apply via the `.mono` utility class; always pair with
  `font-variant-numeric: tabular-nums` so counters do not jitter.

## 3. Spacing scale (px)

`4 / 8 / 12 / 16 / 24 / 32 / 48`

## 4. Type scale (px)

`12 / 14 / 16 / 18 / 24 / 32 / 44`

## 5. Radius

- `--radius-card`: 10px — cards, panels, dialogs.
- `--radius-ctl`: 6px — buttons, inputs, badges, small controls.

## 6. Motion

150–250 ms, `ease-out`. Respect `prefers-reduced-motion: reduce` (already
handled globally in `app/globals.css`, which collapses all animation and
transition durations to near-zero for that preference).

## 7. Rules

1. **One primary CTA per view.** Every other action is a secondary or ghost
   button. A screen with two "primary-looking" buttons is a design bug.
2. **Icons: lucide-react only.** No emoji as icons, anywhere in the product.
3. **Numbers use `.mono`.** Plate text, camera IDs, counts, timestamps,
   coordinates — anything an officer might read off a screen and repeat into
   a radio or a report.
4. **Status colour never carries meaning alone.** A red badge must also say
   "critical" or carry a distinct icon; colour-blind operators and black/white
   printouts of evidence must still be legible.
5. **Contrast ≥ 4.5:1** for all text against its background. Verify new
   colour combinations before shipping them; do not rely on the tokens above
   looking "close enough" at a glance.
