# Third-party notices

## Included in this repository

### three.js r169

- **Where:** `static/vendor/three.module.js`, `static/vendor/OrbitControls.js`, `static/vendor/CSS2DRenderer.js`
- **Licence:** MIT, Copyright © 2010-2024 three.js authors. The full text is in [`static/vendor/LICENSE-three.js.txt`](static/vendor/LICENSE-three.js.txt).
- **Source:** https://github.com/mrdoob/three.js

These files are unmodified copies from the `three@0.169.0` npm package.

## Loaded at runtime, not included

- **VT323 font**, by Peter Hull, used for the clock and sticky notes. Licence: SIL Open Font License 1.1. The page loads it from Google Fonts; it isn't redistributed here. Without internet, the page falls back to a monospace system font.
- **System fonts** (Tahoma, Verdana, Comic Sans MS and others) are only named in CSS and canvas drawing. The browser uses its own copies if installed; none are included here.

## Development dependencies

- **Playwright Test 1.64.1**, by Microsoft, is an Apache-2.0-licensed browser test runner. It is pinned in `package-lock.json` and installed only for development/CI; it is not shipped with the office. Source and licence: [microsoft/playwright](https://github.com/microsoft/playwright). The Chromium browser downloaded separately by Playwright retains its own third-party notices.

## Everything else

The 3D office, its people, textures, signs, posters and the user interface are drawn in code from simple shapes. They're original to this project and covered by its [LICENSE](LICENSE). No game assets, models, images or sounds from other works are included.

The look is a general 1990s-office and retro-desktop style. It's inspired by that era, not copied from any particular product.

## Trademarks

"Claude" and "Claude Code" are trademarks of Anthropic. They're used here only to describe what this project works with. This project isn't affiliated with or endorsed by Anthropic. Other names mentioned in the docs (Raspberry Pi, Tailscale, Samsung Galaxy Tab, iPad, Fully Kiosk Browser, GitHub) belong to their owners and are mentioned only for compatibility.
