# Adaptive Eval — documentation site

Astro Starlight site that ships to GitHub Pages at
`https://microsoft.github.io/adaptive-eval/`.

## What's here

```
site/
├── astro.config.mjs           # nav, theme, integrations
├── src/
│   ├── assets/logo.svg        # placeholder — replace with final logo
│   ├── components/Footer.astro
│   ├── styles/custom.css      # Primer-style overrides over Starlight defaults
│   ├── content.config.ts
│   └── content/docs/
│       ├── index.mdx          # landing page (splash template)
│       ├── get-started/
│       ├── learn/
│       ├── author/
│       ├── run/
│       ├── examples/
│       └── reference/
└── package.json
```

## Run locally

```bash
cd site
npm install
npm run dev          # http://localhost:4321
```

Hot-reload edits to any `.md` / `.mdx` / config file.

## Build

```bash
npm run build        # type-check + static build to dist/
npm run preview      # serve dist/ locally
```

## Deploy

The `pages.yml` workflow builds and deploys to GitHub Pages on push to `main`
that touches `site/**`. Enable Pages on the repo with source = "GitHub Actions"
before the first deploy.

Production URL: `https://microsoft.github.io/adaptive-eval/`.

## Design notes for designers

This skeleton intentionally uses Starlight's default look as the base. Design
work to do (Becky):

1. **Replace the logo** at `src/assets/logo.svg` (currently a placeholder mark).
2. **Tune the color tokens** in `src/styles/custom.css` — the variables already
   match Primer's neutral + accent palette but the accent hue can move.
3. **Verify Mona Sans** loading. Today the site uses system font fallback
   (`-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif`).
   To swap in Mona Sans properly, add the font files under `public/fonts/` and
   a `@font-face` block in `custom.css`.
4. **Landing page hero illustration.** `index.mdx` uses a `splash` template
   today. Becky may want a custom hero with a product screenshot or graphic.
5. **OG / Twitter card images.** None set yet; Starlight supports them via
   frontmatter `head` blocks.
6. **Per-page artwork or callouts** — none today. Optional polish.

Content is locked behind the same source-of-truth tree as the existing
markdown docs. Wording changes should go via PR review with PM and engineering;
visual / layout / token changes are the designer's call.

## Adding a new page

1. Drop a `.md` or `.mdx` file under `src/content/docs/<group>/`.
2. Add it to the sidebar in `astro.config.mjs`.
3. Run `npm run dev` and confirm it renders.
