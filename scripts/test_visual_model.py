#!/usr/bin/env python3
"""Test if gemma4:31b:cloud can process images via Ollama API."""
import base64
import json
import urllib.request
from pathlib import Path

# Get most recent chart
charts = sorted(Path("cache/charts").glob("*_pro.png"), key=lambda f: f.stat().st_mtime, reverse=True)
if not charts:
    print("No charts found")
    exit(1)

chart_path = charts[0]
print(f"Using chart: {chart_path.name} ({chart_path.stat().st_size} bytes)")

with open(chart_path, "rb") as f:
    img_b64 = base64.b64encode(f.read()).decode("utf-8")

print(f"Image base64 length: {len(img_b64)}")

# Test Ollama API directly with image
payload = {
    "model": "gemma4:31b:cloud",
    "messages": [
        {
            "role": "system",
            "content": "You are a chart analyst. Respond with ONLY valid JSON. No markdown, no code blocks.",
        },
        {
            "role": "user",
            "content": 'Analyze this trading chart and respond with ONLY this JSON format:\n{"action":"BUY|SELL|HOLD","confidence":0.0-1.0,"pattern":"pattern name","trend":"bullish|bearish|neutral","analysis":"brief description"}',
            "images": [img_b64],
        },
    ],
    "stream": False,
    "options": {"num_predict": 2048, "temperature": 0.1},
}

print("Sending request to Ollama API...")
req = urllib.request.Request(
    "http://localhost:11434/api/chat",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)

try:
    # This diagnostic is pinned to the loopback Ollama URL above.
    with urllib.request.urlopen(req, timeout=180) as resp:  # nosec B310
        raw = resp.read(4_000_001)
        if len(raw) > 4_000_000:
            raise ValueError("Ollama response exceeds 4 MB")
        data = json.loads(raw.decode("utf-8"))
        content = data.get("message", {}).get("content", "")
        print(f"\nResponse ({len(content)} chars):")
        print(content[:1000])
        print(f"\nDone: {data.get('done', False)}")

        # Try to parse as JSON
        try:
            # Strip any markdown
            clean = content.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            result = json.loads(clean)
            print(f"\nParsed JSON: {json.dumps(result, indent=2)}")
            print(f"Action: {result.get('action')}")
            print(f"Confidence: {result.get('confidence')}")
        except json.JSONDecodeError as e:
            print(f"\nJSON parse error: {e}")
            print(f"Raw content first 200 chars: {content[:200]!r}")

except Exception as e:
    print(f"Error: {e}")
