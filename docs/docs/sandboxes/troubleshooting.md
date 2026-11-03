---
title: "Troubleshoot crosswind-sandboxes errors"
description: "Causes and fixes for crosswind-sandboxes errors: shim protocol failures, a stopped container, S3 mount codes, credential provider failures and backup errors."
---

Start with the exception. `SandboxFileError`, `SandboxS3MountError` and `SandboxBackupError` have a `code`, which is what the entries below are named after, and a `detail` with the reason. See [Errors](api.md#errors) for the full list.

!!! note
    The shim isn't part of this package, so we can't always tell from outside why it failed. The package also discards its `stderr`. If an entry says "usually", treat it as the first thing to check.

## Calling any method

### `sandbox-shim protocol <N> is not supported` or `sandbox-shim returned invalid protocol magic`

**Cause.** The image's `sandbox-shim` doesn't match this package.

**Fix.** Copy the shim from `docker.io/cloudflare/sandbox:1.0.3`, as in [the Dockerfile](getting-started.md#2-write-the-dockerfile), then rebuild the image.

### `sandbox-shim returned truncated control data`

**Cause.** The shim stopped before it finished replying. Usually the container stopped during the call, or the shim couldn't run at all.

**Fix.** Check that `CMD` keeps the container running, as no class here starts or restarts it for you. The shim is a static `linux/amd64` binary, so check the image can run it too.

### `sandbox-shim exited with code <N>`

**Cause.** The shim replied, then exited with an error.

**Fix.** Check that the container stayed up during the call. This is a [`SandboxProtocolError`](api.md#errors), but it isn't a version problem unless the error above also appears.

### A JavaScript error from `exec`

**Cause.** Usually the container isn't running, or the image has no `/usr/local/bin/sandbox-shim`. The runtime's error passes through as it is, so `except SandboxError` won't catch it.

**Fix.** Call `self.ctx.container.start()` first, and check the `COPY` line in your Dockerfile.

## Working with files

### `EXDEV` from `rename`

**Cause.** You moved a file across filesystems, for example into an [S3 mount](s3-mounts.md). We don't fall back to copying.

**Fix.** Read the file and write it to the destination, then remove the original.

## Mounting a bucket

### `self.ctx.exports.S3Gateway` doesn't exist

**Cause.** `ctx.exports` only has classes your entry module defines or imports.

**Fix.** Import `S3Gateway` in your entry module, even though you never call it. The same goes for `DirectoryBackupGateway`.

### `S3_MOUNT_CONFLICT`

**Cause.** A mount with different settings already holds the path.

**Fix.** Call `unmount` first, or mount somewhere else.

### `S3_MOUNT_BUSY`

**Cause.** A process is still using the path. If `unmount` raised this, the gateway keeps refusing that mount's requests, even though the path is still mounted.

**Fix.** Stop the process, then try again.

### `S3_MOUNT_INCOMPATIBLE` or `S3_MOUNT_FAILED`

**Cause.** The shim couldn't mount the bucket. The reason is in the exception's `detail`.

**Fix.** Check the image contains `s3fs`, and check `detail` against your `source`. [`inspect`](api.md#inspect) shows what's holding the path now. An `attachment` status of `"incompatible"` is a mount from another shim protocol version, and `"unmanaged"` is another filesystem.

### Reads or writes fail inside the container after a successful mount

**Cause.** The gateway checks each request `s3fs` sends and answers `403` to any it doesn't allow. `s3fs` only sees a failed request, and the reason stays in the response, so it isn't logged anywhere.

**Fix.** Call [`inspect`](api.md#inspect) first. It sends its own test request, so it can show that the route or credentials are the problem, but not what's wrong with a request `s3fs` made.

| `inspect` shows | What to do |
| --------------- | ---------- |
| `gateway.status` is `"reachable"`, and `upstream.status` is `"rejected"` with `reason` `"access"` and a `detail` of `mount route has been revoked` | An `unmount`, or a `mount` that failed or was aborted, denied this route. Call `mount` again. |
| `gateway.status` is `"error"`, with `reason` `"credential-provider"` | See [Credential provider errors](#credential-provider-errors). |
| `gateway.status` is `"reachable"`, and `upstream.status` is `"usable"` | The route works, so look at the operation that failed. |

If `inspect` looks fine, these are the requests the gateway refuses:

- Writes, on a mount with `access="read-only"`.
- Requests outside the bucket or `key_prefix`, or that aren't an operation `s3fs` normally makes.
- Requests with an `x-amz-*` header it doesn't allow, or an aws-chunked payload. Some `s3fs_options` can make `s3fs` send these, so try removing any you've set.

Also check you call `mount()` before registering `interceptAllOutboundHttp()`. That catch-all takes every host registered after it, including the gateway's.

### Credential provider errors

These come from your [provider](s3-mounts.md) fetcher, and appear as the `detail` of `inspect`'s `gateway` entry.

| `detail` | Fix |
| -------- | --- |
| `credential provider request failed` | The fetcher raised, or couldn't be reached. Check its logs. |
| `credential provider returned HTTP <status>` | Answer `GET https://credentials.sandbox.internal/` with a success status. |
| `credential provider response is too large` | Keep the response to 16,384 bytes. |
| `credential provider returned invalid JSON` | Return a JSON object. |
| `credential provider returned invalid or expired credentials` | Send only `accessKeyId`, `secretAccessKey`, `expiresAt` and optionally `sessionToken`, since any other key is refused. `expiresAt` is an integer in milliseconds, and has to be in the future. |
| `credential provider failed` | Something else went wrong while calling your provider. Check its logs. |

## Backing up a directory

### `env.<NAME> is not an R2 bucket binding`

**Cause.** The binding is missing from the Wrangler config, or isn't an R2 bucket. The gateway raises this, so it can reach you as a JavaScript error or a failed transfer, not a `SandboxError`.

**Fix.** Add an R2 binding with that name.

### `BACKUP_NOT_FOUND`, `BACKUP_INTEGRITY` or `BACKUP_TRANSFER`

See [Handle errors](backups.md#handle-errors). `BACKUP_TRANSFER` can also come from calling `intercept()` during a backup or restore, because that replaces the operation's access to the bucket. Only call it right after the container starts, as in [Use with a catch-all intercept](backups.md#use-with-a-catch-all-intercept).
