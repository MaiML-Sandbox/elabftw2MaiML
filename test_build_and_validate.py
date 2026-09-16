import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")

from lxml import etree
from elabftw2maiml import (
    MaimlBuilder, ExperimentData, Party, PropertyValue, LinkedItem, Step, FileRef,
)

exp = ExperimentData(
    elab_id=123,
    title="PMMA薄膜のFT-IR測定",
    date=datetime(2026, 7, 20, 10, 0, 0, tzinfo=timezone.utc),
    owner=Party(key="elabftw-user-42@elab.example.org", name="ns1:Yamada Taro"),
    body_text="PMMA薄膜サンプルのFT-IRスペクトルを測定した。",
    steps=[
        Step(elab_id=501, title="サンプル準備",
             started_at=datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc),
             finished_at=datetime(2026, 7, 20, 9, 20, tzinfo=timezone.utc),
             is_finished=True),
        Step(elab_id=502, title="FT-IR測定",
             started_at=datetime(2026, 7, 20, 9, 30, tzinfo=timezone.utc),
             finished_at=datetime(2026, 7, 20, 9, 45, tzinfo=timezone.utc),
             is_finished=True),
    ],
    materials=[
        LinkedItem(elab_id=88, title="PMMAサンプル", category="Sample", properties=[
            PropertyValue(key="ns1:SampleID", xsi_type="stringType", value="SA-001"),
            PropertyValue(key="ns1:Thickness", xsi_type="doubleType", value="0.125",
                           units="mm", format_string="0.000"),
        ]),
    ],
    condition_properties=[
        PropertyValue(key="ns1:Resolution", xsi_type="doubleType", value="4.00",
                       format_string="0.00", units="cm-1"),
        PropertyValue(key="ns1:Scans", xsi_type="intType", value="32"),
    ],
    result_properties=[
        PropertyValue(key="ns1:Note", xsi_type="stringType", value="良好なスペクトルが得られた"),
    ],
    uploads=[
        FileRef(filename="spectrum.csv",
                uri="https://elab.example.org/app/download.php?f=spectrum.csv",
                hash_b64="q6L6q6L6q6L6q6L6q6L6q6L6q6L6q6L6q6L6q6L6q6I=",
                hash_method="SHA-256"),
    ],
    elab_url="https://elab.example.org/experiments.php?mode=view&id=123",
)

builder = MaimlBuilder(ns_prefix="ns1", ns_uri="https://example.org/maiml/mylab",
                        elab_host="elab.example.org")
xml_bytes = builder.to_bytes(exp)

out_path = "sample_output.maiml"
with open(out_path, "wb") as f:
    f.write(xml_bytes)
print(f"wrote {out_path} ({len(xml_bytes)} bytes)")

# --- second sample: creator/vendor/instrument derived from custom fields ---
from elabftw2maiml.model import Party as _Party

exp2 = ExperimentData(
    elab_id=124,
    title="汎用天秤による秤量",
    date=datetime(2026, 7, 21, 9, 0, 0, tzinfo=timezone.utc),
    owner=Party(key="elabftw-user-7@elab.example.org", name="Suzuki Hanako"),
    creator=_Party(key="elabftw-device:FT-IR IRAffinity-1S", name="FT-IR IRAffinity-1S"),
    vendor=_Party(key="elabftw-device-vendor:Shimadzu", name="Shimadzu"),
    instruments=[_Party(key="elabftw-instrument:FT-IR IRAffinity-1S", name="FT-IR IRAffinity-1S")],
    steps=[Step(elab_id=601, title="秤量", finished_at=datetime(2026, 7, 21, 9, 5, tzinfo=timezone.utc),
                is_finished=True)],
)
xml_bytes2 = builder.to_bytes(exp2)
with open("sample_output_2.maiml", "wb") as f:
    f.write(xml_bytes2)

# --- validate against the uploaded XSD ---
schema_doc = etree.parse("schemas/maiml.xsd")
schema = etree.XMLSchema(schema_doc)

for label, xb in [("sample_output.maiml", xml_bytes), ("sample_output_2.maiml", xml_bytes2)]:
    doc = etree.fromstring(xb)
    ok = schema.validate(doc)
    print(f"Schema valid ({label}):", ok)
    if not ok:
        for err in schema.error_log:
            print(" -", err)
        sys.exit(1)

print("OK: both sample outputs are valid against maiml.xsd")
