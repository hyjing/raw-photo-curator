# RAW Photo Curator Web

Zero-dependency browser application. Choose a local folder, analyze embedded RAW JPEG previews or image pixels in Web Workers, review Top 5, learn keep/reject preferences locally and export XMP sidecars in a ZIP. No photo uploads, analytics, server inference or external runtime dependencies.

Run `npm test`, then `npm run dev`. Open http://localhost:8790. Static deployment serves `dist/`. The service worker caches the app for offline use after the first visit. Folder access requires a user gesture; browsers without showDirectoryPicker use a directory input or multiple-file picker.

This is a browser MVP: RAW processing extracts the largest embedded JPEG, rather than demosaicing sensor data. RAW without a browser-decodable embedded JPEG reports an error. Composition/horizon are heuristics; scores do not establish artistic quality or RAW recovery potential. Rotation affects preview only. XMP files mark keeps with rating 5; review existing sidecars before placing them alongside originals. Preferences persist in localStorage on this browser and origin; clearing site data deletes them. The browser model is separate from the desktop model and uses eleven preview metrics. No semantic model, EXIF controls or burst grouping yet.
