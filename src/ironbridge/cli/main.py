"""
ironbridge CLI.

    ironbridge generate resource Job --module maintenance --fields "state:str description:str"
    ironbridge generate workflow Job --module maintenance --signals "start:create quote_received approval"
    ironbridge generate module maintenance --resources "Job Invoice"
    ironbridge generate app --name MyApp --spec spec.yaml
"""
import argparse
import json
import sys
from pathlib import Path

from .generators import generate_app, generate_module, generate_resource, generate_workflow
from .components.registry import get, list_all
from .new import new_project


def parse_fields(fields_str: str) -> list[dict]:
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


def load_spec(spec_path: str) -> dict:
    """Load app spec from YAML or JSON."""
    path = Path(spec_path)
    content = path.read_text()

    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml
            return yaml.safe_load(content)
        except ImportError:
            print("PyYAML not installed. Use JSON spec or: pip install pyyaml")
            sys.exit(1)
    else:
        return json.loads(content)


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
    res.add_argument("--actions", "-a", default="get,list", help="Default actions")
    res.add_argument("--no-tenant", action="store_true")
    res.add_argument("--base-path", default="src/lightwork")
    res.add_argument("--test-path", default="tests")

    # generate workflow
    wf = gen_sub.add_parser("workflow", aliases=["w"], help="Generate a Workflow Resource")
    wf.add_argument("name", help="Workflow name (CamelCase)")
    wf.add_argument("--module", "-m", required=True, help="Module name")
    wf.add_argument("--fields", "-f", default="", help="Fields")
    wf.add_argument("--signals", "-s", default="start:create", help="Signals")
    wf.add_argument("--relationships", "--rels", default="", help="Relationships")
    wf.add_argument("--actions", "-a", default="get,list", help="Default actions")
    wf.add_argument("--no-tenant", action="store_true")
    wf.add_argument("--base-path", default="src/lightwork")
    wf.add_argument("--test-path", default="tests")

    # generate module
    mod = gen_sub.add_parser("module", aliases=["m"], help="Generate a Module")
    mod.add_argument("name", help="Module name (CamelCase)")
    mod.add_argument("--resources", "-r", default="", help="Resource names")
    mod.add_argument("--base-path", default="src/lightwork")
    mod.add_argument("--test-path", default="tests")

    # generate app
    app_gen = gen_sub.add_parser("app", aliases=["a"], help="Generate a full app")
    app_gen.add_argument("--name", "-n", default="App", help="App name (CamelCase)")
    app_gen.add_argument("--spec", "-s", help="Path to spec file (YAML or JSON)")
    app_gen.add_argument("--connectors", "-c", default="", help="Connectors (space-separated)")
    app_gen.add_argument("--output", "-o", default=".", help="Output directory")

    # add command (shadcn-style)
    add_parser = sub.add_parser("add", help="Add a component (shadcn-style, copies code into your project)")
    add_parser.add_argument("component", nargs="?", help="Component name (tenancy, threads, auth, soft-delete, timestamps)")
    add_parser.add_argument("--list", "-l", action="store_true", help="List available components")
    add_parser.add_argument("--app-name", default="lightwork", help="App name for variable substitution")
    add_parser.add_argument("--output", "-o", default=".", help="Output directory")
    add_parser.add_argument("--force", "-f", action="store_true", help="Overwrite existing files")

    # validate command
    val = sub.add_parser("validate", aliases=["v"], help="Validate all resources, signals, and graph")
    val.add_argument("--strict", action="store_true", help="Exit 1 on warnings too")
    val.add_argument("--app", default=None, help="App module to import (e.g. lightwork.app)")

    # new command
    new_parser = sub.add_parser("new", help="Create a new ironbridge project")
    new_parser.add_argument("name", help="Project name (CamelCase)")
    new_parser.add_argument("--output", "-o", default=None, help="Output directory (defaults to snake_case of name)")

    args = parser.parse_args()

    if args.command in ("validate", "v"):
        # Import the app to trigger resource registration
        if args.app:
            try:
                __import__(args.app)
            except ImportError as e:
                print(f"  Error importing {args.app}: {e}")
                sys.exit(1)

        from ironbridge.shared.framework.graph import ResourceGraph
        from ironbridge.shared.framework.validation import validate_full

        graph = ResourceGraph()
        graph.build()

        result = validate_full(graph=graph)
        result.print()

        if result.errors:
            sys.exit(1)
        if args.strict and result.warnings:
            sys.exit(1)
        return

    elif args.command == "new":
        import re
        snake = re.sub(r"(?<!^)(?=[A-Z])", "_", args.name).lower()
        output = args.output or snake
        Path(output).mkdir(parents=True, exist_ok=True)

        # Copy ironbridge framework into the project (only shared + cli)
        import shutil
        from importlib.util import find_spec
        spec = find_spec("ironbridge")
        if spec and spec.submodule_search_locations:
            framework_src = Path(spec.submodule_search_locations[0])
            dest = Path(output) / "src" / "ironbridge"
            dest.mkdir(parents=True, exist_ok=True)

            # Copy only framework essentials
            for subdir in ["shared", "cli"]:
                src_dir = framework_src / subdir
                if src_dir.exists():
                    shutil.copytree(src_dir, dest / subdir, dirs_exist_ok=True)

            # Copy __init__.py
            init = framework_src / "__init__.py"
            if init.exists():
                shutil.copy2(init, dest / "__init__.py")

            # Copy ironbridge_web
            web_spec = find_spec("ironbridge_web")
            if web_spec and web_spec.submodule_search_locations:
                web_src = Path(web_spec.submodule_search_locations[0])
                web_dest = Path(output) / "src" / "ironbridge_web"
                if not web_dest.exists():
                    shutil.copytree(web_src, web_dest, dirs_exist_ok=True)

        # Generate project files
        created = new_project(args.name, output)

        print(f"\n  Created project '{args.name}' in ./{output}/\n")
        print(f"  {len(created)} files generated.\n")
        print(f"  Next steps:\n")
        print(f"    cd {output}")
        print(f"    uv venv && uv pip install -e '.[dev]'")
        print(f"    docker compose up -d postgres")
        print(f"    PYTHONPATH=src python -m {snake}_web.main migrate")
        print(f"    PYTHONPATH=src python -m {snake}_web.main")
        print(f"    open http://localhost:8000/docs")
        return

    elif args.command == "add":
        if args.list or not args.component:
            print("\nAvailable components:\n")
            for comp in list_all():
                deps = f" (requires: {', '.join(comp.depends)})" if comp.depends else ""
                print(f"  {comp.name:15s} {comp.description}{deps}")
            print(f"\nUsage: ironbridge add <component> [--app-name <name>]")
            return

        component = get(args.component)
        if not component:
            print(f"Unknown component: {args.component}")
            print(f"Run 'ironbridge add --list' to see available components.")
            return

        # Check dependencies
        for dep in component.depends:
            dep_comp = get(dep)
            if dep_comp:
                print(f"  dependency: {dep}")

        # Render and write files
        variables = {"app_name": args.app_name}
        rendered = component.render(variables)

        created = []
        skipped = []
        for rel_path, content in rendered.items():
            full_path = Path(args.output) / rel_path
            if full_path.exists() and not args.force:
                skipped.append(str(full_path))
                continue
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content)
            created.append(str(full_path))

        # Also install dependencies
        for dep in component.depends:
            dep_comp = get(dep)
            if dep_comp:
                dep_rendered = dep_comp.render(variables)
                for rel_path, content in dep_rendered.items():
                    full_path = Path(args.output) / rel_path
                    if full_path.exists() and not args.force:
                        skipped.append(str(full_path))
                        continue
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    full_path.write_text(content)
                    created.append(str(full_path))

        print(f"\n  Added '{args.component}':")
        for path in created:
            print(f"    created: {path}")
        for path in skipped:
            print(f"    skipped: {path} (exists, use --force to overwrite)")
        if not created and not skipped:
            print("    (nothing to do)")
        return

    elif args.command in ("generate", "g"):
        created = []

        if args.type in ("resource", "r"):
            created = generate_resource(
                name=args.name, module=args.module,
                fields=parse_fields(args.fields),
                relationships=parse_relationships(args.relationships),
                default_actions=args.actions.split(","),
                tenant_scoped=not args.no_tenant,
                base_path=args.base_path, test_path=args.test_path,
            )

        elif args.type in ("workflow", "w"):
            created = generate_workflow(
                name=args.name, module=args.module,
                fields=parse_fields(args.fields),
                signals=parse_signals(args.signals),
                relationships=parse_relationships(args.relationships),
                default_actions=args.actions.split(","),
                tenant_scoped=not args.no_tenant,
                base_path=args.base_path, test_path=args.test_path,
            )

        elif args.type in ("module", "m"):
            created = generate_module(
                name=args.name,
                resources=args.resources.split() if args.resources else [],
                base_path=args.base_path, test_path=args.test_path,
            )

        elif args.type in ("app", "a"):
            if args.spec:
                spec = load_spec(args.spec)
                created = generate_app(
                    name=spec.get("name", args.name),
                    modules=spec.get("modules", []),
                    connectors=spec.get("connectors", []),
                    base_path=args.output,
                )
            else:
                connectors = args.connectors.split() if args.connectors else []
                created = generate_app(
                    name=args.name,
                    modules=[],
                    connectors=connectors,
                    base_path=args.output,
                )
        else:
            gen.print_help()
            return

        print(f"\n  Generated {len(created)} files:")
        for path in created:
            print(f"    {path}")

        if not created:
            print("  (all files already exist)")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
