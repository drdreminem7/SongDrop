# SongDrop

SongDrop is a macOS CLI that prepares legally downloadable online audio and imports the finished
track into Apple Music. Apple Music is the user's library and source of truth; SongDrop does not
maintain a second music collection or catalog.

SongDrop currently supports individual tracks, YouTube/YouTube Music playlists, and text-file URL
batches through `yt-dlp`.

SongDrop also includes a Manifest V3 extension for Brave and other Chromium browsers. The
extension sends the current page to an authenticated service bound only to the local Mac; it does
not send SongDrop jobs to a cloud service.

## Legal and usage scope

Use SongDrop only for media you own, have permission to download, that is in the public domain,
or that is otherwise legally downloadable. You are responsible for following the source site's
terms and applicable law. SongDrop does not circumvent DRM, authentication, paywalls, or other
access controls. It does not implement Spotify downloads or Spotify audio ripping.

## Default workflow

The primary command is simply:

```bash
songdrop "https://www.youtube.com/watch?v=VIDEO_ID"
```

On macOS, SongDrop automatically:

1. downloads the audio into a SongDrop-owned staging session;
2. converts or remuxes it into the requested final format;
3. cleans provider metadata and confidence-matches the recording through MusicBrainz;
4. retrieves exact release artwork from Cover Art Archive and matched lyrics from LRCLIB when
   available;
5. embeds available title, primary/featured artists, album, track number, date/year, artwork,
   and lyrics;
6. flushes and closes the finished file;
7. imports it into Apple Music;
8. verifies the returned persistent ID in Music and verifies a separate managed media file;
9. removes SongDrop's staging file and work directory.

After a successful normal import, `~/Downloads/SongDrop` is empty. SongDrop does not create an
`Artist/Album/Track` library, download database, or permanent history of its own.

## Brave browser extension

Install SongDrop in the Python environment first, then start its local companion service:

```bash
cd /path/to/SongDrop
source .venv/bin/activate
songdrop serve
```

Keep this Terminal process running while using the extension. It listens only on
`http://127.0.0.1:8765`. No pairing code or manual connection step is required. The known
SongDrop extension origin receives a local bearer credential automatically; other extension and
website origins are rejected. SongDrop bounds its in-memory queue and forgets completed job status
after one hour. It does not record a persistent download history.

Load the extension in Brave:

1. Open `brave://extensions`.
2. Enable **Developer mode**.
3. Choose **Load unpacked**.
4. Select this repository's `extension` directory.
5. Pin SongDrop to the toolbar.

Clicking SongDrop on a normal YouTube or YouTube Music track immediately queues the track for Apple
Music. It does not ask for a destination. An explicit `/playlist?list=...` page is the only normal
case that shows two choices:

- **Import into Apple Music**
- **Download playlist to SongDrop folder**

A watch page containing both `v` and `list` parameters still imports only the currently open song.
This matches the CLI behavior; open the explicit playlist page when you want the full playlist.

The extension always requests MP3, SongDrop's current default. Local work remains serialized so
downloads, metadata services, FFmpeg, and Apple Music automation are not run concurrently.

## Critical Apple Music setting

In **Music > Settings > Files**, enable:

> Copy files to Music Media folder when adding to library

SongDrop does not blindly trust the `add` command. It looks up the returned track by its persistent
ID, obtains Music's file location, confirms that file exists, and confirms it is not the same
filesystem object as SongDrop's staging file. If Music still references the staging path, SongDrop
fails safely and keeps the file. It never deletes or modifies Music's managed media file.

The first run may cause macOS to ask whether your terminal can control Music. If necessary, allow
it under **System Settings > Privacy & Security > Automation**.

## Playlists and batches

An explicit playlist URL works with the same primary command:

```bash
songdrop "https://music.youtube.com/playlist?list=PLAYLIST_ID"
```

You can force playlist expansion when a URL includes both a video and a playlist:

```bash
songdrop playlist "https://www.youtube.com/watch?v=VIDEO_ID&list=PLAYLIST_ID"
```

For a batch, create a UTF-8 text file with one URL per line. Blank lines and lines beginning with
`#` are ignored:

```bash
songdrop batch urls.txt
```

Entries are processed sequentially and in source order. SongDrop skips duplicate provider IDs
within the current command, continues after individual failures, and prints a final summary.
`--fail-fast` stops after the first failure. The default expansion limit is 200 entries; use
`--max-items NUMBER` to explicitly allow a larger operation.

Transient YouTube media failures such as HTTP 403, 429, server errors, and timeouts receive a
small number of bounded retries. Each retry performs a fresh extraction so expired or temporarily
invalid signed stream URLs are replaced. Permanent failures such as private or unavailable videos
are not retried repeatedly.

A failed batch writes a small `retry-*.txt` file containing only failed URLs under the staging
root. It can be passed directly to `songdrop batch`. This is short-lived recovery information, not
a music catalog or download history. SongDrop does not currently recreate the source playlist as
an Apple Music playlist; each successfully prepared track is imported into the Music library.

## Download-only testing mode

Use `--download-only` to exercise acquisition, conversion, metadata enrichment, optional artwork,
and final tagging without opening or importing into Apple Music:

```bash
songdrop "https://music.youtube.com/watch?v=VIDEO_ID" --download-only
```

The completed file remains in:

```text
~/Downloads/SongDrop/
```

Download-only filenames contain the resolved track title only, for example:

```text
Stereo Love.mp3
Déjà vu (feat. Bob Taylor).mp3
```

The artist remains embedded in the file's metadata rather than being repeated in the filename.

This mode also works with playlists and batch files:

```bash
songdrop "https://music.youtube.com/playlist?list=PLAYLIST_ID" --download-only
songdrop batch urls.txt --download-only
```

Use `--output` to choose another test-output directory. Download-only files are intentional user
outputs, so SongDrop does not delete them after completion. The normal behavior remains verified
Apple Music import followed by staging cleanup.

Before creating a download-only output, SongDrop checks existing files in that output directory.
It prefers an embedded provider/source ID written by SongDrop, then conservatively compares the
resolved title, artist, and duration for older files without that tag. A match is reported as
`Already downloaded`; no permanent database or history is created.

## Safe failure behavior

SongDrop only deletes staging data after a verified Music import with an independent managed copy.
It preserves recoverable staging data if downloading, conversion, metadata writing, Music import,
verification, or cleanup fails. The CLI prints the exact preserved file or session path. Artwork
lookup/download is optional: if canonical album art is unavailable or invalid, SongDrop imports
the correctly tagged track without artwork. It never substitutes the YouTube video thumbnail.

Preserved files are normally placed directly under:

```text
~/Downloads/SongDrop/
```

For example:

```text
~/Downloads/SongDrop/Artist - Track.m4a
```

Partially completed job data may remain in a `.songdrop-*` session directory under the same root.
SongDrop's deletion guard only permits removal of paths registered as created by the current job.

## Requirements

- macOS with the Music application
- Python 3.12 or newer
- [FFmpeg](https://ffmpeg.org/) on `PATH` when conversion or remuxing is required
- Deno 2.3+ or Node.js 22+ on `PATH` for YouTube's JavaScript challenges
- Network access for source metadata, audio, release artwork, and lyrics

Install FFmpeg with Homebrew:

```bash
brew install ffmpeg
```

SongDrop automatically detects Deno first and Node.js second and passes the selected runtime to
the `yt-dlp` Python API. The official `yt-dlp-ejs` solver package is installed with SongDrop. If
neither runtime is present, install the recommended Deno runtime with `brew install deno`.

### Optional audio fingerprinting

The normal zero-setup resolver matches MusicBrainz using the cleaned title, primary artist, feature
credits, and duration. For stronger audio-based identification, install Chromaprint and provide a
personal AcoustID application key for the current shell:

```bash
brew install chromaprint
export SONGDROP_ACOUSTID_API_KEY="your-application-key"
```

The environment variable takes precedence. You may also put the key in an uncommitted `.env` file
in the directory where you run SongDrop:

```text
SONGDROP_ACOUSTID_API_KEY=your-application-key
```

SongDrop's `.gitignore` excludes `.env`. If either the key or `fpcalc` is missing, SongDrop simply
uses its strict text-and-duration matcher.

## Installation

```bash
cd /path/to/SongDrop
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

For development:

```bash
python -m pip install -e '.[dev]'
```

## CLI examples

```bash
songdrop "https://www.youtube.com/watch?v=VIDEO_ID"
songdrop "https://music.youtube.com/watch?v=VIDEO_ID"
songdrop "https://www.youtube.com/watch?v=VIDEO_ID" --format mp3
songdrop "https://www.youtube.com/watch?v=VIDEO_ID" --keep-file
songdrop "https://www.youtube.com/watch?v=VIDEO_ID" --download-only
songdrop "https://www.youtube.com/watch?v=VIDEO_ID" --output ~/Downloads/MySongDropStage
songdrop "https://music.youtube.com/playlist?list=PLAYLIST_ID"
songdrop playlist "https://www.youtube.com/watch?v=VIDEO_ID&list=PLAYLIST_ID"
songdrop batch urls.txt
songdrop batch urls.txt --fail-fast
songdrop playlist "https://www.youtube.com/playlist?list=PLAYLIST_ID" --max-items 500
songdrop serve
songdrop --help
songdrop --version
```

MP3 is the default and is produced with FFmpeg. Use `--format m4a` when you prefer M4A; SongDrop
then preserves an existing compatible M4A source, attempts a stream-copy remux when possible, and
only encodes AAC when needed. `--keep-file` still performs and verifies the Music import but retains
the final tagged staging copy. `--output` changes only the staging/preservation root; it does not
create a separate SongDrop library. `--download-only` intentionally keeps finished files under
that root and never contacts Apple Music.

## Architecture

```text
Provider
   ↓
yt-dlp download
   ↓
FFmpeg audio processing
   ↓
MetadataResolver
   ├── optional Chromaprint + AcoustID recording ID
   ├── MusicBrainz canonical metadata and release
   ├── Cover Art Archive front cover
   └── LRCLIB matched plain lyrics
   ↓
Mutagen tags, release artwork, lyrics + filesystem flush
   ↓
SongDrop-owned staging file
   ↓
MusicLibraryImporter protocol
   ↓
AppleMusicImporter (safe osascript argument invocation)
   ↓
persistent-ID lookup + independent managed-file verification
   ↓
owned staging cleanup
```

For collections, a thin orchestration layer sits above that unchanged per-track pipeline:

```text
CollectionProvider / UTF-8 URL file
   ↓ flat discovery only
ordered TrackRequest values
   ↓ current-run provider-ID deduplication
BatchDownloadService (sequential, failure-isolated)
   ↓ one item at a time
the complete single-track pipeline shown above
   ↓
summary + optional failed-URL retry file
```

The relevant modules are:

```text
src/songdrop/
├── api.py
├── api_models.py
├── cli.py
├── config.py
├── models.py
├── providers/
│   ├── base.py
│   └── youtube.py
└── services/
    ├── apple_music.py
    ├── batch.py
    ├── converter.py
    ├── downloader.py
    ├── enrichment.py
    ├── jobs.py
    ├── library.py
    └── metadata.py
```

The repository-level `extension/` directory contains the dependency-free Manifest V3 client. Its
service worker classifies the active tab, obtains local authorization automatically, submits jobs,
and tracks completion. The small panel is used only for explicit playlist destination selection;
ordinary single-track clicks do not open it.

macOS automation is isolated in `apple_music.py`; providers and audio processing do not import or
control Music. Enrichment clients are isolated in `enrichment.py`, use no LLM, and keep no catalog
or persistent lookup history. During one command, metadata HTTP responses are held in a bounded
in-memory cache. Requests are paced per service (including MusicBrainz's one-request-per-second
limit), and timeouts, rate limits, and transient server failures receive bounded backoff retries.
The cache disappears when the command exits.

## Tests and quality checks

Automated tests mock Music and never launch the real application:

```bash
pytest
ruff check .
mypy src
npm run check:extension
npm run test:extension
```

## Current limitations

- macOS and Apple Music are the only library destination.
- M4A and MP3 output only.
- Playlist and batch imports are sequential. They do not create or synchronize an Apple Music
  playlist yet.
- Structured provider metadata takes precedence. When it is missing, SongDrop applies one narrow
  fallback for `Music` uploads: it accepts `Artist - Title` only when the uploader/channel
  corroborates the primary artist, and strips only known presentation suffixes such as
  `Official Music Video`, `Official Audio`, and `Lyric Video`. In inferred titles, feature credits
  are normalized from `Artist feat. Guest - Title` to title `Title (feat. Guest)` with `Artist` as
  the primary artist. Album data is never guessed.
- Canonical display titles omit redundant edition labels such as `Original Version`, `Radio Edit`,
  and named radio-edit credits. Meaningful variants such as `Live`, `Remix`, `Acoustic`,
  `Instrumental`, and `Remastered` remain in the title to avoid misrepresenting the recording.
- MusicBrainz matches require exact normalized title and primary artist, corroborated feature
  credits when present, and compatible duration. Low-confidence responses are ignored.
- Compilation releases are not selected merely because they have artwork. If the selected exact
  release has no Cover Art Archive front image, SongDrop embeds no image rather than a wrong one.
- Lyrics are embedded only when LRCLIB's returned title and artist match the resolved track.
- The local companion service must currently be started manually with `songdrop serve`.
- The unpacked extension targets Brave and Chromium browsers; it is not packaged for a browser
  store yet.
- No Spotify integration, GUI, playlist synchronization, or SQLite catalog.

## Future phases

- Optional Apple Music playlist creation after successful batch import.
- A signed browser-store release and optional macOS background launch agent for the local service.
- Spotify URLs and playlists as metadata/input sources only, never DRM ripping.
- Playlist synchronization built against Apple Music as the source of truth.

These additions do not require a permanent SongDrop track, artist, album, or library database.
