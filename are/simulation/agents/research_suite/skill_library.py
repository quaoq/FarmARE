import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

TOKEN_REGEX = re.compile(r"[a-zA-Z0-9_]+")


def _tokenize(text: str) -> set[str]:
    return {token.lower() for token in TOKEN_REGEX.findall(text)}


@dataclass(frozen=True)
class SkillRecord:
    skill_id: str
    title: str
    description: str
    workflow: dict
    keywords: tuple[str, ...]
    source_path: str

    def searchable_text(self) -> str:
        kw = " ".join(self.keywords)
        return f"{self.title}\n{self.description}\n{kw}"


class DynamicSkillLibrary:
    def __init__(self, skill_dir: str | Path):
        self.skill_dir = Path(skill_dir)
        self.records: list[SkillRecord] = []
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        self._loaded = True

        if not self.skill_dir.exists() or not self.skill_dir.is_dir():
            logger.warning(f"Skill library directory not found: {self.skill_dir}")
            return

        for skill_path in sorted(self.skill_dir.glob("*.json")):
            try:
                with open(skill_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if not isinstance(data, dict):
                    continue
                workflow = data.get("workflow", {})
                if not isinstance(workflow, dict):
                    workflow = {}
                keywords = data.get("keywords", [])
                if not isinstance(keywords, list):
                    keywords = []
                skill = SkillRecord(
                    skill_id=str(data.get("skill_id", skill_path.stem)),
                    title=str(data.get("title", skill_path.stem)),
                    description=str(data.get("description", "")),
                    workflow=workflow,
                    keywords=tuple(str(keyword) for keyword in keywords),
                    source_path=str(skill_path),
                )
                self.records.append(skill)
            except Exception as error:
                logger.warning(f"Failed to load skill file {skill_path}: {error}")

    def retrieve(
        self, query: str, top_k: int = 3, min_score: float = 0.1
    ) -> list[tuple[SkillRecord, float]]:
        self.load()
        if not query.strip() or top_k <= 0 or len(self.records) == 0:
            return []

        query_tokens = _tokenize(query)
        if len(query_tokens) == 0:
            return []

        scored: list[tuple[SkillRecord, float]] = []
        for record in self.records:
            skill_tokens = _tokenize(record.searchable_text())
            if len(skill_tokens) == 0:
                continue
            overlap = query_tokens & skill_tokens
            if not overlap:
                continue
            score = len(overlap) / max(1, len(query_tokens))
            if score < min_score:
                continue
            scored.append((record, score))

        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_k]
