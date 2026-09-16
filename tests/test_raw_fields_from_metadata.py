"""`elabftw2maiml/elabftw_client.py` の `raw_fields_from_metadata()` /
`ElabftwClient.fetch_raw_custom_fields()` (Phase 5-3: 実際のeLabFTWの
Extra Fieldsを`interpretation.field_mapping.RawField`へ変換する) の単体テスト。
"""
from elabftw2maiml.elabftw_client import ElabftwClient, raw_fields_from_metadata
from elabftw2maiml.interpretation import RawField

from .fixtures import FIXTURES, make_client


class TestRawFieldsFromMetadata:
    def test_converts_extra_fields_to_raw_fields(self):
        metadata = {
            "extra_fields": {
                "AcceleratingVoltage(kV)": {"type": "number", "value": "200", "group_id": 1},
                "sampleID": {"type": "text", "value": "HS-100MG001", "group_id": 2},
            },
            "elabftw": {
                "extra_fields_groups": [
                    {"id": 1, "name": "CONDITION"},
                    {"id": 2, "name": "MATERIAL"},
                ],
            },
        }

        raw_fields = raw_fields_from_metadata(metadata)

        by_name = {f.name: f for f in raw_fields}
        assert by_name["AcceleratingVoltage(kV)"].value == "200"
        assert by_name["AcceleratingVoltage(kV)"].group == "CONDITION"
        assert by_name["AcceleratingVoltage(kV)"].unit is None
        assert by_name["sampleID"].group == "MATERIAL"

    def test_field_without_group_has_none_group(self):
        metadata = {
            "extra_fields": {
                "Scans": {"type": "number", "value": "32", "group_id": None},
            },
        }
        raw_fields = raw_fields_from_metadata(metadata)
        assert raw_fields[0].group is None

    def test_no_metadata_returns_empty_list(self):
        assert raw_fields_from_metadata(None) == []

    def test_no_extra_fields_returns_empty_list(self):
        assert raw_fields_from_metadata({}) == []

    def test_all_entries_are_raw_field_instances(self):
        metadata = {"extra_fields": {"X": {"type": "text", "value": "y", "group_id": None}}}
        raw_fields = raw_fields_from_metadata(metadata)
        assert all(isinstance(f, RawField) for f in raw_fields)


class TestFetchRawCustomFields:
    def test_fetches_and_converts_real_fixture(self):
        fixture_name = "fixture_a"
        builder_fn = FIXTURES[fixture_name]
        fake_experiment, raw_experiment_json, raw_items_json_by_id = builder_fn()
        client = make_client(ElabftwClient, fake_experiment, raw_experiment_json, raw_items_json_by_id)

        raw_fields = client.fetch_raw_custom_fields(fake_experiment.id)

        by_name = {f.name: f for f in raw_fields}
        assert "Resolution" in by_name
        assert by_name["Resolution"].group == "CONDITION"
        assert by_name["SampleNote"].group == "MATERIAL"
        assert by_name["AnalysisSummary"].group == "RESULT"
        # グループ無しのフィールドは group=None のまま (CONDITIONへのフォールバックは
        # field_mapping.py/interpretation.pipeline側の責務であり、ここでは行わない)
        assert by_name["Scans"].group is None
