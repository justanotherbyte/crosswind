---
name: Crosswind
description: Docs for typed, async Python SDKs on Cloudflare Workers, built to behave like one native developer utility.
colors:
  bg: "#191b1d"
  surface: "#202225"
  elevated: "#282b2e"
  border: "#34373b"
  border-strong: "#484c51"
  text: "#e6e7e8"
  text-2: "#92979e"
  label: "#7f858c"
  muted: "#686d74"
  accent: "#67cdbb"
  purple: "#a99fd6"
  amber: "#d0ae74"
  red: "#d98a82"
  code-function: "#b9d4e6"
typography:
  display:
    fontFamily: "Geist, -apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif"
    fontSize: "2.1rem"
    fontWeight: 600
    lineHeight: 1.08
    letterSpacing: "-0.034em"
  headline:
    fontFamily: "Geist, -apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 600
    lineHeight: 1.15
    letterSpacing: "-0.028em"
  title:
    fontFamily: "Geist, -apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif"
    fontSize: "1rem"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.016em"
  subtitle:
    fontFamily: "Geist, -apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif"
    fontSize: "0.8rem"
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: "-0.008em"
  body:
    fontFamily: "Geist, -apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 400
    lineHeight: 1.65
  chrome:
    fontFamily: "Geist, -apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif"
    fontSize: "0.625rem"
    fontWeight: 400
    lineHeight: 1.35
  label:
    fontFamily: "Geist, -apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif"
    fontSize: "0.55rem"
    fontWeight: 500
    lineHeight: 1.35
  code:
    fontFamily: "Geist Mono, SFMono-Regular, Consolas, Menlo, monospace"
    fontSize: "0.625rem"
    fontWeight: 400
    lineHeight: 1.6
  mono-label:
    fontFamily: "Geist Mono, SFMono-Regular, Consolas, Menlo, monospace"
    fontSize: "0.55rem"
    fontWeight: 400
    lineHeight: 1.5
  mono-micro:
    fontFamily: "Geist Mono, SFMono-Regular, Consolas, Menlo, monospace"
    fontSize: "0.5rem"
    fontWeight: 400
    lineHeight: "0.8rem"
rounded:
  marker: "1px"
  s: "4px"
  m: "6px"
  l: "8px"
  xl: "10px"
spacing:
  xs: "0.2rem"
  s: "0.3rem"
  m: "0.4rem"
  l: "0.6rem"
  xl: "0.8rem"
  2xl: "1.4rem"
components:
  button:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    typography: "{typography.chrome}"
    rounded: "{rounded.m}"
    padding: "0.28rem 0.65rem"
  button-hover:
    backgroundColor: "{colors.elevated}"
    textColor: "{colors.text}"
  button-primary:
    backgroundColor: "color-mix(in srgb, #67cdbb 14%, #191b1d)"
    textColor: "{colors.accent}"
    typography: "{typography.chrome}"
    rounded: "{rounded.m}"
    padding: "0.28rem 0.65rem"
  button-primary-hover:
    backgroundColor: "color-mix(in srgb, #67cdbb 20%, #191b1d)"
    textColor: "{colors.accent}"
  input-search:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    typography: "{typography.chrome}"
    rounded: "{rounded.m}"
    height: "1.4rem"
    width: "11rem"
  input-search-open:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.xl}"
    height: "2rem"
    width: "34rem"
  nav-link:
    textColor: "{colors.text-2}"
    typography: "{typography.chrome}"
    rounded: "{rounded.m}"
    padding: "0.2rem 0.4rem 0.2rem 0.7rem"
  nav-link-hover:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
  nav-link-active:
    backgroundColor: "{colors.elevated}"
    textColor: "{colors.text}"
  badge:
    textColor: "{colors.text-2}"
    typography: "{typography.mono-micro}"
    rounded: "{rounded.s}"
    padding: "0 0.2rem"
  badge-cli:
    textColor: "{colors.purple}"
    typography: "{typography.mono-micro}"
    rounded: "{rounded.s}"
    padding: "0 0.2rem"
  keycap:
    backgroundColor: "{colors.elevated}"
    textColor: "{colors.text-2}"
    typography: "{typography.mono-micro}"
    rounded: "{rounded.s}"
    padding: "0 0.18rem"
  inline-code:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.s}"
    padding: "0.08em 0.32em"
  code-panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    typography: "{typography.code}"
    rounded: "{rounded.l}"
    padding: "0.65rem 0.8rem"
  callout:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.l}"
    padding: "0.5rem 0.7rem"
  command:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.purple}"
    typography: "{typography.code}"
    rounded: "{rounded.m}"
  palette:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.xl}"
  palette-row:
    textColor: "{colors.text-2}"
    rounded: "{rounded.m}"
    padding: "0.38rem 0.4rem"
  palette-row-selected:
    backgroundColor: "{colors.elevated}"
    textColor: "{colors.text}"
---

# Design System: Crosswind

## Overview

**Creative North Star: "The Native Utility"**

The docs behave like one developer tool that a reader already has open. Slim chrome, keyboard shortcuts shown as keycaps, a status bar for a footer, and search that opens as a command palette all come from native desktop tooling. Real code is the main visual material, and the home page previews it inside a package palette.

The system is dark only and dense. Three graphite steps and 1px borders carry all structure. Colour appears in small amounts, and each colour has a job. Tiny square markers show position and state, so no icon set is needed. The only illustrations are the puffin logo in the header and the package diagram.

The world rejects the SaaS landing page, meaning a generic hero and a grid of feature cards. The site runs on MkDocs Material, and every token maps onto Material's variables, so generated API pages inherit the same look.

**Key Characteristics:**
- Dark only, with no theme toggle.
- Geist for text and chrome, Geist Mono for anything a developer would type or read as a machine value.
- 1px borders and tonal steps instead of shadows at rest.
- Square markers, compact rectangular badges and keycaps.
- A 140ms state transition on almost every interactive element.

## Colors

Near-neutral graphite with a cool cast, one teal accent, and two quiet secondary hues with narrow jobs. The palette is user-pinned. Label Grey and amber are added steps.

### Primary
- **Terminal Teal** (#67cdbb): Marks where the reader is and what they can act on. It colours links, the primary button, the active square marker, the focus ring, search matches, the text caret and code strings. Tints use `color-mix` at 10% to 42%.

### Secondary
- **Muted Command Purple** (#a99fd6): The colour of commands and the CLI badge. Python keywords in code use it as a secondary use.

### Tertiary
- **Low-Saturation Red** (#d98a82): An added danger colour, used only on danger, error and failure callouts.
- **Low-Saturation Amber** (#d0ae74): An added warning colour. It marks warning and caution callouts, quote callout labels, and the alpha status. Syntax highlighting also uses it for numbers, constants and builtins.

### Neutral
- **Graphite Ground** (#191b1d): The page, header, tabs, footer and sidebars.
- **Panel Graphite** (#202225): Code panels, callouts, the palette, inline code, default buttons and the sidebar hover.
- **Raised Graphite** (#282b2e): Title bars of code panels, keycaps, the selected row and the active sidebar link.
- **Hairline** (#34373b): Every resting border and divider.
- **Hover Hairline** (#484c51): Borders on hover, and on open search and the diagram tooltip.
- **Soft White** (#e6e7e8): Headings, body text and the current item.
- **Secondary Grey** (#92979e): Secondary text at any size, on any of the three graphite steps. Idle navigation, captions, table headers and code comments use it.
- **Label Grey** (#7f858c): An added step for small labels, such as sidebar section names and the fact table's terms. It reaches 4.64:1 on Graphite Ground, 4.28:1 on Panel Graphite and 3.82:1 on Raised Graphite.
- **Decoration Grey** (#686d74): Decoration only, because it fails 4.5:1 on every step (3.31:1 on Graphite Ground). Use it for command prompts, breadcrumb separators, idle square markers and unchecked task boxes.
- **Pale Function Blue** (#b9d4e6): Function names in syntax highlighting. It appears nowhere else.

### Named Rules
**The Label Ground Rule.** Label Grey sits on Graphite Ground only. Small text on a panel or a raised step uses Secondary Grey, because Label Grey drops below 4.5:1 there.

**The Decoration Rule.** Decoration Grey never carries information a reader needs to read. If removing it would lose meaning, the element needs Secondary Grey.

**The Purple Rule.** Purple means a command, the CLI, or a Python keyword. Links and buttons stay teal or neutral.

## Typography

**Display Font:** Geist (with -apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif)
**Body Font:** Geist (with the same fallback)
**Label/Mono Font:** Geist Mono (with SFMono-Regular, Consolas, Menlo, monospace)

**Character:** One neutral grotesque carries headings, prose and chrome, tightened as sizes grow. Geist Mono separates machine values from prose at the same small sizes. The site loads Geist at 400, 500 and 600, and Geist Mono at 400 and 500.

### Hierarchy

Sizes are rem at a 20px root. Pixel values below are the rendered result.

- **Display** (600, 42px, 1.08): The home page heading only. It drops to 32px at 960px wide and below. The direction contract asked for 44px, and the build ships 42px.
- **Headline** (600, 30px, 1.15): The H1 of each doc page.
- **Title** (600, 20px, 1.3): H2 sections, each under a 1px top rule.
- **Subtitle** (600, 16px, 1.4): H3, and the label column on home page sections. H4 is 14px in Secondary Grey.
- **Body** (400, 15px, 1.65): Prose, capped at 72ch. The home lede is 16px at 1.55 in Secondary Grey. Callouts are 14px.
- **Chrome** (400, 12.5px, 1.35): Tabs, sidebar links, buttons and the search input. Active items step up to 500. Tables are 13px with 12px headers.
- **Label** (500, 11px): Sidebar section names and small captions. Sentence case, no tracking.
- **Code** (Geist Mono 400, 12.5px, 1.6): Code panels with tabular numerals and no ligatures. Inline code is 0.84em.
- **Mono label** (Geist Mono 400, 11px, 1.5): File names, callout labels and the copied toast.
- **Mono micro** (Geist Mono 400, 10px): Badges and keycaps.

### Named Rules
**The Mono Rule.** Geist Mono is for code, commands, shortcuts, statuses and identifiers. Everything else, including every label a person wrote, is Geist.

**The Root Rule.** The html element is pinned at 125%, which is 20px at the browser default, and Material's wide-screen growth is off. So .05rem is 1px, and every rem value in this file assumes that root.

## Layout

The main grid is capped at 66rem (1320px). On doc pages the primary sidebar is 11rem (220px), the secondary sidebar is 10rem (200px), and the content column is capped at 42rem (840px) from 1220px wide. Code and tables take the column's full width while prose keeps its 72ch measure.

The home hero is a text column beside a 16rem (320px) fact table with a 3rem gap, aligned to the bottom edge. The palette below it takes the full grid width, with a 17rem (340px) side column. Later home sections use a 13rem (260px) label column with the heading absolutely placed in it.

Spacing clusters on 4, 6, 8, 12 and 16px. The 0.8rem (16px) inset is the standard horizontal padding for panels, title bars and result rows. Rows stay compact, with 0.2rem to 0.4rem of vertical padding.

Hero, palette and section columns collapse to one at 960px. The sidebars become Material's drawer below 1220px, where each link grows to 14px with 0.5rem of vertical padding. On the home page, the packages table turns into a stack of rows at 960px. The diagram's caption text moves out of the scene at 720px.

## Elevation & Depth

The system is flat and tonal. Depth comes from the three graphite steps and 1px borders, so a raised thing is lighter and a title bar is one step above its panel. Page sections divide with a thin rule, never with a background band.

### Shadow Vocabulary
- **Float** (`box-shadow: 0 8px 24px rgb(0 0 0 / 28%)`): Elements that float above the page, which are open search, the copied toast and the diagram tooltip.
- **Palette contact** (`box-shadow: 0 1px 2px rgb(0 0 0 / 20%)`): The package palette at rest. It is the only resting shadow.

Open search also dims the page behind it with `rgb(12 13 14 / 64%)`.

### Named Rules
**The Float Rule.** A shadow means the element sits above the page. Panels in the document flow, including the header, stay flat.

## Shapes

Small, even radii on rectangles. There are four steps: 4px for badges, keycaps and inline code, 6px for buttons, nav links, and the search field, 8px for panels and tables, and 10px for the largest containers, which are the palette, open search and the diagram. The contract named 8px radii, and the build added the other steps around it. Square markers are 5px or 6px with a 1px radius. All strokes are 1px, and panels clip their contents with `overflow: hidden`.

### Named Rules
**The Square Rule.** State is marked with a tiny square, not a dot, stripe or icon. Teal is current, Decoration Grey is idle, amber is a warning or alpha status.

## Components

Components are quiet and tool-like. State changes are colour, border or background shifts at 140ms. Palette pane entry and diagram messages ease with `cubic-bezier(0.23, 1, 0.32, 1)`. `prefers-reduced-motion` turns transitions and the pane animation off. Focus everywhere is a 1px teal outline with a 2px offset.

### Buttons
- **Shape:** Gently rounded rectangle (6px) with a 1px border.
- **Primary:** Teal text on a 14% teal tint of Graphite Ground, with a 42% teal border. It does not fill solid.
- **Default:** Soft White on Panel Graphite with a Hairline border.
- **Hover / Focus:** Default moves to Raised Graphite and Hover Hairline. Primary moves to a 20% tint and a 70% border.
- **Active:** Opacity 0.75.

### Badges and keycaps
- **Badge:** Mono micro text in a 4px outlined rectangle with no fill, reading SDK or CLI. The CLI badge is purple with a 34% purple border.
- **Keycap:** Mono micro text on Raised Graphite with a Hairline border and no shadow. It labels real shortcuts only, which are Ctrl K, the arrows, Enter, Esc, and P and N on the previous and next links, shown only to hover-capable pointers.

### Cards / Containers
- **Corner Style:** 8px for code panels, callouts, tables and previous and next links.
- **Background:** Panel Graphite, with title bars on Raised Graphite.
- **Shadow Strategy:** Flat, as in Elevation & Depth.
- **Border:** 1px Hairline, moving to Hover Hairline on interactive panels.
- **Internal Padding:** 0.65rem by 0.8rem in code, 0.5rem by 0.7rem in callouts.

### Inputs / Fields
- **Style:** The search field is a 1.4rem tall Panel Graphite field with a 6px radius and a keycap at its right edge. The palette filter is borderless inside its own row.
- **Focus:** The search border moves to Hover Hairline, and the palette filter icon turns teal. Inputs drop the global focus ring because the container shows focus. The caret is teal.
- **Open search:** Ctrl K or Cmd K opens a centred 34rem field at 14vh, 2rem tall with a 10px radius. Once a query exists, it joins the results as one panel. Each result is one title and one line of context, with a hint row of keycaps underneath.

### Navigation
- **Header and tabs:** Two thin rows on Graphite Ground, each with a 1px bottom border and no shadow. The repository name is Geist Mono.
- **Tabs:** 12.5px Secondary Grey. The active tab is Soft White at 500 with a 5px teal square.
- **Sidebar:** Secondary Grey links with a 6px radius. Hover uses Panel Graphite. The active link uses Raised Graphite with a 5px teal square in the gutter, so the label does not shift. The active table of contents entry turns teal with no square.
- **Mobile:** Material's drawer with full-width rows, each divided by a 1px border.

### Code panels
Panel Graphite, 8px radius, with a 1px border. A file name or tab row sits in a Raised Graphite title bar, with a 1px teal indicator under the active tab. A copy icon sits at the top right. Inline code is a small 4px outlined chip on Panel Graphite.

### Package field
The home hero's right column: an 8px grid of tiny squares on Panel Graphite, drawn on a canvas, with the package name, position squares and a pause button in a footer row. It morphs to one recognisable scene per package every 6 seconds, in step with the heading carousel: a robot broadcasting to clients for Agents, a terminal inside marching walls for Sandboxes, shipping containers on a crane for Containers, a cloud serving requests for Workers, and the wind symbol blowing blocks off a stack for Wynd. Squares near the pointer brighten. The package preview below follows it, and picking a package in the preview moves the hero and stops the carousel. It holds on hover or focus of the hero or the preview and stops off screen. Reduced motion starts it stopped on a still frame. It stays flat, so it never reads as the isometric package diagram.

### Callouts
A panel with a 6px square and mono label in a 72px left column, text in the right column, stacked on phones. Each type tints its label, marker, border (38%) and background (7%): teal for notes, tips and other information, amber for warnings, cautions and quote callouts with a bold label, red for danger, errors and failures.

### Command
A bordered inline group holding a purple Geist Mono command, a Decoration Grey prompt the reader cannot select, and an icon button that copies the command.

### Package palette
The signature component. A 10px panel with a filter field, rows and a keycap hint row on the left, and the selected package's summary and code on the right. Each row holds a square, a name, a mono package id and a badge. The selected row uses Raised Graphite and a teal square. Arrow keys swap the preview, and Enter opens the docs. The pane enters over 160ms with a 2px rise. Without JavaScript, the panes stay stacked and readable.

### Fact table
A bordered definition list with 1px row rules, 12px text, Label Grey terms and Soft White values. Status rows carry a square marker.

### Status bar
The footer's last row, in 11px Secondary Grey on Graphite Ground, with an amber square and the alpha state in Geist Mono on the left and links on the right. It holds the independence statement that PRODUCT.md requires.

### Ecosystem diagram
An SVG isometric scene in a 10px panel, with Geist Mono labels, 1px hairline strokes and teal for the active path. Its faces use the graphite steps, plus three in-between greys scoped to the diagram (`#1e2022`, `#26292c`, `#1c1e20`). Uppercase tracked labels belong to this SVG only.

## Do's and Don'ts

### Do:
- **Do** keep the page dark, using only the three graphite steps for grounds.
- **Do** use 1px Hairline borders (#34373b) and thin rules to separate things.
- **Do** set commands, shortcuts, statuses, package names and file names in Geist Mono.
- **Do** mark the current item with a 5px or 6px teal square.
- **Do** show a keycap only when the shortcut works.
- **Do** write rem values against the 20px root, where .05rem is 1px.
- **Do** use Secondary Grey (#92979e) for small text on Panel Graphite or Raised Graphite.
- **Do** map new colours through the `--cw-*` tokens and Material's `--md-*` variables, so generated API pages inherit them.

### Don't:
- **Don't** add a light theme or a theme toggle.
- **Don't** build a hero with a mascot or a grid of feature cards. The puffin stays in the header as the logo.
- **Don't** use Decoration Grey (#686d74) for text a reader needs.
- **Don't** put Label Grey (#7f858c) on Panel Graphite or Raised Graphite.
- **Don't** use purple for links, buttons or general emphasis.
- **Don't** raise amber's saturation, or use it for anything other than warnings, alpha status and syntax values.
- **Don't** divide page sections with background bands.
- **Don't** put a shadow on a panel in the document flow, other than the palette's contact shadow.
- **Don't** set uppercase or tracked labels in HTML chrome.
- **Don't** use radii above 10px, or pill shapes.
