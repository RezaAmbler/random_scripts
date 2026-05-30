# CLAUDE.md

This repo is a personal collection of **random, standalone scripts**. Each script
solves a one-off or recurring task and is otherwise unrelated to the others.

## Organization convention

**Keep scripts categorized into folders by purpose.** Do not dump scripts in the
repo root. When a new script arrives, place it in the most fitting category folder,
creating a new one if nothing fits.

Current categories (extend as needed):

| Folder        | Purpose                                              |
|---------------|------------------------------------------------------|
| `qnap/`       | Scripts for the QNAP NAS (maintenance, file ops)     |

When adding a category, pick a short, lowercase, descriptive folder name
(e.g. `media/`, `backup/`, `network/`, `git/`, `macos/`).

## Per-script expectations

- Each script should be self-contained and runnable on its own.
- Add a short header comment at the top of every script: what it does, how to run
  it, and any dependencies/assumptions (OS, runtime version, required tools).
- If a category grows non-trivial, add a `README.md` inside that folder describing
  the scripts in it.
- Prefer clear over clever; these are tools the user returns to occasionally.

## Maintaining this repo

- Update the category table above whenever a new folder is introduced.
- Keep the root `README.md` index in sync with the folders that exist.
- Match the language/style conventions already present in a given folder.
