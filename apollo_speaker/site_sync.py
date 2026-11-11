"""Apollo in Real Time page: explains the browser extension and opens apolloinrealtime.org in the default browser."""
from __future__ import annotations

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

SITE_MISSIONS = (("11", "Apollo 11"), ("13", "Apollo 13"), ("17", "Apollo 17"))
SITE_URL = "https://apolloinrealtime.org/{mission}/?t=-04:00:00"

HELP_HTML = """
<h2>Sync apolloinrealtime.org with Orbiter</h2>
<p>This app publishes the Ground Elapsed Time (GET) from Orbiter on <b>http://127.0.0.1:{port}</b>.
The <b>NASSP GET Speech</b> browser extension reads it and drives the Apollo in Real Time page
in Chrome or Edge, so you get the site's full audio and video.</p>
<h3>One-time setup</h3>
<ol>
<li>In Chrome or Edge open <b>chrome://extensions</b> (or <b>edge://extensions</b>) and turn on <b>Developer mode</b>.</li>
<li>Click <b>Load unpacked</b> and choose the <b>browser_extension</b> folder next to this app.</li>
</ol>
<h3>Using it</h3>
<ol>
<li>Start this app and Orbiter, then click a mission below. The site opens at T-4 hours, the usual NASSP start.</li>
<li>A small badge at the bottom-left of the page shows the sync status.</li>
<li>When Orbiter pauses, the site pauses. At other time accelerations the site keeps playing
without sync; when Orbiter returns to 1&times;, it jumps to the current GET.</li>
</ol>
<p>Pause sync requires the current Orbiter add-on. If its pause pipe or GET is unavailable, the
site is held and the badge reports that sync is unavailable; older add-ons cannot provide pause sync.</p>
<p>This app keeps running in the background to feed the extension. Open <b>Transcript</b> only when you want
speech; it is not loaded until then.</p>
"""


class SiteSyncView(QWidget):
    back_requested = Signal()
    offset_changed = Signal(float)

    def __init__(self, port: int, offset: float, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)

        top = QHBoxLayout()
        self.transcript_btn = QPushButton("Open transcript \u2192")
        self.transcript_btn.setToolTip("Load the transcript and speak it (not loaded until you open it)")
        self.transcript_btn.clicked.connect(self.back_requested)
        top.addWidget(self.transcript_btn)
        top.addStretch(1)
        lay.addLayout(top)

        text = QLabel(HELP_HTML.format(port=port))
        text.setWordWrap(True)
        text.setTextFormat(text.textFormat().RichText)
        lay.addWidget(text)

        row = QHBoxLayout()
        row.addWidget(QLabel("Open in browser:"))
        for mission, label in SITE_MISSIONS:
            b = QPushButton(label)
            b.setToolTip(SITE_URL.format(mission=mission))
            b.clicked.connect(lambda _=False, m=mission: QDesktopServices.openUrl(QUrl(SITE_URL.format(mission=m))))
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        self.status = QLabel("")
        lay.addWidget(self.status)
        lay.addStretch(1)
