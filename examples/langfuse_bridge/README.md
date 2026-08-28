# Langfuse artifact bridge

This example sends a completed ASSERT run to Langfuse. **ASSERT produced the
judgments; Langfuse stores and visualizes them.** The checked-in sample is
synthetic, so the bridge needs Langfuse project credentials but no model
credential and makes no judge-model call.

## Run the synthetic sample

Create a Langfuse project in Cloud or a self-hosted deployment that supports
the OTLP HTTP endpoint. Set the current documented Langfuse environment
variables:

```powershell
$env:LANGFUSE_BASE_URL = "https://your-langfuse-origin.example"
$env:LANGFUSE_PUBLIC_KEY = "<project-public-key>"
$env:LANGFUSE_SECRET_KEY = "<project-secret-key>"

python examples/langfuse_bridge/run_bridge.py
```

`LANGFUSE_BASE_URL` is the origin only, without `/api/public`. The exporter
does not read `.env`, print credential values, retry requests, or install the
Langfuse SDK.

## Export your own run

Run ASSERT normally, then point the bridge at the completed run directory:

```powershell
python examples/langfuse_bridge/run_bridge.py `
  --run-dir artifacts/results/<suite>/<run>
```

The directory must contain matching, complete `inference_set.jsonl` and
`scores.jsonl` files. See the [integration guide](../../docs/integrations/langfuse.md)
for the mapping, limitations, and data-handling notes.
