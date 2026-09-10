# V11 — Authenticity Intelligence

Version 11 adds an explainable, heuristic **Authenticity Intelligence** engine to
SocialSense AI. It assesses whether analyzed media (YouTube videos, Reddit posts)
may contain AI-generated or synthetic components and reports its reasoning openly.

> **IMPORTANT LIMITATION**: This is a *heuristic* engine. It computes
> **probabilities** and lists **possible** synthetic indicators. It never claims
> definitive detection of deepfakes, voice clones, or AI-generated content. All
> outputs are explainable, and the UI, exports, and reports carry that disclaimer.

---

## Architecture

```
create_youtube_analysis / create_reddit_analysis (analysis_service.py)
        │
        └── AuthenticityService.analyze()  (services/authenticity_service.py)
              ├── ThumbnailAnalysisService    (services/thumbnail_analysis_service.py)
              ├── AudioAnalysisService        (services/audio_analysis_service.py)
              ├── FrameAnalysisService        (services/frame_analysis_service.py)
              └── MediaMetadataService        (services/media_metadata_service.py)
                └── _compute_authenticity()  → MediaAnalysis (persisted)
```

The engine runs at the end of both pipelines, after all other intelligence steps
(transcript, entities, channel context), and persists a single `MediaAnalysis`
row linked one-to-one to the `Analysis`.

## Scoring model

| Component | Weight | Key output |
|-----------|--------|------------|
| Thumbnail | 25% | `thumbnail_ai_probability` / `score` |
| Audio (text inference) | 25% | `voice_clone_probability`, `speech_consistency` / `score` |
| Frame | 30% | `manipulation_probability`, `face_consistency`, `temporal_consistency` / `score` |
| Metadata | 20% | `metadata_ai_probability` / `score` |

- `overall_ai_probability` = weighted average of available component scores,
  renormalized when a component is unavailable.
- `overall_authenticity_score` = `100 - overall_ai_probability`.
- `confidence` = blend of component coverage and inter-component agreement.
- `deepfake_score` = frame component `manipulation_probability`.
- `synthetic_voice_score` = audio component `voice_clone_probability`.
- All scores are clamped to 0–100. 60+ on a sub-score flags that indicator as a
  risk bucket (AI video, deepfake risk, voice clone, etc.).

## Component behavior

- **Thumbnail**: in demo mode, 3 deterministic simulated scenarios
  (AI-generated / authentic / mixed), selected by CRC32 of the video id. In real
  mode it tries to download the thumbnail (`i.ytimg.com`) and runs heuristic
  pixel checks (extrema consistency, repeated patterns, smoothing estimate) with
  Pillow; any failure falls back to an honest "unavailable" result.
- **Audio**: SocialSense AI does **not** have the audio waveform. The service
  performs **transcript-based text inference** only (sentence length variance,
  vocabulary diversity, filler-word ratio, repetition) and clearly labels this
  with `analysis_mode: transcript`. It never pretends to analyze the waveform.
- **Frame**: SocialSense AI does not download/decode video files. When no frames
  are available (the normal case) the service **gracefully falls back** with
  `available: False` and an explicit note that frame-level analysis was not
  performed. Real-frame analysis (PIL frames) is supported internally for future
  use.
- **Metadata**: checks title/description markers, missing camera/encoder
  metadata, timestamp patterns (including off-peak uploads), and unusual aspect
  ratios from whatever metadata is available.

## Schema

New table `media_analyses` (migration `v11_001`, `down_revision = 'v9_001'`):

| Column | Type | Notes |
|--------|------|-------|
| `id` | integer PK | |
| `analysis_id` | integer FK → analyses.id | unique, one-to-one, cascade delete |
| `overall_ai_probability` | float | 0–100 |
| `overall_authenticity_score` | float | 0–100 |
| `confidence` | float | 0–100 |
| `deepfake_score` | float | 0–100 |
| `synthetic_voice_score` | float | 0–100 |
| `thumbnail_ai_score` | float | 0–100 |
| `frame_manipulation_score` | float | 0–100 |
| `metadata_score` | float | 0–100 |
| `summary` | text | human-readable, heuristic disclaimer |
| `reasons` | text | JSON list of explainable reasons |
| `created_at` / `updated_at` | datetime | |

## Configuration (defaults)

```
ENABLE_MEDIA_ANALYSIS=true
ENABLE_THUMBNAIL_ANALYSIS=true
ENABLE_AUDIO_ANALYSIS=true
ENABLE_FRAME_ANALYSIS=true
ENABLE_METADATA_ANALYSIS=true
ENABLE_AUTHENTICITY_ENGINE=true
MAX_VIDEO_FRAMES=30
```

## UI / integrations

- **Dashboard**: Authenticity Intelligence section with 5 metrics — AI Videos,
  Authentic Videos, Deepfake Risk, Voice Clones, Avg Authenticity.
- **Result page**: Authenticity Intelligence card with overall scores,
  confidence, component sub-scores, indicators, and explainable reasons.
- **Exports**: CSV gains an `# Authenticity Intelligence (heuristic)` section;
  JSON gains a `media_analysis` object.
- **Scheduled reports**: JSON/CSV/HTML reports include `authenticity_intelligence`
  aggregates.
- **Background jobs**: new progress stages "Running Authenticity Engine" (98%)
  and "Saving Media Analysis" (99%).

## Known limitations

- Heuristic only — outputs are probabilities, not proof of AI generation.
- Audio analysis is transcript-based text inference, not waveform analysis.
- No direct video-frame access; frame component usually reports unavailable.
- Thumbnail analysis depends on thumbnail retrievability.
- Possible false positives/negatives on stylized but authentic media.
- Demo results are simulated and clearly labeled.

## Future improvements

- Integration with a real media fingerprinting service (AudioLDM-style spectral
  checks, face-detection blinking analysis).
- Frame extraction pipeline (ffmpeg) for genuine temporal analysis.
- Model confidence calibration against labeled datasets.
