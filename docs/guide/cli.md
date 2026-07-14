# CLI

## Create a project

```bash
ironbridge new MyApp
```

Creates a full runnable project: `pyproject.toml`, `docker-compose.yml`, `alembic/`, app scaffold, web entry point, tests.

## Generate scaffolds

### Resource

```bash
ironbridge generate resource Property --module catalog \
  --fields "address:str postcode:str? bedrooms:int=0 rent:Decimal?" \
  --relationships "branch:belongs_to:Branch" \
  --actions "get,list,create"
```

Field syntax: `name:type`, `name:type?` (nullable), `name:type=default`.

Types: `str`, `int`, `float`, `bool`, `datetime`, `Decimal`.

### Workflow

```bash
ironbridge generate workflow Job --module maintenance \
  --fields "state:str=opened description:str urgency:str=routine" \
  --signals "start:create quote_received approval" \
  --relationships "branch:belongs_to:Branch invoices:has_many:Invoice"
```

Signal syntax: `name` (mid-workflow), `name:create` (starts the workflow).

### Module

```bash
ironbridge generate module Maintenance --resources "Job Invoice"
```

## Add components

Shadcn-style: copies code into your project. You own it, you customize it.

```bash
ironbridge add --list              # see available
ironbridge add tenancy             # adds Branch, PlatformUser, BranchMember
ironbridge add threads             # adds Thread, Message
ironbridge add auth                # adds JWT resolver
```

Components may have dependencies. `ironbridge add tenancy` also installs its dependencies.

## Validate

Check all resources, signals, relationships, and graph for problems:

```bash
ironbridge validate --app my_app.app
ironbridge validate --strict       # exit 1 on warnings too
```

Catches:
- Missing FK fields
- Unresolved relationship targets
- Signals without handlers
- Route collisions
- Unhandled signals (static analysis)
