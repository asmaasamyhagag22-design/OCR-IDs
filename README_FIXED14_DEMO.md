# Fixed14-v2 local research demo

This is a local, non-production interface for one Egyptian ID-card image. It
uses the frozen **Fixed14-v2 Research Candidate** (seed 271828) behind the
existing document geometry, perspective correction, orientation, canonical
card, and broad national-ID band pipeline.

## Launch on Windows PowerShell

```powershell
Set-Location C:\Users\asmaa\Documents\OCR_ID_parallel
.\.venv\Scripts\Activate.ps1
python scripts\phase2l_d_local_demo.py
```

The exact entry-point command after activation is:

```powershell
python scripts\phase2l_d_local_demo.py
```

Tkinter was selected because no compatible Gradio or other lightweight web
framework is installed. The demo opens a native desktop window, so there is no
local URL. It creates no HTTP listener; `127.0.0.1` is the declared local-only
host if a web wrapper is added later, and `0.0.0.0` is never used.

Supported input formats: JPEG (`.jpg`, `.jpeg`) and PNG (`.png`), one image at
a time.

## Privacy and expected behavior

- All processing is local. Nothing is uploaded.
- The selected image and extracted value stay in memory. The demo creates no
  result history, cache, database, analytics, or telemetry.
- The result is never written to stdout, stderr, an application log, a file,
  URL, filename, or query parameter.
- Reset clears the image reference, preview, result, and application clipboard.
- The first extraction loads DocCornerNet, PP-OCRv5 for the frozen orientation
  decision, and Fixed14-v2. Allow roughly 30–70 seconds for this cold start on
  the verified CPU-only environment. Later submissions reuse the loaded models;
  the measured warm end-to-end synthetic inference was about 1.73 seconds.
- Always review the 14-digit result before using it. This candidate is validated
  for controlled research integration only and is not production-approved.

Close the window to stop the application. If launched from an attached
PowerShell console, `Ctrl+C` also stops it.
