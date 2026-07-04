"""
tests/test_ghio_marker.py — hoger.ghio.marker 單元測試 + integration 驗證。

需要 GH_IO.dll 才能跑（本機有 Rhino 8 時會執行；CI 或無 Rhino 環境會 skip 整個模組）。
integration 級測試另外標 `@pytest.mark.integration`，需要 Rhino.Compute 在線
（COMPUTE_URL），預設 `pytest`（addopts = "-m 'not integration'"）不會收集。

全部測試一律先把 tests/fixtures/plain_slider_panel.gh 複製到 tmp_path 操作，
原檔案（版控中）絕不修改。
"""
from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path

import pytest

from hoger.ghio import loader

if not loader.is_available():
    pytest.skip("GH_IO.dll not available", allow_module_level=True)

from hoger.ghio import marker  # noqa: E402
from hoger.ghio import scanner  # noqa: E402

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "plain_slider_panel.gh"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def fixture_copy(tmp_path):
    dst = tmp_path / "plain_slider_panel.gh"
    shutil.copy2(FIXTURE_PATH, dst)
    return dst


@pytest.fixture
def fixture_guids():
    result = scanner.scan_gh(FIXTURE_PATH)
    slider = next(i for i in result.inputs if i.object_type == "Number Slider")
    panel = next(o for o in result.outputs if o.object_type == "Panel")
    return {"slider": slider.instance_guid, "panel": panel.instance_guid}


# ── basic marking ───────────────────────────────────────────────────


def test_apply_marks_basic(fixture_copy, fixture_guids):
    result = marker.apply_marks(
        fixture_copy,
        input_marks=[{"guid": fixture_guids["slider"], "name": "size"}],
        output_marks=[{"guid": fixture_guids["panel"], "name": "report"}],
    )

    assert result.marked_inputs == ["RH_IN:size"]
    assert result.marked_outputs == ["RH_OUT:report"]
    assert result.updated == []
    assert result.backup_path is not None
    assert Path(result.backup_path).exists()

    scan = scanner.scan_gh(fixture_copy)
    slider = next(i for i in scan.inputs if i.instance_guid == fixture_guids["slider"])
    panel = next(o for o in scan.outputs if o.instance_guid == fixture_guids["panel"])
    assert slider.existing_mark == "RH_IN:size"
    assert panel.existing_mark == "RH_OUT:report"


def test_backup_file_matches_original_bytes(fixture_copy, fixture_guids):
    original_bytes = fixture_copy.read_bytes()
    result = marker.apply_marks(
        fixture_copy,
        input_marks=[{"guid": fixture_guids["slider"], "name": "size"}],
        output_marks=[],
    )
    backup_bytes = Path(result.backup_path).read_bytes()
    assert backup_bytes == original_bytes


def test_backup_filename_pattern(fixture_copy, fixture_guids):
    result = marker.apply_marks(
        fixture_copy,
        input_marks=[{"guid": fixture_guids["slider"], "name": "size"}],
        output_marks=[],
    )
    backup_path = Path(result.backup_path)
    assert backup_path.parent == fixture_copy.parent
    # {stem}.{YYYYmmdd_HHMMSS}.bak
    assert backup_path.name.startswith(fixture_copy.stem + ".")
    assert backup_path.name.endswith(".bak")
    middle = backup_path.name[len(fixture_copy.stem) + 1 : -len(".bak")]
    assert len(middle) == len("YYYYmmdd_HHMMSS")


# ── idempotency ──────────────────────────────────────────────────────


def test_apply_marks_idempotent_updates_existing_group(fixture_copy, fixture_guids):
    marker.apply_marks(
        fixture_copy,
        input_marks=[{"guid": fixture_guids["slider"], "name": "size"}],
        output_marks=[],
    )
    before_scan = scanner.scan_gh(fixture_copy)
    before_marked_count = before_scan.already_marked_count

    result2 = marker.apply_marks(
        fixture_copy,
        input_marks=[{"guid": fixture_guids["slider"], "name": "size2"}],
        output_marks=[],
    )

    assert result2.updated == ["RH_IN:size2"]
    assert result2.marked_inputs == []

    after_scan = scanner.scan_gh(fixture_copy)
    assert after_scan.already_marked_count == before_marked_count

    slider = next(
        i for i in after_scan.inputs if i.instance_guid == fixture_guids["slider"]
    )
    assert slider.existing_mark == "RH_IN:size2"


# ── name validation ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad_name",
    ["", "has space", "中文", "xRH_INy", "RH_OUT_thing", "name-with-dash"],
)
def test_invalid_name_raises_mark_error(fixture_copy, fixture_guids, bad_name):
    with pytest.raises(marker.MarkError):
        marker.apply_marks(
            fixture_copy,
            input_marks=[{"guid": fixture_guids["slider"], "name": bad_name}],
            output_marks=[],
        )


def test_invalid_name_error_message_contains_name(fixture_copy, fixture_guids):
    with pytest.raises(marker.MarkError) as excinfo:
        marker.apply_marks(
            fixture_copy,
            input_marks=[{"guid": fixture_guids["slider"], "name": "has space"}],
            output_marks=[],
        )
    assert "has space" in str(excinfo.value)


def test_invalid_name_leaves_file_unmodified(fixture_copy, fixture_guids):
    before = _sha256_bytes(fixture_copy.read_bytes())
    with pytest.raises(marker.MarkError):
        marker.apply_marks(
            fixture_copy,
            input_marks=[{"guid": fixture_guids["slider"], "name": "bad name"}],
            output_marks=[],
        )
    after = _sha256_bytes(fixture_copy.read_bytes())
    assert before == after
    assert list(fixture_copy.parent.glob("*.bak")) == []


# ── guid validation ──────────────────────────────────────────────────


def test_nonexistent_guid_raises_mark_error(fixture_copy):
    fake_guid = str(uuid.uuid4())
    with pytest.raises(marker.MarkError, match=fake_guid):
        marker.apply_marks(
            fixture_copy,
            input_marks=[{"guid": fake_guid, "name": "size"}],
            output_marks=[],
        )


def test_nonexistent_guid_leaves_file_unmodified(fixture_copy):
    before = _sha256_bytes(fixture_copy.read_bytes())
    fake_guid = str(uuid.uuid4())
    with pytest.raises(marker.MarkError):
        marker.apply_marks(
            fixture_copy,
            input_marks=[{"guid": fake_guid, "name": "size"}],
            output_marks=[],
        )
    after = _sha256_bytes(fixture_copy.read_bytes())
    assert before == after
    assert list(fixture_copy.parent.glob("*.bak")) == []


def test_all_or_nothing_valid_guid_not_marked_if_other_invalid(
    fixture_copy, fixture_guids
):
    """One valid + one invalid guid in the same call -> neither gets written."""
    fake_guid = str(uuid.uuid4())
    before = _sha256_bytes(fixture_copy.read_bytes())
    with pytest.raises(marker.MarkError):
        marker.apply_marks(
            fixture_copy,
            input_marks=[
                {"guid": fixture_guids["slider"], "name": "size"},
                {"guid": fake_guid, "name": "other"},
            ],
            output_marks=[],
        )
    after = _sha256_bytes(fixture_copy.read_bytes())
    assert before == after


def test_guid_case_insensitive_matching(fixture_copy, fixture_guids):
    """Guids must be matched case-insensitively: an uppercase form of an
    existing InstanceGuid must be accepted and produce a working mark."""
    upper_slider = fixture_guids["slider"].upper()
    assert upper_slider != fixture_guids["slider"]  # guard: test is meaningful

    result = marker.apply_marks(
        fixture_copy,
        input_marks=[{"guid": upper_slider, "name": "size"}],
        output_marks=[],
    )
    assert result.marked_inputs == ["RH_IN:size"]

    scan = scanner.scan_gh(fixture_copy)
    slider = next(i for i in scan.inputs if i.instance_guid == fixture_guids["slider"])
    assert slider.existing_mark == "RH_IN:size"


# ── duplicate guid within one call ──────────────────────────────────


def test_duplicate_guid_in_same_call_raises_mark_error(fixture_copy, fixture_guids):
    with pytest.raises(marker.MarkError):
        marker.apply_marks(
            fixture_copy,
            input_marks=[
                {"guid": fixture_guids["slider"], "name": "size"},
                {"guid": fixture_guids["slider"], "name": "size2"},
            ],
            output_marks=[],
        )


def test_duplicate_guid_across_input_and_output_raises_mark_error(
    fixture_copy, fixture_guids
):
    with pytest.raises(marker.MarkError):
        marker.apply_marks(
            fixture_copy,
            input_marks=[{"guid": fixture_guids["slider"], "name": "size"}],
            output_marks=[{"guid": fixture_guids["slider"], "name": "size_out"}],
        )


# ── backup=False ─────────────────────────────────────────────────────


def test_backup_false_produces_no_bak_file(fixture_copy, fixture_guids):
    result = marker.apply_marks(
        fixture_copy,
        input_marks=[{"guid": fixture_guids["slider"], "name": "size"}],
        output_marks=[],
        backup=False,
    )
    assert result.backup_path is None
    assert list(fixture_copy.parent.glob("*.bak")) == []


# ── integration: /io + /grasshopper against live Rhino.Compute ──────


@pytest.mark.integration
class TestMarkerIntegration:
    @pytest.fixture(autouse=True)
    def _require_compute(self):
        from hoger.core import compute_client

        if not compute_client.health():
            pytest.skip("Rhino.Compute is not running at the configured COMPUTE_URL")

    def test_io_recognizes_marks_and_grasshopper_uses_injected_value(
        self, fixture_copy, fixture_guids
    ):
        from hoger.core import compute_client
        from hoger.core import trees

        marker.apply_marks(
            fixture_copy,
            input_marks=[{"guid": fixture_guids["slider"], "name": "size"}],
            output_marks=[{"guid": fixture_guids["panel"], "name": "report"}],
        )

        io_response = compute_client.io_query(str(fixture_copy))
        input_names = {i["Name"]: i for i in io_response["Inputs"]}
        output_names = {o["Name"] for o in io_response["Outputs"]}

        assert "RH_IN:size" in input_names
        assert "RH_OUT:report" in output_names

        size_input = input_names["RH_IN:size"]
        assert size_input.get("Minimum") == 0.0 or size_input.get("Minimum") == 0
        assert size_input.get("Maximum") == 10.0

        tree_payload = trees.scalar_tree("RH_IN:size", 7.5)
        eval_response = compute_client.evaluate(str(fixture_copy), [tree_payload])

        values = eval_response.get("values", [])
        assert values, f"no values in eval response: {eval_response}"

        report_value = None
        for entry in values:
            inner = entry.get("InnerTree", {})
            for branch_items in inner.values():
                for item in branch_items:
                    data = item.get("data")
                    if data is not None and "7.5" in str(data):
                        report_value = data
        assert report_value is not None, f"7.5 not found in output values: {values}"
