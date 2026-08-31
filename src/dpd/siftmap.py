"""Shared SiftMap page readers.

One home for the small DOM reads that more than one pipeline needs, so a fix
lands once. The alternative -- each caller keeping its own copy -- is exactly
how `manage-sold` ended up with a private two-county FIPS table that silently
returned Knox for every Maryland county.

The count reader here is the implementation proven by `dpd_siftmap_discover`
while 152 SiftMap presets were built and reload-verified.
"""
from __future__ import annotations

import re

# SiftMap renders the result total as a leaf element reading "<N> Properties".
# Match only childless elements: ancestors also contain the substring, and the
# outermost match would be the whole document.
_COUNT_JS = r"""() => {
    const hit = [...document.querySelectorAll('*')].find(
        el => el.children.length === 0 && /[\d,]+\s*Propert/i.test(el.textContent || ''));
    return hit ? hit.textContent.trim() : '';
}"""

_COUNT_RE = re.compile(r"([\d,]+)\s*Propert", re.I)


async def result_count(page) -> int | None:
    """Read the '<N> Properties' figure off the map.

    Returns None when the figure is not on the page yet -- which is NOT the same
    as zero. A caller that treats None as 0 will report an empty county for what
    is really a page that had not finished rendering.
    """
    txt = await page.evaluate(_COUNT_JS)
    m = _COUNT_RE.search(txt or "")
    return int(m.group(1).replace(",", "")) if m else None
