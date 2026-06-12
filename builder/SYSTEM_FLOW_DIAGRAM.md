# System Flow Diagram

## Desktop Flow (Screen Width > 768px)

```
┌─────────────────────────────────────────────────────────────┐
│                        INITIAL STATE                         │
│                                                              │
│  ┌──────────────┐  ┌────────────────────────────────────┐  │
│  │   SIDEBAR    │  │        MAIN CONTENT                │  │
│  │   VISIBLE    │  │    (margin-left: 260px)            │  │
│  │              │  │                                    │  │
│  │  • Nav       │  │  Header: [☰] Page Title  [Theme]  │  │
│  │  • Links     │  │                                    │  │
│  │  • Submenus  │  │  Content Area                      │  │
│  │              │  │                                    │  │
│  └──────────────┘  └────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                            │
                            │ User clicks [☰] hamburger
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    SIDEBAR HIDDEN STATE                      │
│                                                              │
│  [SIDEBAR           ┌────────────────────────────────────┐  │
│   SLIDES OUT        │      MAIN CONTENT EXPANDS          │  │
│   TO LEFT]          │      (margin-left: 0)              │  │
│                     │                                    │  │
│   transform:        │  Header: [☰→X] Page Title [Theme] │  │
│   translateX(-100%) │                                    │  │
│                     │  Content Area (full width)         │  │
│                     │                                    │  │
│                     └────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                            │
                            │ User clicks [X] hamburger
                            ▼
                    [Returns to Initial State]
```

## Mobile Flow (Screen Width ≤ 768px)

```
┌─────────────────────────────────────────────────────────────┐
│                    MOBILE INITIAL STATE                      │
│                                                              │
│  [SIDEBAR OFF-SCREEN]    ┌──────────────────────────────┐  │
│                          │    MAIN CONTENT              │  │
│  transform:              │    (no margin)               │  │
│  translateX(-100%)       │                              │  │
│                          │  Header: [☰] Title  [Theme]  │  │
│                          │                              │  │
│                          │  Content Area                │  │
│                          │  (full width)                │  │
│                          │                              │  │
│                          └──────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                            │
                            │ User clicks [☰] hamburger
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                   MOBILE SIDEBAR OPEN STATE                  │
│                                                              │
│  ┌────────────────┐  [DARK OVERLAY]                        │
│  │   SIDEBAR      │  ┌──────────────────────────────┐     │
│  │   SLIDES IN    │  │ Content (dimmed & blurred)   │     │
│  │   OVER CONTENT │  │                              │     │
│  │                │  │ Header: [X] Title  [Theme]   │     │
│  │  • Navigation  │  │                              │     │
│  │  • Links       │  │ Content Area                 │     │
│  │  • Submenus    │  │ (partially visible)          │     │
│  │                │  │                              │     │
│  │  [User Info]   │  │                              │     │
│  └────────────────┘  └──────────────────────────────┘     │
│                                                              │
│  Click overlay or [X] → Returns to Initial State            │
└─────────────────────────────────────────────────────────────┘
```

## Hamburger Animation States

```
┌──────────────────────────────────────────────────────────┐
│                  HAMBURGER BUTTON                         │
└──────────────────────────────────────────────────────────┘

        DEFAULT STATE              ACTIVE STATE
        (Sidebar Open)            (Sidebar Closed)

        ───────────               ╱
        ───────────               ✗
        ───────────               ╲

        Three horizontal          Three lines transform:
        lines                     • Top: rotates 45° up
                                 • Middle: fades out
                                 • Bottom: rotates -45° down
```

## Theme Button Animation

```
┌──────────────────────────────────────────────────────────┐
│                   THEME BUTTON                            │
└──────────────────────────────────────────────────────────┘

    LIGHT MODE              DARK MODE
    
      ☀️                      🌙
    (visible)              (visible)
    🌙 (hidden)            ☀️ (hidden)
    
    Click: ☀️ rotates out,  🌙 rotates in
    Hover: Radial gradient pulse effect
```

## Component Interaction Flow

```
┌────────────────────────────────────────────────────────────┐
│                      USER INTERACTION                       │
└────────────────────────────────────────────────────────────┘
                            │
                            ▼
              ┌─────────────────────────┐
              │  Clicks Hamburger [☰]  │
              └─────────────────────────┘
                            │
                            ▼
              ┌─────────────────────────┐
              │  JavaScript Detects     │
              │  Click Event            │
              └─────────────────────────┘
                            │
                            ▼
              ┌─────────────────────────┐
              │  Check: isMobile?       │
              └─────────────────────────┘
                     │            │
          ┌──────────┘            └──────────┐
          │                                  │
          ▼                                  ▼
    [MOBILE]                           [DESKTOP]
          │                                  │
          ▼                                  ▼
┌─────────────────────┐          ┌─────────────────────┐
│ toggleMobile()      │          │ toggleDesktop()     │
│                     │          │                     │
│ • Add/remove        │          │ • Add/remove        │
│   'mobile-open'     │          │   'hidden' class    │
│ • Show/hide overlay │          │ • Adjust margin     │
│ • Lock body scroll  │          │ • No overlay        │
│ • Animate hamburger │          │ • Animate hamburger │
└─────────────────────┘          └─────────────────────┘
          │                                  │
          └──────────┐            ┌──────────┘
                     │            │
                     ▼            ▼
              ┌─────────────────────────┐
              │  CSS Transitions        │
              │  Animate Changes        │
              └─────────────────────────┘
                            │
                            ▼
              ┌─────────────────────────┐
              │  Visual Update Complete │
              └─────────────────────────┘
```

## Class State Matrix

```
┌─────────────┬──────────────┬──────────────┬──────────────┐
│   State     │   Sidebar    │  Main        │  Hamburger   │
│             │   Classes    │  Wrapper     │  Button      │
├─────────────┼──────────────┼──────────────┼──────────────┤
│ Desktop     │ (none)       │ with-sidebar │ (none)       │
│ Open        │              │              │              │
├─────────────┼──────────────┼──────────────┼──────────────┤
│ Desktop     │ hidden       │ with-sidebar │ active       │
│ Closed      │              │ sidebar-     │              │
│             │              │ hidden       │              │
├─────────────┼──────────────┼──────────────┼──────────────┤
│ Mobile      │ (none)       │ with-sidebar │ (none)       │
│ Closed      │              │              │              │
├─────────────┼──────────────┼──────────────┼──────────────┤
│ Mobile      │ mobile-open  │ with-sidebar │ active       │
│ Open        │              │              │              │
└─────────────┴──────────────┴──────────────┴──────────────┘
```

## File Dependencies

```
┌─────────────────────────────────────────────────────────────┐
│                      TEMPLATE LAYER                          │
│                                                              │
│  base.html                                                   │
│    ├── Loads CSS files                                      │
│    ├── Includes sidebar.html (conditional)                  │
│    └── Loads JS files                                       │
│                                                              │
│  sidebar.html                                                │
│    └── Navigation structure                                 │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                       STYLE LAYER                            │
│                                                              │
│  base.css                                                    │
│    ├── Layout & structure                                   │
│    ├── Hamburger menu styles                                │
│    ├── Theme button styles                                  │
│    └── Header/footer styles                                 │
│                                                              │
│  sidebar.css                                                 │
│    ├── Sidebar positioning                                  │
│    ├── Navigation styles                                    │
│    ├── Slide animations                                     │
│    └── Responsive behavior                                  │
│                                                              │
│  theme.css                                                   │
│    ├── Color variables                                      │
│    ├── Light mode                                           │
│    └── Dark mode                                            │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                      BEHAVIOR LAYER                          │
│                                                              │
│  sidebar.js                                                  │
│    ├── SidebarManager class                                 │
│    ├── Toggle logic                                         │
│    ├── Resize handling                                      │
│    └── Event listeners                                      │
│                                                              │
│  theme.js                                                    │
│    ├── ThemeManager class                                   │
│    ├── Theme switching                                      │
│    └── Preference saving                                    │
└─────────────────────────────────────────────────────────────┘
```

## Performance Optimization

```
┌─────────────────────────────────────────────────────────────┐
│                   RENDERING PIPELINE                         │
└─────────────────────────────────────────────────────────────┘

   CSS Transforms                  Layout Calculation
   (GPU Accelerated)               (CPU Only)
   
   ✅ transform: translateX()      ❌ left/right position
   ✅ opacity                      ❌ width changes
   ✅ scale                        ❌ display: none/block
   
   Why We Use Transform:
   • Hardware accelerated
   • No layout reflow
   • Smooth 60fps animations
   • Better battery on mobile
```

## Event Flow Timeline

```
Time │ Event
─────┼──────────────────────────────────────────────────────
  0  │ User clicks hamburger button
 10ms│ JavaScript: click event fires
 20ms│ JavaScript: Toggle classes applied
 30ms│ CSS: Transition starts
     │ • Sidebar: transform changes
     │ • Hamburger: lines rotate
     │ • Main: margin adjusts
300ms│ CSS: Transition completes
310ms│ JavaScript: transitionend event
320ms│ Ready for next interaction
```
