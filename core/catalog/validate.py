"""Catalog attribute validation — JSON Schema (Draft 2020-12) + CEL constraints (MVP-046).

Every attribute write is checked against the pack's registered schema: the Draft 2020-12
`properties`/`required`/types (with `additionalProperties:false` so unknown attributes are
rejected), then the `constraints` block — cross-field CEL expressions evaluated over
`attributes`. Failures come back as `{path, error, rule}` problems. Compiled validators + CEL
programs are cached per (pack, version) so repeated writes stay well under the 10ms budget.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import celpy
from jsonschema import Draft202012Validator

_ENV = celpy.Environment()


@dataclass
class AttributeProblem:
    path: str
    error: str
    rule: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "error": self.error, "rule": self.rule}


class ValidationProblems(Exception):
    def __init__(self, problems: list[AttributeProblem]) -> None:
        super().__init__(f"{len(problems)} attribute validation problem(s)")
        self.problems = problems


class _SchemaValidator:
    def __init__(self, json_schema: dict[str, Any]) -> None:
        # Reject unknown attributes; drop the non-standard `constraints` keyword (handled by CEL).
        schema = {k: v for k, v in json_schema.items() if k != "constraints"}
        schema["additionalProperties"] = False
        self._js = Draft202012Validator(schema)
        self._constraints = [
            (c["cel"], c.get("message", c["cel"]), _ENV.program(_ENV.compile(c["cel"])))
            for c in json_schema.get("constraints", [])
        ]

    def validate(self, attributes: dict[str, Any]) -> list[AttributeProblem]:
        # Structural errors first; if the shape is wrong, cross-field CEL would misfire.
        structural = [
            AttributeProblem(path=err.json_path, error=err.message, rule="schema")
            for err in sorted(self._js.iter_errors(attributes), key=lambda e: list(e.path))
        ]
        if structural:
            return structural

        activation = {"attributes": celpy.json_to_cel(attributes)}
        problems: list[AttributeProblem] = []
        for expr, message, program in self._constraints:
            try:
                ok = bool(program.evaluate(activation))
            except celpy.CELEvalError as exc:  # a constraint that can't evaluate is a failure
                problems.append(AttributeProblem(path="$", error=str(exc), rule=expr))
                continue
            if not ok:
                problems.append(AttributeProblem(path="$", error=message, rule=expr))
        return problems


_CACHE: dict[Any, _SchemaValidator] = {}


def validate_attributes(
    attributes: dict[str, Any], *, json_schema: dict[str, Any], cache_key: Any
) -> list[AttributeProblem]:
    """Validate `attributes` against the pack schema. Returns [] when valid. Compiled
    validators/CEL programs are cached by `cache_key` (e.g. (pack_id, version))."""
    validator = _CACHE.get(cache_key)
    if validator is None:
        validator = _CACHE[cache_key] = _SchemaValidator(json_schema)
    return validator.validate(attributes)


def assert_valid(
    attributes: dict[str, Any], *, json_schema: dict[str, Any], cache_key: Any
) -> None:
    problems = validate_attributes(attributes, json_schema=json_schema, cache_key=cache_key)
    if problems:
        raise ValidationProblems(problems)
