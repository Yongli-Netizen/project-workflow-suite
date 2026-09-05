"""Validate this skill's portable structure using only the Python standard library."""
from __future__ import annotations

import argparse
from pathlib import Path
import re


ALLOWED_KEYS = {"name", "description", "license", "allowed-tools", "metadata"}


def validate(skill_dir: Path) -> list[str]:
    errors: list[str] = []
    skill_dir = skill_dir.resolve(strict=True)
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return ["SKILL.md not found"]
    content = skill_md.read_text(encoding="utf-8")
    match = re.match(r"^---\r?\n(.*?)\r?\n---(?:\r?\n|$)", content, re.DOTALL)
    if not match:
        return ["invalid or missing YAML frontmatter"]

    top_level: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line[:1].isspace():
            continue
        item = re.fullmatch(r"([A-Za-z][A-Za-z0-9_-]*):(?:\s*(.*))?", line)
        if not item:
            errors.append(f"unsupported top-level frontmatter line: {line}")
            continue
        key, value = item.group(1), (item.group(2) or "").strip().strip("\"'")
        if key in top_level:
            errors.append(f"duplicate frontmatter key: {key}")
        top_level[key] = value

    unexpected = set(top_level) - ALLOWED_KEYS
    if unexpected:
        errors.append("unexpected frontmatter keys: " + ", ".join(sorted(unexpected)))
    name = top_level.get("name", "")
    description = top_level.get("description", "")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        errors.append("name must be nonempty hyphen-case")
    elif name != skill_dir.name:
        errors.append("frontmatter name must match the skill directory")
    if not description:
        errors.append("description is required")
    elif len(description) > 1024 or "<" in description or ">" in description:
        errors.append("description is invalid")

    body = content[match.end():]
    if re.search(r"(?m)^[ ]{0,3}\[TODO:[^\n]*\][ \t]*$", body):
        errors.append("unfinished TODO placeholder")
    for target in re.findall(r"\[[^\]]+\]\((?!https?://|#)([^)]+)\)", body):
        path = (skill_dir / target).resolve()
        if not path.is_relative_to(skill_dir) or not path.exists():
            errors.append(f"missing or unsafe linked resource: {target}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill_dir", nargs="?", default=str(Path(__file__).parents[1]))
    args = parser.parse_args()
    errors = validate(Path(args.skill_dir))
    if errors:
        for error in errors:
            print("ERROR: " + error)
        return 1
    print("Skill is valid (standard-library portability check).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
