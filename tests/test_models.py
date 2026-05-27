import asyncio
import aiohttp
import time

import pytest

models = ["qwen3-coder-next:cloud", "glm-5:cloud", "mistral-large-3:675b-cloud", "cogito-2.1:671b-cloud", "deepseek-v3.2:cloud"]


@pytest.fixture(params=models)
def model(request):
    return request.param


async def test_model(model):
    prompt = "Responde SOLO con JSON: {\"bias\":\"LONG\",\"confidence\":0.8,\"reasoning\":\"test\"}"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False
    }
    start = time.time()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post("http://localhost:11434/api/chat", json=payload, timeout=10) as resp:
                data = await resp.json()
                content = data.get("message", {}).get("content", "")
                print(f"{model}: {time.time()-start:.2f}s -> {content[:50]}")
    except Exception as e:
        print(f"{model}: ERROR {e}")

async def main():
    for m in models:
        await test_model(m)

if __name__ == "__main__":
    asyncio.run(main())
