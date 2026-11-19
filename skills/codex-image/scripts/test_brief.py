"""Tests for brief.py (stdlib unittest, offline).

Run from the repo root: python3 -m unittest discover -s skills/codex-image/scripts -p "test_*.py"
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import brief  # noqa: E402

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "post-cover.brief.json"


class BriefTestCase(unittest.TestCase):
    """Builds a temp dir with fake ref images and a temp registry."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.imgs: list[str] = []
        for name in ("face1.jpg", "face2.jpg", "style1.png", "style2.png", "logo.png", "extra.webp"):
            p = self.dir / name
            p.write_bytes(b"\x89PNG fake")
            self.imgs.append(str(p))
        registry_path = self.dir / "brands.json"
        registry_path.write_text(
            json.dumps(
                {
                    "presets": {
                        "demo": {
                            "description": "test",
                            "references": [
                                {"path": self.imgs[0], "role": "face", "note": "main"},
                                {"path": self.imgs[2], "role": "style"},
                                {"path": self.imgs[4], "role": "logo"},
                            ],
                            "style_notes": ["Near-black background, yellow box"],
                            "avoid": ["purple"],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        self.registry = brief.load_registry(registry_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def base(self, **overrides: object) -> dict[str, object]:
        data: dict[str, object] = {
            "type": "post-illustration",
            "purpose": "Explain agents",
            "placement": "Telegram post",
            "route": "subscription",
            "quality": "medium",
            "aspect": "square",
            "scene": "Desk with laptop",
            "subject": "A robot",
            "style": "Flat illustration",
            "text": [{"content": "АГЕНТЫ", "position": "top", "typography": "bold sans"}],
            "references": [{"path": self.imgs[1], "role": "subject"}],
            "extra": {"post_topic": "agents"},
        }
        data.update(overrides)
        return data

    def problems(self, **overrides: object) -> list[str]:
        path = self.dir / "b.json"
        path.write_text(json.dumps(self.base(**overrides), ensure_ascii=False), encoding="utf-8")
        return brief.check(brief.load_brief(path), self.registry)

    def assertProblem(self, problems: list[str], needle: str) -> None:
        self.assertTrue(any(needle in p for p in problems), f"{needle!r} not in {problems}")


class CheckTests(BriefTestCase):
    def test_valid_brief_passes(self) -> None:
        self.assertEqual(self.problems(), [])

    def test_missing_text(self) -> None:
        self.assertProblem(self.problems(text=[]), "точный текст")
        self.assertProblem(self.problems(text=[{"content": "", "position": "top"}]), "пустой текст")
        self.assertEqual(self.problems(text=[], no_text=True), [])

    def test_text_without_position_or_with_quotes(self) -> None:
        self.assertProblem(self.problems(text=[{"content": "ИИ"}]), "положение")
        self.assertProblem(self.problems(text=[{"content": '"ИИ"', "position": "top"}]), "кавычки")

    def test_bad_subscription_size(self) -> None:
        self.assertProblem(self.problems(size="2048x1152"), "недоступен по подписке")
        self.assertProblem(self.problems(aspect="wide"), "aspect")

    def test_api_route_is_not_supported(self) -> None:
        self.assertProblem(self.problems(route="api"), "только subscription")

    def test_too_many_refs(self) -> None:
        refs = [{"path": p, "role": "style"} for p in self.imgs]
        self.assertProblem(self.problems(references=refs), "максимум 5")

    def test_missing_ref_file(self) -> None:
        refs = [{"path": str(self.dir / "nope.png"), "role": "face"}]
        self.assertProblem(self.problems(references=refs), "файл не найден")

    def test_ref_without_role(self) -> None:
        self.assertProblem(self.problems(references=[{"path": self.imgs[1]}]), "нет роли")
        self.assertProblem(self.problems(references=[self.imgs[1]]), "нет роли")
        self.assertProblem(self.problems(references=[{"path": self.imgs[1], "role": "vibe"}]), "нет роли")

    def test_no_refs_needs_reason(self) -> None:
        self.assertProblem(self.problems(references=[]), "нет референсов")
        self.assertEqual(self.problems(references=[], references_none_reason="abstract"), [])

    def test_rejected_folder(self) -> None:
        bad = self.dir / "_rejected" / "01.jpg"
        bad.parent.mkdir()
        bad.write_bytes(b"x")
        self.assertProblem(self.problems(references=[{"path": str(bad), "role": "face"}]), "отбракованной")

    def test_svg_ref_rejected(self) -> None:
        svg = self.dir / "logo.svg"
        svg.write_text("<svg/>")
        self.assertProblem(self.problems(references=[{"path": str(svg), "role": "logo"}]), "png/jpg/webp")

    def test_cover_rules(self) -> None:
        p = self.problems(
            type="cover",
            aspect="landscape",
            text=[{"content": "ОДИН ДВА ТРИ ЧЕТЫРЕ ПЯТЬ", "position": "left"}],
            extra={},
        )
        self.assertProblem(p, "не больше 4")
        self.assertProblem(p, "face_emotion")
        self.assertProblem(p, "референса лица")

    def test_unknown_preset(self) -> None:
        self.assertProblem(self.problems(preset="nope"), "нет в реестре")


class PresetTests(BriefTestCase):
    def test_preset_refs_first_and_dedup(self) -> None:
        b = brief.Brief(
            preset="demo",
            references=[brief.Reference(self.imgs[0], "face"), brief.Reference(self.imgs[3], "style")],
        )
        refs, notes, avoid = brief.resolve(b, self.registry)
        self.assertEqual([r.path for r in refs], [self.imgs[0], self.imgs[2], self.imgs[4], self.imgs[3]])
        self.assertEqual(notes, ["Near-black background, yellow box"])
        self.assertEqual(avoid, ["purple"])

    def test_preset_replace_roles(self) -> None:
        b = brief.Brief(
            preset="demo", preset_replace_roles=["logo"], references=[brief.Reference(self.imgs[5], "logo")]
        )
        refs, _, _ = brief.resolve(b, self.registry)
        self.assertEqual([r.path for r in refs], [self.imgs[0], self.imgs[2], self.imgs[5]])

    def test_preset_satisfies_style_and_refs(self) -> None:
        self.assertEqual(self.problems(preset="demo", style="", references=[]), [])


class PromptTests(BriefTestCase):
    def build(self, **overrides: object) -> tuple[str, str]:
        path = self.dir / "b.json"
        path.write_text(json.dumps(self.base(**overrides), ensure_ascii=False), encoding="utf-8")
        b = brief.load_brief(path)
        self.assertEqual(brief.check(b, self.registry), [])
        prompt = brief.build_prompt(b, self.registry)
        return prompt, brief.run_command(b, self.registry, prompt)

    def test_prompt_order_and_text(self) -> None:
        prompt, cmd = self.build(preset="demo")
        heads = [line.split(":", 1)[0] for line in prompt.splitlines()]
        self.assertEqual(heads, ["Intent", "References", "Scene", "Subject", "Details", "Text", "Constraints"])
        self.assertIn('"АГЕНТЫ", top, bold sans', prompt)
        self.assertIn("exactly once", prompt)
        self.assertIn("No extra text", prompt)
        self.assertIn("Image 1: face reference", prompt)
        self.assertIn("Image 4: subject reference", prompt)
        self.assertIn("Avoid: purple", prompt)
        self.assertIn("scripts/run.sh", cmd)
        self.assertTrue(cmd.rstrip().endswith(self.imgs[1]))
        self.assertIn(" medium square ", cmd)

    def test_no_face_no_face_constraint(self) -> None:
        prompt, _ = self.build()
        self.assertNotIn("keep the face", prompt.lower())

    def test_no_text_prompt(self) -> None:
        prompt, _ = self.build(text=[], no_text=True)
        self.assertIn("Text: no text", prompt)


class CliTests(unittest.TestCase):
    def run_cli(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = brief.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_questions_type_specific(self) -> None:
        code, out, _ = self.run_cli("questions", "--type", "cover")
        self.assertEqual(code, 0)
        self.assertIn("320x180", out)

    def test_init_then_check_fails_with_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "b.json"
            self.assertEqual(self.run_cli("init", str(path), "--type", "banner")[0], 0)
            self.assertIn("platform", json.loads(path.read_text(encoding="utf-8"))["extra"])
            code, _, err = self.run_cli("check", str(path))
            self.assertEqual(code, 1)
            self.assertIn("вернитесь к заказчику", err)
            self.assertEqual(self.run_cli("init", str(path))[0], 1)

    def test_example_brief_passes(self) -> None:
        self.assertTrue(EXAMPLE.is_file(), "example brief missing")
        code, _, err = self.run_cli("check", str(EXAMPLE))
        self.assertEqual(code, 0, err)


if __name__ == "__main__":
    unittest.main()
