"""The committed API contract, and the breaking-change classifier.

An integrator holding the new client library needs to know which parts of the
surface they can build on. The answer is made concrete by committing
openapi.json and refusing to let it drift silently: this test fails if the
app's surface no longer matches the committed file, so a surface change is
always a reviewed commit, never an accident.

The classifier tests pin what "breaking" means -- from the CLIENT's side, a
change that can make a request that worked yesterday fail today.
"""
from __future__ import annotations

import json
import pathlib

from fws import app as app_mod
from fws import contract

SPEC = pathlib.Path(__file__).resolve().parent.parent / "openapi.json"


class TestTheCommittedSpecIsHonest:
    def test_openapi_json_is_committed(self):
        assert SPEC.exists(), (
            "openapi.json is missing; run tools/check_contract.py --write")

    def test_its_surface_matches_the_live_app(self):
        """The gate, robust across the CI version matrix.

        Compared SEMANTICALLY, not byte-for-byte: FastAPI/pydantic emit
        cosmetically different JSON on different dependency sets (this test
        was byte-exact once and passed on 3.12/3.13 while failing on 3.11 for
        exactly that reason). What a client actually depends on -- the set of
        operations and their required inputs -- must match; wording and
        formatting may differ between environments without being a contract
        change.

        `tools/check_contract.py` (run in the guards job) checks the same
        surface equivalence; `--write` regenerates the committed bytes when a
        change is intentional. Here we check the thing that must hold on every
        Python in the matrix.
        """
        committed = json.loads(SPEC.read_text())
        live = contract.snapshot(app_mod.app)
        changes = contract.classify_changes(committed, live)
        assert changes == {"breaking": [], "additive": []}, (
            "the API surface has drifted from openapi.json:\n"
            f"  {changes}\n"
            "If intended, run `python tools/check_contract.py --write` and "
            "commit; if not, you changed the API by accident.")

    def test_the_snapshot_omits_the_volatile_version(self):
        """info.version tracks the package version; leaving it in would make
        every release a spec change and bury the diffs that matter."""
        assert "version" not in contract.snapshot(app_mod.app).get("info", {})

    def test_the_dump_is_canonical(self):
        """Sorted keys and a trailing newline, so two runs of the same
        surface are byte-identical and git shows only real changes."""
        spec = contract.snapshot(app_mod.app)
        assert contract.dumps(spec) == contract.dumps(spec)
        assert contract.dumps(spec).endswith("\n")


class TestBreakingIsDefinedFromTheClientSide:
    def _base(self):
        return {"paths": {"/api/v1/x": {
            "get": {"responses": {}},
            "post": {"requestBody": {"content": {"application/json": {
                "schema": {"required": ["a"], "properties": {}}}}}},
        }}}

    def test_removing_a_route_is_breaking(self):
        old = self._base()
        new = {"paths": {}}
        changes = contract.classify_changes(old, new)
        assert any("removed: GET /api/v1/x" in c for c in changes["breaking"])

    def test_adding_a_route_is_additive(self):
        old = {"paths": {}}
        new = self._base()
        changes = contract.classify_changes(old, new)
        assert changes["breaking"] == []
        assert any("added:" in c for c in changes["additive"])

    def test_a_new_required_body_field_is_breaking(self):
        old = self._base()
        new = self._base()
        new["paths"]["/api/v1/x"]["post"]["requestBody"]["content"][
            "application/json"]["schema"]["required"] = ["a", "b"]
        changes = contract.classify_changes(old, new)
        assert any("required body field 'b'" in c
                   for c in changes["breaking"])

    def test_relaxing_a_required_field_is_additive(self):
        old = self._base()
        new = self._base()
        new["paths"]["/api/v1/x"]["post"]["requestBody"]["content"][
            "application/json"]["schema"]["required"] = []
        changes = contract.classify_changes(old, new)
        assert changes["breaking"] == []
        assert any("no longer required" in c for c in changes["additive"])

    def test_a_new_required_query_param_is_breaking(self):
        old = self._base()
        new = self._base()
        new["paths"]["/api/v1/x"]["get"]["parameters"] = [
            {"name": "flag", "in": "query", "required": True}]
        changes = contract.classify_changes(old, new)
        assert any("required query param 'flag'" in c
                   for c in changes["breaking"])

    def test_a_path_parameter_does_not_count_as_a_new_requirement(self):
        """Path params are part of the URL, not an added requirement on an
        existing call -- flagging them would make every {id} route a false
        positive."""
        old = self._base()
        new = self._base()
        new["paths"]["/api/v1/x"]["get"]["parameters"] = [
            {"name": "id", "in": "path", "required": True}]
        changes = contract.classify_changes(old, new)
        assert changes["breaking"] == []

    def test_identical_specs_show_no_change(self):
        spec = contract.snapshot(app_mod.app)
        changes = contract.classify_changes(spec, spec)
        assert changes == {"breaking": [], "additive": []}


class TestResolvingRefs:
    def test_a_required_field_behind_a_ref_is_seen(self):
        """FastAPI puts request bodies in components/schemas and $refs them.
        A differ that did not resolve the ref would see no fields at all and
        call every body change additive."""
        old = {
            "paths": {"/x": {"post": {"requestBody": {"content": {
                "application/json": {"schema": {"$ref":
                                                "#/components/schemas/R"}}}}}}},
            "components": {"schemas": {"R": {"required": ["a"]}}},
        }
        new = json.loads(json.dumps(old))
        new["components"]["schemas"]["R"]["required"] = ["a", "b"]
        changes = contract.classify_changes(old, new)
        assert any("required body field 'b'" in c
                   for c in changes["breaking"])
