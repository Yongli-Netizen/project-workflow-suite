import importlib.util
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).parents[1] / "scripts" / "validate_skill.py"
SPEC = importlib.util.spec_from_file_location("validate_skill", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ValidateSkillTests(unittest.TestCase):
    def test_installed_skill_is_valid(self):
        self.assertEqual(MODULE.validate(Path(__file__).parents[1]), [])

    def test_rejects_missing_link_and_wrong_name(self):
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "demo-skill"
            skill.mkdir()
            (skill / "SKILL.md").write_text(
                "---\nname: other-skill\ndescription: demo\n---\n[missing](references/no.md)\n",
                encoding="utf-8",
            )
            errors = MODULE.validate(skill)
            self.assertIn("frontmatter name must match the skill directory", errors)
            self.assertIn("missing or unsafe linked resource: references/no.md", errors)


if __name__ == "__main__":
    unittest.main()
