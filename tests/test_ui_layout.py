"""Layout and live-script checks that need a real browser engine.

Correct markup and correct CSS can still leave a page unusable. A flex item
that is also a scroll container has an automatic minimum size of zero, so it
silently shrinks below its content and clips it - which is exactly how the
conversion page ended up with a 3900px track list that nothing could scroll to,
while every other test in this suite passed. These checks measure the rendered
result in a headless browser instead of trusting the rules, and skip where no
browser is installed.
"""

import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import _run_local_match_job

_REPO = Path(__file__).resolve().parent.parent
_STYLESHEET = (_REPO / "src" / "spotm3u" / "static" / "style.css").resolve()
_MACOS_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Enough rows to be far taller than any window these tests open, so the page
# has to scroll for the last track to be reachable at all.
_ROWS = 60

_PROBE = """
<script>
(() => {
  const list = document.getElementById("track-list");
  if (list) {
    for (let index = 0; index < %(rows)d; index += 1) {
      const row = document.createElement("li");
      row.className = "track-complete";
      row.innerHTML = '<span class="track-number">' + (index + 1) + '</span>'
        + '<div class="track-art-row"><div class="track-artwork"></div>'
        + '<div class="track-art-main"><div class="track-head">'
        + '<strong class="track-title">Song ' + (index + 1) + '</strong></div>'
        + '<div class="track-artists">Artist</div></div></div>';
      list.appendChild(row);
    }
  }
  const panel = document.getElementById("progress");
  if (panel) {
    panel.hidden = false;
  }
  const page = document.querySelector("main.page");
  page.scrollTop = 400;
  const scrolled = page.scrollTop;
  page.scrollTop = 0;
  document.documentElement.dataset.probe = JSON.stringify({
    pageClient: page.clientHeight,
    pageScroll: page.scrollHeight,
    scrolled,
    listScroll: list ? list.scrollHeight : 0,
    panelOverflow: panel ? getComputedStyle(panel).overflow : "",
  });
  document.documentElement.dataset.labels = JSON.stringify(
    ["enriching-metadata", "running", "completed", "validating-audio", "a-new-stage"]
      .map((status) => window.SPOTM3U_STATUS_LABEL(status))
  );
})();
</script>
"""


def _browser() -> str:
    """A Chromium-based browser to measure with, or skip the test."""
    for name in ("google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    if sys.platform == "darwin" and Path(_MACOS_CHROME).is_file():
        return _MACOS_CHROME
    pytest.skip("no Chromium-based browser is installed")


def _measure(tmp_path: Path, client, url: str) -> tuple[dict, list[str]]:
    """Render ``url``, measure it in a headless browser, return the numbers."""
    browser = _browser()
    body = client.get(url, follow_redirects=True).get_data(as_text=True)
    # ``--dump-dom`` reads local files, so the stylesheet is pointed at the
    # working copy rather than at the server.
    body = re.sub(
        r'<link rel="stylesheet" href="[^"]*">',
        f'<link rel="stylesheet" href="file://{_STYLESHEET}">',
        body,
    )
    page = tmp_path / "probe.html"
    page.write_text(body.replace("</body>", _PROBE % {"rows": _ROWS} + "</body>"), encoding="utf-8")

    for headless in ("--headless=new", "--headless"):
        completed = subprocess.run(
            [
                browser,
                headless,
                "--disable-gpu",
                "--no-sandbox",
                "--window-size=1200,800",
                "--virtual-time-budget=2500",
                "--dump-dom",
                f"file://{page}",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        measured = re.search(r'data-probe="([^"]+)"', completed.stdout)
        labels = re.search(r'data-labels="([^"]+)"', completed.stdout)
        if measured and labels:
            return (
                json.loads(html.unescape(measured.group(1))),
                json.loads(html.unescape(labels.group(1))),
            )
    raise AssertionError(f"the browser produced no measurements:\n{completed.stderr[-2000:]}")


def test_the_conversion_page_scrolls_its_whole_track_list(tmp_path, monkeypatch) -> None:
    client, job, _download_dir = _run_local_match_job(tmp_path, monkeypatch)

    metrics, _labels = _measure(tmp_path, client, f"/processing/{job.job_id}/1")

    # The list is much taller than the window, so the page - not a panel around
    # the list - has to be the region that scrolls, or the rest is unreachable.
    assert metrics["listScroll"] > metrics["pageClient"]
    assert metrics["pageScroll"] > metrics["pageClient"]
    assert metrics["scrolled"] > 0
    assert metrics["panelOverflow"] != "hidden"


def test_live_status_text_is_never_a_raw_slug(tmp_path, monkeypatch) -> None:
    client, job, _download_dir = _run_local_match_job(tmp_path, monkeypatch)

    _metrics, labels = _measure(tmp_path, client, f"/processing/{job.job_id}/1")

    # Every stage and playlist status the live pages print, in the order asked:
    # a track stage explained once in _status_icons.html, job statuses named
    # beside it, and anything unlisted read as words rather than as its slug.
    assert labels == [
        "Adding tags, artwork and lyrics",
        "Processing in progress",
        "Processing complete",
        "Checking downloaded audio",
        "A new stage",
    ]
