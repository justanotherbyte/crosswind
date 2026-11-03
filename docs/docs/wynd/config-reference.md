---
title: "cloudflare.config.py reference"
description: "Every class and field in wynd.config, the Wrangler entry each one compiles to, the errors a config can raise and what it doesn't support."
---

Each class here compiles to part of a Wrangler config. Import them from
`wynd.config`. [Write config in Python](python-config.md) shows how to use
them, and [Generated API](generated-api.md) has each class's signature.

> **Warning**
>
> Python config support is new. Its classes and fields may change between
> minor releases.

## `Config`

`cloudflare.config.py` sets `config` to a `Config`, or to a function that
takes the [mode](python-config.md#use-modes) and returns one.

| Field               | Type                                   | Wrangler key        |
| ------------------- | -------------------------------------- | ------------------- |
| `worker`            | `Worker`                               | The top level       |
| `account_id`        | `str`                                  | `account_id`        |
| `compliance_region` | `"public"` or `"fedramp-high"`         | `compliance_region` |

## `Worker`

`name` and `compatibility_date` are required. Each other field compiles to the
Wrangler key of the same name unless the table says otherwise.

| Field                 | Type                                             |
| --------------------- | ------------------------------------------------ |
| `name`                | `str`                                            |
| `compatibility_date`  | `str` in `YYYY-MM-DD` form, checked when the `Worker` is built |
| `entrypoint`          | `str`, compiled to `main`                        |
| `base_dir`            | `str`                                            |
| `compatibility_flags` | `list[str]`, added after `python_workers`, which Wynd always writes first |
| `env`                 | `dict[str, Binding]`. See [Bindings](#bindings)  |
| `exports`             | `dict[str, Export]`. See [Exports](#exports)     |
| `triggers`            | `list[Trigger]`. See [Triggers](#triggers)       |
| `domains`             | `list[str]`, compiled to `routes` with `custom_domain: true` |
| `assets`              | `Assets(directory, html_handling=None, not_found_handling=None, run_worker_first=None, base_path=None)` |
| `observability`       | `Observability(enabled=None, head_sampling_rate=None, redact_query_string=None, logs=None, traces=None)` |
| `placement`           | `Placement(mode, hint=None)`, with `mode` as `"off"` or `"smart"` |
| `limits`              | `Limits(cpu_ms=None, subrequests=None)`          |
| `logpush`, `workers_dev`, `preview_urls` | `bool`                        |
| `tail_consumers`      | `list[TailConsumer(worker)]`, compiled to `tail_consumers[].service` |
| `rules`               | `list[Rule(type, globs, fallthrough=None)]`, compiled to `rules[]` |

A field left as `None`, or an empty list, doesn't appear in the compiled file.
The exception is `triggers.crons`, described under [Triggers](#triggers).

`Rule` sets the module type of files under the source directory that match
`globs`. `type` is one of `"ESModule"`, `"CommonJS"`, `"CompiledWasm"`,
`"Text"`, `"Data"`, `"PythonModule"` and `"PythonRequirement"`. Wynd copies
the whole source directory when it deploys, so the files a rule matches ship
with the Worker:

```python
from wynd.config import Rule

Rule(type="Text", globs=["**/*.txt"], fallthrough=True)
```

> **Warning**
>
> `entrypoint=None` compiles to a config with no `main`. Wynd can't upload
> that config yet: `wynd deploy`, `wynd versions upload` and `wynd preview`
> stop with `wynd: error: cloudflare.config.py sets no main`, and so do
> `wynd dev` and `wynd types` on a crosswind-workers project. See
> [Troubleshooting](troubleshooting.md#sets-no-main).

## Bindings

`env` maps each binding name to a binding from `wynd.config.bindings`. Each
class compiles to one Wrangler entry, with the dictionary key as the binding
name. A `None` or `False` value leaves the name out.

| Class                                        | Wrangler entry                       |
| -------------------------------------------- | ------------------------------------ |
| `Text(value)`                                | `vars`                               |
| `Json(value)`                                | `vars`, with any JSON value          |
| `Secret()`                                   | `secrets.required`                   |
| `KV(id=None)`                                | `kv_namespaces`                      |
| `D1(name=None, id=None)`                     | `d1_databases`, as `database_name` and `database_id` |
| `R2(name=None, jurisdiction=None)`           | `r2_buckets`, as `bucket_name`. `jurisdiction` is `"eu"` or `"fedramp"` |
| `Queue(name, delivery_delay=None)`           | `queues.producers`                   |
| `DurableObject(export, worker=None)`         | `durable_objects.bindings`. Without `worker`, `export` must be a live Durable Object in this Worker's `exports` |
| `Workflow(export, worker=None, name=None)`   | `workflows`. Without `worker`, `export` must be an `exports.Workflow`. With `worker`, `name` is required |
| `Service(worker, export=None, props=None)`   | `services`. `export` names an entrypoint of the other Worker |
| `AI()`                                       | `ai`, one per Worker                 |
| `VersionMetadata()`                          | `version_metadata`, one per Worker   |
| `Assets()`                                   | `assets.binding`, one per Worker. Needs `Worker.assets` |

`wynd types` types a `Secret()` as `str` in `Env`. See
[Types for each binding](types.md#types-for-each-binding).

## Exports

`exports` maps each class name in the entry module to its kind. Durable
Object classes take one of five states:

| Class                                          | Declares                                    |
| ---------------------------------------------- | ------------------------------------------- |
| `exports.DurableObject(storage="sqlite", container=None)` | A Durable Object class the Worker runs. `storage` is `"sqlite"` or `"legacy-kv"` |
| `exports.DeletedDurableObject()`               | A class you removed, whose data Cloudflare deletes |
| `exports.RenamedDurableObject(renamed_to=...)` | A class you renamed. `renamed_to` must be a live Durable Object in `exports` |
| `exports.TransferredDurableObject(transferred_to=...)` | A class you moved to another Worker |
| `exports.ExpectingTransferDurableObject(transfer_from=..., storage="sqlite", container=None)` | A class this Worker receives from another Worker |
| `exports.Worker(cache=None)`                   | A `WorkerEntrypoint` class, with `cache` as `cache.enabled` |
| `exports.Workflow(name, limits_steps=None)`    | A `WorkflowEntrypoint` class. It needs exactly one `bindings.Workflow` in `env` |

Wynd compiles these to Wrangler's `exports` map, which Wrangler 4.129.0
uploads in place of `migrations`. Read the warning under
[Declare Durable Objects](python-config.md#declare-durable-objects) before you
change a deployed Worker's states.

### `Container`

`Container(name, dockerfile=None, image=None, build_context=None, build_vars=None, instance_type=None, max_instances=None)`
goes on the live Durable Object export that runs it, which needs
`storage="sqlite"`. It compiles to an entry in `containers[]`, with
`dockerfile` as `image`, `build_context` as `image_build_context` and
`build_vars` as `image_vars`.

`Container` takes exactly one of `dockerfile` and `image`, a prebuilt image
reference. `build_context` and `build_vars` need `dockerfile`. Paths are
relative to `cloudflare.config.py`. Two exports can't run containers with
the same `name`. [Containers](../containers/overview.md) is the SDK for the
Durable Object class itself.

## Triggers

| Class                                   | Wrangler entry          |
| --------------------------------------- | ----------------------- |
| `triggers.Cron(schedule)`               | `triggers.crons`        |
| `triggers.Fetch(pattern, zone=None)`    | `routes`, with `zone` as `zone_name` |
| `triggers.Queue(name, max_batch_size=None, max_batch_timeout=None, max_retries=None, max_concurrency=None, retry_delay=None, dead_letter_queue=None)` | `queues.consumers`, with `name` as `queue` |

Wynd always writes `triggers.crons`, as `[]` when the config has no `Cron`.
Wrangler clears the deployed Worker's cron triggers when that list is empty,
so a cron trigger you add in the dashboard goes away on the next deploy.

`Worker.domains` holds custom domains, and `triggers.Fetch` holds routes.

## Errors

Each error stops the command before Wrangler runs, and the command exits
with `1`.

| Cause | How to trigger it | Message |
| --- | --- | --- |
| A value fails a check inside one object | `compatibility_date="soon"` | `wynd: error: cloudflare.config.py:4: compatibility_date 'soon' isn't a YYYY-MM-DD date` |
| A misspelled or missing argument | `Worker(nmae="hello", ...)` | `wynd: error: cloudflare.config.py:4: Worker.__init__() got an unexpected keyword argument 'nmae'` |
| Rules that span objects fail | A binding to a class missing from `exports`, and a second `AI()` | `wynd: error: cloudflare.config.py has errors:` followed by one line per rule, such as `env['COUNTER']: exports has no live Durable Object named 'Counter'` and `env['AI2']: a Worker has one AI binding` |
| No `config` | The file names its value `settings` | ``wynd: error: cloudflare.config.py must set `config` to a Config, or to a function that takes the mode and returns one`` |
| A value JSON can't hold | `bindings.Json({"a", "b"})`, a set | `wynd: error: cloudflare.config.py holds a value JSON can't encode: Object of type set is not JSON serializable` |
| A mode with other characters | `-e eu.west` | `wynd: error: mode 'eu.west' may hold only letters, digits, _ and -` |
| Both config kinds in one directory | A `wrangler.jsonc` beside `cloudflare.config.py` | `wynd: error: <directory> has cloudflare.config.py and wrangler.jsonc. Keep one.` |

The line number is the line in `cloudflare.config.py` where the failing call
starts, here `worker=Worker(`. Wynd reports every rule that spans objects in
one message, so you can fix them all at once.

Any other exception in your config, such as a `NameError`, prints Python's
traceback, which ends at the line in `cloudflare.config.py`.

Errors Wynd finds after compiling, such as a bad source directory, name your
config file, for example
`main and base_dir must point into a subdirectory beside cloudflare.config.py`.
Fix them in `cloudflare.config.py`. [Troubleshoot Wynd errors](troubleshooting.md)
lists them.

## What it doesn't support

- Binding kinds other than those in [Bindings](#bindings), such as
  Vectorize, Hyperdrive, Browser Rendering, rate limits and Analytics Engine.
  Keep a Wrangler file for a Worker that needs one.
- Wrangler's `migrations`, `env` sections, `unsafe` and per-binding `dev`
  settings.
- `.dev.vars.<mode>` files. Wrangler reads `.dev.vars` in every mode.
- Importing one `cloudflare.config.py` from another, since the dotted file
  name can't be imported.
- Running without Wynd. Plain `wrangler` doesn't read `cloudflare.config.py`.
