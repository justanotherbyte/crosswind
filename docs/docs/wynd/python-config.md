---
title: "Configure a Python Worker in cloudflare.config.py"
description: "Write a Worker's config as Python with wynd.config: create one with wynd init, declare bindings, Durable Objects and triggers, pick settings per mode, check the compiled Wrangler config and fix its errors."
---

Write your Worker's config in `cloudflare.config.py` when you want typed
settings and a Python function for each mode. `wynd init` creates this file
for new [crosswind-workers](../workers/overview.md) projects. Wynd compiles it
into a Wrangler config, so `wynd dev`, `wynd deploy` and `wynd types` also work
with it. You can use `wrangler.toml` or `wrangler.jsonc` instead.

Keep your Wrangler file if you also run plain `wrangler` on the project, or if
it uses a binding or setting that `wynd.config` doesn't model. The list is
under [What it doesn't support](config-reference.md#what-it-doesnt-support).

What the Python file adds:

- Values are typed dataclasses, so your editor and type checker catch a
  misspelled field or a missing argument before Wynd runs.
- One function returns the config for each mode, such as `staging`, in place
  of Wrangler's `env` sections.
- You declare a Durable Object class once, by its state, and Wynd writes
  Wrangler's `exports` map. You don't write `migrations`.

> **Warning**
>
> Python config support is new. Its classes and fields may change between
> minor releases.

## Before you begin

- Wynd 0.3.0 or later. `uvx wynd` runs the latest release, and
  `uvx wynd@<version>` pins a release.
- [uv](https://docs.astral.sh/uv/) 0.13.4 or later. Check with `uv --version`.
- Node.js on `PATH`, for `npx`. Wynd runs Wrangler 4.129.0 through it.

## Create a project

```sh
uvx wynd init hello
```

```text
wynd: wrote src/env.py
wynd: created hello. Run `wynd dev` in /home/you/hello to serve it.
```

`wynd init [directory]` writes into `directory`, or into the current
directory without one. The Worker's name is the directory name in lowercase,
with each run of other characters replaced by `-`.

In a terminal, `wynd init` asks which Workers SDK to build on and which add-on
SDKs to add. Flags answer the same questions, and it asks only about the ones you
leave out. Without a terminal it uses the defaults: crosswind-workers and no
add-ons.

| Flag                                  | Adds to `dependencies`                       |
| ------------------------------------- | -------------------------------------------- |
| `--sdk crosswind-workers` (default)   | `crosswind-workers`                          |
| `--sdk workers-runtime-sdk`           | `workers-runtime-sdk`, with no `src/env.py` |
| `--containers` / `--no-containers`    | `containers-py`, the [Containers](../containers/overview.md) SDK |
| `--agents` / `--no-agents`            | `cf-agents`, the [Agents](../agents/overview.md) SDK |

It writes:

| File                   | Contents                                                                 |
| ---------------------- | ------------------------------------------------------------------------ |
| `cloudflare.config.py` | The Worker's config                                                      |
| `pyproject.toml`       | `crosswind-workers` in `dependencies`, and `wynd>=<the running version>` in the `dev` group |
| `.python-version`      | `3.14`, the Python the Worker runs                                        |
| `src/main.py`          | A `Default` entrypoint that returns `Hello from Python!`                  |
| `src/env.py`           | The generated `Env` class                                                 |
| `.gitignore`           | `.venv/` and `.wynd/`, added to an existing file |

The config sets the Worker's name, entry module and compatibility date:

```python title="cloudflare.config.py"
from wynd.config import Config, Worker

config = Config(
    worker=Worker(
        name="hello",
        entrypoint="src/main.py",
        compatibility_date="2026-09-08",
    ),
)
```

`entrypoint` is Wrangler's `main`. Wynd adds the `python_workers`
compatibility flag itself, so `compatibility_flags` lists only extra flags.

`wynd init` stops without writing anything if the directory already has any
of these files, or a `wrangler.json`, `wrangler.jsonc` or `wrangler.toml`:

```text
wynd: error: /home/you/hello already has src/main.py, wrangler.toml. wynd init only writes a new project.
```

Run the Worker:

```sh
cd hello
uvx wynd dev
```

```text
 ⛅️ wrangler 4.129.0
...
[wrangler:info] Ready on http://localhost:8787
```

In another terminal, `curl http://localhost:8787` prints
`Hello from Python!`.

Then run `uv sync`. It creates `.venv` on Python 3.14 and installs Wynd from
the `dev` group, so your editor resolves `wynd.config`. Wynd itself doesn't
need it, because `uvx wynd` runs the config with its own copy. Wynd never
installs the `dev` group into `python_modules/`, so it doesn't reach the
Worker.

## Add bindings

`env` maps each binding name to a binding from `wynd.config.bindings`. This
config adds a text and a JSON variable, a secret, KV, D1 and a Durable
Object, plus a cron trigger:

```python title="cloudflare.config.py"
from wynd.config import Config, Worker, bindings, exports, triggers


def config(mode):
    staging = mode == "staging"
    suffix = "-staging" if staging else ""
    return Config(
        worker=Worker(
            name=f"counter{suffix}",
            entrypoint="src/main.py",
            compatibility_date="2026-09-08",
            env={
                "GREETING": bindings.Text("hello"),
                "LIMITS": bindings.Json({"uploads": 10, "users": 3}),
                "API_TOKEN": bindings.Secret(),
                "CACHE": bindings.KV(id="<your KV namespace id>"),
                "DATABASE": bindings.D1(name=f"counter-db{suffix}"),
                "COUNTER": bindings.DurableObject("Counter"),
                "DEBUG": staging and bindings.Text("1"),
            },
            exports={"Counter": exports.DurableObject()},
            triggers=[triggers.Cron("0 * * * *")],
        ),
    )
```

```python title="src/main.py"
from env import Env
from workers import DurableObject, Response, WorkerEntrypoint


class Counter(DurableObject[Env]):
    async def increment(self) -> int:
        value = (await self.ctx.storage.get("value") or 0) + 1
        await self.ctx.storage.put("value", value)
        return value


class Default(WorkerEntrypoint[Env]):
    async def fetch(self, request):
        value = await self.env.COUNTER.get_by_name("main").increment()
        return Response(f"{self.env.GREETING}: {value}")
```

`config` here is a function of the mode. A config with no modes sets
`config = Config(...)`, as `wynd init` writes it. A `None` or `False` value
leaves the binding out, so `"DEBUG": staging and bindings.Text("1")` exists
only in staging.

`bindings.DurableObject("Counter")` names a class in `exports`, and
`exports.DurableObject()` declares that class with SQLite storage. Wynd
compiles both into the Wrangler config, so the Worker needs no
`migrations`.

Set the secret's local value in `.dev.vars`, beside `cloudflare.config.py`:

```text title=".dev.vars"
API_TOKEN=local-token
```

Run it in staging mode:

```sh
uvx wynd dev -e staging
```

Wrangler lists the staging bindings, including `DEBUG`, and two requests to
`http://localhost:8787` print `hello: 1` and `hello: 2`:

```text
Using secrets defined in .dev.vars
Your Worker has access to the following bindings:
Binding                                       Resource                  Mode
env.COUNTER (Counter)                         Durable Object            local
env.CACHE (<your KV namespace id>)            KV Namespace              local
env.DATABASE (counter-db-staging)             D1 Database               local
env.GREETING ("hello")                        Environment Variable      local
env.LIMITS ({"uploads":10,"users":3})         Environment Variable      local
env.DEBUG ("1")                               Environment Variable      local
env.API_TOKEN ("(hidden)")                    Environment Variable      local
```

Without `.dev.vars`, Wrangler warns
`Missing required secrets: API_TOKEN.` and the Worker starts without it.
For production, set the secret with `uvx wynd secret put API_TOKEN`.

### Bindings and their types

Each binding class compiles to one Wrangler entry, with the dictionary key as
the binding name. The [reference](config-reference.md#bindings) lists every
class, its arguments and its Wrangler entry.

`wynd types` types a `Secret()` as `str` in `Env`, from the
`secrets.required` list the class compiles to. You don't need a `.dev.vars`
entry for the type. You need one only for a local value. See
[Generate typed bindings](types.md).

### Declare Durable Objects

`exports` maps each class name in the entry module to its kind.
`exports.DurableObject()` declares a class the Worker runs. The other four
Durable Object states record a class you deleted, renamed, transferred out or
expect to receive, and `exports.Worker` and `exports.Workflow` declare
`WorkerEntrypoint` and `WorkflowEntrypoint` classes. The
[reference](config-reference.md#exports) lists each one.

A `Container` goes on the Durable Object export that runs it, and needs
`storage="sqlite"`. The class itself comes from
[Containers](../containers/overview.md):

```python
from wynd.config import Container, exports

image = Container(name="image", dockerfile="./Dockerfile", max_instances=3)
exports.DurableObject(container=image)
```

`Container` takes exactly one of `dockerfile` and `image`, a prebuilt image
reference. Paths are relative to `cloudflare.config.py`.

> **Warning**
>
> Wrangler 4.129.0 uploads the `exports` map in place of `migrations`.
> `wynd dev` runs Durable Objects declared this way, but no Wynd test deploys
> them to Cloudflare yet. Before you move a deployed Worker whose Wrangler
> config has `migrations` to `cloudflare.config.py`, deploy the change to a
> copy of the Worker first. A wrong state can delete a class's data.

### Triggers and other settings

`triggers` takes `triggers.Cron`, `triggers.Fetch` for a route and
`triggers.Queue` for a queue consumer. Declare every cron trigger in the
config. Wynd always writes `triggers.crons`, as `[]` when the config has no
`Cron`, so a deploy removes any cron trigger you added in the dashboard.

`Worker` also takes settings such as `domains`, `assets`, `observability`,
`placement`, `limits` and `rules`, and `Config` takes `account_id` and
`compliance_region`. Import the settings classes from `wynd.config`. The
[reference](config-reference.md#worker) lists every field.

## Use modes

The mode is the value of `-e` or `--env`, or of `$CLOUDFLARE_ENV` when you
pass neither. Wynd calls `config(mode)` with it, and with `None` when there
is none:

```sh
uvx wynd dev                  # config(None)
uvx wynd deploy -e staging    # config("staging")
```

Wrangler never sees the mode. Wynd removes `-e` and `--env` from the
arguments and runs Wrangler without `CLOUDFLARE_ENV`, because the compiled
file has no `env` sections. Wrangler therefore doesn't append the mode to
the Worker's name, so name staging Workers yourself, as the example does
with `suffix`. It also reads `.dev.vars` in every mode, and never
`.dev.vars.staging`.

A mode may hold only letters, digits, `_` and `-`, because it becomes part of
a file name.

`src/env.py` holds the bindings of the last mode a command compiled. If modes
have different bindings, run `uvx wynd types` in the mode you check types
against before you commit.

## Check the compiled config

Wynd compiles `cloudflare.config.py` into a Wrangler file under `.wynd/run/`:

| Mode      | Compiled file                                   |
| --------- | ----------------------------------------------- |
| No mode   | `.wynd/run/cloudflare.config.py.json`           |
| `staging` | `.wynd/run/cloudflare.config.py.staging.json`   |

For a config in a subdirectory of the project, the path includes that directory.

Every Wynd command compiles it first, passthrough commands such as
`wynd whoami` included, so every command runs your config's Python. `wynd dev`
and passthrough commands write the compiled file. Wynd rewrites it only when
its text changes, so a running `wynd dev` doesn't reload when nothing changed.
The `.wynd/` line in the `.gitignore` that `wynd init` writes keeps these files
out of Git.

`.wynd/run/` stands in for the config's directory, as
[Where Wynd keeps files](commands.md#where-wynd-keeps-files) describes, and Wynd
rewrites relative paths so they still resolve from there. Plain `npx wrangler`
doesn't look in `.wynd/`, so it ignores these files.

A running `wynd dev` watches `cloudflare.config.py` and compiles it again when
you save, and Wrangler reloads. A broken edit prints the error, and `wynd dev`
keeps serving the last good config. A reload doesn't reinstall packages or
rewrite `env.py`, so restart `wynd dev` after you change the compatibility date,
flags or bindings.

To see what Wrangler gets, run a passthrough command in the mode and open the
file:

```sh
uvx wynd whoami -e staging
cat .wynd/run/cloudflare.config.py.staging.json
```

```json title=".wynd/run/cloudflare.config.py.staging.json"
{
  "name": "counter-staging",
  "main": "../../src/main.py",
  "compatibility_date": "2026-09-08",
  "compatibility_flags": [
    "python_workers"
  ],
  "exports": {
    "Counter": {
      "type": "durable-object",
      "storage": "sqlite"
    }
  },
  "vars": {
    "GREETING": "hello",
    "LIMITS": {
      "uploads": 10,
      "users": 3
    },
    "DEBUG": "1"
  },
  "secrets": {
    "required": [
      "API_TOKEN"
    ]
  },
  "kv_namespaces": [
    {
      "binding": "CACHE",
      "id": "<your KV namespace id>"
    }
  ],
  "d1_databases": [
    {
      "binding": "DATABASE",
      "database_name": "counter-db-staging"
    }
  ],
  "durable_objects": {
    "bindings": [
      {
        "name": "COUNTER",
        "class_name": "Counter"
      }
    ]
  },
  "triggers": {
    "crons": [
      "0 * * * *"
    ]
  }
}
```

`uvx wynd deploy --dry-run -e staging` then stages the upload from this file
into `.wynd/build/wrangler.json`, as for any Wrangler config, and Wrangler
prints the bindings it would deploy.

## How Wynd finds the config

Without `--config`, Wynd looks for `cloudflare.config.py` in the working
directory and each parent, and runs Wrangler's own search for
`wrangler.json`, `wrangler.jsonc` and `wrangler.toml`. The file in the deeper
directory wins. If one directory has both kinds, every command stops:

```text
wynd: error: /home/you/hello has cloudflare.config.py and wrangler.jsonc. Keep one.
```

`--config cloudflare.config.py` names the file directly.

The file runs with its own directory on `sys.path`, so it can import a
helper module beside it. It can't import your Worker code, which needs the
Workers runtime.

## Move a Wrangler config to Python

1. Write `cloudflare.config.py` beside your Wrangler file, with the same
   name, `main` as `entrypoint`, date, flags and bindings. Write each `env`
   section as a branch on the mode, and each Durable Object class in
   `migrations` as its state in `exports`.
2. Delete the Wrangler file, since Wynd refuses a directory with both.
3. Run `uv add --dev wynd`, so your editor resolves `wynd.config`.
4. Run `uvx wynd deploy --dry-run` for each mode, and compare Wrangler's
   bindings list with the one your Wrangler file gave.

Read the warning under [Declare Durable Objects](#declare-durable-objects)
before you deploy.

## Errors

Each error stops the command before Wrangler runs. A message with a line
number, such as
`wynd: error: cloudflare.config.py:4: compatibility_date 'soon' isn't a YYYY-MM-DD date`,
comes from one object. A list under `cloudflare.config.py has errors:` comes
from rules across objects, and Wynd reports all of them at once. The
[reference](config-reference.md#errors) lists each message and the change that
triggers it.
