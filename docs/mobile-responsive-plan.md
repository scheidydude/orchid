# Mobile Responsive Plan — Option A (Single Codebase)

**Approach:** Progressive enhancement via CSS `@media` breakpoints + targeted React
changes. Desktop layout unchanged. Mobile degrades gracefully across all 5 phases.

**Breakpoints:**
- `≤768px` — mobile (phones, small tablets portrait)
- `769px–1024px` — tablet (optional, Phase 5)
- `≥1025px` — desktop (current behavior, untouched)

**Stack reality:**
- 719-line `index.css`, zero `@media` rules today
- Heavy inline `style={}` throughout components — CSS-only overrides limited; some
  components need JS changes
- `DependencyGraph` uses Cytoscape (canvas, fixed 380px height) — hardest problem
- `PhaseTimeline`, `SessionBurndown`, `MilestoneProgress` use recharts — has
  responsive container support built in
- 10 tabs in a fixed row — overflows immediately on 375px screen

---

## Phase 1 — CSS Foundation (no JS changes)

**Goal:** Stop layout from breaking on mobile. Everything visible and scrollable.
No polish yet.

### Tasks

**1.1 Viewport meta tag**
Add to `index.html`:
```html
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
```
Without this, mobile browsers zoom out and render at 980px. Nothing else matters
until this lands.

**1.2 Sidebar collapse**
```css
@media (max-width: 768px) {
  .app-body { flex-direction: column; }
  .sidebar  { width: 100%; height: auto; border-right: none;
              border-bottom: 1px solid var(--border); padding: 8px 0;
              overflow-x: auto; overflow-y: hidden;
              display: flex; flex-direction: row; }
}
```
Sidebar becomes a horizontal scrolling project strip at top. Not ideal UX but
functional and zero JS.

**1.3 Tab bar scroll**
```css
@media (max-width: 768px) {
  .panel-tabs { overflow-x: auto; overflow-y: hidden;
                flex-wrap: nowrap; -webkit-overflow-scrolling: touch;
                scrollbar-width: none; }
  .panel-tabs::-webkit-scrollbar { display: none; }
  .panel-tab  { flex-shrink: 0; }
}
```
All 10 tabs visible via horizontal scroll. No tabs hidden or dropped.

**1.4 Header shrink**
```css
@media (max-width: 768px) {
  .app-header { padding: 8px 12px; gap: 8px; }
  .app-header .logo { font-size: 16px; }
  .project-path { display: none; }   /* path is noise on mobile */
}
```

**1.5 Panel body full width**
```css
@media (max-width: 768px) {
  .panel-body { padding: 8px; }
}
```

**1.6 Overflow guard**
```css
@media (max-width: 768px) {
  body, #root { overflow-x: hidden; max-width: 100vw; }
}
```

**Deliverable:** App renders on iPhone without horizontal overflow. All content
reachable. No regression on desktop.

**Test:** Chrome DevTools → iPhone 14 Pro (390px). Safari on physical device.

---

## Phase 2 — Navigation UX

**Goal:** Replace horizontal-strip sidebar with proper mobile nav. Tab bar becomes
usable, not just scrollable.

### Tasks

**2.1 `useMediaQuery` hook**
```js
// src/hooks/useMediaQuery.js
import { useState, useEffect } from 'react'
export function useMediaQuery(query) {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const mq = window.matchMedia(query)
    const handler = e => setMatches(e.matches)
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [query])
  return matches
}
```

**2.2 Sidebar → hamburger drawer**
In `App.jsx`:
- Add `const isMobile = useMediaQuery('(max-width: 768px)')`
- Add `const [drawerOpen, setDrawerOpen] = useState(false)`
- On mobile: render hamburger button in header; `<nav>` becomes an overlay drawer
  (`position: fixed`, slide in from left, backdrop closes it)
- `ProjectSwitcher` unchanged — renders identically inside drawer
- Drawer closes on project select

CSS additions:
```css
.sidebar-drawer { position: fixed; top: 0; left: 0; height: 100vh; width: 280px;
                  z-index: 200; transform: translateX(-100%);
                  transition: transform 0.2s ease; }
.sidebar-drawer.open { transform: translateX(0); }
.sidebar-backdrop  { position: fixed; inset: 0; z-index: 199;
                     background: rgba(0,0,0,0.5); }
```

**2.3 Tab bar → icon strip on mobile (optional)**
If 10 scrollable tabs feel clunky after Phase 1 testing, add tab icons and reduce
label to 2–3 char abbreviations on mobile only:
```js
const TAB_META = {
  Tasks:     { icon: '☑', short: 'Tasks' },
  Planning:  { icon: '🗺', short: 'Plan' },
  PM:        { icon: '📊', short: 'PM' },
  Stream:    { icon: '📡', short: 'Stream' },
  Decisions: { icon: '🗳', short: 'Dec' },
  Sessions:  { icon: '📜', short: 'Sess' },
  Recall:    { icon: '🔍', short: 'Recall' },
  Memory:    { icon: '🧠', short: 'Mem' },
  Config:    { icon: '⚙', short: 'Cfg' },
  Settings:  { icon: '🔧', short: 'Set' },
}
```
Render `isMobile ? meta.short : tab` in tab button. Icons optional — evaluate
after Phase 1 testing.

**2.4 "New Project" button**
On mobile header, collapse to `+` icon only. Full label on desktop.

**Deliverable:** One-thumb navigation. Sidebar accessible without layout breakage.
Tab bar fits on 375px without scrolling (short labels) or scrolls comfortably.

---

## Phase 3 — Component Touch Adaptation

**Goal:** Interactive elements hit 44px minimum touch targets. Modals go
full-screen. Forms usable on mobile keyboard.

### Affected components (in priority order)

**3.1 `RunControls.jsx`** (80 lines)
- Run/Stop buttons: ensure `min-height: 44px` on mobile
- Status text can wrap — add `flex-wrap: wrap` guard

**3.2 `TaskRow.jsx`** (80 lines)
- Row tap target: ensure full-row is tappable, not just text
- Status badge: needs `min-width` so it doesn't collapse
- On mobile: stack task ID + title vertically if row is too narrow

**3.3 `TaskBoard.jsx`** (152 lines)
- Table layout breaks on mobile — convert to card stack on `≤768px`
- Each task = card with ID, title, status, type visible
- Use CSS `@media` + class toggle, or conditional render in JSX

**3.4 `AddTaskModal.jsx`** (95 lines)
- Current: centered modal, fixed width
- Mobile: `position: fixed; inset: 0; border-radius: 0` — full screen sheet
- Input fields: `font-size: 16px` minimum (prevents iOS auto-zoom on focus)

**3.5 `ProjectSwitcher.jsx`** (162 lines)
- Already a list — confirm touch targets ≥44px tall per item
- Active indicator visible without hover state

**3.6 `AgentStream.jsx`** (60 lines)
- Scrolling log — works on mobile if `overflow-y: auto` + `-webkit-overflow-scrolling: touch`
- Font size: can stay small (this is a log)

**3.7 `RecallSearch.jsx`** (91 lines)
- Search input: `font-size: 16px` to prevent iOS zoom
- Results list: full width, adequate touch targets

**3.8 `Settings.jsx` + `ProjectSettings.jsx`**
- Form fields: `font-size: 16px`, labels above inputs (not beside) on mobile
- Save buttons: full-width on mobile

**Deliverable:** Core workflow (pick project → view tasks → run → watch stream)
fully usable one-handed on iPhone.

---

## Phase 4 — Dense Visualization Components

**Goal:** PM tab components don't break or become unusable on mobile.
Fallback strategies for canvas-based graphs.

### Components

**4.1 `DependencyGraph.jsx` — Cytoscape canvas (244 lines, HARD)**

Cytoscape renders to a `<div ref={containerRef}>` at fixed `height: 380`. On mobile
this is 380px tall but the canvas gets cramped width — nodes overlap, labels clip.

Strategy: **mobile fallback to table view**

```jsx
// In DependencyGraph.jsx
const isMobile = useMediaQuery('(max-width: 768px)')

if (isMobile) {
  return <DependencyTable tasks={tasks} />  // simple <table> of id → deps
}
// existing cytoscape render
```

`DependencyTable` is a new ~30-line component: task ID, title, status, depends-on
list. All the data, none of the graph. Toggle button ("Show Graph") lets mobile
users opt into the canvas view if they rotate to landscape.

**4.2 `PhaseTimeline.jsx` — flex bar chart (114 lines)**

Fixed `height: 52` flex row of phase bars. On narrow screens, phase labels clip.

Strategy: **scrollable container + `min-width` per bar**

```css
@media (max-width: 768px) {
  .phase-timeline-bars { overflow-x: auto; -webkit-overflow-scrolling: touch; }
}
```
Set `min-width: 60px` per phase bar so labels never clip. User scrolls horizontally
to see full timeline. Low effort, acceptable UX.

**4.3 `SessionBurndown.jsx` + `MilestoneProgress.jsx` (recharts)**

Recharts has `<ResponsiveContainer width="100%" height={N}>`. Both components
likely already use this pattern. Verify and reduce `height` on mobile:

```jsx
<ResponsiveContainer width="100%" height={isMobile ? 180 : 300}>
```

Recharts text labels may overlap on narrow width — use `angle={-45}` on x-axis
ticks or hide every-other label via `interval="preserveStartEnd"`.

**4.4 `PMDashboard.jsx` (70 lines)**

Grid layout of sub-components. On mobile: stack vertically.

```css
@media (max-width: 768px) {
  .pm-dashboard-grid { grid-template-columns: 1fr; }
}
```
(Add `pm-dashboard-grid` class if not already present.)

**4.5 `PlanningTab.jsx` + `DiscussionPanel.jsx`**

Discussion panel is a chat-like stream. Mobile-friendly by nature if width is 100%.
Approval panel: buttons need `min-height: 44px`.

Artifact panel: file tree + content viewer. On mobile: collapsed tree by default,
tap to expand. Add toggle state if not already present.

**Deliverable:** PM tab usable on mobile. DependencyGraph has a usable fallback.
No component overflows or clips text.

---

## Phase 5 — Polish & PWA

**Goal:** Feel like a first-class mobile app, not a shrunken desktop.

### Tasks

**5.1 Tablet breakpoint (769px–1024px)**
Current sidebar at 220px is fine on landscape iPad. Portrait iPad (768px) should
use mobile nav. Verify Phase 1–4 work at 768px exactly.

**5.2 Safe area insets (notch / home bar)**
```css
:root {
  --safe-top:    env(safe-area-inset-top, 0px);
  --safe-bottom: env(safe-area-inset-bottom, 0px);
}
.app-header { padding-top: calc(10px + var(--safe-top)); }
/* bottom nav if added */ .bottom-nav { padding-bottom: calc(8px + var(--safe-bottom)); }
```

**5.3 Touch gesture hints**
Swipe left on sidebar drawer to close (via `touchstart`/`touchend` delta check in
drawer component). Optional — only if drawer close UX feels awkward.

**5.4 PWA manifest**
Add `manifest.json` to `public/`:
```json
{
  "name": "Orchid",
  "short_name": "Orchid",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#0d1117",
  "theme_color": "#0d1117",
  "icons": [{ "src": "/icon-192.png", "sizes": "192x192", "type": "image/png" }]
}
```
Link in `index.html`. Lets users add to home screen — hides browser chrome,
feels native.

**5.5 Focus/scroll management**
When switching tabs on mobile, scroll `panel-body` to top. Prevents user landing
mid-scroll from previous tab.

**5.6 Loading states**
On slow mobile connections, loading spinners must be visible and centered. Audit
all `className="loading"` usages for mobile visibility.

**Deliverable:** App installable via "Add to Home Screen". Notch-safe. Swipe
gestures work. Feels native on iOS/Android.

---

## Risk Register

| Risk | Impact | Mitigation |
|---|---|---|
| Inline `style={}` overrides CSS `@media` | High | Convert hot spots to className + CSS vars |
| Cytoscape canvas unusable on mobile | Medium | Table fallback (Phase 4.1) |
| iOS auto-zoom on `font-size < 16px` inputs | Medium | Set `font-size: 16px` on all inputs |
| recharts labels overlap narrow width | Low | `interval`, `angle` props |
| Drawer overlay z-index conflicts | Low | Audit z-index stack before Phase 2 |
| Phase 1 CSS regression on desktop | Medium | Snapshot test at 1280px before each phase |

---

## Implementation Order & Effort

| Phase | Effort | Files changed | Risk |
|---|---|---|---|
| 1 — CSS Foundation | ~2h | `index.html`, `index.css` | Low |
| 2 — Navigation UX | ~4h | `App.jsx`, `index.css`, new hook | Medium |
| 3 — Touch Adaptation | ~6h | 8 components, `index.css` | Low–Medium |
| 4 — Visualizations | ~6h | 5 components, new `DependencyTable` | Medium |
| 5 — PWA Polish | ~3h | `index.html`, `index.css`, `public/` | Low |

**Total: ~21h.** Each phase ships independently and is usable without the next.
Stop at Phase 2 or 3 if full PM tab support on mobile isn't a priority.

---

## Testing Protocol (each phase)

1. Chrome DevTools → iPhone 14 Pro (390×844) and iPad Mini (768×1024)
2. Physical Safari on iOS — especially keyboard behavior and safe areas
3. Desktop at 1280px — confirm zero regression
4. Scroll every tab, open every modal, run a task end-to-end on mobile

No automated visual regression tests assumed. Manual review per phase is sufficient
given the app's current test posture.
