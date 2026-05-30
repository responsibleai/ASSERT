# Brief: Align documentation site with GitHub Primer–style design

## Goal
Refactor the existing documentation site so its visual language matches
**GitHub's Primer documentation style**. Content stays the same; only layout,
typography, components, and tokens change.

## Reference
**Primer design system docs:** https://primer.style/product/getting-started/

Match Primer's documentation surface specifically:
- Sticky top navigation with a section bar underneath
- **Breadcrumbs** sitting at the top of the article column (hierarchical, not the same as the section bar)
- Left sidebar with grouped, collapsible navigation
- Centered prose article column
- Right-side Table of Contents with scrollspy (“On this page”)
- Light and dark themes
- Cmd / Ctrl + K search palette

## Required layout
Three-column, centered to ~1440px, sticky header:

```
┌──────────────────────────────────────────────────────────────────────┐
│ Top nav (logo · search · theme · GitHub)                              │
│ Section bar (primary area tabs: Getting started / Using …)            │
├──────────────┬──────────────────────────────────────┬───────────────┤
│ Sidebar      │  Breadcrumb: Section > Group > Page    │ On this page  │
│ (280px,      │  Article (prose)                       │ (220px,       │
│ collapsible  │  - h1, h2, h3, p, lists, tables        │ sticky,       │
│ groups,      │  - inline <code>, fenced <pre>         │ scrollspy)    │
│ active link  │  - callouts / blockquotes              │               │
│ highlight)   │  - tabbed code samples (per SDK/lang)  │               │
└──────────────┴──────────────────────────────────────┴───────────────┘
```

Responsive behavior:
- Top nav and section bar stay sticky; both side rails stick below them
- Sidebar collapses below 1024px (hamburger / drawer)
- TOC hides below 1280px; breadcrumbs remain on all widths

## Typography
- Display: `Mona Sans` for headings, nav, UI
- Body: same family, 16px base, line-height ~1.7
- Code: `ui-monospace, SFMono-Regular, Menlo, monospace`
- Headings: tight letter-spacing, semi-bold; h1 32–36px, h2 24px, h3 18px

## Color tokens (CSS variables)
Define for both `[data-theme="light"]` and `[data-theme="dark"]`. Use Primer's
neutral + accent palette as inspiration:
- Surfaces: `--bg`, `--bg-elev`, `--sidebar-bg`, `--border`
- Text: `--fg`, `--fg-muted`, `--link`, `--link-hover`
- Code: `--code-bg`, `--code-fg`
- Accent: `--accent`, `--accent-soft`

## Article content rules
- Wrap article body in a single class (e.g. `.prose-doc`) that owns global typography
- **Inline code:** subtle background pill, monospace, accent-tinted text
- **Fenced code blocks:** dark surface, monospace, optional language label, copy button
- **Tables:** sparse borders, tinted header row, optional zebra rows
- **Blockquotes / callouts:** left bar in accent color, muted background
- **Anchors:** every `h2` / `h3` auto-gets an id; clicking the link icon copies the deep link
- **Internal links:** trailing-slash paths so they resolve on static hosts

## Navigation components — strictly follow Primer

**Strictly follow the Primer design language** for the breadcrumb, left sidebar,
and right TOC. Use Primer's own product docs as the canonical visual reference
and match their behavior, hierarchy, spacing, typography, and active states
exactly — do not invent variants.

Canonical references (mirror these surfaces):
- Primer docs home: https://primer.style/product/
- Primer docs getting started (full three-column layout): https://primer.style/product/getting-started/
- Any deeper page (for breadcrumb hierarchy): https://primer.style/product/components/action-list/

Acceptance check: open any Agent Shield docs page next to the Primer reference
above. The breadcrumb, sidebar groups/rows, and right TOC should be visually
indistinguishable in structure, density, and active-state treatment — only the
content and accent color should differ.

### Section bar (top)
- Sits directly under the top nav, sticky
- One row of primary area tabs (e.g. `Getting started`, `Using Agent Shield`)
- Active tab uses an underline accent in `--accent`, bold weight
- Inactive tabs use `--fg-muted` and a hover background tint
- Search trigger lives on the right edge of this row

### Breadcrumbs (above the article) — Primer parity required
Render exactly like the breadcrumb that appears at the top of every Primer
docs page (above the `h1`). Do **not** substitute the existing section bar
for breadcrumbs — they are two different surfaces and both must exist.
- Format: `Section / Group / Page`, separated by a thin `/` glyph in `--fg-muted`
- All but the last segment are anchor links in `--fg-muted`; hover -> `--fg`
- Last segment is the current page in `--fg`, non-interactive
- 13px / 20px line-height, regular sans (match Primer's body sans)
- 16–24px gap above the `h1`, no border, no background
- Always rendered inside the article column (not full-bleed)
- Hidden below 768px (mobile relies on section bar + page title)

### Left sidebar — Primer parity required
Mirror the left sidebar from Primer's docs:
- 280px fixed width, sticky below the section bar
- Scroll container is the height of the viewport minus header offsets
- Items render as **rows**, not buttons; full-width hit target with 8px radius
- **Groups** are top-level rows with a 12px chevron on the left that rotates 90° when expanded
- Group label is uppercase, 11px, letter-spacing 0.04em, `--fg-muted`
- Child links indent 16px under the group; nested children indent 32px
- Active route: `background: var(--accent-soft)`, `color: var(--accent)`,
  and a 2px left accent bar absolutely positioned at the row's left edge
- Hover (non-active): `background: var(--bg-elev)`
- 13–14px text, 32px row height, 1.5 line height

### Right Table of Contents (“On this page”) — Primer parity required
Mirror the right rail from Primer's docs:
- 220px fixed width, sticky
- Header label: `ON THIS PAGE` — uppercase, 11px, `--fg-muted`,
  letter-spacing 0.04em, 16px margin-bottom
- Generated from the article's `h2` (level 1) and `h3` (level 2) only
- Level 2 entries indent 12px
- Each entry is a link with 4px vertical padding, 13px size, `--fg-muted`
- Active entry (scrollspy): `color: var(--fg)` and a 2px accent left bar
- Smooth scroll on click; updates `history.replaceState` with the hash
- Hides below 1280px viewport width

## Other components to port
| Component | Purpose |
|---|---|
| Top nav | Header, search trigger, theme toggle, repo link |
| Search palette | Cmd / Ctrl + K modal over a prebuilt search index |
| Framework tabs | Tabbed code blocks (e.g. LangChain / Semantic Kernel / OpenAI) |
| Theme provider | Persists theme in `localStorage`, sets `data-theme` on root |

## Footer
- Centered, full-width
- No top border
- Single line: `Made with 💜 by Microsoft`
- Font: monospace (e.g. `ui-monospace, SFMono-Regular, Menlo, monospace`)
- Color: `--fg-muted` (grey)
- Padding: 32px vertical

## Migration steps
1. **Drop in tokens** — establish the `:root` and theme variable blocks above
2. **Replace layout shell** — adopt the three-column sticky layout (top nav + section bar + sidebar + article + TOC)
3. **Add breadcrumbs** above every article `h1` so the hierarchical path is always visible
4. **Rebuild the left sidebar** to use group rows with chevrons, indentation per level, and the active-route treatment (accent bar + tint)
5. **Rebuild the right TOC** with the `ON THIS PAGE` label, h2/h3-only entries, indented level-2, and scrollspy active state
6. **Wrap article content** in the shared `.prose-doc` class; remove conflicting prose styles
7. **Re-style code blocks and tables** to use the shared tokens (no separate highlighter theme)
8. **Enable `trailingSlash: true`** in the framework config so static exports resolve on any host
9. **Add the shared footer** (see Footer section above)
10. **QA checklist:** breadcrumbs render on every page; sidebar active state matches route; TOC scrollspy tracks the current section; sticky behavior at 1024 / 1280 / 1440 / 1920; light + dark themes; search; anchor links; code block copy

## Out of scope
- No content rewrites
- No URL changes beyond adding trailing slashes
- No new component library — keep parity with the Primer-style components above
