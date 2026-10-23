import re

_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Convert text into a lowercase, hyphen-separated URL slug.

    Runs of non-alphanumeric characters become a single hyphen, and leading
    and trailing hyphens are removed. Returns "untitled" if nothing remains.
    """
    slug = _NON_ALPHANUMERIC.sub("-", text.lower()).strip("-")
    return slug or "untitled"
