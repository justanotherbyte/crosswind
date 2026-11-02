---
title: "Introduction to Crosswind"
description: "What Crosswind is, which package does what, how they fit together in one Python Worker, and which ones to install for your project."
---

Crosswind is a set of Python SDKs for
[Python Workers](https://developers.cloudflare.com/workers/languages/python/).

Cloudflare currently ships great developer SDKs for TypeScript, not for Python, which is what Crosswind aims to solve. Each Crosswind package ports one of these SDKs, bringing familiar APIs to Python, while abstracting any Pyodide or FFI work from you.

Each Crosswind SDK speaks the same wire protocols as it's official counter-part. This means you can use any client-side tools provided by the official SDKs to speak to Crosswind.

| Package                                       | What it does                                                                         | Official Counterpart                                                                   |
| --------------------------------------------- | ------------------------------------------------------------------------------------ | ----------------------------------------------------------------------- |
| [`crosswind-workers`](workers/overview.md)    | The runtime SDK every Python Worker imports, with types on bindings, requests and Durable Objects. | [`workers-runtime-sdk`](https://github.com/cloudflare/workers-py)       |
| [`wynd`](wynd/overview.md)                    | Builds, runs and deploys Python Workers, and drops unused package files before upload. | [pywrangler](https://github.com/cloudflare/workers-py)                  |
| [`cf-agents`](agents/overview.md)             | Stateful agents with WebSocket clients, schedules, durable tasks and streaming chat. | [`@cloudflare/agents`](https://developers.cloudflare.com/agents/)                 |
| [`containers-py`](containers/overview.md)     | Starts, stops and routes requests to Cloudflare Containers, and filters their outbound traffic. | [`@cloudflare/containers`](https://github.com/cloudflare/containers)   |
| [`crosswind-sandboxes`](sandboxes/overview.md) | Reads and writes files, mounts S3 buckets and backs up directories in a sandbox container. | [`@cloudflare/sandbox`](https://developers.cloudflare.com/sandbox/)               |

## Why Crosswind?

- [x] Built on top of Durable Objects, just like the official SDKs. Everything is just a Durable Object.
- [x] Pythonic APIs. We replace TypeScript APIs with more Pythonic options where sensible.
- [x] No Pyodide or FFI faff. All APIs we expose expect standard Python types. If a conversion is needed, we handle it under the hood.
- [x] Fully-typed. You don't need to run a command generating a 10,000 line stub file. Our stubs already exist.

## Picking a Runtime SDK and a Build Tool

Cloudflare ships [`pywrangler`]() and [`workers-runtime-sdk`](). They're both great!

Crosswind provides it's own opinionated build tool [`wynd`](), and it's own runtime SDK, [`crosswind-workers`](). They are not required, but they were built to make working with Python Workers that bit nicer:

- [x] Auto-magically generate typed `Env` stub. Workers using `crosswind-workers` can accept this as a generic!
- [x] Keep Node.js build artifacts out of your Python codebase.
- [x] Write your worker configurations with a typed [`cloudflare.config.py`]() instead of using JSONC or TOML.

This is just a few things Crosswind gives you. You can choose to either `wynd` or `crosswind-workers`, both, or neither! All of Crosswind is still compatible with the official tools.

## Limits

Python Workers run on [Pyodide](https://pyodide.org/en/stable/), CPython compiled to WebAssembly. A package with C extensions works only if Pyodide
ships a build of it, so not every library on PyPI imports in a Worker. This is a limitation with the runtime itself, not the SDKs.

## Status

Every Crosswind package is alpha and pre-1.0. A minor release can rename or
remove an API, so pin exact versions and test each upgrade before you deploy
it.

!!! note
    Crosswind is an independent community project. It isn't affiliated with or
    endorsed by Cloudflare.

## Help, contributing and licensing

- Ask questions in the [Crosswind Discord](https://discord.gg/tbZTEeK35p), or
  open an issue on [GitHub](https://github.com/justanotherbyte/crosswind).
- Pull requests are welcome, including ones written with AI tools. Keep them
  to the code quality of the surrounding package.
- Each package uses the license of the TypeScript SDK it ports. The docs are
  MIT-licensed.
