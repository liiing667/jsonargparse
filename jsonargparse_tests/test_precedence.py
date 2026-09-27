"""Tests for the pinned precedence of sources: command line > environment > config files > defaults.

The tests are written with unittest.TestCase so that they run both with pytest and with plain
unittest discovery. Each layer is verified by checking both the effective value and, through the
internal provenance tracking, the source that each value came from.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonargparse import ArgumentParser, Namespace
from jsonargparse._namespace import get_provenance


def get_sources(cfg: Namespace) -> dict:
    """Returns the description of where each value came from, except for config arguments."""
    return {
        key: source.description
        for key, source in get_provenance(cfg).items()
        if key.rsplit(".", 1)[-1] != "config"
    }


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write_config(self, name: str, content: dict) -> Path:
        path = self.dir / name
        path.write_text(json.dumps(content))
        return path


class SourceLayerTests(TempDirTestCase):
    """One key is set in all layers; each test peels one layer off and checks value and source."""

    def make_parser(self, **kwargs):
        parser = ArgumentParser(prog="app", env_prefix="APP", default_env=True, **kwargs)
        parser.add_argument("--op", default="code default")
        parser.add_argument("--config", action="config")
        return parser

    def test_code_default(self):
        parser = self.make_parser()
        cfg = parser.parse_args([])
        self.assertEqual("code default", cfg.op)
        self.assertEqual({"op": "default"}, get_sources(cfg))

    def test_default_config_file_over_code_default(self):
        default_cfg = self.write_config("defaults.json", {"op": "default config file"})
        parser = self.make_parser(default_config_files=[str(default_cfg)])
        cfg = parser.parse_args([])
        self.assertEqual("default config file", cfg.op)
        self.assertEqual({"op": "default config file"}, get_sources(cfg))

    def test_config_file_over_default_config_file(self):
        default_cfg = self.write_config("defaults.json", {"op": "default config file"})
        cfg_file = self.write_config("cfg.json", {"op": "config file"})
        parser = self.make_parser(default_config_files=[str(default_cfg)])
        cfg = parser.parse_args([f"--config={cfg_file}"])
        self.assertEqual("config file", cfg.op)
        self.assertEqual({"op": "config file"}, get_sources(cfg))

    def test_env_over_config_file(self):
        cfg_file = self.write_config("cfg.json", {"op": "config file"})
        parser = self.make_parser()
        with patch.dict(os.environ, {"APP_OP": "env var"}):
            cfg = parser.parse_args([f"--config={cfg_file}"])
        self.assertEqual("env var", cfg.op)
        self.assertEqual({"op": "environment variable"}, get_sources(cfg))

    def test_env_over_default_config_file(self):
        default_cfg = self.write_config("defaults.json", {"op": "default config file"})
        parser = self.make_parser(default_config_files=[str(default_cfg)])
        with patch.dict(os.environ, {"APP_OP": "env var"}):
            cfg = parser.parse_args([])
        self.assertEqual("env var", cfg.op)
        self.assertEqual({"op": "environment variable"}, get_sources(cfg))

    def test_command_line_over_env(self):
        parser = self.make_parser()
        with patch.dict(os.environ, {"APP_OP": "env var"}):
            cfg = parser.parse_args(["--op=command line"])
        self.assertEqual("command line", cfg.op)
        self.assertEqual({"op": "command line argument"}, get_sources(cfg))

    def test_command_line_over_config_file_regardless_of_order(self):
        cfg_file = self.write_config("cfg.json", {"op": "config file"})
        parser = self.make_parser()
        for args in (
            ["--op=command line", f"--config={cfg_file}"],
            [f"--config={cfg_file}", "--op=command line"],
        ):
            with self.subTest(args=args):
                cfg = parser.parse_args(args)
                self.assertEqual("command line", cfg.op)
                self.assertEqual({"op": "command line argument"}, get_sources(cfg))

    def test_all_layers_peeled_one_by_one(self):
        default_cfg = self.write_config("defaults.json", {"op": "default config file"})
        cfg_file = self.write_config("cfg.json", {"op": "config file"})
        parser = self.make_parser(default_config_files=[str(default_cfg)])
        env = {"APP_OP": "env var"}

        with patch.dict(os.environ, env):
            cfg = parser.parse_args(["--op=command line", f"--config={cfg_file}"])
            self.assertEqual("command line", cfg.op)
            cfg = parser.parse_args([f"--config={cfg_file}"])
            self.assertEqual("env var", cfg.op)
        cfg = parser.parse_args([f"--config={cfg_file}"])
        self.assertEqual("config file", cfg.op)
        cfg = parser.parse_args([])
        self.assertEqual("default config file", cfg.op)

        parser = self.make_parser()
        cfg = parser.parse_args([])
        self.assertEqual("code default", cfg.op)

    def test_env_over_parse_string_and_parse_path(self):
        cfg_file = self.write_config("cfg.json", {"op": "config file"})
        parser = self.make_parser()
        with patch.dict(os.environ, {"APP_OP": "env var"}):
            self.assertEqual("env var", parser.parse_string('{"op": "config string"}').op)
            self.assertEqual("env var", parser.parse_path(cfg_file).op)

    def test_later_config_file_overrides_earlier(self):
        one = self.write_config("one.json", {"op": "one"})
        two = self.write_config("two.json", {"op": "two"})
        parser = self.make_parser()
        cfg = parser.parse_args([f"--config={one}", f"--config={two}"])
        self.assertEqual("two", cfg.op)
        self.assertEqual({"op": "config file"}, get_sources(cfg))

    def test_including_config_file_overrides_included_regardless_of_key_order(self):
        included = self.write_config("included.json", {"op": "included", "other": "included"})
        # own key before the config key
        outer1 = self.write_config("outer1.json", {"op": "outer", "config": str(included)})
        # own key after the config key
        outer2 = self.write_config("outer2.json", {"config": str(included), "op": "outer"})
        parser = self.make_parser()
        parser.add_argument("--other", default="code default")
        for outer in (outer1, outer2):
            with self.subTest(outer=outer):
                cfg = parser.parse_args([f"--config={outer}"])
                self.assertEqual("outer", cfg.op)
                self.assertEqual("included", cfg.other)


class SubcommandInheritanceTests(TempDirTestCase):
    """Values inherited from subcommand parsers never override higher precedence parent values."""

    def make_parser(self, **kwargs):
        parser = ArgumentParser(prog="app", env_prefix="APP", default_env=True, **kwargs)
        parser.add_argument("--op", default="code default")
        parser.add_argument("--config", action="config")
        subparser = ArgumentParser()
        subparser.add_argument("--sub_op", default="subcommand default")
        subcommands = parser.add_subcommands(required=False)
        subcommands.add_subcommand("sub", subparser)
        return parser

    def test_subcommand_default(self):
        parser = self.make_parser()
        cfg = parser.parse_args(["sub"])
        self.assertEqual("subcommand default", cfg.sub.sub_op)
        self.assertEqual("default", get_sources(cfg)["sub.sub_op"])

    def test_subcommand_default_does_not_override_parent_config_file(self):
        cfg_file = self.write_config("cfg.json", {"sub": {"sub_op": "config file"}})
        parser = self.make_parser()
        cfg = parser.parse_args([f"--config={cfg_file}", "sub"])
        self.assertEqual("config file", cfg.sub.sub_op)
        self.assertEqual("config file", get_sources(cfg)["sub.sub_op"])

    def test_subcommand_default_does_not_override_parent_env(self):
        parser = self.make_parser()
        with patch.dict(os.environ, {"APP_SUB__SUB_OP": "env var"}):
            cfg = parser.parse_args(["sub"])
        self.assertEqual("env var", cfg.sub.sub_op)
        self.assertEqual("environment variable", get_sources(cfg)["sub.sub_op"])

    def test_subcommand_default_does_not_override_parent_command_line(self):
        parser = self.make_parser()
        cfg = parser.parse_args(["sub", "--sub_op=command line"])
        self.assertEqual("command line", cfg.sub.sub_op)
        self.assertEqual("command line argument", get_sources(cfg)["sub.sub_op"])

    def test_subcommand_selected_by_config_file_env_still_wins(self):
        cfg_file = self.write_config(
            "cfg.json",
            {"subcommand": "sub", "op": "config file", "sub": {"sub_op": "config file"}},
        )
        parser = self.make_parser()
        env = {"APP_OP": "env var", "APP_SUB__SUB_OP": "env var"}
        with patch.dict(os.environ, env):
            cfg = parser.parse_args([f"--config={cfg_file}"])
        self.assertEqual("env var", cfg.op)
        self.assertEqual("env var", cfg.sub.sub_op)
        self.assertEqual("environment variable", get_sources(cfg)["op"])
        self.assertEqual("environment variable", get_sources(cfg)["sub.sub_op"])

    def test_subcommand_selected_by_config_file_command_line_still_wins(self):
        cfg_file = self.write_config("cfg.json", {"subcommand": "sub", "op": "config file"})
        parser = self.make_parser()
        cfg = parser.parse_args([f"--config={cfg_file}", "--op=command line"])
        self.assertEqual("command line", cfg.op)
        self.assertEqual("command line argument", get_sources(cfg)["op"])

    def test_subcommand_env_overrides_subcommand_default_config_file(self):
        default_cfg = self.write_config("defaults.json", {"sub": {"sub_op": "default config file"}})
        parser = self.make_parser(default_config_files=[str(default_cfg)])
        cfg = parser.parse_args(["sub"])
        self.assertEqual("default config file", cfg.sub.sub_op)
        with patch.dict(os.environ, {"APP_SUB__SUB_OP": "env var"}):
            cfg = parser.parse_args(["sub"])
        self.assertEqual("env var", cfg.sub.sub_op)


if __name__ == "__main__":
    unittest.main()
