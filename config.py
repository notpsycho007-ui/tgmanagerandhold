import os


def _parse_ids(raw: str) -> set[int]:
    ids = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# ADMIN_IDS="123,456" (একাধিক admin) অথবা পুরনো ADMIN_ID="123" দুটোই কাজ করবে
ADMIN_IDS = _parse_ids(os.getenv("ADMIN_IDS", "")) | _parse_ids(os.getenv("ADMIN_ID", ""))

DATABASE_PATH = os.getenv("DATABASE_PATH", "telemanager.db")

PAGE_SIZE = 10        # এক page-এ কয়টা account দেখাবে
SEARCH_LIMIT = 20     # search-এ সর্বোচ্চ কয়টা result
MAX_NAME_LEN = 64
