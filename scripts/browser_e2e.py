"""Synthetic UI acceptance; run in CI, never against an existing CDN."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.async_api import async_playwright


async def run():
    evidence = Path("test-results")
    evidence.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as state:
        env = dict(os.environ, RESOURCE_PIPELINE_STATE=state, PYTHONPATH="src")
        server = subprocess.Popen([sys.executable, "-m", "uvicorn", "resource_pipeline.api:create_app", "--factory", "--host", "127.0.0.1", "--port", "8765"], env=env)
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen("http://127.0.0.1:8765/api/jobs", timeout=1).close()
                    break
                except OSError:
                    time.sleep(.1)
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                await page.goto("http://127.0.0.1:8765")
                await page.screenshot(path=str(evidence / "01-upload-home.png"), full_page=True)
                for revision in (1, 2):
                    response = await page.request.get(f"http://127.0.0.1:8765/api/demo/{revision}.zip")
                    await page.locator("#file").set_input_files({"name": f"synthetic-v{revision}.zip", "mimeType": "application/zip", "buffer": await response.body()})
                    await page.get_by_role("button", name="上传并开始提取", exact=True).click()
                    await page.locator("#job-state").filter(has_text="READY_FOR_REVIEW").wait_for(timeout=15000)
                    # A browser click without affirmative review must not exist
                    # as an enabled action, even though the output is complete.
                    assert not await page.locator("#publish").is_enabled()
                    await page.screenshot(path=str(evidence / f"02-review-v{revision}.png"), full_page=True)
                    await page.locator("#confirm").check()
                    await page.locator("#publish").click()
                    await page.locator("#job-state").filter(has_text="PUBLISHED").wait_for(timeout=15000)
                    await page.locator("#refresh-client").click()
                    await page.locator("#client-status").filter(has_text=f"已验证版本 0.0.{revision}").wait_for()
                    await page.locator("#family").select_option("items")
                    await page.locator("#load-family").click()
                    await page.get_by_role("heading", name="合成物品", exact=True).wait_for()
                    if revision == 2:
                        await page.get_by_role("heading", name="新增合成物品", exact=True).wait_for()
                    await page.screenshot(path=str(evidence / f"03-client-v{revision}.png"), full_page=True)
                pointer = await (await page.request.get("http://127.0.0.1:8765/api/current")).json()
                assert pointer["previous"] and pointer["release"] != pointer["previous"]
                assert not errors, errors
                (evidence / "acceptance.json").write_text(json.dumps({"passed": True, "syntheticOnly": True, "pageErrors": errors, "pointer": pointer}, ensure_ascii=False, indent=2))
                await browser.close()
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


asyncio.run(run())
