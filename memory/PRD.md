# Pontis / Jack — Candidate-side AI Dashboard

## Problem Statement
React + Tailwind dashboard for "Jack" (originally "Eve" for Pontis) — the candidate-side AI recruitment agent.
Strict 3-column architecture: Sidebar nav + Chat hub + Living Profile.
Iterated with user toward a warm cream/off-white iPhone-like aesthetic (reference: Jack and Jill).

## User Personas
- Job candidate onboarding through a voice intake + chat flow with an AI agent (Jack).
- Wants to review matched roles, refine their profile, and be discoverable to employers.

## Core Requirements (locked)
- 3-column layout, **draggable/resizable** partitions (default: 18/32/50).
- Warm cream/beige palette (#F5F0E7) with white embossed cards; consistent across all tabs.
- User messages appear as white pill bubbles on the right.
- Sidebar: New jobs (badge), Tracked jobs, Profile, Documents, Coaching, Recent activity, user footer.
- Chat hub: Call summary pill, day divider, Jack messages as plain text, quick actions, composer.
- Living Profile: strength bar, Open-to-matches badge, embossed profile/experience/education/skills cards.
- Jobs tab: embossed job cards with match %, Track / Not-for-me buttons.

## Implemented (Feb 2026)
- Frontend refactored into `/components/Sidebar.jsx`, `ChatHub.jsx`, `LivingProfile.jsx`.
- `App.js` uses `react-resizable-panels` for draggable columns.
- Backend `/api/chat` endpoint using `emergentintegrations.LlmChat` with `gpt-5-nano` (cheapest OpenAI).
- Real chat with Jack wired via axios, with typing indicator and sonner toasts.
- Mock data lives in `/frontend/src/mock.js` — jobs, profile, docs, recent activity, quick actions.

## Backlog / Next
- P0: Persist chats + profile to Mongo (currently mock state).
- P1: Streaming SSE responses for the chat.
- P1: Move "Coaching" into a dedicated tab.
- P2: Voice input via Whisper (deferred per user).
- P2: Auth (JWT or Emergent Google login) + multi-user profiles.
