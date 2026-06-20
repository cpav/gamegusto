"""Streamlit Community Cloud entry point.

Streamlit runs this file top-to-bottom on every rerun. It simply delegates to the
app defined under ``ui/`` so the deployment target is a single, stable file
(set this as the app's "Main file path" on share.streamlit.io).
"""

from __future__ import annotations

from ui.app import main

main()
