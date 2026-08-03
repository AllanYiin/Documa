from __future__ import annotations

import json
from types import ModuleType

import pytest

from documa.adapters.native_binding import (
    NativeBindingSpec,
    load_native_binding,
    native_exception_to_documa,
)
from documa.core.errors import DocumaError


def _spec() -> NativeBindingSpec:
    return NativeBindingSpec(
        parser_id="fixture_parser",
        module_name="fixture_native_parser",
        identity_labels=("version", "contract"),
        expected_identity=("1.2.3", "layout-v1"),
        required_calls=("parse",),
        not_installed_code="FIXTURE_NOT_INSTALLED",
        incompatible_code="FIXTURE_INCOMPATIBLE",
        suggested_action="Reinstall the fixture parser.",
    )


def test_load_native_binding_validates_identity_calls_and_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = ModuleType("fixture_native_parser")
    module.version_info = lambda: ("1.2.3", "layout-v1")
    module.parse = lambda source: source
    module.capabilities = lambda: {"formats": ["fixture"]}
    monkeypatch.setattr(
        "documa.adapters.native_binding.import_module", lambda name: module
    )

    binding = load_native_binding(_spec())

    assert binding.module is module
    assert binding.identity == {"version": "1.2.3", "contract": "layout-v1"}
    assert binding.capabilities == {"formats": ["fixture"]}


def test_load_native_binding_reports_contract_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = ModuleType("fixture_native_parser")
    module.version_info = lambda: ("9.9.9", "layout-v1")
    module.parse = lambda source: source
    monkeypatch.setattr(
        "documa.adapters.native_binding.import_module", lambda name: module
    )

    with pytest.raises(DocumaError) as caught:
        load_native_binding(_spec())

    assert caught.value.detail.code == "FIXTURE_INCOMPATIBLE"
    assert caught.value.detail.context["mismatches"]["version"] == {
        "expected": "1.2.3",
        "actual": "9.9.9",
    }


def test_native_error_envelope_is_shared_and_structured() -> None:
    error = ValueError(
        json.dumps(
            {
                "code": "INPUT_LIMIT",
                "message": "limit reached",
                "recoverable": False,
                "context": {"limit": 10},
            }
        )
    )

    converted = native_exception_to_documa(
        error,
        source="sample.bin",
        default_code="PARSE_FAILED",
        default_message="parse failed",
        default_recoverable=True,
        suggested_action="Inspect the source.",
    )

    assert converted.detail.code == "INPUT_LIMIT"
    assert converted.detail.recoverable is False
    assert converted.detail.context == {"source": "sample.bin", "limit": 10}
