"""Built-in learning library: topic notes and Q&A with model answers.

Content lives in knowledge/*.json. The tutor searches it through tools, and the UI browses it.
Search is a small keyword ranker (no external services), which is plenty for a curated library.
"""

import json
import random
import re
from functools import lru_cache
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"

_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "what", "how", "why", "when", "which", "who", "do", "does",
    "did", "to", "of", "in", "on", "for", "and", "or", "with", "me", "my", "i", "you", "your", "it",
    "about", "explain", "tell", "between", "difference", "can", "should", "would", "be", "this",
    "that", "from", "by", "at", "as", "vs", "please", "mujhe", "samjhao", "kya", "hai", "ka", "ki",
}
_TOKEN = re.compile(r"[a-z0-9][a-z0-9.\-/+]*")


def tokenize(text: str) -> list[str]:
    tokens = []
    for tok in _TOKEN.findall(text.lower()):
        tok = tok.strip(".-/")
        if tok and tok not in _STOPWORDS:
            tokens.append(tok)
    return tokens


SUBJECT_ORDER = ["Networking", "Cybersecurity", "Python", "AI", "English"]


@lru_cache
def load_topics(directory: str = str(KNOWLEDGE_DIR)) -> tuple[dict, ...]:
    topics: list[dict] = []
    for path in sorted(Path(directory).glob("*.json")):
        topics.extend(json.loads(path.read_text(encoding="utf-8")))
    order = {s: i for i, s in enumerate(SUBJECT_ORDER)}
    topics.sort(key=lambda t: order.get(t["subject"], len(order)))  # stable: keeps file order within a subject
    return tuple(topics)


def _score(query_tokens: list[str], fields: list[tuple[str, int]]) -> float:
    score = 0.0
    for text, weight in fields:
        words = set(tokenize(text))
        joined = " " + " ".join(words) + " "
        for tok in query_tokens:
            if tok in words:
                score += weight
            elif len(tok) > 3 and tok in joined:  # partial match, e.g. "subnet" in "subnetting"
                score += weight * 0.4
    return score


def _topic_matches(topic: dict, subject: str | None) -> bool:
    return not subject or topic["subject"].lower() == subject.lower()


def search(query: str, subject: str | None = None, limit: int = 3,
           topics: tuple[dict, ...] | None = None) -> list[dict]:
    """Best matching topic notes and Q&A items for a query."""
    topics = topics if topics is not None else load_topics()
    q = tokenize(query)
    if not q:
        return []
    results = []
    for t in topics:
        if not _topic_matches(t, subject):
            continue
        header = [(t["title"], 4), (" ".join(t["tags"]), 3)]
        topic_score = _score(q, header + [(" ".join(t["key_points"]), 1)])
        if topic_score > 0:
            results.append((topic_score, {
                "type": "topic", "topic": t["title"], "subject": t["subject"],
                "key_points": t["key_points"], "commands": t.get("commands", []),
            }))
        for item in t["qa"]:
            s = _score(q, [(item["q"], 3), (item["a"], 1)]) + _score(q, header) * 0.3
            if s > 0:
                results.append((s, {"type": "qa", "topic": t["title"], "subject": t["subject"],
                                    "q": item["q"], "a": item["a"], "level": item["level"]}))
    results.sort(key=lambda r: r[0], reverse=True)
    return [r for _, r in results[:limit]]


def find_topic(query: str, topics: tuple[dict, ...] | None = None) -> dict | None:
    topics = topics if topics is not None else load_topics()
    q = tokenize(query)
    best, best_score = None, 0.0
    for t in topics:
        s = _score(q, [(t["id"].replace("-", " "), 4), (t["title"], 4), (" ".join(t["tags"]), 3)])
        if s > best_score:
            best, best_score = t, s
    return best


def practice_questions(topic: str, count: int = 3, level: str | None = None,
                       seed: int | None = None, topics: tuple[dict, ...] | None = None) -> dict | None:
    """Random practice questions (with model answers for grading) from the best matching topic."""
    t = find_topic(topic, topics)
    if t is None:
        return None
    pool = [item for item in t["qa"] if not level or item["level"].lower() == level.lower()] or t["qa"]
    rng = random.Random(seed)
    picked = rng.sample(pool, min(max(count, 1), len(pool)))
    return {"topic": t["title"], "subject": t["subject"], "questions": picked}


def catalog() -> list[dict]:
    """Everything the UI library needs, grouped by topic."""
    return [
        {"id": t["id"], "subject": t["subject"], "title": t["title"], "level": t["level"],
         "key_points": t["key_points"], "commands": t.get("commands", []), "qa": t["qa"]}
        for t in load_topics()
    ]


def list_topic_titles() -> list[str]:
    return [f'{t["subject"]}: {t["title"]}' for t in load_topics()]
