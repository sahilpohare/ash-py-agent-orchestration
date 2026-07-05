"""
ironbridge CLI.

    ironbridge generate resource Job --module maintenance --fields "state:str description:str"
    ironbridge generate workflow Job --module maintenance --fields "state:str" --signals "start:create quote_received approval"
    ironbridge generate module maintenance --resources "Job Invoice"
"""
import argparse
import sys

from .generators import generate_module, generate_resource, generate_workflow


def parse_fields(fields_str: str) -> list[dict]:
    """Parse "name:type name:type?=default" into field dicts."""
    if not fields_str:
        return []
    fields = []
    for part in fields_str.split():
        nullable = "?" in part
        part = part.replace("?", "")

        if "=" in part:
            field_part, default = part.split("=", 1)
        else:
            field_part, default = part, None

        if ":" in field_part:
            name, ftype = field_part.split(":", 1)
        else:
            name, ftype = field_part, "str"

        f = {"name": name, "type": ftype, "nullable": nullable}
        if default is not None:
            f["default"] = default
        fields.append(f)
    return fields


def parse_signals(signals_str: str) -> list[dict]:
    """Parse "start:create quote_received approval" into signal dicts."""
    if not signals_str:
        return [{"name": "start", "create": True}]
    signals = []
    for part in signals_str.split():
        if ":" in part:
            name, kind = part.split(":", 1)
            signals.append({"name": name, "create": kind == "create"})
        else:
            signals.append({"name": part, "create": False})
    return signals


def parse_relationships(rels_str: str) -> list[dict]:
    """Parse "branch:belongs_to:Branch invoices:has_many:Invoice" into rel dicts."""
    if not rels_str:
        return []
    rels = []
    for part in rels_str.split():
        pieces = part.split(":")
        if len(pieces) >= 3:
            rels.append({"name": pieces[0], "type": pieces[1], "target": pieces[2]})
        elif len(pieces) == 2:
            rels.append({"name": pieces[0], "type": "belongs_to", "target": pieces[1]})
    return rels


def main():
    parser = argparse.ArgumentParser(prog="ironbridge", description="Ironbridge framework CLI")
    sub = parser.add_subparsers(dest="command")

    gen = sub.add_parser("generate", aliases=["g"], help="Generate scaffolds")
    gen_sub = gen.add_subparsers(dest="type")

    # generate resource
    res = gen_sub.add_parser("resource", aliases=["r"], help="Generate a Resource")
    res.add_argument("name", help="Resource name (CamelCase)")
    res.add_argument("--module", "-m", required=True, help="Module name")
    res.add_argument("--fields", "-f", default="", help="Fields: 'name:type name:type?=default'")
    res.add_argument("--relationships", "--rels", default="", help="Relationships: 'name:type:Target'")
    res.add_argument("--actions", "-a", default="get,list", help="Default actions (comma-separated)")
    res.add_argument("--no-tenant", action="store_true", help="Not tenant-scoped")
    res.add_argument("--base-path", default="src/lightwork")
    res.add_argument("--test-path", default="tests")

    # generate workflow
    wf = gen_sub.add_parser("workflow", aliases=["w"], help="Generate a Workflow Resource")
    wf.add_argument("name", help="Workflow name (CamelCase)")
    wf.add_argument("--module", "-m", required=True, help="Module name")
    wf.add_argument("--fields", "-f", default="", help="Fields")
    wf.add_argument("--signals", "-s", default="start:create", help="Signals: 'start:create quote_received approval'")
    wf.add_argument("--relationships", "--rels", default="", help="Relationships")
    wf.add_argument("--actions", "-a", default="get,list", help="Default actions")
    wf.add_argument("--no-tenant", action="store_true", help="Not tenant-scoped")
    wf.add_argument("--base-path", default="src/lightwork")
    wf.add_argument("--test-path", default="tests")

    # generate module
    mod = gen_sub.add_parser("module", aliases=["m"], help="Generate a Module")
    mod.add_argument("name", help="Module name (CamelCase)")
    mod.add_argument("--resources", "-r", default="", help="Resource names (space-separated)")
    mod.add_argument("--base-path", default="src/lightwork")
    mod.add_argument("--test-path", default="tests")

    args = parser.parse_args()

    if args.command in ("generate", "g"):
        if args.type in ("resource", "r"):
            created = generate_resource(
                name=args.name,
                module=args.module,
                fields=parse_fields(args.fields),
                relationships=parse_relationships(args.relationships),
                default_actions=args.actions.split(","),
                tenant_scoped=not args.no_tenant,
                base_path=args.base_path,
                test_path=args.test_path,
            )
        elif args.type in ("workflow", "w"):
            created = generate_workflow(
                name=args.name,
                module=args.module,
                fields=parse_fields(args.fields),
                signals=parse_signals(args.signals),
                relationships=parse_relationships(args.relationships),
                default_actions=args.actions.split(","),
                tenant_scoped=not args.no_tenant,
                base_path=args.base_path,
                test_path=args.test_path,
            )
        elif args.type in ("module", "m"):
            created = generate_module(
                name=args.name,
                resources=args.resources.split() if args.resources else [],
                base_path=args.base_path,
                test_path=args.test_path,
            )
        else:
            gen.print_help()
            return

        for path in created:
            print(f"  created: {path}")

        if not created:
            print("  (all files already exist)")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
