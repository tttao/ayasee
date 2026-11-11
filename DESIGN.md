---
name: ayasee
description: Staged, gated IaC promotion on GitHub Actions. A sober signal box with two surfaces, the documentation site and the estate cockpit.
colors:
  # Documentation site (website/stylesheets/extra.css), light scheme
  signal-teal: "#12343b"
  signal-teal-light: "#1d4d57"
  signal-teal-deep: "#0c2429"
  lamp-orange: "#c2571a"
  rail-link-teal: "#0f6b78"
  ballast-cream: "#f4f1ea"
  lichen-wash: "#e6efe9"
  slate-muted: "#4c5b5e"
  # Documentation site, dark scheme (slate)
  signal-teal-night: "#0c2429"
  lamp-orange-night: "#f08a4b"
  rail-link-teal-night: "#6fc6d1"
  slate-muted-night: "#a9b8ba"
  # Cockpit (cockpit-site/src/styles.css), light scheme
  desk-grey: "#f6f7f9"
  panel-white: "#ffffff"
  ink: "#1b1f24"
  ink-muted: "#4a5058"
  rule-grey: "#c9cfd6"
  cobalt-link: "#0b4fb3"
  synced-fg: "#0a5a28"
  synced-bg: "#d7f0df"
  pending-fg: "#0b3b8c"
  pending-bg: "#dbe7fb"
  waiting-fg: "#663c00"
  waiting-bg: "#fdebc8"
  drift-fg: "#5a1d8c"
  drift-bg: "#eddffa"
  failed-fg: "#85101f"
  failed-bg: "#fbdadd"
  held-fg: "#ffffff"
  held-bg: "#8a1022"
  unknown-fg: "#40454d"
  unknown-bg: "#e6e8eb"
  # Cockpit, dark scheme
  desk-grey-night: "#0e1113"
  panel-night: "#14171a"
  ink-night: "#e8eaed"
  ink-muted-night: "#aab0b8"
  rule-grey-night: "#343a41"
  cobalt-link-night: "#8ab4ff"
  synced-fg-night: "#9be8b5"
  synced-bg-night: "#12331f"
  pending-fg-night: "#b4d0ff"
  pending-bg-night: "#14284d"
  waiting-fg-night: "#ffd98f"
  waiting-bg-night: "#46300a"
  drift-fg-night: "#dcc1ff"
  drift-bg-night: "#35204f"
  failed-fg-night: "#ffbcc3"
  failed-bg-night: "#50141c"
  held-bg-night: "#b3203a"
  unknown-fg-night: "#d0d4d9"
  unknown-bg-night: "#32363c"
typography:
  docs-display:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, Arial, sans-serif"
    fontSize: "2.6rem"
    fontWeight: 700
    letterSpacing: "-0.03em"
  docs-tagline:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, Arial, sans-serif"
    fontSize: "1.15rem"
    fontWeight: 600
    lineHeight: 1.45
  docs-fine-print:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, Arial, sans-serif"
    fontSize: "0.72rem"
  docs-code:
    fontFamily: "ui-monospace, SF Mono, Cascadia Code, Menlo, Consolas, monospace"
  cockpit-title:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, Arial, sans-serif"
    fontSize: "1.15rem"
    fontWeight: 700
  cockpit-body:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, Arial, sans-serif"
    fontSize: "15px"
    lineHeight: 1.4
  cockpit-label:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, Arial, sans-serif"
    fontSize: "0.8rem"
    fontWeight: 600
    letterSpacing: "0.04em"
  cockpit-mono:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "0.9rem"
rounded:
  control: "4px"
  panel: "6px"
  card: "0.5rem"
  hero: "0.6rem"
  pill: "999px"
spacing:
  xs: "0.2rem"
  sm: "0.4rem"
  md: "0.6rem"
  lg: "1rem"
  xl: "2.2rem"
components:
  status-pill-synced:
    backgroundColor: "{colors.synced-bg}"
    textColor: "{colors.synced-fg}"
    typography: "{typography.cockpit-label}"
    rounded: "{rounded.pill}"
    padding: "0.1rem 0.55rem"
  status-pill-pending:
    backgroundColor: "{colors.pending-bg}"
    textColor: "{colors.pending-fg}"
    rounded: "{rounded.pill}"
    padding: "0.1rem 0.55rem"
  status-pill-waiting:
    backgroundColor: "{colors.waiting-bg}"
    textColor: "{colors.waiting-fg}"
    rounded: "{rounded.pill}"
    padding: "0.1rem 0.55rem"
  status-pill-drift:
    backgroundColor: "{colors.drift-bg}"
    textColor: "{colors.drift-fg}"
    rounded: "{rounded.pill}"
    padding: "0.1rem 0.55rem"
  status-pill-failed:
    backgroundColor: "{colors.failed-bg}"
    textColor: "{colors.failed-fg}"
    rounded: "{rounded.pill}"
    padding: "0.1rem 0.55rem"
  status-pill-held:
    backgroundColor: "{colors.held-bg}"
    textColor: "{colors.held-fg}"
    rounded: "{rounded.pill}"
    padding: "0.1rem 0.55rem"
  status-pill-unknown:
    backgroundColor: "{colors.unknown-bg}"
    textColor: "{colors.unknown-fg}"
    rounded: "{rounded.pill}"
    padding: "0.1rem 0.55rem"
  matrix-cell:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "0.4rem 0.5rem"
  cockpit-panel:
    backgroundColor: "{colors.panel-white}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "0.8rem 1rem"
  cockpit-button:
    backgroundColor: "{colors.panel-white}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "0.35rem 0.8rem"
  docs-button-primary:
    backgroundColor: "{colors.signal-teal}"
    textColor: "{colors.ballast-cream}"
  docs-button-primary-hover:
    backgroundColor: "{colors.lamp-orange}"
    textColor: "{colors.panel-white}"
  docs-button:
    backgroundColor: "transparent"
    textColor: "{colors.signal-teal}"
  docs-hero:
    textColor: "{colors.signal-teal}"
    rounded: "{rounded.hero}"
    padding: "2.2rem 2rem 1.6rem"
  docs-card:
    rounded: "{rounded.card}"
---

# Design System: ayasee

## Overview

**Creative North Star: "The Signal Box"**

A railway signal box is a quiet room. It has instruments you can trust, labelled levers and a
diagram of the line, and each signal shows one clear aspect. The signaller watches the state of the
line and sets the route. The trains, and the danger, are outside. ayasee has the same split: the
cockpit and the documentation show the state of an estate and explain the line, and every action
happens elsewhere, in GitHub. The design is sober and exact, with a little warmth. Information
comes first, every state is spelled out, and nothing is decoration for its own sake.

There are two surfaces, and each speaks the same grammar at its own volume. The **documentation
site** is the signal box's notice board. Its palette is a deep signal teal on warm ballast cream,
with one lamp-orange accent that appears only when you point at something. Its warmth is
deliberate: this is where an engineer reads, evaluates and decides. The **cockpit** is the
instrument panel. It uses cool desk greys, plain white panels and a cobalt link, and its colour is
reserved for the seven status aspects. Neither surface adds shadows, decorative gradients, web
fonts or anything loaded from another origin. Depth comes from 1px rules and tonal surfaces; the
only shadows are the two Material keeps on the docs site by default.

Density follows the job. The docs give prose room (76rem grid, cards three to a row on wide
screens). The cockpit packs a repository-by-environment matrix tightly. It keeps the repository
column sticky, so the row you are reading never loses its name.

**Key Characteristics:**
- System fonts only, on both surfaces. Monospace is for SHAs, code and identifiers.
- Flat and ruled: 1px borders and tonal backgrounds. The cockpit has no shadows; the docs keep only
  Material's defaults.
- Every state carries a word and a glyph as well as a colour.
- Light and dark schemes on both surfaces, following `prefers-color-scheme`.
- Colour is a signal, not a mood. The docs have one accent, and the cockpit's colours mean status.

## Colors

Two related palettes: a warm teal and cream notice board for reading, and a cool, neutral
instrument panel whose only saturated colours are status aspects.

### Primary
- **Signal Teal** (`signal-teal`): docs header, tabs and primary buttons, and the colour of the
  hero headline. It is a dark, desaturated teal-navy that reads as institutional rather than techy.
  Light and deep variants (`signal-teal-light`, `signal-teal-deep`) serve Material's hover and
  pressed states. In the dark scheme the header drops to `signal-teal-night`.
- **Cobalt Link** (`cobalt-link`): the cockpit's only interactive colour. It is used for links,
  the legend toggle and focus rings. Dark scheme: `cobalt-link-night`.

### Secondary
- **Lamp Orange** (`lamp-orange`): the docs accent. It appears only on hover and active states:
  buttons, the search highlight and the navigation in use. It is brightened to
  `lamp-orange-night` in the dark scheme.
- **Rail Link Teal** (`rail-link-teal`): body-text links in the docs. It is lighter and greener
  than Signal Teal, so a link in a paragraph reads as a link, not a heading. Dark scheme:
  `rail-link-teal-night`.

### Tertiary: the seven status aspects (cockpit)
Each aspect is a foreground and background pair, tuned to pass contrast in both schemes. They
are listed in precedence order, first match wins:
- **Held** (`held-bg` / `held-fg`): the only *filled* aspect, white on deep crimson. Stop-the-line
  is open and applies are blocked. It is meant to look like a signal at danger.
- **Failed** (`failed-fg` / `failed-bg`): dark red on blush. The last apply failed.
- **Drift** (`drift-fg` / `drift-bg`): aubergine on lilac. Live infrastructure differs from the
  ledger. It is the only purple in the system, so drift never reads as an error or a warning.
- **Waiting** (`waiting-fg` / `waiting-bg`): umber on amber. A gate is waiting for a person.
- **Pending** (`pending-fg` / `pending-bg`): navy on ice blue. Commits are not applied here yet.
- **Synced** (`synced-fg` / `synced-bg`): forest on mint. Applied, and nothing is pending.
- **Unknown** (`unknown-fg` / `unknown-bg`): graphite on fog. No usable data.

### Neutral
- **Ballast Cream** (`ballast-cream`): text on Signal Teal, and the starting point of the docs
  hero wash. That wash runs at 160° from cream to `lichen-wash` and is the system's only gradient.
- **Slate Muted** (`slate-muted` / `slate-muted-night`): docs fine print, such as the status line
  under the hero and the name note.
- **Desk Grey** (`desk-grey` / `desk-grey-night`): the cockpit page background.
- **Panel White** (`panel-white` / `panel-night`): cockpit surfaces such as the top bar, the
  matrix panel, the drawer and messages.
- **Ink** (`ink` / `ink-night`) and **Ink Muted** (`ink-muted` / `ink-muted-night`): cockpit text.
  Muted text is for table headers, ages, short SHAs and "since" lines.
- **Rule Grey** (`rule-grey` / `rule-grey-night`): every border and row divider in the cockpit.

### Named Rules
**The Aspect Rule.** In the cockpit, saturated colour means status and nothing else. A new
decorative colour, or a status colour used for emphasis, breaks the panel.

**The One Lamp Rule.** On the docs site, Lamp Orange is the only accent, and it appears as a
response to the pointer, not at rest.

**The Danger Is Filled Rule.** Only Held gets a filled, white-on-crimson pill. Every other aspect
is tinted. Do not fill another aspect.

## Typography

**Display Font:** system-ui (with -apple-system, Segoe UI, Roboto, Helvetica Neue, Arial, sans-serif)
**Body Font:** the same system stack
**Label/Mono Font:** ui-monospace (with SF Mono / SFMono-Regular, Cascadia Code, Menlo, Consolas)

**Character:** the platform's own voice, with no web fonts. That is a constraint, because the
surfaces must work on GHES and air-gapped Pages. It also fits the product: the type comes from the
same room as the user's terminal and their GitHub. Hierarchy comes from weight, size and case.

### Hierarchy
- **Docs Display** (700, 2.6rem, -0.03em; 2rem below 45em): the hero "ayasee" wordmark only.
- **Docs Tagline** (600, 1.15rem, line-height 1.45, max 44rem; 1rem on small screens): the
  one-sentence promise under the wordmark.
- **Docs Body**: Material's typeset, set in the system stack. Wide reference tables drop to
  0.72rem so they fit the page.
- **Docs Fine Print** (0.72rem, Slate Muted): the status line and the name note.
- **Cockpit Title** (700, 1.15rem): "IaC cockpit" in the top bar, and the drawer heading (1.1rem).
- **Cockpit Body** (400, 15px root, line-height 1.4): everything else in the cockpit.
- **Cockpit Label** (0.8rem, uppercase, 0.04em tracking, Ink Muted): matrix column headers. Pills
  use the same 0.8rem size in weight 600, in sentence case.
- **Mono** (0.8 to 0.9rem): SHAs, compare ranges and identifiers in the drawer, which break
  anywhere so they never overflow.

### Named Rules
**The Platform Voice Rule.** Use no web fonts and no font files. If a surface needs character, get
it from weight, size and spacing.

**The Mono Means Machine Rule.** Monospace is only for values a machine produced or reads: SHAs,
paths, YAML and identifiers. Never use it for decoration or headings.

## Layout

- **Docs:** Material's layout on a widened 76rem grid. The landing page hides navigation and table
  of contents. Its hero box is followed by sections that use card grids
  (`repeat(auto-fit, minmax(min(100%, 20rem), 1fr))`: three per row on wide screens, so six cards
  make two full rows), tabbed code samples and one reference table. Mermaid diagrams scroll
  horizontally rather than shrink. Mobile breakpoint: 44.9375em, where the hero padding tightens
  to 1.4rem 1rem 1.1rem.
- **Cockpit:** a sticky top bar (title, snapshot age, legend toggle aligned right) over an
  optional full-width warning banner and a 1rem-padded main area. The matrix sits in one bordered
  panel that scrolls horizontally. The repository column is sticky on the left (min 12rem), and
  environment cells are at least 9.5rem wide. Cell detail opens in a right-docked drawer of
  min(30rem, 100vw), full height.
- **Rhythm:** a small rem scale (0.2 / 0.4 / 0.6 / 1rem) for the cockpit's tight work. The docs
  use larger steps (2.2rem around the hero).

## Elevation & Depth

The cockpit is flat: it has no `box-shadow` anywhere. The docs site adds none either, but it keeps
two of Material's defaults: the header gets a shadow once the page scrolls, and a grid card lifts
with a small shadow on hover, dropping its border. Elsewhere, depth comes from three things:
1. tone, where a Panel White surface sits on Desk Grey;
2. 1px Rule Grey borders;
3. stacking for things that must stay on top: the sticky top bar and repository column, the legend
   popover, and the drawer over a 40% black backdrop.

### Named Rules
**The Ruled Not Raised Rule.** Separate things with a 1px rule or a tonal step, never with a
shadow. A layer that overlaps content (legend, drawer) gets a border, and the drawer also gets the
backdrop. Material's two default shadows on the docs site may stay, but don't add more.

## Shapes

The corners are gentle and few. In the cockpit, controls have 4px corners, panels and cells 6px,
and status pills are full capsules (999px), so status reads as a different kind of object from
the containers. In the docs, cards have 0.5rem corners and the hero box 0.6rem. There are no
angled or clipped shapes. The one recurring form is the bordered rectangle.

## Components

### Status Pills (signature)
The status pill is the signal aspect: a capsule with a glyph and a word, never colour alone.
- **Shape:** full capsule (999px), padding 0.1rem 0.55rem, inline-flex, 0.35rem gap.
- **Content:** a glyph plus a label, in precedence order: ■ Held, ✕ Failed, △ Drift, ◔ Waiting,
  ↑ Pending, ✓ Synced, ? Unknown. The legend repeats each pill with a one-line explanation.
- **Type:** 0.8rem, weight 600, no wrapping.
- **Colour:** each aspect's foreground and background pair (see Colors). Held is the only filled one.

### Matrix Cells
- **Shape:** a full-width button with 6px corners and padding 0.4rem 0.5rem. It stacks the pill,
  the live SHA (mono) and muted "pending" or "since" lines.
- **States:** transparent border at rest; Rule Grey border on hover (0.15s ease, removed under
  reduced motion); 3px Cobalt Link outline with 1px offset on keyboard focus.
- **Behaviour:** opens the detail drawer. It never acts on the estate.

### Cards / Containers
- **Cockpit panel:** Panel White background, 1px Rule Grey border, 6px corners, no shadow. Error
  messages swap to the Failed pair, with a Failed foreground border and weight 600.
- **Docs cards:** Material grid cards with 0.5rem corners. Each has a bold title, a rule, a short
  paragraph and one link. On hover, Material swaps the border for its small shadow.
- **Docs hero:** a 1px border at 12% Signal Teal, 0.6rem corners, the cream-to-lichen wash, and
  padding of 2.2rem 2rem 1.6rem.

### Buttons
- **Docs primary:** Material `.md-button--primary`, Ballast Cream text on Signal Teal. Lamp Orange
  on hover.
- **Docs secondary:** Material `.md-button`, an outline in Signal Teal that fills Lamp Orange on
  hover.
- **Cockpit:** one quiet control, the drawer's Close. It has Panel White, 1px Rule Grey and 4px
  corners, with padding 0.35rem 0.8rem and a 3px cobalt focus ring. The cockpit has no primary
  button, because it has no primary action of its own: actions are deep links into GitHub.

### Navigation
- **Docs:** Material tabs and sections in the Signal Teal header, with a sun/moon scheme toggle
  and code-copy buttons.
- **Cockpit:** no navigation. A sticky top bar holds the title and the snapshot age (a live
  status region), and a `<details>` legend popover sits on the right. A full-width amber banner
  appears when the snapshot is stale.

### Detail Drawer
A modal `<dialog>` docked to the right, full height, Panel White with a 1px left rule. It has a
heading and a Close button, then sections of muted-term / value pairs. Each outbound action is a
plain cobalt link to GitHub, and a link that is not available is shown as muted text instead.

## Do's and Don'ts

### Do:
- **Do** pair every status colour with its glyph and word (■ Held, ✕ Failed, △ Drift, ◔ Waiting,
  ↑ Pending, ✓ Synced, ? Unknown).
- **Do** define every colour as a token in both light and dark schemes and check contrast in both.
- **Do** separate surfaces with a 1px rule or a tonal step.
- **Do** give every interactive element in the cockpit a 3px Cobalt Link `:focus-visible` outline.
- **Do** set SHAs and identifiers in monospace and let them break anywhere.
- **Do** keep motion to short state transitions (0.15s), and remove it under
  `prefers-reduced-motion: reduce`.
- **Do** show limitations and "pre-alpha" status in the docs' own muted fine print.

### Don't:
- **Don't** load web fonts, icon fonts, images or scripts from another origin. Both surfaces must
  work on air-gapped Pages, and the cockpit's CSP is `default-src 'self'`.
- **Don't** add a `box-shadow` for depth. Material's own header and card-hover shadows on the docs
  site are the only exceptions.
- **Don't** use a status colour for decoration or emphasis, and don't add new saturated colours to
  the cockpit.
- **Don't** give the cockpit a primary or "action" button. Actions are links into GitHub.
- **Don't** use Lamp Orange at rest or as a fill on the docs site; it answers the pointer.
- **Don't** add adopter logos, testimonial blocks or metric counters. There is nothing true to put
  in them.
