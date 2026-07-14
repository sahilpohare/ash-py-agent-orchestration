# Relationships

Declare how resources relate to each other. The framework uses these for graph validation, route nesting, and introspection.

## belongs_to

A resource belongs to a parent. Requires a FK column on the child.

```python
from ironbridge.shared.framework import belongs_to

class Job(Resource):
    branch_id: Mapped[str] = mapped_column(String, nullable=False)
    branch = belongs_to("Branch", key="branch_id")
```

`key` is explicit -- the FK column on this resource. No guessing from names.

## has_many

A resource has many children.

```python
from ironbridge.shared.framework import has_many

class Branch(Resource):
    jobs = has_many("Job", key="branch_id")
```

`key` is the FK column on the child resource (Job.branch_id).

## has_one

Like has_many but expects at most one child.

```python
class Lead(Resource):
    preferences = has_one("LeadPreferences", key="lead_id")
```

## references

A nullable FK to another resource. Not a parent-child relationship -- just a reference.

```python
class Job(Resource):
    property_id: Mapped[str | None] = mapped_column(String, nullable=True)
    property = references("Property", key="property_id")
```

Use `references` when the FK is optional and the target isn't a parent.

## many_to_many

Through a join table.

```python
from ironbridge.shared.framework import many_to_many

class User(Resource):
    roles = many_to_many("Role", through="UserRole", source_key="user_id", target_key="role_id")
```

## String vs. class references

Both work:

```python
branch = belongs_to("Branch", key="branch_id")   # string -- resolved at graph.build()
branch = belongs_to(Branch, key="branch_id")      # class -- resolved immediately
```

Use strings to avoid circular imports between modules.

## ResourceGraph

At startup, the framework builds a graph of all relationships:

```python
from ironbridge.shared.framework import ResourceGraph

graph = ResourceGraph()
graph.build()

graph.roots()                    # Resources with no belongs_to
graph.children_of(Branch)        # [Job]
graph.parent_of(Job)             # Branch
graph.ancestry(Invoice)          # [Job, Branch]
graph.relationships_for(Job)     # all relationships on Job
graph.validate()                 # check for missing FK fields, unresolved targets
```

The graph is built automatically by `Ironbridge(app, modules)`. You only use it directly for introspection or testing.
