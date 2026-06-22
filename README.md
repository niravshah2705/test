# Chain Reaction

A simple realtime multiplayer Chain Reaction game built with vinext.

## Features

- Google Identity Services sign-in when `NEXT_PUBLIC_GOOGLE_CLIENT_ID` is configured.
- Guest mode for local play and validation when OAuth credentials are unavailable.
- Shared room state synced across same-origin tabs with `BroadcastChannel` and `localStorage`.
- Chain Reaction turn rules with cell capacity, explosions, elimination, and win detection.

## Development

```bash
npm install
npm run dev
```

## Environment

Copy `.env.example` to `.env.local` and set `NEXT_PUBLIC_GOOGLE_CLIENT_ID` to enable Google sign-in.
