"""Check the BandUp templates with Switch's own template linter and room models.

    git clone --depth 1 https://github.com/sandbox-quantum/switch /tmp/switch
    pip install pyyaml pydantic sqlalchemy greenlet cryptography pydantic-settings httpx markdown
    python scripts/validate_templates.py /tmp/switch/core templates/*.template.yaml

It lints each document exactly as Switch Console and the server do, renders
it with sample inputs, and validates the rendered rooms, docs, aliases and
links against the server's GroupSpec model.
"""

import sys

import yaml


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    sys.path.insert(0, sys.argv[1])
    from switch_core.rooms_yaml import GroupSpec, interpolate
    from switch_core.template_lint import lint_template

    sample = {"team": "bandup", "learner": "Omar", "module": "Academic", "target_band": "7.0",
              "test_date": "2026-12-12", "proctor": "proctor", "bridge": "Discord", "$creator": "omar"}
    failed = False
    for path in sys.argv[2:]:
        text = open(path, encoding="utf-8").read()
        result = lint_template(text)
        data = yaml.safe_load(text)
        rendered = interpolate({k: data[k] for k in ("group", "rooms", "links") if k in data}, sample)
        group = GroupSpec.model_validate(rendered)
        status = "OK" if not result.errors else "ERRORS"
        failed |= bool(result.errors)
        print(f"{status:6} {path}")
        for e in result.errors:
            print(f"   error: {e}")
        for w in result.warnings:
            print(f"   warning: {w}")
        for room in group.rooms:
            print(f"   room {room.name}: agents {room.agents}, aliases {room.aliases}, "
                  f"docs {[d.name for d in room.docs]}")
        print(f"   links: {[(l.from_, l.to) for l in group.links]}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
