"""Browser automation for Google Takeout archive management page.

Uses Playwright with the local Google Chrome installation to:
1. Open ``https://takeout.google.com/manage/archive/...`` with a persistent
   Chrome user profile (so login / 2FA is preserved across runs).
2. Wait until the Takeout download links/buttons are visible (allowing the user
   to complete Google login or password re-verification if prompted, or
   automatically filling the password if configured).
3. Sequentially trigger each archive download via Chrome, automatically
   handling mid-session Google password re-prompts and retrying transient
   download cancellations.
"""

import logging
import os
import shutil
import time
from pathlib import Path
from typing import Iterator, List, Optional

CHROME_EXECUTABLE = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DEFAULT_PROFILE_DIR = Path.home() / ".cache" / "takeout-media-bridge" / "chrome-profile"


def _try_auto_fill_password(page, password: Optional[str]) -> bool:
    """Detect if the current page is a Google password prompt and auto-submit it."""
    if not password:
        return False
    try:
        pw_input = page.locator('input[type="password"]:visible')
        if pw_input.count() > 0:
            logging.info("Detected Google password re-authentication prompt; auto-filling password...")
            pw_input.first.fill(password)
            pw_input.first.press("Enter")
            page.wait_for_timeout(3000)
            return True
    except Exception as exc:
        logging.debug("Auto-fill password check encountered: %s", exc)
    return False


def _trigger_and_wait_for_download(page, href: str, password: Optional[str]):
    """Trigger *href* download and handle Google re-auth prompts until download starts."""
    pending_downloads = []

    def _on_download(dl):
        pending_downloads.append(dl)

    page.on("download", _on_download)
    try:
        def _click_or_goto():
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

        _click_or_goto()
        reauth_handled = False

        while not pending_downloads:
            page.wait_for_timeout(1500)
            if pending_downloads:
                break

            current_url = page.url
            if "accounts.google.com" in current_url:
                if _try_auto_fill_password(page, password):
                    reauth_handled = True
                    page.wait_for_timeout(4000)
                else:
                    logging.info(
                        "Waiting for Google re-authentication in Chrome window (or set google_password in config.json)..."
                    )
                    page.wait_for_timeout(3500)
            elif reauth_handled and "takeout.google.com" in current_url and not pending_downloads:
                logging.info("Re-authentication finished; re-triggering download for %s", href)
                reauth_handled = False
                _click_or_goto()

        return pending_downloads[0]
    finally:
        try:
            page.remove_listener("download", _on_download)
        except Exception:
            pass


def iter_takeout_page_downloads(
    page_url: str,
    download_dir: Path,
    profile_dir: Optional[Path] = None,
    cdp_url: Optional[str] = None,
    login_timeout_sec: int = 600,
    start_from: int = 1,
    end_at: Optional[int] = None,
    google_password: Optional[str] = None,
) -> Iterator[Path]:
    """Open the Takeout archive page in Chrome and yield each downloaded archive file."""
    from playwright.sync_api import sync_playwright

    password = google_password or os.getenv("GOOGLE_PASSWORD")
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

        download_selector = 'a[href*="takeout/download"], a[href*="download?"]'

        logging.info(
            "Waiting for Takeout download buttons... "
            "(If Chrome asks you to sign in or verify your password, please complete it in the opened Chrome window)"
        )
        deadline = time.time() + login_timeout_sec
        while time.time() < deadline:
            if page.locator(download_selector).count() > 0:
                break
            if "accounts.google.com" in page.url:
                _try_auto_fill_password(page, password)
            page.wait_for_timeout(1500)

        page.wait_for_selector(
            download_selector, state="attached", timeout=30000
        )
        page.wait_for_timeout(2000)

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
            if idx < start_from:
                logging.info("Skipping archive %d/%d (before --start-from %d)", idx, len(hrefs), start_from)
                continue
            if end_at is not None and idx > end_at:
                logging.info("Reached --end-at %d; stopping browser downloads.", end_at)
                break

            max_attempts = 4
            for attempt in range(1, max_attempts + 1):
                try:
                    logging.info(
                        "Starting browser download %d/%d (attempt %d/%d): %s",
                        idx,
                        len(hrefs),
                        attempt,
                        max_attempts,
                        href,
                    )
                    if "takeout.google.com" not in page.url:
                        page.goto(page_url, wait_until="domcontentloaded")
                        page.wait_for_timeout(2000)

                    download = _trigger_and_wait_for_download(page, href, password)
                    suggested_name = (
                        download.suggested_filename or f"takeout_part_{idx:03d}.zip"
                    )
                    dest_path = download_dir / f"{idx:03d}_{suggested_name}"
                    logging.info(
                        "Waiting for download %d/%d (%s) to complete...",
                        idx,
                        len(hrefs),
                        suggested_name,
                    )
                    raw_path_str = download.path()
                    err = download.failure()
                    if err or not raw_path_str:
                        raise RuntimeError(f"Download failed or canceled: {err}")

                    raw_path = Path(raw_path_str)
                    shutil.move(str(raw_path), str(dest_path))
                    logging.info(
                        "Download %d/%d saved to %s (%.1f MB)",
                        idx,
                        len(hrefs),
                        dest_path,
                        dest_path.stat().st_size / (1024 * 1024),
                    )
                    yield dest_path
                    break
                except Exception as exc:
                    logging.warning(
                        "Browser download %d/%d attempt %d/%d failed (%s). Retrying...",
                        idx,
                        len(hrefs),
                        attempt,
                        max_attempts,
                        exc,
                    )
                    if attempt == max_attempts:
                        raise
                    time.sleep(5)

            time.sleep(2)

        if not cdp_url:
            context.close()
