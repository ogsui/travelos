"""Versioned prompt registry.

One Markdown file per prompt version under ``prompts/``. The front matter is a
small, deliberately-typed subset of YAML (no YAML dependency, no anchors, no
code execution). The body may contain ``## System`` and ``## User`` sections.

Rules enforced here:

* every file declares ``name``, ``version`` and ``data_class``;
* prompt names are slugs and versions are positive integers;
* schema references resolve under the prompts directory;
* variables in the user section are always treated as untrusted DATA;
* variables in the system section must be named in ``trusted``;
* a missing variable fails the call instead of silently rendering ``{{x}}``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from travelos.llm.errors import PromptFormatError, PromptNotFoundError, PromptRenderError
from travelos.llm.injection import delimit_untrusted
from travelos.llm.types import DataClass

_FRONT_MATTER_KEYS = frozenset(
    {
        "name",
        "version",
        "data_class",
        "description",
        "input_schema",
        "output_schema",
        "eval_set",
        "temperature",
        "max_tokens",
        "trusted",
        "sensitive",
        "allow",
    }
)
_SECTION = re.compile(r"^##\s+(System|User)\s*$", re.IGNORECASE)
_VARIABLE = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")
_SLUG = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class PromptSpec:
    """A loaded prompt and its declared contract."""

    name: str
    version: int
    data_class: DataClass
    system_template: str
    user_template: str
    path: Path
    description: str | None = None
    input_schema: Mapping[str, Any] | None = None
    output_schema: Mapping[str, Any] | None = None
    eval_set: str | None = None
    temperature: float = 0.0
    max_tokens: int = 1024
    trusted: tuple[str, ...] = ()
    sensitive: tuple[str, ...] = ()
    allow: Mapping[str, tuple[str, ...]] | None = None

    @property
    def requires_json(self) -> bool:
        return self.output_schema is not None

    def render(self, variables: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
        """Render the prompt into ``(role, content)`` pairs.

        User-section variables are sanitised and wrapped in a delimited DATA
        block. System-section variables must be listed in ``trusted`` and come
        from code, never from a customer message.
        """

        rendered: list[tuple[str, str]] = []
        system = self._render_system(variables)
        if system.strip():
            rendered.append(("system", system))
        user = self._render_user(variables)
        if user.strip():
            rendered.append(("user", user))
        if not rendered:
            raise PromptRenderError(f"{self.name} v{self.version}: prompt rendered empty")
        return tuple(rendered)

    def _render_system(self, variables: Mapping[str, object]) -> str:
        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in self.trusted:
                raise PromptRenderError(
                    f"{self.name} v{self.version}: {key!r} cannot be used in the system "
                    "section unless it is listed under trusted"
                )
            if key not in variables:
                raise PromptRenderError(f"{self.name} v{self.version}: missing variable {key!r}")
            return _as_text(variables[key])

        return _VARIABLE.sub(replace, self.system_template)

    def _render_user(self, variables: Mapping[str, object]) -> str:
        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in variables:
                raise PromptRenderError(f"{self.name} v{self.version}: missing variable {key!r}")
            value = _as_text(variables[key])
            if key in self.trusted:
                return value
            return delimit_untrusted(value, label=key)

        return _VARIABLE.sub(replace, self.user_template)


class PromptRegistry:
    """Loads every ``*.md`` prompt under a directory and resolves versions."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._specs: dict[tuple[str, int], PromptSpec] = {}
        self._latest: dict[str, int] = {}
        if not self._root.exists():
            raise PromptFormatError(f"prompts directory does not exist: {self._root}")
        if not self._root.is_dir():
            raise PromptFormatError(f"prompts path is not a directory: {self._root}")
        self.reload()

    def reload(self) -> None:
        specs: dict[tuple[str, int], PromptSpec] = {}
        latest: dict[str, int] = {}
        for path in sorted(self._root.glob("*.md")):
            if path.name.lower() == "readme.md":
                continue
            spec = _load_prompt(path, self._root)
            key = (spec.name, spec.version)
            if key in specs:
                raise PromptFormatError(
                    f"duplicate prompt {spec.name} v{spec.version}: "
                    f"{specs[key].path.name} and {path.name}"
                )
            specs[key] = spec
            latest[spec.name] = max(latest.get(spec.name, 0), spec.version)
        self._specs = specs
        self._latest = latest

    def get(self, name: str, version: int | None = None) -> PromptSpec:
        resolved = version if version is not None else self._latest.get(name)
        if resolved is None:
            raise PromptNotFoundError(name, version)
        spec = self._specs.get((name, resolved))
        if spec is None:
            raise PromptNotFoundError(name, resolved)
        return spec

    def versions(self, name: str) -> tuple[int, ...]:
        return tuple(sorted(version for prompt_name, version in self._specs if prompt_name == name))

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._latest))

    def latest_version(self, name: str) -> int | None:
        return self._latest.get(name)

    @property
    def root(self) -> Path:
        return self._root


def _load_prompt(path: Path, root: Path) -> PromptSpec:
    text = path.read_text(encoding="utf-8")
    front, body = _split_front_matter(text, path)
    if "name" not in front:
        raise PromptFormatError(f"{path.name}: missing 'name'")
    if "version" not in front:
        raise PromptFormatError(f"{path.name}: missing 'version'")
    if "data_class" not in front:
        raise PromptFormatError(f"{path.name}: missing 'data_class'")

    name = str(front["name"])
    if not _SLUG.match(name):
        raise PromptFormatError(f"{path.name}: invalid prompt name {name!r}")
    try:
        version = _int(front["version"], path, "version")
    except PromptFormatError as exc:
        raise PromptFormatError(f"{path.name}: version must be an integer") from exc
    if version < 1:
        raise PromptFormatError(f"{path.name}: version must be >= 1")
    try:
        data_class = DataClass(str(front["data_class"]))
    except ValueError as exc:
        raise PromptFormatError(f"{path.name}: unknown data_class {front['data_class']!r}") from exc

    system_template, user_template = _split_sections(body, path)
    if not system_template.strip() and not user_template.strip():
        raise PromptFormatError(f"{path.name}: prompt body is empty")

    output_schema = _schema(front.get("output_schema"), root, path, "output_schema")
    input_schema = _schema(front.get("input_schema"), root, path, "input_schema")

    temperature = _float(front.get("temperature", 0.0), path, "temperature")
    if not 0.0 <= temperature <= 2.0:
        raise PromptFormatError(f"{path.name}: temperature must be between 0 and 2")
    max_tokens = _int(front.get("max_tokens", 1024), path, "max_tokens")
    if max_tokens < 1:
        raise PromptFormatError(f"{path.name}: max_tokens must be positive")

    return PromptSpec(
        name=name,
        version=version,
        data_class=data_class,
        system_template=system_template,
        user_template=user_template,
        path=path,
        description=str(front["description"]) if "description" in front else None,
        input_schema=input_schema,
        output_schema=output_schema,
        eval_set=str(front["eval_set"]) if "eval_set" in front else None,
        temperature=temperature,
        max_tokens=max_tokens,
        trusted=_string_tuple(front.get("trusted"), path, "trusted"),
        sensitive=_string_tuple(front.get("sensitive"), path, "sensitive"),
        allow=_allow_list(front.get("allow"), path),
    )


def _split_front_matter(text: str, path: Path) -> tuple[dict[str, object], str]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        raise PromptFormatError(f"{path.name}: missing front matter delimited by '---'")
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
    if end is None:
        raise PromptFormatError(f"{path.name}: front matter is not closed")
    front: dict[str, object] = {}
    for raw in lines[1:end]:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition(":")
        if not separator:
            raise PromptFormatError(f"{path.name}: malformed front matter line {raw!r}")
        key = key.strip()
        if key not in _FRONT_MATTER_KEYS:
            raise PromptFormatError(f"{path.name}: unknown front matter key {key!r}")
        front[key] = _scalar(value.strip())
    return front, "\n".join(lines[end + 1 :])


def _split_sections(body: str, path: Path) -> tuple[str, str]:
    sections: dict[str, list[str]] = {"system": [], "user": []}
    current = "system"
    saw_heading = False
    for line in body.split("\n"):
        match = _SECTION.match(line.strip())
        if match:
            current = match.group(1).lower()
            saw_heading = True
            continue
        sections[current].append(line)
    if not saw_heading:
        return "\n".join(sections["system"]).strip(), ""
    return "\n".join(sections["system"]).strip(), "\n".join(sections["user"]).strip()


def _scalar(value: str) -> object:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    lowered = value.lower()
    if lowered in {"true", "yes"}:
        return True
    if lowered in {"false", "no"}:
        return False
    if value.startswith("[") and value.endswith("]"):
        return _list(value)
    if value.startswith("{") and value.endswith("}"):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?\d+\.\d+", value):
        return float(value)
    return value


def _list(value: str) -> list[str]:
    inner = value[1:-1].strip()
    if not inner:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = [item.strip().strip("'\"") for item in inner.split(",")]
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _string_tuple(value: object, path: Path, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise PromptFormatError(f"{path.name}: {key} must be a list")
    return tuple(str(item) for item in value)


def _schema(value: object, root: Path, path: Path, key: str) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return value
    reference = str(value)
    if reference.lstrip().startswith("{"):
        try:
            parsed = json.loads(reference)
        except json.JSONDecodeError as exc:
            raise PromptFormatError(f"{path.name}: {key} is not valid JSON") from exc
        if not isinstance(parsed, Mapping):
            raise PromptFormatError(f"{path.name}: {key} must be a JSON object")
        return parsed
    schema_path = Path(reference)
    if not schema_path.is_absolute():
        schema_path = root / schema_path
    if not schema_path.is_file():
        raise PromptFormatError(f"{path.name}: {key} file not found: {schema_path}")
    try:
        parsed = json.loads(schema_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PromptFormatError(f"{path.name}: {key} is not valid JSON: {schema_path}") from exc
    if not isinstance(parsed, Mapping):
        raise PromptFormatError(f"{path.name}: {key} must be a JSON object: {schema_path}")
    return parsed


def _allow_list(value: object, path: Path) -> Mapping[str, tuple[str, ...]] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise PromptFormatError(f"{path.name}: allow must be an object")
    allowed: dict[str, tuple[str, ...]] = {}
    for key, items in value.items():
        if not isinstance(items, list):
            raise PromptFormatError(f"{path.name}: allow.{key} must be a list")
        allowed[str(key)] = tuple(str(item) for item in items)
    return allowed


def _int(value: object, path: Path, key: str) -> int:
    if isinstance(value, bool):
        raise PromptFormatError(f"{path.name}: {key} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value.strip())
    raise PromptFormatError(f"{path.name}: {key} must be an integer")


def _float(value: object, path: Path, key: str) -> float:
    if isinstance(value, bool):
        raise PromptFormatError(f"{path.name}: {key} must be a number")
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError as exc:
            raise PromptFormatError(f"{path.name}: {key} must be a number") from exc
    raise PromptFormatError(f"{path.name}: {key} must be a number")


def _as_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool | int | float):
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
