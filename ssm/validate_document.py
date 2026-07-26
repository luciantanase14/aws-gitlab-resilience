#!/usr/bin/env python3
"""Structural checks for the Automation document.

Parsing alone proves very little. These are the mistakes that survive a YAML
parse and fail at run time: a dangling onFailure target, a step referencing an
output of a step that runs later, or a parameter that is declared and never used.
"""

import re
import sys

import yaml

REQUIRED_TOP_LEVEL = ("schemaVersion", "description", "parameters", "mainSteps")
REFERENCE = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)\s*\}\}")


def walk_strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from walk_strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk_strings(value)


def check(path):
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)

    errors = []

    for key in REQUIRED_TOP_LEVEL:
        if key not in doc:
            errors.append(f"missing top level key: {key}")
    if errors:
        return errors

    if not str(doc["schemaVersion"]).startswith("0.3"):
        errors.append(f"schemaVersion {doc['schemaVersion']} is not an Automation document")

    steps = doc["mainSteps"]
    names = [s["name"] for s in steps]
    params = set(doc["parameters"])

    if len(names) != len(set(names)):
        errors.append("duplicate step names")

    for index, step in enumerate(steps):
        target = step.get("onFailure", "")
        if isinstance(target, str) and target.startswith("step:"):
            wanted = target.split(":", 1)[1]
            if wanted not in names:
                errors.append(f"{step['name']}: onFailure points at unknown step {wanted}")
            elif names.index(wanted) <= index:
                errors.append(f"{step['name']}: onFailure points backwards at {wanted}")

        for text in walk_strings(step.get("inputs", {})):
            for ref in REFERENCE.findall(text):
                if "." in ref:
                    producer = ref.split(".", 1)[0]
                    if producer not in names:
                        errors.append(f"{step['name']}: references unknown step {producer}")
                    elif names.index(producer) >= index:
                        errors.append(f"{step['name']}: uses output of {producer}, which runs later")
                elif ref not in params:
                    errors.append(f"{step['name']}: references undeclared parameter {ref}")

    # assumeRole and other top level fields reference parameters too.
    outside_steps = {k: v for k, v in doc.items() if k != "mainSteps"}
    used = {
        ref
        for source in [outside_steps] + [s.get("inputs", {}) for s in steps]
        for text in walk_strings(source)
        for ref in REFERENCE.findall(text)
    }
    for unused in sorted(params - used):
        errors.append(f"parameter {unused} is declared but never used")

    return errors


def main():
    failed = False
    for path in sys.argv[1:] or ["ssm/restore-drill.yml"]:
        errors = check(path)
        if errors:
            failed = True
            print(f"{path}")
            for error in errors:
                print(f"  {error}")
        else:
            print(f"{path}: ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
