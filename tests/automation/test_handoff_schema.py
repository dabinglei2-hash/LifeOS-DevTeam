"""Coordinator/delivery integration must preserve exact wire hashes and scope."""
import copy
import unittest
from lads_automation import delivery


class HandoffSchemaTests(unittest.TestCase):
    def test_actual_coordinator_schema_normalizes_after_exact_verification(self):
        before = {"index.html": "a" * 64, "removed.txt": "b" * 64}
        after = {"index.html": "c" * 64, "added.txt": "d" * 64}
        wire = delivery.BASE.changed_files(before, after)
        result = delivery._validated_handoff_changes(before, after, wire)
        self.assertEqual(["added", "modified", "deleted"], [x["kind"] for x in result])
        self.assertEqual("a" * 64, result[1]["old_sha256"])
        self.assertEqual("c" * 64, result[1]["new_sha256"])

    def test_tampered_wire_is_never_normalized_or_accepted(self):
        before, after = {"index.html": "a" * 64}, {"index.html": "b" * 64}
        wire = delivery.BASE.changed_files(before, after)
        bad = []
        for field, value in (("path", "other.html"), ("change", "added"),
                             ("before_sha256", "0" * 64), ("after_sha256", "0" * 64),
                             ("extra", "undeclared")):
            changed = copy.deepcopy(wire)
            changed[0][field] = value
            bad.append(changed)
        bad.extend((wire + wire, [], delivery._changed_files(before, after)))
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(delivery.Stage3DeliveryError):
                    delivery._validated_handoff_changes(before, after, value)
