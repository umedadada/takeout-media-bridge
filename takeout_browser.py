"""Browser automation for Google Takeout archive management page.

Uses Playwright with the local Google Chrome installation to:
1. Open ``https://takeout.google.com/manage/archive/...`` with a persistent
   Chrome user profile (so login / 2FA is preserved across runs).
2. Wait until the Takeout download links/buttons are visible (allowing the user
   to complete Google login or password re-verification if prompted).
3. Sequentially trigger each archive download via Chrome, yielding each
   downloaded archive Path as soon as it finishes so the caller can extract,
   upload to R2, verify, and delete it before downloading the next part.
"""

import logging
import time
from pathlib import Path
from typing import Iterator, List, Optional

CHROME_EXECUTABLE = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DEFAULT_PROFILE_DIR = Path.home() / ".cache" / "takeout-media-bridge" / "chrome-profile"


def iter_takeout_page_downloads(
    page_url: str,
    download_dir: Path,
    profile_dir: Optional[Path] = None,
    cdp_url: Optional[str] = None,
    login_timeout_sec: int = 600,
) -> Iterator[Path]:
    """Open the Takeout archive page in Chrome and yield each downloaded archive file.

    Parameters
    ----------
    page_url:
        The ``https://takeout.google.com/manage/archive/...`` URL.
    download_dir:
        Directory where downloaded archives are saved temporarily.
    profile_dir:
        Persistent Chrome user-data directory so Google login persists.
    cdp_url:
        Optional Chrome DevTools Protocol URL (e.g. ``http://127.0.0.1:9222``)
        to attach to an already-running Chrome instance.
    login_timeout_sec:
        Maximum seconds to wait for the user to log in / re-authenticate in Chrome.
    """
    from playwright.sync_api import sync_playwright

    download_dir.mkdir(parents=True, exist_ok=True)
    user_data_dir = (profile_dir or DEFAULT_PROFILE_DIR).expanduser().resolve()
    user_data_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        if cdp_url:
            logging.info("Connecting to existing Chrome via CDP: %s", cdp_url)
            browser = p.chromium.connect_over_cdp(cdp_url)
            context = (
                browser.contexts[0]
                if browser.contexts
                else browser.new_context(accept_downloads=True)
            )
            page = context.new_page()
        else:
            logging.info(
                "Launching Google Chrome with persistent profile: %s", user_data_dir
            )
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(user_data_dir),
                executable_path=CHROME_EXECUTABLE
                if Path(CHROME_EXECUTABLE).exists()
                else None,
                headless=False,
                accept_downloads=True,
                downloads_path=str(download_dir),
                args=[
                    "--disable-blink-features=AutomationControlled",
                ],
                ignore_default_args=["--enable-automation"],
            )
            page = context.pages[0] if context.pages else context.new_page()

        logging.info("Navigating to Takeout page: %s", page_url)
        page.goto(page_url, wait_until="domcontentloaded")

        # Selector matching Takeout download links
        download_selector = 'a[href*="takeout/download"], a[href*="download?"]'

        logging.info(
            "Waiting for Takeout download buttons... "
            "(If Chrome asks you to sign in or verify your password, please complete it in the opened Chrome window)"
        )
        page.wait_for_selector(
            download_selector, state="attached", timeout=login_timeout_sec * 1000
        )
        # Brief pause to ensure all split archive rows finish rendering
        page.wait_for_timeout(2000)

        # Extract all unique download hrefs on the page
        hrefs: List[str] = page.evaluate(
            """(sel) => {
                const anchors = Array.from(document.querySelectorAll(sel));
                const urls = anchors.map(a => a.href).filter(Boolean);
                return Array.from(new Set(urls));
            }""",
            download_selector,
        )

        if not hrefs:
            raise RuntimeError(
                f"No download links found on {page_url}. Please check the URL or archive status."
            )

        logging.info("Discovered %d archive download link(s) on Takeout page.", len(hrefs))

        for idx, href in enumerate(hrefs, start=1):
            logging.info(
                "Starting browser download %d/%d: %s", idx, len(hrefs), href
            )
            # Trigger download by clicking or navigating to the href
            # If Google prompts for password re-auth mid-session, the user can complete it
            # and Playwright will capture the resulting download event.
            with page.expect_download(timeout=0) as download_info:
                page.evaluate(
                    """(targetHref) => {
                        const a = Array.from(document.querySelectorAll('a')).find(el => el.href === targetHref);
                        if (a) {
                            a.click();
                        } else {
                            window.location.href = targetHref;
                        }
                    }""",
                    href,
                )

            download = download_info.value
            suggested_name = download.suggested_filename or f"takeout_part_{idx:03d}.zip"
            dest_path = download_dir / f"{idx:03d}_{suggested_name}"
            logging.info(
                "Waiting for download %d/%d (%s) to complete...",
                idx,
                len(hrefs),
                suggested_name,
            )
            # save_as blocks until the download finishes streaming to disk
            download.save_as(str(dest_path))
            logging.info(
                "Download %d/%d saved to %s (%.1f MB)",
                idx,
                len(hrefs),
                dest_path,
                dest_path.stat().st_size / (1024 * 1024),
            )
            yield dest_path
            # Small cooldown between consecutive Takeout part downloads
            time.sleep(2)

        if not cdp_url:
            context.close()
