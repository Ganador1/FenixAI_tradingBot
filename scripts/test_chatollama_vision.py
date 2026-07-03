#!/usr/bin/env python3
"""Test if ChatOllama from langchain_ollama supports images with gemma4:31b:cloud."""
import base64
from pathlib import Path
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

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

# Create ChatOllama
llm = ChatOllama(
    model="gemma4:31b:cloud",
    temperature=0.1,
    num_predict=2048,
    base_url="http://localhost:11434",
    timeout=180,
)

# Test 1: image_url format (langchain standard)
print("\n=== Test 1: image_url format ===")
messages = [
    SystemMessage(content="You are a chart analyst. Respond with ONLY valid JSON. No markdown."),
    HumanMessage(content=[
        {"type": "text", "text": 'Analyze this chart. Respond with JSON: {"action":"BUY|SELL|HOLD","confidence":0.0-1.0,"pattern":"name","trend":"bullish|bearish|neutral","analysis":"brief"}'},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
    ]),
]

try:
    response = llm.invoke(messages)
    print(f"Response ({len(response.content)} chars):")
    print(response.content[:500])
except Exception as e:
    print(f"Error: {e}")