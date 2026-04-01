MAX_MSG_LEN = 4096


def split_message(text: str) -> list[str]:
    """Split on paragraph boundaries to stay under Telegram's 4096-char limit."""
    if len(text) <= MAX_MSG_LEN:
        return [text]

    chunks: list[str] = []
    current: list[str] = []

    for para in text.split("\n\n"):
        if sum(len(p) + 2 for p in current) + len(para) > MAX_MSG_LEN:
            chunks.append("\n\n".join(current))
            current = [para]
        else:
            current.append(para)

    if current:
        chunks.append("\n\n".join(current))

    return chunks
