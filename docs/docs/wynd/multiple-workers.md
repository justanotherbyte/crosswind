---
title: "Run several Workers from one Python project"
description: "Keep more than one Worker under one pyproject.toml: lay out their configs, run each with --cwd or --config, bind them to each other, and what they share."
---

Use this layout when two or more Workers share dependencies or code, such as
an API Worker and an auth Worker it calls through a service binding. Each
Worker gets its own config and source directory, and Wynd keeps a separate
`python_modules/` for each. They share one `pyproject.toml`, so they share
one set of dependencies and one `pylock.toml`.

## Lay out the project

Put each Worker's config in its own directory under the project root, with
its source in a subdirectory beside it:

```text
pyproject.toml
pylock.toml
api/
  cloudflare.config.py
  src/main.py
auth/
  cloudflare.config.py
  src/main.py
```

Each config's source directory has to be a subdirectory of the directory
that holds the config, as for a single Worker. See
[Source directory](commands.md#source-directory). The configs can be any mix
of `cloudflare.config.py` and Wrangler files.

The auth Worker exposes an entrypoint, and the API Worker binds to it by the
auth Worker's name:

```python title="auth/cloudflare.config.py"
from wynd.config import Config, Worker, exports

config = Config(
    worker=Worker(
        name="auth",
        entrypoint="src/main.py",
        compatibility_date="2026-09-08",
        exports={"Sessions": exports.Worker()},
    ),
)
```

```python title="api/cloudflare.config.py"
from wynd.config import Config, Worker, bindings

config = Config(
    worker=Worker(
        name="api",
        entrypoint="src/main.py",
        compatibility_date="2026-09-08",
        env={"AUTH": bindings.Service("auth", export="Sessions")},
    ),
)
```

`bindings.DurableObject` and `bindings.Workflow` also take `worker=` to bind
to a class in another Worker. [Bindings](config-reference.md#bindings) lists
their arguments.

## Run one Worker

Pick the Worker with `--cwd`, so Wynd finds its config by searching from that
directory:

```sh
uvx wynd --cwd api dev
uvx wynd --cwd auth deploy
```

`--config api/cloudflare.config.py` from the project root does the same. Each
Worker gets its own run directory, such as `.wynd/run/api/`, with its own
`python_modules/`, staged config and local D1, KV and R2 data. `wynd types`
writes `env.py` into each Worker's own source directory.

The first `wynd dev`, `wynd sync` or upload for each Worker installs its
packages. After that, each
Worker reinstalls only when its own inputs change, as
[When packages reinstall](commands.md#when-packages-reinstall) describes.

## What the Workers share

| Shared                | What it means                                                        |
| --------------------- | -------------------------------------------------------------------- |
| `[project] dependencies` | Every Worker installs every dependency. Pruning drops the packages a Worker never imports from its upload |
| `pylock.toml`         | Each install locks for the Worker being installed and rewrites the file |
| `[tool.wynd]`         | `keep`, `allow-build` and `keep-docstrings` apply to every Worker    |
| `.wynd/build/` and `.wynd/shake.txt` | Each upload replaces them, so they hold the last Worker you uploaded |

Give every Worker the same `compatibility_date` and `python_workers` flags.
Workers on different Python targets lock the same dependencies against
different package indexes, so each install can rewrite `pylock.toml`. A
changed `pylock.toml` makes the other Worker reinstall on its next command,
which rewrites the lock again. With one target, each Worker installs once
and stays installed.

Run the upload commands for one Worker at a time, because they share
`.wynd/build/`.

## Next steps

- [How Wynd builds a Worker](architecture.md) explains the run directories
  and the build tree.
- [Commands and configuration](commands.md) has every flag, including
  `--cwd` and `--config`.
