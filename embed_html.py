#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow"]
# ///
"""Inline the hotlinked photos of a finished page: uv run embed_html.py provs/x.html [...]

Same rule as images.embed(): lead-card photos (thumb big) at 720px, every other picture at 120px,
re-encoded as JPEG. Pictures that cannot be fetched stay hotlinked. Results are cached in
<page>.img.data.json, next to the page, so a second pass costs nothing.
"""

import os
import re
import sys
from html import unescape as html_unescape

import images as IMG

IMG_TAG = re.compile(r'(<img\b[^>]*?\bsrc=")(https?://[^"]+)"')


def embed(path: str) -> tuple:
    html = open(path).read()
    cache_path = re.sub(r"\.html$", ".img.data.json", path)
    cache = IMG.load_cache(cache_path)
    done = kept = size = 0

    def swap(m):
        nonlocal done, kept, size
        url = html_unescape(m.group(2))
        big = re.search(r'class="thumb big">\s*$', html[max(0, m.start() - 60):m.start()])
        uri = IMG.data_uri(url, 720 if big else 120, cache)
        if uri.startswith("data:"):
            done += 1
            size += len(uri)
            return f'{m.group(1)}{uri}"'
        kept += 1
        return m.group(0)

    out = IMG_TAG.sub(swap, html)
    IMG.save_cache(cache_path, cache)
    open(path, "w").write(out)
    return len(html), len(out), done, kept, size


if __name__ == "__main__":
    for p in sys.argv[1:]:
        before, after, done, kept, size = embed(p)
        print(f"{os.path.basename(p)}: {done} photos inlined ({size // 1024} KB), "
              f"{kept} left hotlinked, page {before // 1024} -> {after // 1024} KB")
