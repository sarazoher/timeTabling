---
name: TimeTabling
description: Smart degree planning and course scheduling, from one Excel file
colors:
  night: "#14031d"
  plum-deep: "#250433"
  plum: "#75449a"
  glow: "#8b4fc9"
  glow-deep: "#5a2a86"
  lavender: "#e6c9f5"
  lilac: "#c7a3cd"
  lilac-deep: "#a77fc4"
  wash: "#f4eefa"
  ink: "#250433"
  muted: "#6c5a77"
  on-dark-muted: "#cfbedb"
  white: "#ffffff"
  course-required: "#3b82f6"
  course-elective: "#22c55e"
  course-seminar: "#ec4899"
  course-project: "#f59e0b"
  course-general: "#8b5cf6"
typography:
  display:
    fontFamily: "Heebo, Arial, sans-serif"
    fontSize: "clamp(40px, 6vw, 76px)"
    fontWeight: 900
    lineHeight: 1.02
    letterSpacing: "-0.03em"
  headline:
    fontFamily: "Heebo, Arial, sans-serif"
    fontSize: "clamp(30px, 4vw, 48px)"
    fontWeight: 900
    lineHeight: 1.1
    letterSpacing: "-0.02em"
  title:
    fontFamily: "Heebo, Arial, sans-serif"
    fontSize: "19px"
    fontWeight: 800
    lineHeight: 1.3
  body:
    fontFamily: "Heebo, Arial, sans-serif"
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: "Heebo, Arial, sans-serif"
    fontSize: "14px"
    fontWeight: 600
    lineHeight: 1.4
rounded:
  sm: "10px"
  md: "14px"
  lg: "18px"
  xl: "24px"
  pill: "999px"
spacing:
  xs: "6px"
  sm: "10px"
  md: "18px"
  lg: "28px"
  xl: "56px"
components:
  button-main-dark:
    backgroundColor: "{colors.white}"
    textColor: "{colors.plum-deep}"
    rounded: "{rounded.md}"
    height: "56px"
    padding: "0 26px"
  button-glass-dark:
    backgroundColor: "rgba(255,255,255,0.08)"
    textColor: "{colors.white}"
    rounded: "{rounded.md}"
    height: "56px"
    padding: "0 26px"
  button-main-light:
    backgroundColor: "{colors.plum}"
    textColor: "{colors.white}"
    rounded: "{rounded.md}"
    height: "50px"
    padding: "0 22px"
  glass-dark:
    backgroundColor: "rgba(255,255,255,0.07)"
    textColor: "{colors.white}"
    rounded: "{rounded.xl}"
    padding: "24px"
  glass-light:
    backgroundColor: "rgba(255,255,255,0.55)"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: "22px"
  chat-bubble:
    backgroundColor: "{colors.glow}"
    textColor: "{colors.white}"
    rounded: "{rounded.pill}"
    height: "58px"
---

# Design System: TimeTabling

## Overview

**Creative North Star: "The Lit Timetable"**

A degree's whole week, glowing out of a plum night. The cleverness of the app (a full, legal timetable from one file) is shown as light: course slots that switch on one by one, rules that rise into view, numbers in lavender. Everything else is calm, spacious and plain-spoken in Hebrew first.

The world has two levels of the same palette:

- **Showcase** (sign-in, Home, the building screen, empty states): dark plum night, slow drifting glow, fine grain, the fading week-grid pattern, glass panels, very large type. This is what a poster-day visitor sees in the first minute.
- **Work** (Data, Rules, the timetable, settings): frosted daylight. A pale lilac wash with purple glows bleeding in from two corners, milky glass panels, plum controls. Readable for long tables; it never becomes a plain white box.

Gradients, glass and patterns come from the team's moodboard (October 2026). Gold was tried and rejected: no warm accent. Highlights stay inside the purple family.

**Key Characteristics:**
- Plum, from the college poster, is the base; lavender is the highlight on dark.
- Light comes from glows behind glass, never from flat fills.
- One pattern only: the fading week grid with lit course slots.
- Big confident Hebrew headlines (Heebo 900), lots of empty space.
- Motion is slow and purposeful; all of it stops under reduced motion.

## Colors

A single purple family from deep night to pale wash, plus the five fixed course-category colours.

### Primary
- **Plum** (#75449a): buttons, current nav item and links on the work level; the logo table. Matches the poster.
- **Plum Deep** (#250433): text on light, text on white buttons, the base of dark panels.

### Secondary
- **Glow** (#8b4fc9) and **Glow Deep** (#5a2a86): only inside blurred glows, gradients and the chat bubble. Never as text.
- **Lavender** (#e6c9f5): highlighted words and numbers on dark ("38 courses"), icon strokes on dark.
- **Lilac** (#c7a3cd) and **Lilac Deep** (#a77fc4): the logo's cap and lit cells, avatar, dashed preference outlines.

### Neutral
- **Night** (#14031d): the showcase background under the glow.
- **Wash** (#f4eefa): the work background under the glow.
- **Muted** (#6c5a77): secondary text on light. **On-dark muted** (#cfbedb): secondary text on dark.

### Course categories (fixed, never rebranded)
Required #3b82f6, elective #22c55e, seminar #ec4899, project #f59e0b, general #8b5cf6, with their existing pale fills on light. On dark they appear as a 20–30% tint, a light border and a soft glow of the same colour.

### Named Rules
**The No Gold Rule.** No warm accent colour. Emphasis comes from lavender, white, weight and size.
**The Glow Behind Glass Rule.** Saturated purple appears as light behind something (blurred, at the edges), not as large flat areas. Text sits on night, on glass or on wash, never directly on a bright glow.

## Typography

**Display Font:** Heebo (bundled, OFL) with Arial fallback
**Body Font:** Heebo

**Character:** one family at very different weights: 900 for display, 800 for titles, 400–600 for reading. It carries both Hebrew and Latin, so the RTL and LTR versions feel identical.

### Hierarchy
- **Display** (900, clamp(40px, 6vw, 76px), 1.02, -0.03em): one per showcase page, the result or the promise.
- **Headline** (900, clamp(30px, 4vw, 48px), 1.1): section openers ("מה המערכת שומרת בשבילכם") and work-page titles.
- **Title** (800, 19px, 1.3): card and panel titles.
- **Body** (400, 16–20px, 1.55–1.6): lede on showcase pages is 20px with a 32ch measure.
- **Label** (600, 13–15px): nav, chips, table headers. Numbers use tabular figures.

**The One Display Rule.** A page has at most one display-size line.

## Layout

Showcase pages are full-bleed: content sits on the start side (right in Hebrew) with the week grid on the end side, tilted slightly in 3D and fading out toward the text. The validity chips sit along the bottom of the first screen. Sections below are centred with 70–110px between them.

Work pages use a 1180px container with 24px gutters: title row, a row of three glass cards, a full-width status bar, then long content (tables) last.

Rule cards are 250×250px, centred in rows: hard rules as 3 then 2, preferences as 2 then 2, never a uniform grid.

Phone (≤820px): everything stacks, the nav items move to a second scrollable row, the week grid sits above the text, main buttons go full width, the chat bubble shrinks to a round button. No sideways scroll at 390px.

## Elevation & Depth

Depth comes from glass over light, not from shadows on white.

### Shadow Vocabulary
- **Glass light** (`box-shadow: 0 24px 48px -28px rgba(60,20,90,.45), inset 0 1px 0 #fff`): every work-level panel.
- **Main button dark** (`box-shadow: 0 18px 40px -16px rgba(199,163,205,.7)`): white main button on dark.
- **Bubble** (`box-shadow: 0 18px 40px -12px rgba(139,79,201,.8)`): the chat bubble.

**The Blur Budget Rule.** `backdrop-filter` only on the nav, glass panels and the bubble. If the demo laptop stutters, glass falls back to a solid tint (rgba fill without blur), never to a different design.

## Shapes

Generous rounded rectangles everywhere: 10px for small controls, 14px buttons, 18px work panels, 24px showcase cards, pills for chips and the bubble. Borders are 1px and translucent white on both levels (the bright top edge of glass). The logo is a plum table with a lilac graduation cap tilted 25° outward, tassel hanging straight down, two opposite cells lit.

## Components

### Buttons
- **Main on dark:** white fill, plum-deep text, 56px tall, 14px radius, lifts 2px on hover.
- **Glass on dark:** 8% white fill, 22% white border, blurred backdrop.
- **Main on light:** plum gradient (glow → plum → glow-deep), white text, 50px.
- Focus: 3px lilac outline with 2px offset on every control.

### Glass panels
- **Dark:** gradient from 11% to 3% white, 13% white border, blur 14px, radius 24px.
- **Light:** 55% white, 85% white border, blur 22px with saturate 1.25, radius 18–22px, the glass-light shadow.

### Navigation
- Dark level: sticky bar of night at 35% with blur; current item is a 12% white pill.
- Light level: a floating glass bar 12px from the top; current item is a plum pill with white text.
- Logo plus "TimeTabling" (always Latin, LTR) at the start.

### Status bar (work level)
A pill-shaped gradient bar (plum on the start side fading toward the end) with a white tick circle, one line of plain status, a slow light sheen passing over it every few seconds, and a round plum arrow button.

### Rule cards (showcase)
Glass dark, 250×250, centred content: 62px icon box (32px icon, lavender stroke; dashed outline for preferences), title, one line. Rows slide in from alternating sides as they enter the screen, cards in a row 140ms apart.

### Chat bubble
Fixed bottom corner on the end side (bottom-left in Hebrew), glow gradient pill with "רוצים לשנות משהו? כתבו כאן", a soft pulsing ring. Round icon-only on phones. It is the only way into the assistant and sits on every signed-in page (`_assistant.html`, included by the top menu).

### Assistant drawer
Milky light glass on both levels, sliding in from the same end side (a bottom sheet on phones) over a lightly blurred page. Your messages sit on the end side in plum, the assistant's on the start side in white. A proposal shows as a white card with one main button. Without a key the drawer still opens; the field is disabled and one plain line says why. After sending, the page you were on reloads with the drawer open (`#assistant`).

### Week-grid pattern (signature)
A 6×7 grid of 10px-radius outlines at 9% white; 6–8 slots light up one by one (1.2s ease-out each, 350ms apart) in course colours, then stay lit. On real Home pages the lit slots reflect the actual timetable.

## Do's and Don'ts

### Do:
- **Do** keep plum (#75449a) as the brand colour and lavender (#e6c9f5) as the only highlight on dark.
- **Do** put text on night, glass or wash; check 4.5:1 for body text.
- **Do** use exponential ease-out (cubic-bezier(.16,1,.3,1)) and keep ambient motion slower than 20s per cycle.
- **Do** honour `prefers-reduced-motion`: everything appears in place, glows stop drifting.
- **Do** write the brand as "TimeTabling" with the logo, in both languages.

### Don't:
- **Don't** use gold, orange or any warm accent.
- **Don't** use gradient text, kicker labels above headings, or hard offset shadows.
- **Don't** add a second background pattern (starbursts, orbits, flow lines were considered and rejected).
- **Don't** put a dark showcase background behind long tables or forms on work pages.
- **Don't** recolour the course categories to fit the brand.
