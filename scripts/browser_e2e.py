"""Synthetic UI acceptance; run in CI, never against an existing CDN."""
import asyncio
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

from playwright.async_api import async_playwright


def json_response(value):
    # Playwright supplies Request as the second positional argument when a
    # handler exposes two parameters. Capture data in a one-argument closure.
    async def respond(route):
        await route.fulfill(json=value)
    return respond


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
                def raw_fixture(name):
                    buffer = BytesIO()
                    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                        archive.writestr(name, "Original synthetic raw-intake fixture. No game code or assets.")
                        archive.writestr("metadata.json", '{"game_version":"1.4.5.0","complete":true}')
                    return buffer.getvalue()
                await page.locator("#server-file").set_input_files({"name":"server-1.4.5.0.zip", "mimeType":"application/zip", "buffer":raw_fixture("server.txt")})
                await page.locator("#declared-version").fill("1.4.5.0")
                await page.get_by_role("button", name="上传并检查两份来源", exact=True).click()
                await page.locator("#raw-upload-status").filter(has_text="已阻止").wait_for(timeout=15000)
                assert "缺少同版本客户端" in await page.locator("#raw-blockers").inner_text()
                await page.locator("#client-file").set_input_files({"name":"Content-1.4.5.0.zip", "mimeType":"application/zip", "buffer":raw_fixture("Content/original.txt")})
                # Repeated submit while uploading must not create duplicate jobs.
                await page.locator("#raw-upload").evaluate("form=>{form.requestSubmit();form.requestSubmit();}")
                await page.locator("#raw-upload-status").filter(has_text="已阻止").wait_for(timeout=15000)
                assert "缺少同版本客户端" not in await page.locator("#raw-blockers").inner_text()
                raw_jobs = await (await page.request.get("http://127.0.0.1:8765/api/jobs")).json()
                assert len(raw_jobs) == 2 and all(job["state"] == "BLOCKED" for job in raw_jobs)
                assert all(not job["executedInput"] and not job["extractionComplete"] for job in raw_jobs)
                assert await (await page.request.get("http://127.0.0.1:8765/api/current")).json() is None
                await page.locator("#raw-sources details").first.locator("summary").click()
                await page.locator("#raw-sources pre").first.filter(has_text='"files"').wait_for()
                await page.locator("#raw-sources details").first.locator("summary").click()
                await page.screenshot(path=str(evidence / "01-raw-preflight-blocked.png"), full_page=True)
                for revision in (1, 2):
                    response = await page.request.get(f"http://127.0.0.1:8765/api/demo/{revision}.zip")
                    await page.locator("#file").set_input_files({"name": f"synthetic-v{revision}.zip", "mimeType": "application/zip", "buffer": await response.body()})
                    await page.get_by_role("button", name="上传并开始提取", exact=True).click()
                    await page.locator("#job-state").filter(has_text="READY_FOR_REVIEW").wait_for(timeout=15000)
                    # A browser click without affirmative review must not exist
                    # as an enabled action, even though the output is complete.
                    assert not await page.locator("#publish").is_enabled()
                    assert await page.locator("#publish-status").inner_text() == "", "Previous job success leaked into new review"
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
                # A separately reviewed identical upload keeps the current
                # immutable release/history and does not create another commit.
                await page.locator("#file").set_input_files({"name":"synthetic-v2-again.zip","mimeType":"application/zip","buffer":await response.body()})
                await page.get_by_role("button",name="上传并开始提取",exact=True).click()
                await page.locator("#job-state").filter(has_text="READY_FOR_REVIEW").wait_for(timeout=15000)
                assert await page.locator("#publish-status").inner_text() == ""
                await page.locator("#confirm").check()
                await page.locator("#publish").click()
                await page.locator("#job-state").filter(has_text="PUBLISHED").wait_for(timeout=15000)
                repeated = await (await page.request.get("http://127.0.0.1:8765/api/current")).json()
                assert repeated == pointer
                await page.locator("#publish-status").filter(has_text="未重复推送").wait_for()
                pointer = repeated
                await page.locator("#refresh-client").click()
                await page.locator("#client-status").filter(has_text="已验证版本 0.0.2").wait_for()
                await page.reload(wait_until="domcontentloaded")
                await page.locator("#client-status").filter(has_text="已恢复已验证缓存版本 0.0.2").wait_for()
                await page.route("**/cdn/**", lambda route: route.abort())
                await page.locator("#load-family").click()
                await page.get_by_role("heading", name="新增合成物品", exact=True).wait_for()
                await page.screenshot(path=str(evidence / "04-offline-cached-resources.png"), full_page=True)
                await page.unroute("**/cdn/**")
                for malformed in ({**pointer, "release": None}, {**pointer, "manifestSha256": "b" * 64}, {**pointer, "gameVersion": "9.9.9"}):
                    await page.route("**/api/current", json_response(malformed))
                    await page.locator("#refresh-client").click()
                    await page.locator("#client-status").filter(has_text="保留原版本").wait_for()
                    saved_release = await page.evaluate("""() => new Promise((resolve,reject)=>{
                      const open=indexedDB.open('resource-pipeline-cache-v1');
                      open.onerror=()=>reject(open.error);open.onsuccess=()=>{
                        const db=open.result,request=db.transaction('meta').objectStore('meta').get('active');
                        request.onsuccess=()=>{resolve(request.result.pointer.release);db.close();};
                        request.onerror=()=>reject(request.error);
                      };
                    })""")
                    assert saved_release == pointer["release"], "Malformed pointer replaced the verified cache"
                    await page.locator("#load-family").click()
                    await page.get_by_role("heading", name="新增合成物品", exact=True).wait_for()
                    await page.unroute("**/api/current")
                # A damaged downloaded manifest must not replace the verified
                # snapshot. Use a fresh fake digest/path so no cache masks it.
                await page.route("**/api/current", lambda route: route.fulfill(json={**pointer, "release": "a" * 64, "manifest": "releases/" + "a" * 64 + "/manifest.json", "manifestSha256": "a" * 64}))
                await page.route("**/cdn/releases/" + "a" * 64 + "/manifest.json", lambda route: route.fulfill(body="corrupted manifest"))
                await page.locator("#refresh-client").click()
                await page.locator("#client-status").filter(has_text="SHA-256校验失败；保留原版本").wait_for()
                await page.locator("#load-family").click()
                await page.get_by_role("heading", name="新增合成物品", exact=True).wait_for()
                await page.set_viewport_size({"width":390,"height":844})
                assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Narrow screen horizontal overflow"
                await page.screenshot(path=str(evidence / "05-narrow-corruption-preserves-version.png"), full_page=True)
                assert not errors, errors
                (evidence / "acceptance.json").write_text(json.dumps({"passed": True, "syntheticOnly": True, "rawPreflightBlocked": True, "pointerValidationTested": True, "pageErrors": errors, "pointer": pointer}, ensure_ascii=False, indent=2))
                await browser.close()
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


asyncio.run(run())
