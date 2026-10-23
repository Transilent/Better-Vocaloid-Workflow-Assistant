# Task state and artifact contracts

[简体中文](TaskContracts.zh-CN.md)

The assistant records processing progress separately from the work you complete in an editor. Completing MIDI extraction does not mean that tuning, rendering or publication is complete.

## Files and ownership

| Record | Purpose |
| --- | --- |
| `request.json` | The task's effective processing options, with saved export preferences kept separately. Continuing a task does not rewrite its request. |
| `status.json` | The execution plan, current step, elapsed time and per-step success, failure or cancellation. |
| Producer receipts | Inference and export signatures and hashes. They continue to govern cache reuse. Each voice and exported format can commit independently. |
| `artifacts.json` | A versioned index of generated files, their roles, SHA-256 hashes, producing steps and dependency revisions. It can be reconstructed from producer receipts. |
| `task.json` | The task name, explicitly selected tuned project, Mixdown and finished video, and the active publication snapshot. These selections survive index reconstruction. |

The artifact index supplements the existing receipts. It is not an installer manifest and does not authorize deleting files.

## Check files and continue

Select a task in **Tasks and results → Files and next step**. Checks run in the background. The list distinguishes verified, missing, modified, unreadable and outdated files. A modified file keeps its original expected hash until a producer replaces it or you explicitly register the completed file.

After tuning, register the edited project and exported Mixdown. You can then open the optional video assembly tool, or register an existing finished video and enter the publication workflow. Files stay in their current locations. Registering a new revision makes dependent older selections require review; it does not overwrite them.

The next-step guidance covers processing, retry, tuning, Mixdown, finished video, publication preparation and manual review in the creator center. **Final publication remains manual.** Upload assistance records the platform's preparation state and never assumes that a post was published.

## Resume and recovery

An upstream rerun invalidates downstream evidence before processing starts. Successfully committed voice and format outputs remain individually recorded if another output fails. Existing inference and format signatures decide what can be reused on retry.

Old tasks are indexed from their existing receipts when continued or checked. When an old file has no recorded hash, its current contents provide the migration baseline; this cannot establish what the file contained before migration. Rebuilding the index preserves a backup and does not modify media or projects. Unsupported schemas, malformed records and paths outside a task's allowed automatic-output boundary are rejected.

Quick task-list guidance checks metadata and file presence. Open **Files and next step** for full content hashes. No file checker can prevent a separate editor from saving again after a check; inputs are checked again by the operation that consumes them.

## Cleanup

Cleanup checks live task references again before deletion. A cache or temporary directory containing a registered Mixdown, selected project, calibration input or another live dependency is protected. Protected entries explain which task references them. External references are read-only and never expand the cleanup allowlist. Deleting a task that another task still references is blocked until that reference is changed or removed.

Removing a registration leaves the underlying file untouched. Only explicit task deletion removes task files. Existing model, session and publication cleanup boundaries still apply.
